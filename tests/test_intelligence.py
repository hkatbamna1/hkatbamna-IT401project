"""Exercise the complete workflow with SQLite and the real SDK's mock transport."""
import copy
import json

import httpx
import pytest
from flask import current_app
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models import RecommendationGeneration, SavedSearch, db
from services.ai_service import AiService
from services.generation_store import get_generation
from services.intelligence import generate_recommendations
from services.prompts import PROMPT_VERSION
from tests.test_ai_service import mock_service, success
from tests.test_recommendations import TODAY, large_scope, scope


def run(scope, **changes):
    values = dict(saved_id=scope["saved"], user_id=scope["owner"], values={},
                  preferences="Prefer venue variety.", today=TODAY)
    values.update(changes)
    return generate_recommendations(**values)


def count_generations():
    return db.session.scalar(db.select(db.func.count()).select_from(RecommendationGeneration))


def forbid_ai(monkeypatch):
    def fail(config):
        pytest.fail("No AI client should be constructed for this request")
    monkeypatch.setattr("services.intelligence.AiService.from_config", fail)


def arrange_ai(monkeypatch, *, mutate=None, status=200, transport_error=None, before_response=None):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body["contents"][0]["parts"][0]["text"].split("RETRIEVED CONTEXT (JSON data)\n")[1])
        calls.append(context)
        if before_response:
            before_response()
        if transport_error:
            raise transport_error
        if status != 200:
            return httpx.Response(status, json={"error": {"code": status, "message": "private provider details"}})
        payload = {"recommendations": [{"source_ref": candidate["source_ref"], "rank": index,
                    "explanation": "This concert matches the selected venue and date range.",
                    "supporting_facts": [{"field": "venue", "value": candidate["facts"]["venue"]},
                                         {"field": "event_date", "value": candidate["facts"]["event_date"]}]}
                    for index, candidate in enumerate(context["candidates"][:3], start=1)],
                   "limitations": ["Saved facts do not establish current ticket availability."]}
        if mutate:
            mutate(payload)
        return httpx.Response(200, json=success(payload))

    service, _ = mock_service(handler)
    monkeypatch.setattr("services.intelligence.AiService.from_config", lambda config: service)
    return calls


def test_full_workflow_stores_one_validated_private_result_and_reopens_without_ai(scope, monkeypatch):
    calls = arrange_ai(monkeypatch)
    outcome = run(scope)
    assert outcome.code == "generated" and outcome.has_output and outcome.saved
    assert len(calls) == 1 and len(calls[0]["candidates"]) == 17
    assert len(outcome.output["recommendations"]) == 3
    assert outcome.source_context["considered_count"] == 17
    assert outcome.source_context["matching_count"] == 17
    assert outcome.source_context["truncated"] is False
    assert outcome.model == "gemini-test-actual-version"
    assert outcome.prompt_version == PROMPT_VERSION
    assert count_generations() == 1
    forbid_ai(monkeypatch)
    stored = get_generation(outcome.generation_id, scope["owner"])
    assert stored.saved_search_id == scope["saved"]
    assert stored.user_request == "Prefer venue variety."
    assert stored.output == outcome.output and stored.source_context == outcome.source_context
    assert stored.generated_at.isoformat() + "Z" == outcome.generated_at
    # The display data cannot mutate committed JSON through shared object references.
    original = copy.deepcopy(stored.output)
    outcome.output["limitations"].append("Display-only mutation")
    db.session.expire_all()
    assert get_generation(outcome.generation_id, scope["owner"]).output == original


def test_workflow_sends_and_stores_thirty_candidates_but_returns_at_most_three(large_scope, monkeypatch):
    calls = arrange_ai(monkeypatch)
    outcome = run(large_scope)
    assert outcome.saved and len(calls) == 1
    assert len(calls[0]["candidates"]) == 30 and calls[0]["candidates"][-1]["source_ref"] == "C30"
    assert outcome.source_context["considered_count"] == 30
    assert outcome.source_context["matching_count"] == 42 and outcome.source_context["truncated"]
    assert len(outcome.output["recommendations"]) == 3


def test_database_transaction_is_closed_before_waiting_on_ai(scope, monkeypatch):
    transitions = []
    def begin(connection):
        transitions.append("begin")
    def rollback(connection):
        transitions.append("rollback")
    event.listen(db.engine, "begin", begin)
    event.listen(db.engine, "rollback", rollback)
    try:
        def at_network():
            assert transitions == ["begin", "rollback"]
        arrange_ai(monkeypatch, before_response=at_network)
        assert run(scope).saved
    finally:
        event.remove(db.engine, "begin", begin)
        event.remove(db.engine, "rollback", rollback)


def test_workflow_does_not_commit_or_rollback_unrelated_caller_edits(scope, monkeypatch):
    saved = db.session.get(SavedSearch, scope["saved"])
    original_notes = saved.description
    saved.description = "An unrelated edit that has not been saved"
    arrange_ai(monkeypatch)
    outcome = run(scope)
    assert outcome.saved and saved in db.session.dirty
    assert saved.description == "An unrelated edit that has not been saved"
    with Session(db.engine) as verification:
        assert verification.scalar(db.select(SavedSearch.description).where(SavedSearch.id == scope["saved"])) == original_notes
    db.session.rollback()
    assert count_generations() == 1  # Rolling back the caller does not undo the generation's commit.


def test_filters_sort_and_preferences_are_used_but_display_page_is_ignored(scope, monkeypatch):
    calls = arrange_ai(monkeypatch)
    outcome = run(scope, values={"q": "HOZIER", "source": "jambase", "location": "LOS ANGELES",
                               "date_from": "2026-11-18", "date_to": "2026-11-20", "sort": "date_desc",
                               "page": "not a page"}, preferences="  Prefer the latest date.  ")
    assert outcome.saved and outcome.source_context["considered_count"] == 3
    assert [candidate["external_id"] for candidate in outcome.source_context["candidates"]] == ["h05", "h04", "h03"]
    stored = get_generation(outcome.generation_id, scope["owner"])
    assert stored.user_request == "Prefer the latest date."
    assert stored.retrieval_options["sort"] == "date_desc" and "page" not in stored.retrieval_options
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [{"values": {"q": "x" * 161}}, {"values": {"source": "invalid"}},
                                     {"values": {"date_from": "2027-01-01", "date_to": "2026-01-01"}},
                                     {"preferences": "x" * 1001}])
def test_invalid_requests_use_no_ai_and_create_no_history(scope, monkeypatch, changes):
    forbid_ai(monkeypatch)
    outcome = run(scope, **changes)
    assert outcome.code == "invalid_request" and not outcome.has_output and not outcome.saved
    assert count_generations() == 0


@pytest.mark.parametrize("changes", [{"user_id": None}, {"user_id": "1"}, {"user_id": True},
                                     {"user_id": "other"}, {"saved_id": 999999}])
def test_foreign_missing_and_guest_scopes_use_no_ai(scope, monkeypatch, changes):
    forbid_ai(monkeypatch)
    if changes.get("user_id") == "other":
        changes = {"user_id": scope["other"]}
    outcome = run(scope, **changes)
    assert outcome.code == "not_found" and outcome.source_context is None
    assert not outcome.has_output and count_generations() == 0


def test_empty_context_skips_even_client_configuration(scope, monkeypatch):
    forbid_ai(monkeypatch)
    outcome = run(scope, values={"date_from": "2010-01-01", "date_to": "2011-01-01"})
    assert outcome.code == "empty_context" and not outcome.has_output
    assert count_generations() == 0


def test_retrieval_database_error_is_safe_and_uses_no_quota(scope, monkeypatch, caplog):
    forbid_ai(monkeypatch)
    def fail(*args, **kwargs):
        raise OperationalError("private SQL", {"secret": "private params"}, Exception("private database details"))
    monkeypatch.setattr("services.intelligence.retrieve_recommendation_candidates", fail)
    outcome = run(scope)
    assert outcome.code == "database_unavailable" and not outcome.has_output
    assert "private" not in outcome.message and "private" not in caplog.text
    assert count_generations() == 0


@pytest.mark.parametrize("settings,code", [({"AI_SERVICE_API_KEY": None}, "missing_key"),
                                         ({"AI_SERVICE_API_KEY": "test-key", "AI_SERVICE_TIMEOUT_SECONDS": "bad"}, "configuration")])
def test_missing_key_and_bad_config_do_not_reach_network(scope, monkeypatch, settings, code):
    for key, value in settings.items():
        monkeypatch.setitem(current_app.config, key, value)
    monkeypatch.setattr("services.ai_service.genai.Client", lambda **kwargs: pytest.fail("Network client constructed"))
    outcome = run(scope)
    assert outcome.code == code and not outcome.has_output and count_generations() == 0


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (504, "timeout"), (503, "unavailable"),
                                       (403, "authentication"), (404, "model_unavailable")])
def test_ai_failures_keep_search_usable_without_retry_or_failed_history(scope, monkeypatch, status, code):
    calls = arrange_ai(monkeypatch, status=status)
    outcome = run(scope)
    assert outcome.code == code and not outcome.has_output and not outcome.saved
    assert outcome.source_context is None and "private provider details" not in outcome.message
    assert len(calls) == 1 and count_generations() == 0
    assert db.session.get(SavedSearch, scope["saved"]) is not None


def test_network_timeout_is_not_retried(scope, monkeypatch):
    calls = arrange_ai(monkeypatch, transport_error=httpx.ReadTimeout("private transport details"))
    outcome = run(scope)
    assert outcome.code == "timeout" and "private" not in outcome.message
    assert len(calls) == 1 and count_generations() == 0


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["recommendations"][0].update(source_ref="C99"),
    lambda payload: payload["recommendations"][0]["supporting_facts"][0].update(value="Invented venue"),
    lambda payload: payload.pop("limitations"),
])
def test_invalid_output_is_not_returned_or_stored(scope, monkeypatch, mutate):
    calls = arrange_ai(monkeypatch, mutate=mutate)
    outcome = run(scope)
    assert outcome.code == "invalid_output" and not outcome.has_output
    assert outcome.source_context is None and outcome.model is None
    assert len(calls) == 1 and count_generations() == 0


def test_valid_empty_shortlist_with_limitations_is_stored(scope, monkeypatch):
    calls = arrange_ai(monkeypatch, mutate=lambda payload: payload.update(recommendations=[]))
    outcome = run(scope)
    assert outcome.saved and outcome.output["recommendations"] == [] and outcome.output["limitations"]
    assert len(calls) == 1 and count_generations() == 1


@pytest.mark.parametrize("after_commit", [False, True])
def test_save_failure_returns_validated_output_with_unconfirmed_storage_and_no_retry(scope, monkeypatch, caplog, after_commit):
    calls = arrange_ai(monkeypatch)
    original_commit = Session.commit
    def fail(session):
        if after_commit:
            original_commit(session)  # Simulate a lost acknowledgement after the DB committed.
        raise OperationalError("private SQL", {}, Exception("private details"))
    monkeypatch.setattr("services.intelligence.Session.commit", fail)
    outcome = run(scope)
    assert outcome.code == "storage_failed" and outcome.has_output and not outcome.saved
    assert outcome.generation_id is None and outcome.source_context["considered_count"] == 17
    assert "could not be confirmed" in outcome.message
    assert "private" not in outcome.message and "private" not in caplog.text
    assert len(calls) == 1
    assert count_generations() == int(after_commit)
    assert db.session.get(SavedSearch, scope["saved"]) is not None


def test_insert_failure_rolls_back_partial_generation(scope, monkeypatch):
    calls = arrange_ai(monkeypatch)
    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO encore_recommendation_generations"):
            raise OperationalError("private insert", {}, Exception("database unavailable"))
    event.listen(db.engine, "before_cursor_execute", fail_insert)
    try:
        outcome = run(scope)
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_insert)
    assert outcome.code == "storage_failed" and outcome.has_output and not outcome.saved
    assert len(calls) == 1 and count_generations() == 0


def test_ownership_is_rechecked_after_the_network_wait(scope, monkeypatch):
    def transfer():
        with Session(db.engine) as session:
            session.execute(db.update(SavedSearch).where(SavedSearch.id == scope["saved"]).values(user_id=scope["other"]))
            session.commit()
    calls = arrange_ai(monkeypatch, before_response=transfer)
    outcome = run(scope)
    assert outcome.code == "not_found" and not outcome.has_output and outcome.source_context is None
    assert len(calls) == 1 and count_generations() == 0
