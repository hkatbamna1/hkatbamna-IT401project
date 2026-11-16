"""Private generation persistence and additive A5 migration checks."""
import copy
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from app import create_app
from models import RecommendationGeneration, SavedSearch, User, db
from services.catalog import sync_saved_concerts
from services.generation_store import add_generation, get_generation
from services.prompts import PROMPT_VERSION
from services.recommendations import RecommendationScopeNotFound
from services.retrieval import parse_options
from services.validators import InvalidRecommendation
from tests.test_recommendations import TODAY, response, retrieve, scope


MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")


def stage(scope, **changes):
    batch = retrieve(scope)
    values = dict(user_id=scope["owner"], batch=batch, options=parse_options({"page": "2"}),
                  preferences="Prefer venue variety.", output=response(batch),
                  prompt_version=PROMPT_VERSION, model="gemini-test-version", today=TODAY)
    values.update(changes)
    return add_generation(**values)


def test_store_validates_and_snapshots_context_without_committing(scope):
    batch = retrieve(scope)
    payload = response(batch)
    generation = stage(scope, batch=batch, output=payload)
    assert generation in db.session.new
    assert generation.id is None  # The caller still owns the transaction.
    batch.candidates[0].facts["venue"] = "Changed after staging"
    payload["recommendations"][0]["explanation"] = "Changed after staging"
    db.session.commit()
    identifier = generation.id
    db.session.expire_all()
    stored = get_generation(identifier, scope["owner"])
    assert stored.saved_search.user_id == scope["owner"]
    assert stored.user_request == "Prefer venue variety."
    assert stored.prompt_version == PROMPT_VERSION and stored.model == "gemini-test-version"
    assert stored.generated_at is not None
    assert "page" not in stored.retrieval_options
    context = stored.source_context
    assert context["candidate_limit"] == 30 and context["considered_count"] == 17
    assert context["matching_count"] == 17 and context["truncated"] is False
    assert context["as_of_date"] == TODAY.isoformat()
    assert context["candidates"][0]["external_id"] == "h00"
    assert context["candidates"][0]["facts"]["venue"] == "The Wiltern"
    assert stored.output["recommendations"][0]["explanation"] != "Changed after staging"


def test_generation_sources_remain_unchanged_after_snapshot_refresh(scope):
    generation = stage(scope)
    db.session.commit()
    before = copy.deepcopy(generation.source_context)
    saved = db.session.get(SavedSearch, scope["saved"])
    saved.snapshots = {}
    sync_saved_concerts(saved)
    db.session.commit()
    db.session.expire_all()
    assert get_generation(generation.id, scope["owner"]).source_context == before


def test_generation_reads_and_writes_are_owner_scoped(scope):
    generation = stage(scope)
    db.session.commit()
    for identifier, user_id in ((generation.id, scope["other"]), (999999, scope["owner"]),
                                (generation.id, None), (True, scope["owner"])):
        with pytest.raises(RecommendationScopeNotFound):
            get_generation(identifier, user_id)
    for user_id in (scope["other"], None):
        with pytest.raises(RecommendationScopeNotFound):
            stage(scope, user_id=user_id)
    assert db.session.scalar(db.select(db.func.count()).select_from(RecommendationGeneration)) == 1


@pytest.mark.parametrize("changes", [
    {"output": {"recommendations": [], "limitations": []}},
    {"prompt_version": ""}, {"prompt_version": "x" * 41},
    {"model": "bad\nmodel"}, {"preferences": "x" * 1001},
])
def test_invalid_generation_is_never_staged(scope, changes):
    with pytest.raises(InvalidRecommendation):
        stage(scope, **changes)
    assert not db.session.new


def test_caller_can_rollback_generation(scope):
    stage(scope)
    db.session.rollback()
    assert db.session.scalar(db.select(db.func.count()).select_from(RecommendationGeneration)) == 0


@pytest.mark.parametrize("delete_user", [False, True])
def test_deleting_parent_removes_generated_history(scope, delete_user):
    stage(scope)
    db.session.commit()
    parent = db.session.get(User, scope["owner"]) if delete_user else db.session.get(SavedSearch, scope["saved"])
    db.session.delete(parent)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count()).select_from(RecommendationGeneration)) == 0
    assert db.session.get(User, scope["other"]) is not None


def test_a4_upgrade_preserves_data_and_matches_models_and_downgrades_only_a5(tmp_path):
    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "a5.db")})
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="a4_saved_concerts")
        user = User(google_sub="upgrade-owner", title="Keep account", source="google", source_url="/")
        db.session.add(user)
        db.session.flush()
        saved = SavedSearch(user_id=user.id, title="Keep search", description="Keep notes", source="encore",
                            source_url="/", query_key="upgrade", filters={}, snapshots={})
        db.session.add(saved)
        db.session.commit()
        upgrade(directory=MIGRATIONS)
        upgrade(directory=MIGRATIONS)
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), db.metadata) == []
        assert db.session.get(SavedSearch, saved.id).description == "Keep notes"
        table = RecommendationGeneration.__tablename__
        foreign_keys = inspect(db.engine).get_foreign_keys(table)
        assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
        downgrade(directory=MIGRATIONS, revision="a4_saved_concerts")
        assert table not in inspect(db.engine).get_table_names()
        assert db.session.get(User, user.id).title == "Keep account"
        assert db.session.get(SavedSearch, saved.id).title == "Keep search"
