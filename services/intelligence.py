"""Coordinate one explicit recommendation request, without routes or rendering."""
from dataclasses import dataclass

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models import db, utcnow
from services.ai_service import AiService, AiServiceError
from services.generation_store import add_generation
from services.prompts import build_recommendation_prompt
from services.recommendations import (RecommendationScopeNotFound, retrieve_recommendation_candidates,
                                     snapshot_recommendation_context)
from services.retrieval import InvalidSearch, parse_options
from services.validators import (InvalidRecommendation, validate_recommendation_output,
                                 validate_recommendation_preferences)


AI_FAILURE_MESSAGES = {
    "missing_key": "Recommendations are unavailable because the Gemini key is not configured.",
    "configuration": "Recommendations are unavailable because the AI settings need attention.",
    "rate_limited": "Gemini's request or quota limit has been reached. Please try again later.",
    "timeout": "Recommendations took too long to generate. Please try again later.",
    "authentication": "Gemini access could not be verified. Check the configured key and API access.",
    "model_unavailable": "The configured recommendation model is unavailable.",
    "unavailable": "Recommendations are temporarily unavailable. Please try again later.",
    "invalid_response": "Gemini did not return a complete recommendation response. Please try again later.",
}


@dataclass(frozen=True)
class GenerationOutcome:
    """Only locally validated output may be exposed to the future interface.

    A storage_failed outcome can carry valid output without a confirmed saved ID.
    All other failures carry no output or source facts. Existing search results
    remain the caller's ordinary search results, not substitute AI recommendations.
    """
    code: str
    message: str
    generation_id: int | None = None
    output: dict | None = None
    source_context: dict | None = None
    model: str | None = None
    prompt_version: str | None = None
    generated_at: str | None = None

    @property
    def has_output(self):
        return self.output is not None

    @property
    def saved(self):
        return self.generation_id is not None


def _invalid_output():
    return GenerationOutcome("invalid_output", "Generated recommendations could not be verified. Nothing was saved.")


def generate_recommendations(*, saved_id, user_id, values, preferences="", today=None):
    """Retrieve -> build context -> generate once -> validate -> commit privately.

    Requires a Flask application context and a server-derived account ID. Ignore
    display pagination. Dedicated sessions keep this operation's commit/rollback
    separate from other request work, and release retrieval resources before AI.
    There are no automatic retries, provider refreshes, or model fallbacks.
    """
    try:
        options = parse_options({key: values[key] for key in
            ("q", "source", "location", "date_from", "date_to", "sort") if key in values})
        preferences = validate_recommendation_preferences(preferences)
    except (InvalidSearch, InvalidRecommendation) as error:
        return GenerationOutcome("invalid_request", str(error))

    try:
        with Session(db.engine) as retrieval_session:
            batch = retrieve_recommendation_candidates(options, saved_id, user_id, today=today,
                                                       session=retrieval_session)
    except RecommendationScopeNotFound:
        return GenerationOutcome("not_found", "Saved search not found.")
    except SQLAlchemyError as error:
        current_app.logger.warning("Recommendation retrieval unavailable (%s)", type(error).__name__)
        return GenerationOutcome("database_unavailable", "Saved concerts could not be loaded for recommendations. Please try again later.")

    if not batch.candidates:
        return GenerationOutcome("empty_context", "No eligible saved concerts match these filters. Broaden the filters or save more upcoming concerts.")

    try:
        prompt = build_recommendation_prompt(batch, preferences)
        response = AiService.from_config(current_app.config).generate(prompt)
    except AiServiceError as error:
        code = error.code if error.code in AI_FAILURE_MESSAGES else "unavailable"
        return GenerationOutcome(code, AI_FAILURE_MESSAGES[code])
    except InvalidRecommendation:
        return _invalid_output()

    try:
        output = validate_recommendation_output(response.text, batch, today=today)
    except InvalidRecommendation:
        return _invalid_output()
    completed_at = utcnow()
    display_values = dict(output=output, source_context=snapshot_recommendation_context(batch),
                          model=response.model, prompt_version=prompt.version,
                          generated_at=completed_at.isoformat() + "Z")

    try:
        with Session(db.engine) as storage_session:
            generation = add_generation(
                user_id=user_id, batch=batch, options=options, preferences=preferences,
                output=output, prompt_version=prompt.version, model=response.model,
                today=today, session=storage_session)
            generation.generated_at = completed_at
            storage_session.flush()
            generation_id = generation.id
            storage_session.commit()
    except RecommendationScopeNotFound:
        # The search may have been deleted or ownership changed during the call.
        return GenerationOutcome("not_found", "Saved search not found.")
    except InvalidRecommendation:
        # Revalidation at the storage boundary may reject stale or invalid facts.
        return _invalid_output()
    except SQLAlchemyError as error:
        current_app.logger.warning("Recommendation storage unavailable (%s)", type(error).__name__)
        return GenerationOutcome("storage_failed",
            "Recommendations were generated and verified, but saving could not be confirmed. Keep this result open; it may not appear in your history.",
            **display_values)
    return GenerationOutcome("generated", "Recommendation result saved.", generation_id=generation_id, **display_values)
