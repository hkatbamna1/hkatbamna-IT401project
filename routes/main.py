import requests
from flask import abort, current_app, jsonify, render_template, request

from services.search_service import MissingApiKeyError, SearchService
from services.setlist_service import MissingSetlistApiKeyError, SetlistService
from services.persistence import result_page, save_history, validate_filters


def register_routes(app):
    def acquire(source, filters, page):
        factory = (lambda: SearchService(current_app.config.get("JAMBASE_API_KEY"))) if source == "jambase" else (
            lambda: SetlistService(current_app.config.get("SETLISTFM_API_KEY")))
        return result_page(source, filters, page, factory)

    @app.get("/")
    def index():
        filters = validate_filters(request.args)
        searched = any(filters.values())
        pages, errors = {}, {}
        if searched:
            for source in ("jambase", "setlistfm"):
                try:
                    pages[source] = acquire(source, filters, 1)
                except (MissingApiKeyError, MissingSetlistApiKeyError) as exc:
                    errors[source] = str(exc)
                except requests.RequestException:
                    errors[source] = "The concert provider could not complete this search. Please try again shortly."
        saved_id = save_history(filters, pages)
        upcoming, past = pages.get("jambase", {}), pages.get("setlistfm", {})
        return render_template(
            "index.html", **filters, searched=searched, saved_id=saved_id, result_pages=pages,
            events=upcoming.get("events", []), past_events=past.get("events", []),
            has_more=upcoming.get("has_more", False), past_has_more=past.get("has_more", False),
            next_page=2, past_next_page=2,
            total_results=upcoming.get("total", 0), past_total_results=past.get("total", 0),
            upcoming_error=errors.get("jambase"), past_error=errors.get("setlistfm"),
        )

    def load_page(source):
        filters = validate_filters(request.args)
        if not any(filters.values()):
            abort(400, description="Enter an artist, venue, or city before loading concerts.")
        try:
            page = int(request.args.get("page", 1))
        except ValueError:
            abort(400, description="Page must be a whole number between 1 and 1000.")
        if not 1 <= page <= 1000:
            abort(400, description="Page must be a whole number between 1 and 1000.")
        try:
            result = acquire(source, filters, page)
        except (MissingApiKeyError, MissingSetlistApiKeyError) as exc:
            return jsonify(error=str(exc)), 400
        except requests.RequestException:
            return jsonify(error="The concert provider could not load more concerts."), 502
        saved_id = save_history(filters, {source: result})
        return jsonify(html=render_template("_event_cards.html", result_events=result["events"]),
                       has_more=result["has_more"], next_page=page + 1,
                       cached=result["cached"], retrieved_at=result["retrieved_at"], saved_id=saved_id)

    @app.get("/api/events")
    def load_more_events():
        return load_page("jambase")

    @app.get("/api/past-events")
    def load_more_past_events():
        return load_page("setlistfm")

    @app.get("/about")
    def about():
        return render_template("about.html")
