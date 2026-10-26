"""Validate, retrieve, rank and paginate stored concerts using SQLAlchemy only."""
from dataclasses import dataclass
from datetime import date

from flask import current_app
from sqlalchemy import case, or_
from sqlalchemy.exc import SQLAlchemyError

from models import Concert, SavedConcert, SavedSearch, SearchEvent, User, db
from services.catalog import normalize


SORTS = {"relevance": "Best match", "date_asc": "Concert date: earliest first",
         "date_desc": "Concert date: latest first", "alphabetical": "Title: A to Z"}
SOURCES = {"jambase": "JamBase", "setlistfm": "Setlist.fm"}
LIBRARY_SORTS = {"recent": "Recently searched", "oldest": "Oldest searches first", **SORTS}
PAGE_SIZE = 12


class InvalidSearch(ValueError):
    pass


@dataclass(frozen=True)
class SearchOptions:
    query: str = ""
    source: str = ""
    location: str = ""
    date_from: str = ""
    date_to: str = ""
    sort: str = "relevance"
    page: int = 1

    @property
    def filters(self):
        return {key: getattr(self, key) for key in ("source", "location", "date_from", "date_to")}

    def url_values(self):
        return {"q": self.query, **self.filters, "sort": self.sort}


def parse_options(args, *, sorts=None, default_sort="relevance"):
    query = args.get("q", "")
    location = args.get("location", "").strip()
    for label, value in (("Keyword", query), ("City", location)):
        if len(value) > 160 or any(unic < 32 or 127 <= unic < 160 for unic in map(ord, value)):
            raise InvalidSearch(f"{label} must be at most 160 characters without control characters.")
    source, sort = args.get("source", ""), args.get("sort", default_sort)
    if source and source not in SOURCES:
        raise InvalidSearch("Choose JamBase, Setlist.fm, or all providers.")
    if sort not in (sorts or SORTS):
        raise InvalidSearch("Choose one of the available sorting options.")
    try:
        page = int(args.get("page", "1"))
        if not 1 <= page <= 1000000:
            raise ValueError
    except (ValueError, TypeError):
        raise InvalidSearch("Page must be a whole number between 1 and 1,000,000.") from None
    dates = {key: args.get(key, "") for key in ("date_from", "date_to")}
    for value in dates.values():
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                raise InvalidSearch("Enter valid dates in YYYY-MM-DD format.") from None
    if all(dates.values()) and dates["date_from"] > dates["date_to"]:
        raise InvalidSearch("The start date must be on or before the end date.")
    return SearchOptions(query=query, source=source, location=location, sort=sort, page=page, **dates)


def keyword_matches(model, query):
    return [field.contains(query, autoescape=True) for field in
            (model.search_title, model.search_artist, model.search_venue, model.search_tour)]


def relevance_score(model, query):
    if not query:
        return db.literal(0)
    matches = keyword_matches(model, query)
    return (case((or_(model.search_title == query, model.search_artist == query), 5),
                 (or_(matches[0], matches[1]), 3), else_=0)
            + case((matches[2], 2), else_=0) + case((matches[3], 1), else_=0))


def apply_filters(stmt, options, model):
    if options.source:
        stmt = stmt.where(model.source == options.source)
    if options.location:
        stmt = stmt.where(model.search_location.contains(normalize(options.location), autoescape=True))
    if options.date_from:
        stmt = stmt.where(model.event_date >= date.fromisoformat(options.date_from))
    if options.date_to:
        stmt = stmt.where(model.event_date <= date.fromisoformat(options.date_to))
    return stmt


def search_statement(options, model=Concert):
    query = normalize(options.query)
    stmt = apply_filters(db.select(model), options, model)
    if query:
        stmt = stmt.where(or_(*keyword_matches(model, query)))
    score = relevance_score(model, query)
    order = {
        "relevance": ([score.desc()] if query else []) + [model.search_title.asc()],
        "date_asc": [model.event_date.is_(None), model.event_date.asc()],
        "date_desc": [model.event_date.is_(None), model.event_date.desc()],
        "alphabetical": [model.search_title.asc()],
    }[options.sort]
    return stmt.order_by(*order, model.id.asc())


def retrieve(options):
    return paginate(search_statement(options), options)


def paginate(statement, options):
    results = db.paginate(statement, page=options.page, per_page=PAGE_SIZE,
                          max_per_page=PAGE_SIZE, error_out=False)
    if options.page > max(results.pages, 1):
        raise InvalidSearch("That result page does not exist. Start again from the first page.")
    return results


def retrieve_saved(options, saved_id):
    return paginate(search_statement(options, SavedConcert).where(SavedConcert.saved_search_id == saved_id), options)


def retrieve_library(options, user_id):
    """Retrieve only the owner's saved searches; match metadata and saved facts."""
    query = normalize(options.query)
    metadata_match = or_(db.func.lower(SavedSearch.title).contains(options.query.strip().lower(), autoescape=True),
                         db.func.lower(SavedSearch.description).contains(options.query.strip().lower(), autoescape=True))
    child = db.select(SavedConcert.saved_search_id.label("saved_id"),
                      db.func.count().label("matches"),
                      db.func.max(relevance_score(SavedConcert, query)).label("score"),
                      db.func.min(SavedConcert.event_date).label("first_date"),
                      db.func.max(SavedConcert.event_date).label("last_date"))
    child = child.join(SavedSearch, SavedSearch.id == SavedConcert.saved_search_id).where(SavedSearch.user_id == user_id)
    child = apply_filters(child, options, SavedConcert)
    if query:
        child = child.where(or_(metadata_match, *keyword_matches(SavedConcert, query)))
    child = child.group_by(SavedConcert.saved_search_id).subquery()
    stmt = db.select(SavedSearch).outerjoin(child, child.c.saved_id == SavedSearch.id).where(SavedSearch.user_id == user_id)
    has_filters = any(options.filters.values())
    if query or has_filters:
        condition = child.c.matches > 0
        if query and not has_filters:
            condition = or_(condition, metadata_match)
        stmt = stmt.where(condition)
    score = db.func.coalesce(child.c.score, 0) + (case(
        (db.func.lower(SavedSearch.title) == options.query.strip().lower(), 8),
        (metadata_match, 3), else_=0) if query else 0)
    order = {"recent": [SavedSearch.last_viewed_at.desc()], "oldest": [SavedSearch.last_viewed_at.asc()],
             "relevance": [score.desc(), db.func.lower(SavedSearch.title)],
             "alphabetical": [db.func.lower(SavedSearch.title)],
             "date_asc": [child.c.first_date.is_(None), child.c.first_date.asc()],
             "date_desc": [child.c.last_date.is_(None), child.c.last_date.desc()]}[options.sort]
    results = paginate(stmt.order_by(*order, SavedSearch.id.asc()), options)
    identifiers = [row.id for row in results.items]
    counts = dict(db.session.execute(db.select(child.c.saved_id, child.c.matches).where(child.c.saved_id.in_(identifiers))).all())
    return results, counts


def concert_card(concert):
    card = dict(concert.card_data)
    card.update(name=concert.title, artist=concert.artist, venue=concert.venue,
                location=concert.location, tour=concert.tour, catalog_id=None if isinstance(concert, SavedConcert) else concert.id,
                retrieved_label=concert.retrieved_at.strftime("%b %d, %Y %H:%M UTC"))
    return card


def record_search(options, total, identity=None, scope=None):
    """Best effort: retrieval succeeds even if writing telemetry fails."""
    try:
        user_id = None
        if identity:
            user_id = db.session.scalar(db.select(User.id).where(User.google_sub == identity["sub"]))
        filters = {**options.filters, **({"_scope": scope} if scope else {})}
        db.session.add(SearchEvent(query=options.query, normalized_query=normalize(options.query),
                                   filters=filters, sort=options.sort,
                                   result_count=total, user_id=user_id))
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.warning("Search telemetry unavailable (%s)", type(error).__name__)
