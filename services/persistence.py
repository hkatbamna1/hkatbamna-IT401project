"""Shared expiring API cache and private, durable search snapshots."""
import copy
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import abort, current_app, session
from sqlalchemy.exc import IntegrityError

from models import CachedResult, SavedSearch, User, db, utcnow


SOURCES = {"jambase": "https://www.jambase.com", "setlistfm": "https://www.setlist.fm"}


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return ""
    try:
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname else ""
    except ValueError:
        return ""


def validate_filters(values):
    filters = {}
    for name in ("artist", "venue", "location"):
        value = values.get(name, "")
        if not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
            abort(400, description="Search fields must be text of at most 160 characters without control characters.")
        filters[name] = " ".join(value.split())
    return filters


def query_key(filters, source=None, page=None):
    canonical = {key: value.casefold() for key, value in filters.items()}
    data = {"filters": canonical, "source": source, "page": page, "version": 1}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def query_title(filters):
    return " / ".join(value for value in filters.values() if value)[:200]


def current_user():
    identity = session.get("user")
    if not identity:
        return None
    user = db.session.scalar(db.select(User).where(User.google_sub == identity["sub"]))
    if user is None:
        user = User(google_sub=identity["sub"], title=identity.get("name", "Encore listener")[:200],
                    email=identity.get("email"), source="google", source_url="https://accounts.google.com",
                    description="Google-authenticated Encore account")
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            user = db.session.scalar(db.select(User).where(User.google_sub == identity["sub"]))
            if user is None:
                raise
    return user


def clean_events(events, source):
    """Keep normalized card fields, remove unsafe URLs and duplicate identities."""
    cleaned, seen = [], set()
    for value in events:
        if not isinstance(value, dict):
            continue
        event = copy.deepcopy(value)
        event["name"] = str(event.get("name") or "Untitled event")[:200]
        event["source"] = source
        event["venue"] = str(event.get("venue") or "Venue TBA")[:200]
        for field in ("url", "ticket_url", "image"):
            event[field] = safe_url(event.get(field))
        identifier = str(event.get("id") or "")
        if not identifier:
            identifier = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        if identifier in seen:
            continue
        seen.add(identifier)
        event["id"] = identifier
        cleaned.append(event)
    return cleaned


def result_page(source, filters, page, factory):
    key = query_key(filters, source, page)
    cached = db.session.scalar(db.select(CachedResult).where(CachedResult.cache_key == key))
    now = utcnow()
    from_cache = cached is not None and cached.expires_at > now
    if not from_cache:
        service = factory()
        events = clean_events(service.search(**filters, page=page), source)
        events.sort(key=lambda event: event.get("date_iso") or ("9999-12-31" if source == "jambase" else ""),
                    reverse=source == "setlistfm")
        total = max(int(service.pagination.get("totalItems") or len(events)), len(events))
        more = bool(service.pagination.get("nextPage")) if source == "jambase" else service.has_more()
        values = dict(title=query_title(filters), description=f"{source} search results, page {page}",
                      source=source, source_url=SOURCES[source], filters=filters, page=page,
                      events=events, total=total, has_more=more, retrieved_at=now,
                      expires_at=now + timedelta(hours=current_app.config["CACHE_HOURS"][source]))
        if cached is None:
            cached = CachedResult(cache_key=key, **values)
            db.session.add(cached)
        else:
            for name, value in values.items():
                setattr(cached, name, value)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            cached = db.session.scalar(db.select(CachedResult).where(CachedResult.cache_key == key))
            if cached is None:
                raise
    return {"events": copy.deepcopy(cached.events), "total": cached.total, "has_more": cached.has_more,
            "retrieved_at": cached.retrieved_at.isoformat(), "cached": from_cache, "page": page}


def save_history(filters, pages):
    if not pages or not session.get("user"):
        return None
    user = current_user()
    key = query_key(filters)
    # Retry a competing insert once; unique constraints prevent duplicate history.
    for attempt in range(2):
        saved = db.session.scalar(db.select(SavedSearch).where(
            SavedSearch.user_id == user.id, SavedSearch.query_key == key).with_for_update())
        if saved is None:
            saved = SavedSearch(user_id=user.id, query_key=key, title=query_title(filters),
                                description="", source="encore", source_url="/?" + urlencode(filters),
                                filters=filters, snapshots={})
            db.session.add(saved)
        snapshots = copy.deepcopy(saved.snapshots)
        for source, page in pages.items():
            # A fresh first page starts a new snapshot for this source so old
            # pagination isn't mixed with results from a later API retrieval.
            source_pages = snapshots.setdefault(source, {})
            if page["page"] == 1 and source_pages.get("1", {}).get("retrieved_at") != page["retrieved_at"]:
                snapshots[source] = source_pages = {}
            source_pages[str(page["page"])] = page
        saved.snapshots = snapshots
        saved.last_viewed_at = utcnow()
        saved.retrieved_at = max(datetime.fromisoformat(p["retrieved_at"])
                                 for group in snapshots.values() for p in group.values())
        try:
            db.session.commit()
            return saved.id
        except IntegrityError:
            db.session.rollback()
            if attempt:
                raise
