"""Database-only candidate selection for concert recommendations; no AI calls."""
from dataclasses import asdict, dataclass
from datetime import date

from models import SavedConcert, SavedSearch, db, utcnow
from services.retrieval import search_statement


CANDIDATE_LIMIT = 30
RECOMMENDATION_LIMIT = 3
FACT_FIELDS = ("title", "artist", "venue", "location", "event_date", "tour", "genre", "status")
INELIGIBLE_STATUSES = ("cancelled", "canceled", "postponed", "performed")
STATUS_FRAGMENTS = ("https://schema.org/", "http://schema.org/", "event", "_", "-", " ")


class RecommendationScopeNotFound(LookupError):
    """Missing and inaccessible saved searches intentionally share one error."""


def normalized_status(value):
    value = value.strip().lower()
    for fragment in STATUS_FRAGMENTS:
        value = value.replace(fragment, "")
    return value


@dataclass(frozen=True)
class RecommendationCandidate:
    # References are local to this batch. Provider identities survive index rebuilds.
    source_ref: str
    source: str
    external_id: str
    source_url: str
    retrieved_at: str
    facts: dict[str, str]


@dataclass(frozen=True)
class RecommendationCandidates:
    saved_search_id: int
    as_of_date: date
    matching_count: int
    candidates: tuple[RecommendationCandidate, ...]

    @property
    def considered_count(self):
        return len(self.candidates)

    @property
    def truncated(self):
        return self.matching_count > self.considered_count


def snapshot_recommendation_context(batch):
    """Copy the exact source batch into JSON-compatible storage metadata."""
    snapshot = asdict(batch)
    snapshot["as_of_date"] = batch.as_of_date.isoformat()
    snapshot["candidates"] = list(snapshot["candidates"])
    snapshot.update(candidate_limit=CANDIDATE_LIMIT, considered_count=batch.considered_count,
                    truncated=batch.truncated)
    return snapshot


def retrieve_recommendation_candidates(options, saved_id, user_id, *, today=None, session=None):
    """Select up to CANDIDATE_LIMIT eligible matches in the existing sort order.

    Ignore display pagination. Verify ownership here, rather than depending on a
    future route to do so. The cutoff defaults to the UTC calendar date; callers
    can supply the application's chosen calendar date explicitly.
    """
    session = db.session if session is None else session
    if any(type(value) is not int or value < 1 for value in (saved_id, user_id)):
        raise RecommendationScopeNotFound("Saved search not found.")
    owned_id = session.scalar(db.select(SavedSearch.id).where(
        SavedSearch.id == saved_id, SavedSearch.user_id == user_id))
    if owned_id is None:
        raise RecommendationScopeNotFound("Saved search not found.")

    cutoff = today if today is not None else utcnow().date()
    # SQL filtering happens before LIMIT, so cancelled shows do not consume slots.
    status = db.func.lower(db.func.trim(db.func.coalesce(
        SavedConcert.card_data["status"].as_string(), "")))
    for fragment in STATUS_FRAGMENTS:
        status = db.func.replace(status, fragment, "")
    statement = (search_statement(options, SavedConcert)
                 .join(SavedSearch, SavedSearch.id == SavedConcert.saved_search_id)
                 .where(SavedSearch.id == saved_id, SavedSearch.user_id == user_id,
                        SavedConcert.event_date >= cutoff,
                        status.not_in(INELIGIBLE_STATUSES)))
    matching_count = session.scalar(db.select(db.func.count()).select_from(
        statement.order_by(None).subquery()))
    rows = session.scalars(statement.limit(CANDIDATE_LIMIT)).all()
    candidates = []
    for index, row in enumerate(rows, start=1):
        facts = {field: getattr(row, field) for field in
                 ("title", "artist", "venue", "location", "tour") if getattr(row, field)}
        facts["event_date"] = row.event_date.isoformat()
        for field in ("genre", "status"):
            value = row.card_data.get(field)
            if isinstance(value, str) and value.strip() and len(value) <= 200:
                if field != "genre" or value.strip().casefold() not in {"live music", "past show"}:
                    facts[field] = value.strip()
        candidates.append(RecommendationCandidate(
            source_ref=f"C{index:02d}", source=row.source, external_id=row.external_id,
            source_url=row.source_url, retrieved_at=row.retrieved_at.isoformat() + "Z", facts=facts))
    return RecommendationCandidates(saved_id, cutoff, matching_count, tuple(candidates))
