"""Verify the evaluator's scoring and isolation without spending model quota."""
import copy
from unittest.mock import patch

import httpx
from google import genai

from scripts.a5_evaluation import QUALITY_CASES, assess_quality, evaluate, output_fingerprint
from services.ai_service import AiService


def test_offline_evaluation_checks_contracts_without_claiming_model_quality():
    with patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Real HTTP attempted")):
        report = evaluate()
    assert report["summary"]["attempted_cases"] == report["summary"]["contract_passes"] == 13
    assert report["summary"]["live_model_calls"] == 0
    assert report["summary"]["mocked_model_calls"] == 9
    assert report["summary"]["automatic_quality_passes"] is None
    assert report["summary"]["explanation_review"] == "not_evaluated"
    assert report["hosted_database_access"] is False
    assert all(case["quality"] is None and case["prompt_privacy_canaries_absent"] for case in report["cases"])
    assert all(case["model_calls"] == case["expected_model_calls"] for case in report["cases"])
    primary = report["cases"][0]
    assert primary["candidate_count"] == 30 and primary["matching_count"] == 36
    assert primary["source_context"]["truncated"] is True


def primary_case():
    result = evaluate(case_ids=("venue_dates",))["cases"][0]
    return result["validated_output"], result["source_context"]


def test_quality_scoring_distinguishes_preference_fit_from_grounded_but_unhelpful_choices():
    output, context = primary_case()
    correct = assess_quality(QUALITY_CASES[0], output, context)
    assert correct["automatic_pass"] and correct["expected_choice_precision"] == 1
    assert correct["selected_ids"] == ["event-06", "event-12", "event-18"]
    assert correct["retrieval_first_three_ids"] == ["event-01", "event-02", "event-03"]
    assert correct["retrieval_baseline_expected_choice_precision"] == 0
    wrong = copy.deepcopy(output)
    for item, source in zip(wrong["recommendations"], context["candidates"][:3]):
        item.update(source_ref=source["source_ref"], supporting_facts=[{"field": "venue", "value": source["facts"]["venue"]}])
    scored = assess_quality(QUALITY_CASES[0], wrong, context)
    assert scored["source_grounded"] and scored["supporting_facts_match"]
    assert not scored["automatic_pass"] and scored["expected_choice_precision"] == 0


def test_quality_scoring_checks_order_and_independent_evidence_values():
    output, context = primary_case()
    reversed_output = copy.deepcopy(output)
    reversed_output["recommendations"].reverse()
    for rank, item in enumerate(reversed_output["recommendations"], start=1):
        item["rank"] = rank
    scored = assess_quality(QUALITY_CASES[0], reversed_output, context)
    assert scored["expected_choice_precision"] == 1 and not scored["expected_order_match"]
    wrong_fact = copy.deepcopy(output)
    wrong_fact["recommendations"][0]["supporting_facts"][0]["value"] = "Invented venue"
    assert not assess_quality(QUALITY_CASES[0], wrong_fact, context)["supporting_facts_match"]


def test_unsupported_requirement_checks_need_no_choices_and_both_missing_facts():
    _, context = primary_case()
    output = {"recommendations": [], "limitations": ["Ticket prices and distance are unavailable."]}
    assert assess_quality(QUALITY_CASES[1], output, context)["automatic_pass"]
    output["limitations"] = ["Ticket prices are unavailable."]
    assert not assess_quality(QUALITY_CASES[1], output, context)["automatic_pass"]


def test_review_fingerprint_changes_with_output_or_source_facts():
    output, context = primary_case()
    original = output_fingerprint(output, context)
    assert output_fingerprint(copy.deepcopy(output), copy.deepcopy(context)) == original
    output["limitations"].append("Changed explanation evidence.")
    assert output_fingerprint(output, context) != original


def test_live_quota_failure_stops_immediately_without_retries_or_further_requests():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": 429, "message": "Synthetic quota limit"}})
    def factory(**kwargs):
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(handler), "trust_env": False}
        return genai.Client(**kwargs)
    service = AiService("fixture-only-key", client_factory=factory)
    with patch("scripts.a5_evaluation.AiService.from_config", return_value=service), patch("scripts.a5_evaluation.time.sleep") as sleep:
        report = evaluate(live=True)
    assert len(calls) == report["summary"]["live_model_calls"] == 1
    assert report["summary"]["attempted_cases"] == 1 and report["summary"]["planned_cases"] == 3
    assert report["cases"][0]["outcome"] == "rate_limited" and report["cases"][0]["validated_output"] is None
    assert "quota reached" in report["stopped_reason"]
    sleep.assert_not_called()
