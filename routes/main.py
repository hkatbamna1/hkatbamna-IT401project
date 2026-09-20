import requests
from flask import current_app, render_template, request

from services.search_service import MissingApiKeyError, SearchService


def register_routes(app):
    @app.route("/")
    def index():
        artist = request.args.get("artist", "").strip()
        venue = request.args.get("venue", "").strip()
        location = request.args.get("location", "").strip()
        searched = any((artist, venue, location))
        events = []
        error = None

        if searched:
            try:
                service = SearchService(current_app.config.get("TICKETMASTER_API_KEY"))
                events = service.search(artist=artist, venue=venue, location=location)
            except MissingApiKeyError as exc:
                error = str(exc)
            except requests.RequestException:
                current_app.logger.exception("Ticketmaster search failed")
                error = "Ticketmaster could not complete the search. Please try again shortly."

        return render_template(
            "index.html",
            events=events,
            searched=searched,
            error=error,
            artist=artist,
            venue=venue,
            location=location,
        )

    @app.route("/about")
    def about():
        return render_template("about.html")
