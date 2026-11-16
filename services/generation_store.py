"""Owner-scoped storage helpers; callers control commit and rollback."""
from models import RecommendationGeneration, SavedSearch, db
from services.recommendations import RecommendationScopeNotFound, snapshot_recommendation_context
from services.retrieval import parse_options
from services.validators import InvalidRecommendation, validate_recommendation_output, validate_recommendation_preferences


def _metadata(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)):
        raise InvalidRecommendation("Generation model and prompt version must be bounded nonempty text.")
    return value.strip()


def add_generation(*, user_id, batch, options, preferences, output, prompt_version, model, today=None, session=None):
    """Stage a validated generation; never persist unchecked model output.

    Ownership is inherited from SavedSearch instead of duplicating an account ID
    that could disagree with the parent. No AI call or commit occurs here.
    """
    session = db.session if session is None else session
    if type(user_id) is not int or user_id < 1:
        raise RecommendationScopeNotFound("Saved search not found.")
    saved = session.scalar(db.select(SavedSearch).where(
        SavedSearch.id == batch.saved_search_id, SavedSearch.user_id == user_id).with_for_update())
    if saved is None:
        raise RecommendationScopeNotFound("Saved search not found.")
    preferences = validate_recommendation_preferences(preferences)
    checked_options = parse_options(options.url_values())
    validated = validate_recommendation_output(output, batch, today=today)
    generation = RecommendationGeneration(
        saved_search=saved, user_request=preferences,
        retrieval_options=checked_options.url_values(),
        source_context=snapshot_recommendation_context(batch), output=validated,
        prompt_version=_metadata(prompt_version, 40), model=_metadata(model, 120))
    session.add(generation)
    return generation


def get_generation(generation_id, user_id):
    """Return only the owner's stored result; missing and foreign IDs look alike."""
    if any(type(value) is not int or value < 1 for value in (generation_id, user_id)):
        raise RecommendationScopeNotFound("Generated recommendations not found.")
    generation = db.session.scalar(db.select(RecommendationGeneration).join(SavedSearch).where(
        RecommendationGeneration.id == generation_id, SavedSearch.user_id == user_id))
    if generation is None:
        raise RecommendationScopeNotFound("Generated recommendations not found.")
    return generation
