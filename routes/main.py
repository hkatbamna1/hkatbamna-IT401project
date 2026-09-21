import requests
from flask import current_app, jsonify, render_template, request

from services.search_service import MissingApiKeyError, SearchService
from services.setlist_service import MissingSetlistApiKeyError, SetlistService


def register_routes(app):
    def get_page_number():
        try:
            return max(int(request.args.get("page", 1)), 1)
        except (TypeError, ValueError):
            return 1

    @app.route("/")
    def index():
        artist = request.args.get("artist", "").strip()
        venue = request.args.get("venue", "").strip()
        location = request.args.get("location", "").strip()
        searched = any((artist, venue, location))
        events = []
        past_events = []
        has_more = False
        past_has_more = False
        next_page = 2
        past_next_page = 2
        total_results = 0
        past_total_results = 0
        upcoming_error = None
        past_error = None

        if searched:
            try:
                service = SearchService(current_app.config.get("JAMBASE_API_KEY"))
                events = service.search(
                    artist=artist,
                    venue=venue,
                    location=location,
                    page=1,
                )
                events.sort(key=lambda event: event.get("date_iso") or "9999-12-31")
                pagination = service.pagination
                total_results = pagination.get("totalItems", len(events))
                has_more = bool(pagination.get("nextPage"))
            except MissingApiKeyError as exc:
                upcoming_error = str(exc)
            except requests.RequestException:
                current_app.logger.exception("JamBase search failed")
                upcoming_error = "JamBase could not complete the search. Please try again shortly."

            try:
                setlist_service = SetlistService(
                    current_app.config.get("SETLISTFM_API_KEY")
                )
                past_events = setlist_service.search(
                    artist=artist,
                    venue=venue,
                    location=location,
                    page=1,
                )
                past_events.sort(
                    key=lambda event: event.get("date_iso") or "",
                    reverse=True,
                )
                past_total_results = setlist_service.pagination.get(
                    "totalItems", len(past_events)
                )
                past_has_more = setlist_service.has_more()
            except MissingSetlistApiKeyError as exc:
                past_error = str(exc)
            except requests.RequestException:
                current_app.logger.exception("Setlist.fm search failed")
                past_error = (
                    "Setlist.fm could not complete the search. Please try again shortly."
                )

        return render_template(
            "index.html",
            events=events,
            past_events=past_events,
            has_more=has_more,
            past_has_more=past_has_more,
            next_page=next_page,
            past_next_page=past_next_page,
            total_results=total_results,
            past_total_results=past_total_results,
            searched=searched,
            upcoming_error=upcoming_error,
            past_error=past_error,
            artist=artist,
            venue=venue,
            location=location,
        )

    @app.route("/api/events")
    def load_more_events():
        artist = request.args.get("artist", "").strip()
        venue = request.args.get("venue", "").strip()
        location = request.args.get("location", "").strip()
        page = get_page_number()

        try:
            service = SearchService(current_app.config.get("JAMBASE_API_KEY"))
            events = service.search(
                artist=artist,
                venue=venue,
                location=location,
                page=page,
            )
            events.sort(key=lambda event: event.get("date_iso") or "9999-12-31")
        except MissingApiKeyError as exc:
            return jsonify({"error": str(exc)}), 400
        except requests.RequestException:
            current_app.logger.exception("JamBase pagination request failed")
            return jsonify({"error": "JamBase could not load more concerts."}), 502

        return jsonify(
            {
                "html": render_template("_event_cards.html", result_events=events),
                "has_more": bool(service.pagination.get("nextPage")),
                "next_page": page + 1,
            }
        )

    @app.route("/api/past-events")
    def load_more_past_events():
        artist = request.args.get("artist", "").strip()
        venue = request.args.get("venue", "").strip()
        location = request.args.get("location", "").strip()
        page = get_page_number()

        try:
            service = SetlistService(current_app.config.get("SETLISTFM_API_KEY"))
            events = service.search(
                artist=artist,
                venue=venue,
                location=location,
                page=page,
            )
            events.sort(
                key=lambda event: event.get("date_iso") or "",
                reverse=True,
            )
        except MissingSetlistApiKeyError as exc:
            return jsonify({"error": str(exc)}), 400
        except requests.RequestException:
            current_app.logger.exception("Setlist.fm pagination request failed")
            return jsonify({"error": "Setlist.fm could not load more concerts."}), 502

        return jsonify(
            {
                "html": render_template("_event_cards.html", result_events=events),
                "has_more": service.has_more(),
                "next_page": page + 1,
            }
        )

    @app.route("/about")
    def about():
        return render_template("about.html")
