"""Project acquired response pages into a searchable catalog, without API calls."""
import copy
import hashlib
import unicodedata
from datetime import date

from models import CachedResult, Concert, db


def normalize(value):
    """Whitespace normalization and Unicode case folding, for queries AND records."""
    return " ".join(unicodedata.normalize("NFC", value or "").split()).casefold()


def ingest_events(events, source, retrieved_at):
    # Imported here because persistence also uses this module on acquisition.
    from services.persistence import clean_events

    changed = 0
    for event in clean_events(events, source):
        external_id = event["id"]
        if len(external_id) > 255:
            external_id = "sha256:" + hashlib.sha256(external_id.encode()).hexdigest()
        row = db.session.scalar(db.select(Concert).where(
            Concert.source == source, Concert.external_id == external_id).with_for_update())
        if row is not None and row.retrieved_at >= retrieved_at:
            continue
        title = event["name"]
        artist = str(event.get("artist") or (title if source == "setlistfm" else ""))[:200]
        venue = event["venue"]
        tour = str(event.get("tour") or "")[:200]
        location = str(event.get("location") or "")[:160]
        try:
            event_date = date.fromisoformat(event.get("date_iso") or "")
        except (ValueError, TypeError):
            event_date = None
        values = dict(title=title, description=tour, source=source, source_url=event["url"],
                      retrieved_at=retrieved_at, artist=artist, venue=venue, tour=tour,
                      location=location, event_date=event_date, search_title=normalize(title),
                      search_artist=normalize(artist), search_venue=normalize(venue),
                      search_tour=normalize(tour), search_location=normalize(location),
                      card_data=copy.deepcopy(event))
        if row is None:
            db.session.add(Concert(external_id=external_id, **values))
        else:
            for field, value in values.items():
                setattr(row, field, value)
        changed += 1
    return changed


def sync_saved_concerts(saved):
    """Rebuild the private search index from authoritative, unmodified snapshots."""
    from models import SavedConcert
    from services.persistence import clean_events
    from datetime import datetime

    db.session.flush()
    db.session.execute(db.delete(SavedConcert).where(SavedConcert.saved_search_id == saved.id))
    latest = {}
    for source, pages in saved.snapshots.items():
        for page in pages.values():
            timestamp = datetime.fromisoformat(page["retrieved_at"])
            for card in clean_events(page.get("events", []), source):
                key = source, card["id"]
                if key not in latest or timestamp > latest[key][0]:
                    latest[key] = timestamp, card
    for (source, identifier), (timestamp, card) in latest.items():
        title, venue = card["name"], card["venue"]
        artist = str(card.get("artist") or (title if source == "setlistfm" else ""))[:200]
        tour, location = str(card.get("tour") or "")[:200], str(card.get("location") or "")[:160]
        try:
            event_date = date.fromisoformat(card.get("date_iso") or "")
        except (ValueError, TypeError):
            event_date = None
        if len(identifier) > 255:
            identifier = "sha256:" + hashlib.sha256(identifier.encode()).hexdigest()
        db.session.add(SavedConcert(saved_search_id=saved.id, external_id=identifier, title=title,
            artist=artist, venue=venue, tour=tour, location=location, description=tour, source=source,
            source_url=card["url"], retrieved_at=timestamp, event_date=event_date, card_data=card,
            search_title=normalize(title), search_artist=normalize(artist), search_venue=normalize(venue),
            search_tour=normalize(tour), search_location=normalize(location)))
    return len(latest)


def backfill_saved_concerts():
    from models import SavedSearch
    last_id, count = 0, 0
    while True:
        saved = db.session.scalars(db.select(SavedSearch).where(SavedSearch.id > last_id)
                                  .order_by(SavedSearch.id).limit(25)).all()
        if not saved:
            break
        for row in saved:
            count += sync_saved_concerts(row)
        last_id = saved[-1].id
        db.session.commit()
    return count


def backfill_catalog():
    """Bounded, repeatable import of cached provider facts; leaves snapshots alone."""
    last_id, changed = 0, 0
    while True:
        pages = db.session.scalars(db.select(CachedResult).where(CachedResult.id > last_id)
                                   .order_by(CachedResult.id).limit(50)).all()
        if not pages:
            break
        for page in pages:
            changed += ingest_events(page.events, page.source, page.retrieved_at)
        last_id = pages[-1].id
        db.session.commit()
    return changed
