"""Shared validation and presentation controls for acquired result pages."""
from datetime import date

from services.catalog import normalize
from services.retrieval import parse_options


def event_score(card, query):
    if not query:
        return 0
    title = normalize(card.get("name", ""))
    artist = normalize(card.get("artist") or (card.get("name") if card.get("source") == "setlistfm" else ""))
    score = 5 if query in (title, artist) else 3 if query in title or query in artist else 0
    return score + (2 if query in normalize(card.get("venue", "")) else 0) + (1 if query in normalize(card.get("tour", "")) else 0)


def discovery_options(args, filters):
    values = dict(args)
    values["q"] = filters["artist"] or filters["venue"] or filters["location"]
    return parse_options(values)


def refine_events(events, options):
    filtered = []
    for card in events:
        if options.source and card.get("source") != options.source:
            continue
        try:
            event_date = date.fromisoformat(card.get("date_iso") or "").isoformat()
        except (ValueError, TypeError):
            event_date = ""
        if (options.date_from and (not event_date or event_date < options.date_from)
                or options.date_to and (not event_date or event_date > options.date_to)):
            continue
        value = dict(card)
        value["sort_score"] = event_score(card, normalize(options.query))
        value["sort_title"] = normalize(card.get("name", ""))
        value["sort_date"] = event_date
        filtered.append(value)
    if options.sort == "relevance":
        filtered.sort(key=lambda e: (-e["sort_score"], e["sort_title"], e.get("id", "")))
    elif options.sort == "alphabetical":
        filtered.sort(key=lambda e: (e["sort_title"], e.get("id", "")))
    else:
        filtered.sort(key=lambda e: ((not e["sort_date"]),
            -(date.fromisoformat(e["sort_date"]).toordinal()) if e["sort_date"] and options.sort == "date_desc" else e["sort_date"], e.get("id", "")))
    return filtered
