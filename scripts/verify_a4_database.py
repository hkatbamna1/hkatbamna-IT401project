"""Verify the current private-library flow against MariaDB and run isolated tests.

Only the telemetry insert test writes to MariaDB, and it is rolled back. HTTP
checks use an existing owner's identity in a test client with page=1 (no telemetry).
There is no authentication bypass route or new hosted account.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from models import Concert, SavedConcert, SavedSearch, SearchEvent, User, db
from services.retrieval import LIBRARY_SORTS, SORTS, parse_options, retrieve_library, retrieve_saved


def verify():
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    tests = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (ROOT / "docs" / "a4-tests.txt").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    if tests.returncode:
        raise RuntimeError("Automated checks failed; inspect docs/a4-tests.txt.")
    passed = int(re.search(r"(\d+) passed", tests.stdout).group(1))
    app = create_app()
    with app.app_context():
        sample = db.session.scalar(db.select(SavedConcert).order_by(SavedConcert.id))
        if sample is None:
            raise RuntimeError("Run backfill-saved-concerts or save a real discovery before verification.")
        saved = db.session.get(SavedSearch, sample.saved_search_id)
        user = db.session.get(User, saved.user_id)
        saved_id, owner_id = saved.id, user.id
        identity = {"sub": user.google_sub, "name": user.title}
        query, source = sample.artist or sample.title, sample.source
        counts = {"catalog_records": db.session.scalar(db.select(db.func.count()).select_from(Concert)),
                  "private_concert_records": db.session.scalar(db.select(db.func.count()).select_from(SavedConcert)),
                  "saved_search_records": db.session.scalar(db.select(db.func.count()).select_from(SavedSearch))}
        for sort in LIBRARY_SORTS:
            results, _ = retrieve_library(parse_options({"q": query, "sort": sort}, sorts=LIBRARY_SORTS), owner_id)
            assert results.total > 0 and all(row.user_id == owner_id for row in results.items)
        for sort in SORTS:
            assert retrieve_saved(parse_options({"q": query.swapcase(), "source": source, "sort": sort}), saved_id).total > 0
            retrieve_saved(parse_options({"sort": sort}), saved_id)  # Includes the blank-query relevance case.
        assert retrieve_saved(parse_options({"q": "' OR 1=1 --"}), saved_id).total == 0
        entry = SearchEvent(query="A4 rollback verification", normalized_query="a4 rollback verification",
                            result_count=0, filters={"_scope": "library"}, sort="relevance", user_id=owner_id)
        db.session.add(entry)
        db.session.flush()
        assert entry.id is not None
        db.session.rollback()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = identity
    with patch("services.api_service.ApiService.get", side_effect=AssertionError("Unexpected concert API call")):
        for sort in SORTS:
            assert client.get(f"/profile/searches/{saved_id}", query_string={"q": query, "sort": sort, "page": 1}).status_code == 200
        assert client.get("/profile", query_string={"q": query, "sort": "relevance", "page": 1}).status_code == 200
        assert client.get(f"/profile/searches/{saved_id}?q=zzzx-no-concert&page=1").status_code == 200
        legacy = client.get("/search?q=example")
        assert legacy.status_code == 302 and legacy.headers["Location"] == "/profile?q=example"
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "database": "Configured MySQL-compatible MariaDB",
              "retrieval_scope": "Owner-scoped My library and private SavedConcert snapshots", **counts,
              "automated_tests_passed": passed, "case_normalization": "passed", "literal_input": "passed",
              "all_sort_options": "passed, with and without a keyword", "ownership_scope": "passed",
              "database_only_search": "passed; concert API calls blocked during HTTP checks",
              "legacy_search_redirect": "passed", "telemetry_insert": "passed; verification rolled back"}
    (ROOT / "docs" / "a4-database-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    verify()
