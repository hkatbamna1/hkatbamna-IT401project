"""Frozen synthetic corpus and relevance judgments for repeatable A4 evaluation.

These are test fixtures, not assertions about actual concert schedules. Production
continues to use acquired provider data. No fixture is written to the hosted DB.
"""
from datetime import datetime, timezone

from models import SavedSearch, User, db
from services.catalog import ingest_events, sync_saved_concerts
from services.retrieval import LIBRARY_SORTS, parse_options, retrieve_library, retrieve_saved


CAPTURED_AT = datetime(2026, 10, 24, 12)


def seed_corpus():
    def event(identifier, artist, venue, location, date, source="jambase", **extra):
        parsed = datetime.fromisoformat(date)
        return {"id": identifier, "name": artist, "artist": artist if source == "jambase" else "",
                "venue": venue, "location": location, "date_iso": date,
                "date_day": parsed.strftime("%d"), "date_month": parsed.strftime("%b").upper(),
                "date_label": parsed.strftime("%a, %b %d, %Y").upper(), "time_label": "TIME NOT LISTED",
                "genre": "Evaluation fixture", "status": "Fixture", "image": "",
                "source_label": "JamBase" if source == "jambase" else "Setlist.fm",
                "url": "https://www.jambase.com" if source == "jambase" else "https://www.setlist.fm",
                "link_label": "Provider website", "source": source, **extra}

    corpus = [event("h01", "Hozier", "Hollywood Bowl", "Los Angeles, CA", "2026-11-01"),
              event("h02", "Hozier", "Greek Theatre", "Los Angeles, CA", "2026-11-02", name="Hozier Live"),
              event("h03", "Hozier", "The Wiltern", "Los Angeles, CA", "2024-04-10", source="setlistfm")]
    corpus += [event(f"h{number:02d}", "Hozier", "Seattle Fixture Hall", "Seattle, WA", f"2027-01-{number:02d}",
                     name=f"Hozier Seattle Night {number:02d}") for number in range(4, 15)]
    corpus += [event("tribute", "Echo Collective", "Troubadour", "Los Angeles, CA", "2026-12-01", name="Hozier tribute night"),
               event("radiohead", "Radiohead", "The Wiltern", "Los Angeles, CA", "2016-08-08",
                     source="setlistfm", tour="A Moon Shaped Pool"),
               event("rhcp", "Red Hot Chili Peppers", "Kia Forum", "Inglewood, CA", "2022-07-31",
                     source="setlistfm", tour="Unlimited Love"),
               event("muse", "Muse", "The Wiltern", "Los Angeles, CA", "2026-12-05")]
    for source in ("jambase", "setlistfm"):
        ingest_events([item for item in corpus if item["source"] == source], source, CAPTURED_AT)
    db.session.commit()
    return corpus


CASES = [
    {"query": "Hozier", "filters": {}, "expected": [f"h{i:02d}" for i in range(1, 15)],
     "intent": "Find performances by Hozier; a tribute performed by someone else is irrelevant.",
     "top_expected": ["h01", "h02", "h03"]},
    {"query": "The Wiltern", "filters": {}, "expected": ["h03", "radiohead", "muse"],
     "intent": "Find concerts at this venue.", "top_expected": ["h03", "radiohead", "muse"]},
    {"query": "Hozier", "filters": {"location": "Los Angeles", "source": "setlistfm"},
     "expected": ["h03"], "intent": "Find collected Setlist.fm Hozier performances in Los Angeles.",
     "top_expected": ["h03"]},
    {"query": "A Moon Shaped Pool", "filters": {}, "expected": ["radiohead"],
     "intent": "Find a concert by its tour name.", "top_expected": ["radiohead"]},
    {"query": "RHCP", "filters": {}, "expected": ["rhcp"],
     "intent": "Find Red Hot Chili Peppers using the abbreviation; exposes absent synonym expansion.",
     "top_expected": ["rhcp"]},
    {"query": "zzzx-no-concert", "filters": {}, "expected": [],
     "intent": "Verify an honest no-results state.", "top_expected": []},
]


def seed_private_corpus(corpus=None):
    """Create isolated owners and exact snapshots, using the production indexer."""
    corpus = seed_corpus() if corpus is None else corpus
    owner = User(google_sub="a4-evaluation-owner", title="Evaluation listener", source="google", source_url="https://accounts.google.com")
    other = User(google_sub="a4-evaluation-other", title="Another listener", source="google", source_url="https://accounts.google.com")
    db.session.add_all([owner, other])
    db.session.flush()
    def saved(user, title, key, events, notes=""):
        row = SavedSearch(user_id=user.id, title=title, query_key=key, description=notes, source="encore", source_url="/",
            filters={"artist": "", "venue": "", "location": ""},
            snapshots={source: {"1": {"retrieved_at": CAPTURED_AT.isoformat(), "events": [item for item in events if item["source"] == source]}}
                       for source in ("jambase", "setlistfm")})
        db.session.add(row)
        sync_saved_concerts(row)
        return row
    primary = saved(owner, "Concert comparison", "evaluation-primary", corpus, "Bring friends and compare venues")
    notes = saved(owner, "Weekend ideas", "evaluation-notes", [], "Remember earplugs")
    exclusive = dict(corpus[0], id="other-owner-only", name="Private exclusive", artist="Private exclusive")
    saved(other, "Other listener's private plans", "evaluation-other", [exclusive])
    db.session.commit()
    return {"owner_id": owner.id, "owner_sub": owner.google_sub, "saved_id": primary.id, "notes_id": notes.id}


def evaluate(owner_id, saved_id, client=None):
    report = {"dataset": "18 synthetic, frozen concert fixtures in an isolated SQLite database",
              "retrieved_at": CAPTURED_AT.isoformat() + "Z",
              "evaluated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Current private SavedConcert retrieval and owner-scoped My library; no shared-catalog retrieval.",
              "method": "Case-normalized contiguous phrase matching; OR across fields, AND across filters.",
              "precision_scope": "All result pages, not only the first 12 results.", "queries": []}
    for case in CASES:
        rows, page = [], 1
        while True:
            arguments = {"q": case["query"], **case["filters"], "page": str(page)}
            results = retrieve_saved(parse_options(arguments), saved_id)
            if client:
                import re
                from html import unescape
                response = client.get(f"/profile/searches/{saved_id}", query_string=arguments)
                assert response.status_code == 200
                displayed = [unescape(value) for value in re.findall(r'data-event-id="([^"]*)"', response.get_data(as_text=True))]
                assert displayed == [row.external_id for row in results.items]
            rows.extend({"id": row.external_id, "title": row.title, "artist": row.artist,
                         "venue": row.venue, "source": row.source} for row in results.items)
            if not results.has_next:
                break
            page += 1
        returned = [row["id"] for row in rows]
        expected, actual = set(case["expected"]), set(returned)
        useful = len(actual & expected)
        report["queries"].append({**case, "returned": rows,
            "false_positives": sorted(actual - expected), "missed": sorted(expected - actual),
            "precision": useful / len(actual) if actual else None,
            "recall": useful / len(expected) if expected else None,
            "useful_result_in_top_three": bool(set(returned[:3]) & set(case["top_expected"])) if expected else None})
    report["observations"] = [
        "Hozier returns all 14 relevant fixtures plus one tribute; precision 14/15 and recall 14/14.",
        "Exact artist matches outrank the tribute's partial title match.",
        "Venue, tour and combined provider/city cases retrieve all judged relevant fixtures.",
        "RHCP misses Red Hot Chili Peppers because substring matching has no abbreviation dictionary.",
        "No-results precision is undefined when nothing is returned; recall is undefined when no relevant records exist.",
        "An artist-alias dictionary could improve recall; distinguishing performer intent from title mentions could improve precision.",
        "This small controlled evaluation does not measure recall across provider catalogs or uncollected concerts.",
    ]
    report["library_queries"] = []
    library_cases = [("Hozier", "Concert comparison"), ("The Wiltern", "Concert comparison"),
                     ("Bring friends", "Concert comparison"), ("Remember earplugs", "Weekend ideas"),
                     ("Private exclusive", None), ("zzzx-no-concert", None)]
    for query, expected in library_cases:
        options = parse_options({"q": query, "sort": "relevance"}, sorts=LIBRARY_SORTS)
        results, counts = retrieve_library(options, owner_id)
        returned = [{"title": row.title, "matching_concerts": counts.get(row.id, 0)} for row in results.items]
        assert [row["title"] for row in returned] == ([expected] if expected else [])
        if client:
            response = client.get("/profile", query_string={"q": query, "sort": "relevance"})
            assert response.status_code == 200
            assert b"Other listener&#39;s private plans" not in response.data
        report["library_queries"].append({"query": query, "expected_saved_titles": [expected] if expected else [],
            "returned": returned, "passed": True})
    report["http_order_checked"] = client is not None
    return report
