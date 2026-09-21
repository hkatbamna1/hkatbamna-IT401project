from datetime import date, datetime

from services.api_service import ApiService


SETLISTFM_BASE_URL = "https://api.setlist.fm/rest/1.0"
SETLISTFM_KEY_PLACEHOLDER = "paste_your_setlistfm_api_key_here"


class MissingSetlistApiKeyError(RuntimeError):
    """Raised when a Setlist.fm search is attempted without an API key."""


class SetlistService:
    """Search and normalize past concerts from Setlist.fm."""

    def __init__(self, api_key, api_service=None, today=None):
        self.api_key = api_key
        self.api = api_service or ApiService(
            SETLISTFM_BASE_URL,
            default_headers={
                "Accept": "application/json",
                "Accept-Language": "en",
                "User-Agent": "Encore/1.0 (student concert discovery app)",
                "x-api-key": api_key or "",
            },
        )
        self.today = today or date.today()
        self.pagination = {}

    def search(self, artist="", venue="", location="", page=1):
        self.pagination = {}
        if not self.api_key or self.api_key == SETLISTFM_KEY_PLACEHOLDER:
            raise MissingSetlistApiKeyError(
                "Add your Setlist.fm key to SETLISTFM_API_KEY in .env."
            )

        params = {"p": max(int(page), 1)}
        if artist:
            params["artistName"] = artist
        if venue:
            params["venueName"] = venue
        if location:
            city, state_code = self._split_location(location)
            params["cityName"] = city
            if state_code:
                params["stateCode"] = state_code

        payload = self.api.get("search/setlists", params=params)
        self.pagination = {
            "page": payload.get("page", params["p"]),
            "itemsPerPage": payload.get("itemsPerPage", 0),
            "totalItems": payload.get("total", 0),
        }
        seen = set()
        results = []
        for setlist in payload.get("setlist", []):
            event_date = self._parse_date(setlist.get("eventDate", ""))
            identifier = setlist.get("id", "")
            if not event_date or event_date >= self.today or identifier in seen:
                continue
            seen.add(identifier)
            results.append(self._normalize_setlist(setlist, event_date))
        return results

    def has_more(self):
        page = int(self.pagination.get("page") or 1)
        per_page = int(self.pagination.get("itemsPerPage") or 0)
        total = int(self.pagination.get("totalItems") or 0)
        return bool(per_page and page * per_page < total)

    @staticmethod
    def _normalize_setlist(setlist, event_date):
        artist = setlist.get("artist", {})
        venue = setlist.get("venue", {})
        city = venue.get("city", {})
        country = city.get("country", {})
        region = city.get("stateCode", "")
        country_name = country.get("name", "")
        location_parts = [city.get("name", ""), region or country_name]
        tour_name = setlist.get("tour", {}).get("name", "")
        artist_name = artist.get("name", "Unknown artist")

        return {
            "id": setlist.get("id", ""),
            "name": artist_name,
            "artist": "",
            "tour": tour_name,
            "supporting_artists": [],
            "supporting_label": "",
            "additional_artist_count": 0,
            "venue": venue.get("name", "Venue unknown"),
            "location": ", ".join(part for part in location_parts if part),
            "genre": "Past show",
            "date_iso": event_date.isoformat(),
            "date_day": event_date.strftime("%d"),
            "date_month": event_date.strftime("%b").upper(),
            "date_label": event_date.strftime("%a, %b %d, %Y").upper(),
            "time_label": "TIME NOT LISTED",
            "image": "",
            "image_alt": "",
            "url": setlist.get("url", ""),
            "ticket_url": "",
            "status": "Performed",
            "source": "setlistfm",
            "source_label": "Setlist.fm",
            "link_label": "View setlist",
        }

    @staticmethod
    def _parse_date(value):
        try:
            return datetime.strptime(value, "%d-%m-%Y").date()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _split_location(location):
        parts = [part.strip() for part in location.split(",", 1)]
        city = parts[0]
        state_code = parts[1].upper() if len(parts) == 2 and len(parts[1]) == 2 else ""
        return city, state_code
