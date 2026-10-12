"""Read the real server, exercise rolled-back ORM writes, then warm one real query.

Run from the repository root: python scripts/verify_database.py
No demo user or history record is committed. The real API cache is retained.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app import create_app
from models import User, SavedSearch, CachedResult, db


app = create_app()
with app.app_context():
    print("Server:", db.session.execute(text("SELECT VERSION()")).scalar())
    user = User(google_sub="encore-rollback-verification", title="Verification",
                source="test", source_url="https://example.com")
    db.session.add(user)
    db.session.flush()
    saved = SavedSearch(user_id=user.id, query_key="verification", title="Verification",
                        source="test", source_url="https://example.com", filters={}, snapshots={})
    db.session.add(saved)
    db.session.flush()
    saved.title = "Updated verification"
    db.session.flush()
    assert db.session.get(SavedSearch, saved.id).title == "Updated verification"
    db.session.delete(saved)
    db.session.flush()
    db.session.rollback()
    print("MariaDB create/read/update/delete verified; test records rolled back.")

client = app.test_client()
first = client.get("/", query_string={"artist": "Hozier", "location": "Los Angeles"})
assert first.status_code == 200, first.status_code
with app.app_context():
    cached = db.session.scalars(db.select(CachedResult)).all()
    print("Persisted result pages:", len(cached))
    print("Stored concerts:", sum(len(row.events) for row in cached))
    assert cached, "No provider returned a persistable result. Check API keys/network."

# Fail hard if replay attempts an API call, including location resolution.
from unittest.mock import patch
with patch("services.api_service.ApiService.get", side_effect=AssertionError("Unexpected API call")):
    second = client.get("/", query_string={"artist": "Hozier", "location": "Los Angeles"})
    assert second.status_code == 200
print("Repeated search served from MariaDB with no external API calls.")
