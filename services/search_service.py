import re
from datetime import datetime

from services.api_service import ApiService


TICKETMASTER_BASE_URL = "https://app.ticketmaster.com/discovery/v2"
API_KEY_PLACEHOLDER = "paste_your_ticketmaster_api_key_here"


class MissingApiKeyError(RuntimeError):
    """Raised when Ticketmaster search is used without an API key."""


class SearchService:
    """Search Ticketmaster for music events by artist, venue, and location."""

    def __init__(self, api_key, api_service=None):
        self.api_key = api_key
        self.api = api_service or ApiService(TICKETMASTER_BASE_URL)

    def search(self, artist="", venue="", location="", limit=12):
        if not self.api_key or self.api_key == API_KEY_PLACEHOLDER:
            raise MissingApiKeyError(
                "Add your Ticketmaster key to TICKETMASTER_API_KEY in .env."
            )

        params = {
            "classificationName": "music",
            "size": min(max(int(limit), 1), 50),
            "sort": "date,asc",
        }

        if artist:
            attraction_id = self._find_entity_id("attractions", artist)
            if not attraction_id:
                return []
            params["attractionId"] = attraction_id

        if venue:
            venue_id = self._find_entity_id("venues", venue)
            if not venue_id:
                return []
            params["venueId"] = venue_id

        if location:
            location = location.strip()
            postal_code_pattern = (
                r"[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d|\d{5}(?:-\d{4})?"
            )
            if re.fullmatch(postal_code_pattern, location):
                params["postalCode"] = location
            else:
                params["city"] = location

        payload = self._get("events.json", params=params)
        events = payload.get("_embedded", {}).get("events", [])
        return [self._normalize_event(event) for event in events]

    def _find_entity_id(self, resource, query):
        payload = self._get(
            f"{resource}.json",
            params={"keyword": query.strip(), "size": 10},
        )
        entities = payload.get("_embedded", {}).get(resource, [])
        exact_match = next(
            (
                item
                for item in entities
                if item.get("name", "").casefold() == query.strip().casefold()
            ),
            None,
        )
        match = exact_match or (entities[0] if entities else None)
        return match.get("id") if match else None

    def _get(self, path, params=None):
        params = dict(params or {})
        params["apikey"] = self.api_key
        return self.api.get(path, params=params)

    @staticmethod
    def _normalize_event(event):
        embedded = event.get("_embedded", {})
        venue = next(iter(embedded.get("venues", [])), {})
        attraction = next(iter(embedded.get("attractions", [])), {})
        start = event.get("dates", {}).get("start", {})
        classification = next(iter(event.get("classifications", [])), {})
        genre = classification.get("genre", {}).get("name", "Live music")
        if genre == "Undefined":
            genre = "Live music"

        local_date = start.get("localDate", "")
        try:
            parsed_date = datetime.strptime(local_date, "%Y-%m-%d")
            date_day = parsed_date.strftime("%d")
            date_month = parsed_date.strftime("%b").upper()
            date_label = parsed_date.strftime("%a, %b %d").upper()
        except ValueError:
            date_day, date_month, date_label = "--", "TBA", "DATE TBA"

        local_time = start.get("localTime", "")
        try:
            time_label = (
                datetime.strptime(local_time, "%H:%M:%S")
                .strftime("%I:%M %p")
                .lstrip("0")
            )
        except (ValueError, TypeError):
            time_label = "TIME TBA"

        artist_name = attraction.get("name", "")
        artist_image = SearchService._best_image(attraction.get("images", []))
        event_image = SearchService._best_image(event.get("images", []))
        image = artist_image or event_image
        image_alt = (
            f"{artist_name or 'Featured artist'} artist image"
            if artist_image
            else f"{event.get('name', 'Concert')} event image"
        )
        city = venue.get("city", {}).get("name", "")
        state = venue.get("state", {}).get("stateCode", "")
        place = ", ".join(part for part in (city, state) if part)

        return {
            "id": event.get("id", ""),
            "name": event.get("name", "Untitled event"),
            "artist": artist_name,
            "venue": venue.get("name", "Venue TBA"),
            "location": place,
            "genre": genre,
            "date_day": date_day,
            "date_month": date_month,
            "date_label": date_label,
            "time_label": time_label,
            "image": image,
            "image_alt": image_alt,
            "url": event.get("url", ""),
        }

    @staticmethod
    def _best_image(images):
        candidates = [image for image in images if image.get("ratio") == "16_9"] or images
        best = max(candidates, key=lambda image: image.get("width", 0), default={})
        return best.get("url", "")
