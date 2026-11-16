"""Build escaped-template display data from original, validated source snapshots."""
import json
from datetime import date, datetime

from services.persistence import safe_url
from services.recommendations import RecommendationCandidate, RecommendationCandidates, CANDIDATE_LIMIT
from services.validators import InvalidRecommendation, validate_recommendation_output


FIELD_LABELS = {"title": "Concert", "artist": "Artist", "venue": "Venue", "location": "City",
                "event_date": "Date", "tour": "Tour", "genre": "Genre", "status": "Listing status"}


def recommendation_view(output, context):
    """Validate historical results at their original cutoff, not today's date.

    Model-authored explanations are always plain text. Links and identifying
    concert facts come only from the saved source context, never generated URLs.
    """
    try:
        candidates = tuple(RecommendationCandidate(**item) for item in context["candidates"])
        if not 1 <= len(candidates) <= CANDIDATE_LIMIT:
            raise ValueError
        if len({candidate.source_ref for candidate in candidates}) != len(candidates):
            raise ValueError
        if any(not isinstance(candidate.facts, dict) or any(not isinstance(value, str) for value in candidate.facts.values())
               for candidate in candidates):
            raise ValueError
        batch = RecommendationCandidates(context["saved_search_id"], date.fromisoformat(context["as_of_date"]),
                                         context["matching_count"], candidates)
        checked = validate_recommendation_output(output, batch, today=batch.as_of_date)
        sources = {candidate.source_ref: candidate for candidate in candidates}
        cards = []
        for item in checked["recommendations"]:
            source = sources[item["source_ref"]]
            facts = source.facts
            cards.append({"rank": item["rank"], "title": facts.get("title") or "Concert",
                "artist": facts.get("artist", ""), "venue": facts.get("venue", ""),
                "location": facts.get("location", ""), "genre": facts.get("genre", ""),
                "date_label": date.fromisoformat(facts["event_date"]).strftime("%a, %b %d, %Y"),
                "source_label": {"jambase": "JamBase", "setlistfm": "Setlist.fm"}.get(source.source, "Original listing"),
                "source_url": safe_url(source.source_url),
                "retrieved_label": datetime.fromisoformat(source.retrieved_at).strftime("%b %d, %Y %H:%M UTC"),
                "explanation": item["explanation"],
                "supporting_facts": [{"label": FIELD_LABELS[fact["field"]], "value": fact["value"]}
                                     for fact in item["supporting_facts"]]})
        return {"cards": cards, "limitations": checked["limitations"],
                "considered_count": batch.considered_count, "matching_count": batch.matching_count,
                "truncated": batch.truncated, "structured_output": json.dumps(checked, indent=2, ensure_ascii=False)}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InvalidRecommendation("This saved recommendation result could not be verified.") from None
