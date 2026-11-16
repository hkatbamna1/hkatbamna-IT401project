"""Recommendation output contract and local checks independent of an AI SDK."""
import json
from datetime import date

from models import utcnow
from services.recommendations import CANDIDATE_LIMIT, FACT_FIELDS, INELIGIBLE_STATUSES, RECOMMENDATION_LIMIT, normalized_status


PREFERENCE_LIMIT = 1000
MAX_OUTPUT_LENGTH = 20000


class InvalidRecommendation(ValueError):
    """A request or generated response does not meet the recommendation contract."""


# Provider-independent JSON Schema. Local validation also checks source grounding.
RECOMMENDATION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recommendations", "limitations"],
    "properties": {
        "recommendations": {
            "type": "array", "maxItems": RECOMMENDATION_LIMIT,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["source_ref", "rank", "explanation", "supporting_facts"],
                "properties": {
                    "source_ref": {"type": "string", "enum": [f"C{index:02d}" for index in range(1, CANDIDATE_LIMIT + 1)]},
                    "rank": {"type": "integer", "minimum": 1, "maximum": RECOMMENDATION_LIMIT},
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 600},
                    "supporting_facts": {
                        "type": "array", "minItems": 1, "maxItems": 5,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["field", "value"],
                            "properties": {
                                "field": {"type": "string", "enum": list(FACT_FIELDS)},
                                "value": {"type": "string", "minLength": 1, "maxLength": 200},
                            },
                        },
                    },
                },
            },
        },
        "limitations": {
            "type": "array", "maxItems": 5,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
}


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)):
        raise InvalidRecommendation("Expected bounded, nonempty text without control characters.")
    return value


def validate_recommendation_preferences(value):
    """Optional multiline preference text; filters remain separate search controls."""
    if (not isinstance(value, str) or len(value) > PREFERENCE_LIMIT
            or any((ord(c) < 32 and c not in "\n\t\r") or 127 <= ord(c) < 160 for c in value)):
        raise InvalidRecommendation("Preferences must be text of at most 1,000 characters without unsupported control characters.")
    return value.strip()


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InvalidRecommendation("Missing or unexpected response fields.")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRecommendation("Duplicate JSON fields are not allowed.")
        result[key] = value
    return result


def validate_recommendation_output(payload, batch, *, today=None):
    """Return a fresh validated object, or reject the entire response.

    Accept a decoded object or strict JSON text, never markdown/code fences.
    Evidence values must exactly match facts from the selected source. This does
    not prove that free-text explanations are factually or semantically correct.
    """
    if isinstance(payload, str):
        if len(payload) > MAX_OUTPUT_LENGTH:
            raise InvalidRecommendation("Recommendation response is too large.")
        try:
            payload = json.loads(payload, object_pairs_hook=_unique_keys,
                                 parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise InvalidRecommendation("Expected a valid JSON recommendation object.") from error
    _object(payload, ("recommendations", "limitations"))
    recommendations, limitations = payload["recommendations"], payload["limitations"]
    if not isinstance(recommendations, list) or len(recommendations) > RECOMMENDATION_LIMIT:
        raise InvalidRecommendation("Return at most three recommendations.")
    if not isinstance(limitations, list) or len(limitations) > 5:
        raise InvalidRecommendation("Expected at most five limitations.")
    limitations = [_text(value, 300) for value in limitations]
    if not recommendations and not limitations:
        raise InvalidRecommendation("Explain why no recommendations could be made.")

    sources = {candidate.source_ref: candidate for candidate in batch.candidates}
    cutoff = max(batch.as_of_date, today if today is not None else utcnow().date())
    seen, validated = set(), []
    for rank, item in enumerate(recommendations, start=1):
        _object(item, ("source_ref", "rank", "explanation", "supporting_facts"))
        reference = _text(item["source_ref"], 3)
        if reference not in sources or reference in seen:
            raise InvalidRecommendation("Recommendations must reference unique retrieved candidates.")
        if type(item["rank"]) is not int or item["rank"] != rank:
            raise InvalidRecommendation("Ranks must be consecutive integers starting at one.")
        candidate = sources[reference]
        try:
            eligible_date = date.fromisoformat(candidate.facts.get("event_date", "")) >= cutoff
        except (ValueError, TypeError):
            eligible_date = False
        if not eligible_date or normalized_status(candidate.facts.get("status", "")) in INELIGIBLE_STATUSES:
            raise InvalidRecommendation("A recommended concert is no longer eligible.")
        explanation = _text(item["explanation"], 600)
        evidence = item["supporting_facts"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 5:
            raise InvalidRecommendation("Each recommendation needs one to five supporting facts.")
        fields, facts = set(), []
        for fact in evidence:
            _object(fact, ("field", "value"))
            field, value = _text(fact["field"], 20), _text(fact["value"], 200)
            if field not in FACT_FIELDS or field in fields or candidate.facts.get(field) != value:
                raise InvalidRecommendation("Supporting facts must match the referenced concert exactly.")
            fields.add(field)
            facts.append({"field": field, "value": value})
        seen.add(reference)
        validated.append({"source_ref": reference, "rank": rank, "explanation": explanation,
                          "supporting_facts": facts})
    return {"recommendations": validated, "limitations": limitations}


def _reject_constant(value):
    raise InvalidRecommendation("Nonstandard JSON numbers are not allowed.")
