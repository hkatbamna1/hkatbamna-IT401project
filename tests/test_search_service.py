import pytest

from services.search_service import MissingApiKeyError, SearchService


class FakeApiService:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "geographies/cities":
            return {
                "cities": [
                    {
                        "identifier": "jambase:wrong-city",
                        "name": "Los Angeles",
                        "address": {"addressRegion": "US-TX"},
                    },
                    {
                        "identifier": "jambase:4227404",
                        "name": "Los Angeles",
                        "address": {"addressRegion": "US-CA"},
                    },
                ]
            }
        return {
            "pagination": {
                "page": 1,
                "perPage": 12,
                "totalItems": 25,
                "nextPage": "https://api.data.jambase.com/v3/events?page=2",
            },
            "events": [
                {
                    "@type": "Concert",
                    "identifier": "jambase:event-1",
                    "name": "Hozier at Hollywood Bowl",
                    "url": "https://www.jambase.com/show/hozier-hollywood-bowl",
                    "image": "event.jpg",
                    "eventStatus": "scheduled",
                    "startDate": "2026-10-03T19:30:00-07:00",
                    "location": {
                        "name": "Hollywood Bowl",
                        "address": {
                            "addressLocality": "Los Angeles",
                            "addressRegion": {
                                "name": "California",
                                "alternateName": "CA",
                            },
                        },
                    },
                    "performer": [
                        {
                            "name": "Opening Artist",
                            "image": "opening.jpg",
                            "genre": ["indie"],
                            "x-isHeadliner": False,
                        },
                        {
                            "name": "Hozier",
                            "image": "hozier.jpg",
                            "genre": ["alternative-rock"],
                            "x-isHeadliner": True,
                        },
                    ],
                    "offers": [
                        {
                            "category": "ticketingLinkPrimary",
                            "url": "https://tickets.example.com/hozier",
                        }
                    ],
                }
            ]
        }


def test_search_combines_artist_venue_and_resolved_city_filters():
    api = FakeApiService()
    service = SearchService("test-key", api_service=api)

    events = service.search(
        artist="Hozier", venue="Hollywood Bowl", location="Los Angeles, CA"
    )

    assert api.calls == [
        (
            "geographies/cities",
            {"geoCityName": "Los Angeles", "perPage": 20, "geoStateIso": "US-CA"},
        ),
        (
            "events",
            {
                "page": 1,
                "perPage": 12,
                "artistName": "Hozier",
                "venueName": "Hollywood Bowl",
                "geoCityId": "jambase:4227404",
            },
        ),
    ]
    assert events[0] == {
        "id": "jambase:event-1",
        "name": "Hozier at Hollywood Bowl",
        "artist": "Hozier",
        "supporting_artists": ["Opening Artist"],
        "supporting_label": "With",
        "additional_artist_count": 0,
        "venue": "Hollywood Bowl",
        "location": "Los Angeles, CA",
        "genre": "Alternative Rock",
        "date_iso": "2026-10-03",
        "date_day": "03",
        "date_month": "OCT",
        "date_label": "SAT, OCT 03, 2026",
        "time_label": "7:30 PM",
        "image": "hozier.jpg",
        "image_alt": "Hozier image",
        "url": "https://www.jambase.com/show/hozier-hollywood-bowl",
        "ticket_url": "https://tickets.example.com/hozier",
        "status": "Scheduled",
        "source": "jambase",
        "source_label": "JamBase",
        "link_label": "View on JamBase",
    }
    assert service.pagination["totalItems"] == 25
    assert service.pagination["nextPage"].endswith("page=2")


def test_artist_search_without_location_uses_one_event_request():
    api = FakeApiService()
    service = SearchService("test-key", api_service=api)

    service.search(artist="Hozier", page=3, limit=5)

    assert api.calls[0] == (
        "events",
        {"page": 3, "perPage": 5, "artistName": "Hozier"},
    )


def test_unknown_city_returns_no_events():
    class EmptyCityApi(FakeApiService):
        def get(self, path, params=None):
            self.calls.append((path, params))
            return {"cities": []}

    api = EmptyCityApi()
    service = SearchService("test-key", api_service=api)

    assert service.search(location="Atlantis") == []
    assert len(api.calls) == 1
    assert service.pagination == {}


def test_event_image_is_fallback_for_default_artist_image():
    event = {
        "name": "A Concert",
        "image": "event.jpg",
        "performer": [
            {
                "name": "An Artist",
                "image": "https://example.com/jambase-default-band-image-bw.jpg",
            }
        ],
    }

    result = SearchService._normalize_event(event)

    assert result["image"] == "event.jpg"


def test_searched_opener_is_prioritized_in_lineup_summary():
    event = {
        "@type": "Concert",
        "name": "Harry Styles Live",
        "performer": [
            {"name": "Harry Styles", "x-isHeadliner": True},
            {"name": "First Support", "x-isHeadliner": False},
            {"name": "LCD Soundsystem", "x-isHeadliner": False},
            {"name": "Second Support", "x-isHeadliner": False},
        ],
    }

    result = SearchService._normalize_event(event, searched_artist="LCD Soundsystem")

    assert result["artist"] == "Harry Styles"
    assert result["supporting_label"] == "With"
    assert result["supporting_artists"] == [
        "LCD Soundsystem",
        "First Support",
        "Second Support",
    ]
    assert result["additional_artist_count"] == 0


def test_festival_lineup_is_limited_and_keeps_searched_artist_visible():
    performers = [
        {"name": "Festival Headliner", "x-isHeadliner": True},
        *[
            {"name": f"Festival Artist {number}", "x-isHeadliner": False}
            for number in range(1, 10)
        ],
    ]
    event = {
        "@type": "Festival",
        "name": "A Very Large Festival",
        "performer": performers,
    }

    result = SearchService._normalize_event(
        event, searched_artist="Festival Artist 9"
    )

    assert result["supporting_label"] == "Lineup includes"
    assert result["supporting_artists"] == [
        "Festival Artist 9",
        "Festival Artist 1",
        "Festival Artist 2",
    ]
    assert result["additional_artist_count"] == 6


def test_jambase_client_uses_required_headers():
    service = SearchService("test-key")

    headers = service.api._headers()

    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"].startswith("Encore/")


def test_missing_key_raises_helpful_error():
    service = SearchService(None)

    with pytest.raises(MissingApiKeyError, match="JAMBASE_API_KEY"):
        service.search(artist="Hozier")
