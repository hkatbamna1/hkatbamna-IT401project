from services.search_service import SearchService


class FakeApiService:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "attractions.json":
            return {
                "_embedded": {
                    "attractions": [
                        {"id": "wrong", "name": "Hozier Tribute"},
                        {"id": "artist-123", "name": "Hozier"},
                    ]
                }
            }
        if path == "venues.json":
            return {
                "_embedded": {
                    "venues": [{"id": "venue-456", "name": "Hollywood Bowl"}]
                }
            }
        return {
            "_embedded": {
                "events": [
                    {
                        "id": "event-789",
                        "name": "Hozier: Unreal Unearth Tour",
                        "url": "https://example.com/tickets",
                        "dates": {
                            "start": {"localDate": "2026-10-03", "localTime": "19:30:00"}
                        },
                        "classifications": [{"genre": {"name": "Rock"}}],
                        "images": [
                            {"url": "small.jpg", "ratio": "16_9", "width": 640},
                            {"url": "large.jpg", "ratio": "16_9", "width": 2048},
                        ],
                        "_embedded": {
                            "attractions": [
                                {
                                    "name": "Hozier",
                                    "images": [
                                        {
                                            "url": "artist-small.jpg",
                                            "ratio": "16_9",
                                            "width": 640,
                                        },
                                        {
                                            "url": "artist-large.jpg",
                                            "ratio": "16_9",
                                            "width": 2048,
                                        },
                                    ],
                                }
                            ],
                            "venues": [
                                {
                                    "name": "Hollywood Bowl",
                                    "city": {"name": "Los Angeles"},
                                    "state": {"stateCode": "CA"},
                                }
                            ],
                        },
                    }
                ]
            }
        }


def test_search_resolves_artist_and_venue_then_combines_filters():
    api = FakeApiService()
    service = SearchService("test-key", api_service=api)

    events = service.search(
        artist="Hozier", venue="Hollywood Bowl", location="Los Angeles"
    )

    assert [call[0] for call in api.calls] == [
        "attractions.json",
        "venues.json",
        "events.json",
    ]
    event_params = api.calls[-1][1]
    assert event_params["attractionId"] == "artist-123"
    assert event_params["venueId"] == "venue-456"
    assert event_params["city"] == "Los Angeles"
    assert event_params["classificationName"] == "music"
    assert event_params["apikey"] == "test-key"
    assert events[0] == {
        "id": "event-789",
        "name": "Hozier: Unreal Unearth Tour",
        "artist": "Hozier",
        "venue": "Hollywood Bowl",
        "location": "Los Angeles, CA",
        "genre": "Rock",
        "date_day": "03",
        "date_month": "OCT",
        "date_label": "SAT, OCT 03",
        "time_label": "7:30 PM",
        "image": "artist-large.jpg",
        "image_alt": "Hozier artist image",
        "url": "https://example.com/tickets",
    }


def test_zip_location_uses_postal_code_filter():
    api = FakeApiService()
    service = SearchService("test-key", api_service=api)

    service.search(location="90068")

    params = api.calls[-1][1]
    assert params["postalCode"] == "90068"
    assert "city" not in params


def test_event_image_is_used_when_artist_image_is_unavailable():
    event = {
        "name": "A Concert",
        "images": [{"url": "event.jpg", "ratio": "16_9", "width": 1024}],
        "_embedded": {"attractions": [{"name": "An Artist"}]},
    }

    result = SearchService._normalize_event(event)

    assert result["image"] == "event.jpg"
    assert result["image_alt"] == "A Concert event image"
