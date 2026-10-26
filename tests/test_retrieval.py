from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade
from sqlalchemy import event, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError

from app import create_app
from models import CachedResult, Concert, SavedConcert, SavedSearch, SearchEvent, User, db
from services.catalog import backfill_catalog, ingest_events, sync_saved_concerts
from services.retrieval import LIBRARY_SORTS, parse_options, retrieve, retrieve_library, search_statement


NOW = datetime(2026, 10, 24, 12)
MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")


def concert(identifier, title="Hozier", **overrides):
    return {"id": identifier, "name": title, "artist": "Hozier", "venue": "The Wiltern",
            "location": "Los Angeles, CA", "date_iso": "2027-01-10", "date_label": "JAN 10, 2027",
            "source_label": "JamBase", "url": "https://example.com/concert", **overrides}


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ingest_events([concert(str(i), title=f"Hozier show {i:02d}") for i in range(15)], "jambase", NOW)
        ingest_events([concert("past", "Radiohead", artist="", tour="A Moon Shaped Pool")], "setlistfm", NOW)
        user = User(google_sub="retrieval-owner", title="Listener", source="google", source_url="https://accounts.google.com")
        db.session.add(user)
        db.session.flush()
        saved = SavedSearch(user_id=user.id, title="Weekend plans", description="Invite friends", source="encore",
            source_url="/", query_key="fixture", filters={}, snapshots={source: {"1": {
                "retrieved_at": NOW.isoformat(), "events": [dict(row.card_data) for row in
                    db.session.scalars(db.select(Concert).where(Concert.source == source))]}}
                for source in ("jambase", "setlistfm")})
        db.session.add(saved)
        sync_saved_concerts(saved)
        db.session.commit()
    return app


def ids(options):
    return [row.external_id for row in retrieve(parse_options(options)).items]


def owner_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = {"sub": "retrieval-owner", "name": "Listener"}
    return client


def test_search_is_database_only_and_paginates_before_display(app):
    statements = []
    with app.app_context():
        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement.upper())
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch("services.api_service.ApiService.get", side_effect=AssertionError("API called")):
                first = owner_client(app).get("/profile/searches/1?q=Hozier&source=jambase&location=Los+Angeles&sort=date_asc")
            assert first.status_code == 200
            assert first.data.count(b'<article class="concert-card') == 12
            assert b"Showing 1" in first.data and b"of 15" in first.data
            assert b"Page 1 of 2" in first.data
            assert b"page=2" in first.data and b"source=jambase" in first.data
            assert b"location=Los+Angeles" in first.data and b"sort=date_asc" in first.data
            second = owner_client(app).get("/profile/searches/1?q=Hozier&source=jambase&location=Los+Angeles&sort=date_asc&page=2")
            assert second.status_code == 200
            assert second.data.count(b'<article class="concert-card') == 3
            assert b"Previous" in second.data
            assert any("LIMIT" in stmt and "ENCORE_SAVED_CONCERTS" in stmt for stmt in statements)
            logs = db.session.scalars(db.select(SearchEvent)).all()
            assert len(logs) == 1 and logs[0].result_count == 15
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)


def test_normalization_and_original_query(app):
    with app.app_context():
        assert len(ids({"q": "  HOZIER  "})) == 12
    page = owner_client(app).get("/profile/searches/1?q=++HOZIER++")
    assert b'value="  HOZIER  "' in page.data
    with app.app_context():
        log = db.session.scalar(db.select(SearchEvent))
        assert log.query == "  HOZIER  " and log.normalized_query == "hozier"


def test_searches_venue_tour_and_setlist_artist(app):
    with app.app_context():
        assert ids({"q": "A Moon Shaped Pool"}) == ["past"]
        assert ids({"q": "RADIOHEAD"}) == ["past"]
        assert len(ids({"q": "WILTERN", "source": "setlistfm"})) == 1


def test_boolean_filters_and_date_boundaries(app):
    with app.app_context():
        options = {"q": "Hozier", "source": "jambase", "location": "LOS ANGELES",
                   "date_from": "2027-01-10", "date_to": "2027-01-10"}
        assert retrieve(parse_options(options)).total == 15
        assert not ids({**options, "source": "setlistfm"})
        assert not ids({**options, "date_from": "2027-01-11", "date_to": ""})
        assert not ids({**options, "location": "Seattle"})


def test_ranking_exact_partial_venue_tour_and_stable_ties(app):
    with app.app_context():
        db.session.query(Concert).delete()
        ingest_events([concert("exact", "Hozier", venue="Other"),
                       concert("partial", "Hozier tribute", artist="Other", venue="Other"),
                       concert("venue", "Other", artist="Other", venue="Hozier Hall"),
                       concert("tour", "Other", artist="Other", venue="Other", tour="Hozier tour")], "jambase", NOW)
        db.session.commit()
        assert ids({"q": "hozier"}) == ["exact", "partial", "venue", "tour"]
        assert ids({"q": "hozier"}) == ["exact", "partial", "venue", "tour"]


@pytest.mark.parametrize("sort,expected", [("date_asc", ["older", "newer", "missing"]),
                                         ("date_desc", ["newer", "older", "missing"]),
                                         ("alphabetical", ["missing", "newer", "older"])])
def test_sorting_and_unknown_dates_last(app, sort, expected):
    with app.app_context():
        db.session.query(Concert).delete()
        ingest_events([concert("older", "C", date_iso="2020-01-01"),
                       concert("newer", "B", date_iso="2030-01-01"),
                       concert("missing", "A", date_iso="bad")], "jambase", NOW)
        db.session.commit()
        assert ids({"q": "Hozier", "sort": sort}) == expected


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_browses_saved_concerts_without_logging(app, query):
    page = owner_client(app).get("/profile/searches/1", query_string={"q": query})
    assert page.status_code == 200 and b"16 saved concerts" in page.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SearchEvent)) == 0


@pytest.mark.parametrize("args", [{"page": "abc"}, {"page": "0"}, {"page": "-1"}, {"page": "1000001"},
                                  {"page": "3"}, {"source": "invalid"}, {"sort": "oops"},
                                  {"date_from": "2026-02-30"}, {"date_from": "20260101"},
                                  {"date_from": "2027-01-01", "date_to": "2026-01-01"},
                                  {"q": "x" * 161}, {"q": "hello\x00"}, {"location": "x" * 161}])
def test_invalid_search_is_helpful_and_not_logged(app, args):
    page = owner_client(app).get("/profile/searches/1", query_string={"q": "Hozier", **args})
    assert page.status_code == 400 and b'role="alert"' in page.data
    assert b"Traceback" not in page.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SearchEvent)) == 0


@pytest.mark.parametrize("query", ["%", "_", "' OR 1=1 --", "<script>alert(1)</script>"])
def test_input_is_literal_parameterized_and_html_escaped(app, query):
    response = owner_client(app).get("/profile/searches/1", query_string={"q": query})
    assert response.status_code == 200 and b"No matching records" in response.data
    assert b"<script>alert(1)</script>" not in response.data
    with app.app_context():
        stmt = search_statement(parse_options({"q": query}))
        compiled = stmt.compile(dialect=mysql.dialect())
        if len(query) > 1:
            assert query not in str(compiled)
        else:
            assert "/" + query in compiled.params.values()
        assert compiled.params


def test_zero_results_are_logged_and_page_range_validated(app):
    response = owner_client(app).get("/profile/searches/1?q=nonexistent")
    assert response.status_code == 200 and b"No matching records" in response.data
    assert owner_client(app).get("/profile/searches/1?q=nonexistent&page=2").status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(SearchEvent)).result_count == 0


def test_literal_like_characters_can_match_actual_records(app):
    with app.app_context():
        ingest_events([concert("literal", "100%_Live/Concert", artist="Other")], "jambase", NOW)
        db.session.commit()
        for query in ("%", "_", "/"):
            assert ids({"q": query}) == ["literal"]


def test_discovery_acquisition_populates_and_refreshes_catalog(app, monkeypatch):
    calls = []
    class Provider:
        def __init__(self, key):
            self.pagination = {"totalItems": 1}
        def search(self, **kwargs):
            calls.append(kwargs)
            return [concert("acquired", "Fresh concert" if len(calls) == 1 else "Fresh updated", artist="Fresh")]
    class EmptyProvider(Provider):
        def search(self, **kwargs):
            return []
        def has_more(self):
            return False
    monkeypatch.setattr("routes.main.SearchService", Provider)
    monkeypatch.setattr("routes.main.SetlistService", EmptyProvider)
    client = owner_client(app)
    assert client.get("/?artist=Fresh").status_code == 200
    assert b"Fresh concert" in client.get("/profile/searches/2?q=Fresh&page=1").data
    assert client.get("/?artist=Fresh").status_code == 200
    assert len(calls) == 1
    with app.app_context():
        for cached in db.session.scalars(db.select(CachedResult)):
            cached.expires_at = NOW
        db.session.commit()
    assert client.get("/?artist=Fresh").status_code == 200
    assert b"Fresh updated" in client.get("/profile/searches/2?q=Fresh&page=1").data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Concert)
                                 .where(Concert.external_id == "acquired")) == 1


def test_database_failure_and_telemetry_failure_are_distinct(app, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("private SQL", {}, Exception("secret details"))
    with monkeypatch.context() as context:
        context.setattr("services.retrieval.db.paginate", fail)
        response = owner_client(app).get("/profile/searches/1?q=Hozier")
        assert response.status_code == 503 and b"temporarily unavailable" in response.data
        assert b"secret details" not in response.data
    with monkeypatch.context() as context:
        context.setattr("services.retrieval.db.session.commit", fail)
        response = owner_client(app).get("/profile/searches/1?q=Hozier")
        assert response.status_code == 200 and b"15 saved concerts" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SearchEvent)) == 0


def test_search_activity_and_library_require_login_and_are_private(app):
    alice, bob, guest = app.test_client(), app.test_client(), app.test_client()
    with app.app_context():
        for name in ("alice", "bob"):
            db.session.add(User(google_sub=name, title=name, source="google", source_url="https://accounts.google.com"))
        db.session.commit()
    for client, name, query in ((alice, "alice", "Hozier"), (bob, "bob", "Radiohead")):
        with client.session_transaction() as session:
            session["user"] = {"sub": name, "name": name}
        assert client.get("/profile", query_string={"q": query}).status_code == 200
    assert guest.get("/profile?q=anonymous-query").status_code == 302
    activity = alice.get("/profile/search-activity")
    assert activity.status_code == 200 and b"Hozier" in activity.data
    assert b"Radiohead" not in activity.data and b"anonymous-query" not in activity.data
    assert "no-store" in activity.headers["Cache-Control"]
    assert guest.get("/profile/search-activity").status_code == 302
    with app.app_context():
        log = db.session.scalar(db.select(SearchEvent).where(SearchEvent.query == "anonymous-query"))
        assert log is None


def test_ingestion_deduplicates_preserves_latest_and_normalizes_unicode(app):
    with app.app_context():
        ingest_events([concert("unicode", "Straße", artist="Björk")], "jambase", NOW)
        ingest_events([concert("unicode", "Old")], "jambase", NOW - timedelta(days=1))
        ingest_events([concert("unicode", "Street", artist="Björk")], "jambase", NOW + timedelta(days=1))
        db.session.commit()
        assert ids({"q": "BJO\u0308RK"}) == ["unicode"]
        rows = db.session.scalars(db.select(Concert).where(Concert.external_id == "unicode")).all()
        assert len(rows) == 1 and rows[0].title == "Street"


def test_detail_page_and_missing_fields(app):
    with app.app_context():
        ingest_events([{"id": "empty"}], "jambase", NOW)
        db.session.commit()
        identifier = db.session.scalar(db.select(Concert.id).where(Concert.external_id == "empty"))
    response = app.test_client().get(f"/concerts/{identifier}")
    assert response.status_code == 200 and b"Untitled event" in response.data and b"Venue TBA" in response.data
    assert app.test_client().get("/concerts/999999").status_code == 404


def test_library_combines_metadata_and_saved_facts_but_excludes_shared_or_other_users(app):
    with app.app_context():
        bob = User(google_sub="bob-private", title="Bob", source="google", source_url="https://accounts.google.com")
        db.session.add(bob)
        db.session.flush()
        other = SavedSearch(user_id=bob.id, title="Other private plans", description="Secret notes", source="encore", source_url="/",
            query_key="other", filters={}, snapshots={"jambase": {"1": {"retrieved_at": NOW.isoformat(),
                "events": [concert("other", "Other exclusive", artist="Other exclusive")]}}})
        db.session.add(other)
        sync_saved_concerts(other)
        ingest_events([concert("shared-only", "Shared exclusive", artist="Shared exclusive")], "jambase", NOW)
        db.session.commit()
    client = owner_client(app)
    for term in ("Hozier", "The Wiltern", "A Moon Shaped Pool", "Weekend", "Invite friends"):
        response = client.get("/profile", query_string={"q": term})
        assert response.status_code == 200 and b"Weekend plans" in response.data
        assert b"Other private plans" not in response.data
    for term in ("Shared exclusive", "Other exclusive", "Secret notes"):
        response = client.get("/profile", query_string={"q": term})
        assert b"No matching records" in response.data
    response = client.get("/profile?q=Hozier&source=jambase&location=Los+Angeles&date_from=2027-01-10&date_to=2027-01-10&sort=date_desc")
    assert response.status_code == 200 and b"15 matching concerts" in response.data
    assert b"date_from=2027-01-10" in response.data and b"q=Hozier" in response.data
    assert b"No matching records" in client.get("/profile?q=Hozier&source=setlistfm").data
    assert b"No matching records" in client.get("/profile?location=Seattle").data
    assert client.get("/profile/searches/2").status_code == 404


def test_library_metadata_matches_do_not_require_a_concert_and_sort_by_concert_dates(app):
    with app.app_context():
        empty = SavedSearch(user_id=1, title="Empty plans", description="Keep these notes", source="encore",
                            source_url="/", query_key="empty", filters={}, snapshots={})
        older = SavedSearch(user_id=1, title="Earlier show", description="", source="encore", source_url="/", query_key="older", filters={},
            snapshots={"jambase": {"1": {"retrieved_at": NOW.isoformat(), "events": [concert("old", date_iso="2020-01-01")]}}})
        db.session.add_all([empty, older])
        sync_saved_concerts(older)
        db.session.commit()
        def titles(**values):
            options = parse_options(values, sorts=LIBRARY_SORTS, default_sort="recent")
            return [row.title for row in retrieve_library(options, 1)[0].items]
        assert titles(q="Keep these notes") == ["Empty plans"]
        assert titles(q="Keep these notes", source="jambase") == []
        assert titles(sort="date_asc") == ["Earlier show", "Weekend plans", "Empty plans"]
        assert titles(sort="date_desc") == ["Weekend plans", "Earlier show", "Empty plans"]


def test_saved_search_projection_is_independent_and_deleted_with_its_owner_record(app):
    with app.app_context():
        before = db.session.scalar(db.select(SavedConcert).where(SavedConcert.external_id == "0")).title
        shared = db.session.scalar(db.select(Concert).where(Concert.external_id == "0"))
        shared.title = "New shared version"
        db.session.commit()
    response = owner_client(app).get("/profile/searches/1?page=1")
    assert before.encode() in response.data and b"New shared version" not in response.data
    client = owner_client(app)
    with client.session_transaction() as session:
        session["logout_csrf"] = "delete-test"
    assert client.post("/profile/searches/1/delete", data={"csrf_token": "delete-test"}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SavedConcert)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Concert)) == 16


def test_old_collection_url_redirects_to_private_library_with_query_preserved(app):
    response = owner_client(app).get("/search?q=Hozier&source=jambase&sort=date_desc")
    assert response.status_code == 302
    assert response.headers["Location"] == "/profile?q=Hozier&source=jambase&sort=date_desc"


def test_initial_library_guidance_distinguishes_browsing_from_query_results(app):
    client = owner_client(app)
    initial = client.get("/profile")
    assert b"Enter a keyword or use filters" in initial.data
    searched = client.get("/profile?q=Hozier")
    assert b"Enter a keyword or use filters" not in searched.data
    assert b'"Hozier"' in searched.data or b'Hozier' in searched.data


def test_documented_evaluation_uses_private_services_and_current_http_routes():
    from scripts.a4_evaluation import evaluate, seed_private_corpus
    preview = create_app("testing")
    with preview.app_context():
        db.create_all()
        scope = seed_private_corpus()
        client = preview.test_client()
        with client.session_transaction() as session:
            session["user"] = {"sub": scope["owner_sub"], "name": "Evaluation listener"}
        with patch("services.api_service.ApiService.get", side_effect=AssertionError("API called")):
            report = evaluate(scope["owner_id"], scope["saved_id"], client)
        assert report["http_order_checked"] is True
        assert report["queries"][0]["precision"] == pytest.approx(14 / 15)
        assert report["queries"][0]["recall"] == 1
        assert report["queries"][4]["missed"] == ["rhcp"]
        assert all(case["passed"] for case in report["library_queries"])


def test_discover_filters_loaded_pages_and_preserves_controls_when_loading_more(app, monkeypatch):
    class Provider:
        def __init__(self, key):
            self.pagination = {"totalItems": 4, "nextPage": "more"}
        def search(self, **kwargs):
            page = kwargs["page"]
            return [concert(f"{page}-late", "Z show", date_iso="2028-01-01"),
                    concert(f"{page}-early", "A show", date_iso="2020-01-01")]
    monkeypatch.setattr("routes.main.SearchService", Provider)
    monkeypatch.setattr("routes.main.SetlistService", lambda key: pytest.fail("Excluded provider called"))
    client = app.test_client()
    response = client.get("/?artist=Hozier&source=jambase&date_from=2027-01-01&sort=date_desc")
    assert response.status_code == 200 and b"Z show" in response.data and b"A show" not in response.data
    assert b"1 matching concerts loaded" in response.data and b"Showing 1 of 4" not in response.data
    assert b"date_from=2027-01-01" in response.data and b"sort=date_desc" in response.data
    more = client.get("/api/events?artist=Hozier&source=jambase&date_from=2027-01-01&sort=date_desc&page=2")
    assert more.status_code == 200 and "Z show" in more.json["html"] and "A show" not in more.json["html"]
    assert client.get("/api/past-events?artist=Hozier&source=jambase&page=2").status_code == 400
    response = client.get("/?artist=Hozier&source=jambase&date_from=2030-01-01")
    assert b"Load more concerts" in response.data and b"in the loaded results" in response.data
    assert client.get("/?artist=Hozier&date_from=bad").status_code == 400


@pytest.mark.parametrize("existing_a3", [False, True])
def test_migrations_fresh_and_existing_preserve_data_and_match_models(tmp_path, existing_a3):
    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "migration.db")})
    with app.app_context():
        if existing_a3:
            db.metadata.create_all(db.engine, tables=[User.__table__, SavedSearch.__table__, CachedResult.__table__])
            user = User(google_sub="migration-user", title="Preserve me", source="google", source_url="https://accounts.google.com")
            db.session.add(user)
            db.session.flush()
            db.session.add(SavedSearch(user_id=user.id, title="My title", description="Private notes",
                                      source="encore", source_url="/", query_key="test", filters={}, snapshots={}))
            db.session.add(CachedResult(title="Cache", description="", source="setlistfm", source_url="https://www.setlist.fm",
                cache_key="test", filters={}, page=1, events=[concert("backfill", "Radiohead", artist="")],
                total=1, has_more=False, retrieved_at=NOW, expires_at=NOW))
            db.session.commit()
        upgrade(directory=MIGRATIONS)
        upgrade(directory=MIGRATIONS)
        assert {"encore_concerts", "encore_search_events", "alembic_version"} <= set(inspect(db.engine).get_table_names())
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), db.metadata) == []
        if existing_a3:
            saved = db.session.scalar(db.select(SavedSearch))
            assert saved.title == "My title" and saved.description == "Private notes" and saved.snapshots == {}
            assert backfill_catalog() == 1
            assert backfill_catalog() == 0
            row = db.session.scalar(db.select(Concert))
            assert row.artist == "Radiohead"
            assert "Private notes" not in str(row.card_data)
