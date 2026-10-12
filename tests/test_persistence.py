from datetime import timedelta

import pytest
import requests
from sqlalchemy.exc import OperationalError

from app import create_app
from models import CachedResult, SavedSearch, User, db, utcnow


@pytest.fixture
def setup(monkeypatch):
    calls = []
    class Provider:
        def __init__(self, key):
            self.pagination = {"totalItems": 2, "nextPage": "page2"}
        def search(self, **filters):
            calls.append(filters)
            page = filters["page"]
            return [{"id": str(page), "name": f"Concert {page}", "url": "https://example.com/concert",
                     "venue": "Test venue", "source_label": "Test source"}]
        def has_more(self):
            return True
    monkeypatch.setattr("routes.main.SearchService", Provider)
    monkeypatch.setattr("routes.main.SetlistService", Provider)
    app = create_app("testing")
    with app.app_context():
        db.create_all()
    client = app.test_client()
    sign_in(client)
    return app, client, calls


def sign_in(client, sub="alice"):
    with client.session_transaction() as session:
        session["user"] = {"sub": sub, "name": sub, "email": None}
        session["logout_csrf"] = "csrf-test"


def first_id(app):
    with app.app_context():
        return db.session.scalar(db.select(SavedSearch.id))


def test_cache_and_history_survive_new_app_and_do_not_repeat_calls(setup, tmp_path):
    app, client, calls = setup
    # A disk-backed test DB proves persistence across application instances.
    overrides = {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "persist.db")}
    app = create_app("testing", overrides)
    with app.app_context():
        db.create_all()
    client = app.test_client()
    sign_in(client)
    assert client.get("/?artist=Hozier").status_code == 200
    assert len(calls) == 2
    again = create_app("testing", overrides).test_client()
    sign_in(again)
    assert again.get("/?artist=HOZIER").status_code == 200
    assert len(calls) == 2
    assert b"Hozier" in again.get("/profile").data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SavedSearch)) == 1


def test_saved_pages_crud_and_replay_make_no_api_calls(setup):
    app, client, calls = setup
    client.get("/?artist=Hozier")
    client.get("/api/events?artist=Hozier&page=2")
    search_id = first_id(app)
    assert len(calls) == 3
    page = client.get(f"/profile/searches/{search_id}")
    assert page.status_code == 200 and b"Concert 2" in page.data
    assert "no-store" in page.headers["Cache-Control"]
    assert len(calls) == 3
    response = client.post(f"/profile/searches/{search_id}/edit", data={
        "csrf_token": "csrf-test", "title": "Summer plans", "description": "Invite friends"})
    assert response.status_code == 302
    client.get("/?artist=Hozier")
    assert b"Summer plans" in client.get("/profile").data
    assert b"Invite friends" in client.get(f"/profile/searches/{search_id}").data
    assert client.post(f"/profile/searches/{search_id}/delete", data={"csrf_token": "csrf-test"}).status_code == 302
    assert client.get(f"/profile/searches/{search_id}").status_code == 404
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(CachedResult)) == 3


def test_other_users_cannot_read_modify_or_delete_history(setup):
    app, alice, calls = setup
    alice.get("/?artist=Hozier")
    search_id = first_id(app)
    bob = app.test_client()
    sign_in(bob, "bob")
    assert bob.get(f"/profile/searches/{search_id}").status_code == 404
    for action in ("edit", "delete"):
        assert bob.post(f"/profile/searches/{search_id}/{action}", data={
            "csrf_token": "csrf-test", "title": "Hijacked"}).status_code == 404
    bob.get("/?artist=Hozier")
    assert len(calls) == 2  # Shared cache; separate private histories.
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SavedSearch)) == 2


def test_expired_cache_refresh_does_not_mutate_other_users_snapshot(setup):
    app, alice, calls = setup
    alice.get("/?artist=Hozier")
    search_id = first_id(app)
    with app.app_context():
        before = db.session.get(SavedSearch, search_id).snapshots
        for cached in db.session.scalars(db.select(CachedResult)):
            cached.expires_at = utcnow() - timedelta(hours=1)
        db.session.commit()
    bob = app.test_client()
    sign_in(bob, "bob")
    bob.get("/?artist=Hozier")
    assert len(calls) == 4
    with app.app_context():
        assert db.session.get(SavedSearch, search_id).snapshots == before
    alice.get(f"/profile/searches/{search_id}")
    assert len(calls) == 4


def test_validation_and_csrf_reject_without_calls_or_changes(setup):
    app, client, calls = setup
    assert client.get("/api/events").status_code == 400
    assert client.get("/", query_string={"artist": "x" * 161}).status_code == 400
    assert client.get("/api/events?artist=a&page=-1").status_code == 400
    assert not calls
    client.get("/?artist=a")
    search_id = first_id(app)
    assert client.post(f"/profile/searches/{search_id}/delete").status_code == 400
    assert client.post(f"/profile/searches/{search_id}/edit", data={"csrf_token": "csrf-test", "title": ""}).status_code == 400
    assert client.get(f"/profile/searches/{search_id}/delete").status_code == 405


def test_guests_use_cache_without_personal_history(setup):
    app, _, calls = setup
    guest = app.test_client()
    assert guest.get("/profile").status_code == 302
    guest.get("/?artist=a")
    guest.get("/?artist=a")
    assert len(calls) == 2
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SavedSearch)) == 0


def test_empty_results_cached_but_provider_errors_not_cached(setup, monkeypatch):
    app, client, calls = setup
    def empty(self, **kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr("routes.main.SearchService.search", empty)
    client.get("/?artist=nothing")
    client.get("/?artist=nothing")
    assert len(calls) == 2
    def fail(self, **kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr("routes.main.SearchService.search", fail)
    client.get("/?artist=failure")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(CachedResult)) == 2


def test_database_failure_does_not_spend_api_calls(setup, monkeypatch):
    app, client, calls = setup
    def fail(*args, **kwargs):
        raise OperationalError("private sql", {}, Exception("private detail"))
    monkeypatch.setattr("services.persistence.db.session.scalar", fail)
    response = client.get("/?artist=a")
    assert response.status_code == 503
    assert b"private detail" not in response.data
    assert not calls


def test_duplicate_events_and_unsafe_urls_cleaned():
    from services.persistence import clean_events
    records = clean_events([{"id": "1", "name": "", "url": "javascript:alert(1)"},
                            {"id": "1", "name": "duplicate"}], "jambase")
    assert len(records) == 1
    assert records[0]["name"] == "Untitled event"
    assert records[0]["url"] == ""


def test_expired_first_page_resets_old_saved_pagination(setup):
    app, client, calls = setup
    client.get("/?artist=a")
    client.get("/api/events?artist=a&page=2")
    search_id = first_id(app)
    with app.app_context():
        saved = db.session.get(SavedSearch, search_id)
        assert set(saved.snapshots["jambase"]) == {"1", "2"}
        for row in db.session.scalars(db.select(CachedResult).where(CachedResult.source == "jambase")):
            row.expires_at = utcnow() - timedelta(hours=1)
        db.session.commit()
    client.get("/?artist=a")
    with app.app_context():
        assert set(db.session.get(SavedSearch, search_id).snapshots["jambase"]) == {"1"}
    assert len(calls) == 4


def test_unicode_csrf_is_rejected_and_malformed_url_is_removed(setup):
    from services.persistence import safe_url
    assert safe_url("https://[broken") == ""
    assert safe_url("https://example.com/\npath") == ""
    app, client, _ = setup
    client.get("/?artist=a")
    search_id = first_id(app)
    assert client.post(f"/profile/searches/{search_id}/delete", data={"csrf_token": "é"}).status_code == 400
    assert client.post("/auth/logout", data={"csrf_token": "é"}).status_code == 400
