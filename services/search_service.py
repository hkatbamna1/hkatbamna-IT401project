from datetime import datetime

from services.api_service import ApiService


JAMBASE_BASE_URL = "https://api.data.jambase.com/v3"
JAMBASE_KEY_PLACEHOLDER = "paste_your_jambase_api_key_here"


class MissingApiKeyError(RuntimeError):
    """Raised when JamBase search is used without an API key."""


class SearchService:
    """Search JamBase events by artist, venue, and city."""

    def __init__(self, api_key, api_service=None):
        self.api_key = api_key
        self.api = api_service or ApiService(
            JAMBASE_BASE_URL,
            api_key=api_key,
            default_headers={
                "Accept": "application/json",
                "User-Agent": "Encore/1.0 (student concert discovery app)",
            },
        )
        self.pagination = {}

    def search(self, artist="", venue="", location="", page=1, limit=12):
        self.pagination = {}
        if not self.api_key or self.api_key == JAMBASE_KEY_PLACEHOLDER:
            raise MissingApiKeyError(
                "Add your JamBase key to JAMBASE_API_KEY in .env."
            )

        params = {
            "page": max(int(page), 1),
            "perPage": min(max(int(limit), 1), 100),
        }
        if artist:
            params["artistName"] = artist
        if venue:
            params["venueName"] = venue
        if location:
            city_id = self._resolve_city_id(location)
            if not city_id:
                return []
            params["geoCityId"] = city_id

        payload = self.api.get("events", params=params)
        self.pagination = payload.get("pagination", {})
        return [
            self._normalize_event(event, searched_artist=artist)
            for event in payload.get("events", [])
        ]

    def _resolve_city_id(self, location):
        city_name, state_code = self._split_location(location)
        params = {"geoCityName": city_name, "perPage": 20}
        if state_code:
            params["geoStateIso"] = f"US-{state_code}"

        payload = self.api.get("geographies/cities", params=params)
        cities = payload.get("cities", [])
        exact_match = next(
            (
                city
                for city in cities
                if city.get("name", "").casefold() == city_name.casefold()
                and self._city_matches_state(city, state_code)
            ),
            None,
        )
        match = exact_match or (cities[0] if cities else None)
        return match.get("identifier") if match else None

    @staticmethod
    def _city_matches_state(city, state_code):
        if not state_code:
            return True
        region = city.get("address", {}).get("addressRegion", "")
        return region.upper() in {state_code.upper(), f"US-{state_code.upper()}"}

    @staticmethod
    def _normalize_event(event, searched_artist=""):
        venue = event.get("location", {})
        address = venue.get("address", {})
        performers = event.get("performer", [])
        headliner = next(
            (performer for performer in performers if performer.get("x-isHeadliner")),
            performers[0] if performers else {},
        )
        artist_name = headliner.get("name", "")
        image = SearchService._preferred_image(headliner, event)
        genres = headliner.get("genre", [])
        genre = SearchService._format_genre(genres[0]) if genres else "Live music"
        lineup = SearchService._lineup_summary(
            performers,
            searched_artist=searched_artist,
            is_festival=event.get("@type") == "Festival",
        )
        region = address.get("addressRegion", {})
        if isinstance(region, dict):
            region = region.get("alternateName") or region.get("name", "")
        city = address.get("addressLocality", "")

        return {
            "id": event.get("identifier", ""),
            "name": event.get("name", "Untitled event"),
            "artist": artist_name,
            **lineup,
            "venue": venue.get("name", "Venue TBA"),
            "location": ", ".join(part for part in (city, region) if part),
            "genre": genre,
            **SearchService._format_date(event.get("startDate", "")),
            "time_label": SearchService._format_time(event.get("startDate", "")),
            "image": image,
            "image_alt": f"{artist_name or event.get('name', 'Concert')} image",
            "url": event.get("url", ""),
            "ticket_url": SearchService._ticket_url(event.get("offers", [])),
            "status": SearchService._format_status(event.get("eventStatus", "")),
            "source": "jambase",
            "source_label": "JamBase",
            "link_label": "View on JamBase",
        }

    @classmethod
    def _lineup_summary(cls, performers, searched_artist="", is_festival=False):
        unique_performers = []
        seen = set()
        for performer in performers:
            name = performer.get("name", "").strip()
            canonical_name = cls._canonical(name)
            if name and canonical_name not in seen:
                seen.add(canonical_name)
                unique_performers.append(performer)

        headliners = [
            performer
            for performer in unique_performers
            if performer.get("x-isHeadliner")
        ]
        if not headliners and unique_performers:
            headliners = [unique_performers[0]]
        headliner_names = {
            cls._canonical(performer.get("name", "")) for performer in headliners
        }
        supporting = [
            performer.get("name", "")
            for performer in unique_performers
            if cls._canonical(performer.get("name", "")) not in headliner_names
        ]

        query = cls._canonical(searched_artist)
        if query:
            match = next(
                (
                    name
                    for name in supporting
                    if query in cls._canonical(name) or cls._canonical(name) in query
                ),
                None,
            )
            if match:
                supporting.remove(match)
                supporting.insert(0, match)

        displayed = supporting[:3]
        large_lineup = is_festival or len(unique_performers) > 6
        return {
            "supporting_artists": displayed,
            "supporting_label": "Lineup includes" if large_lineup else "With",
            "additional_artist_count": max(len(supporting) - len(displayed), 0),
        }

    @staticmethod
    def _preferred_image(performer, event):
        artist_image = performer.get("image", "")
        if artist_image and "jambase-default-band-image" not in artist_image:
            return artist_image
        return event.get("image", "") or artist_image

    @staticmethod
    def _ticket_url(offers):
        primary = next(
            (offer for offer in offers if offer.get("category") == "ticketingLinkPrimary"),
            None,
        )
        offer = primary or (offers[0] if offers else {})
        return offer.get("url", "")

    @staticmethod
    def _format_date(value):
        parsed = SearchService._parse_datetime(value)
        if not parsed:
            return {
                "date_iso": "",
                "date_day": "--",
                "date_month": "TBA",
                "date_label": "DATE TBA",
            }
        return {
            "date_iso": parsed.strftime("%Y-%m-%d"),
            "date_day": parsed.strftime("%d"),
            "date_month": parsed.strftime("%b").upper(),
            "date_label": parsed.strftime("%a, %b %d, %Y").upper(),
        }

    @staticmethod
    def _format_time(value):
        if not value or "T" not in value:
            return "TIME TBA"
        parsed = SearchService._parse_datetime(value)
        return parsed.strftime("%I:%M %p").lstrip("0") if parsed else "TIME TBA"

    @staticmethod
    def _parse_datetime(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None

    @staticmethod
    def _split_location(location):
        parts = [part.strip() for part in location.split(",", 1)]
        city = parts[0]
        state_code = parts[1].upper() if len(parts) == 2 and len(parts[1]) == 2 else ""
        return city, state_code

    @staticmethod
    def _format_genre(genre):
        return genre.replace("-", " ").title()

    @staticmethod
    def _format_status(status):
        return status.replace("_", " ").title() if status else "Scheduled"

    @staticmethod
    def _canonical(value):
        return "".join(character for character in value.casefold() if character.isalnum())
