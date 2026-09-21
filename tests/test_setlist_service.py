from datetime import date

import pytest

from services.setlist_service import MissingSetlistApiKeyError, SetlistService


class FakeApiService:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return {
            "total": 41,
            "page": 2,
            "itemsPerPage": 20,
            "setlist": [
                {
                    "id": "past-1",
                    "eventDate": "18-04-2017",
                    "url": "https://www.setlist.fm/setlist/radiohead/past-1",
                    "artist": {"name": "Radiohead"},
                    "tour": {"name": "A Moon Shaped Pool"},
                    "venue": {
                        "name": "Greek Theatre",
                        "city": {
                            "name": "Berkeley",
                            "stateCode": "CA",
                            "country": {"name": "United States"},
                        },
                    },
                },
                {
                    "id": "future-1",
                    "eventDate": "21-09-2026",
                    "artist": {"name": "Radiohead"},
                    "venue": {"name": "Future Arena"},
                },
            ],
        }


def test_search_combines_filters_and_only_returns_past_concerts():
    api = FakeApiService()
    service = SetlistService(
        "test-key",
        api_service=api,
        today=date(2026, 9, 20),
    )

    events = service.search(
        artist="Radiohead",
        venue="Greek Theatre",
        location="Berkeley, CA",
        page=2,
    )

    assert api.calls == [
        (
            "search/setlists",
            {
                "p": 2,
                "artistName": "Radiohead",
                "venueName": "Greek Theatre",
                "cityName": "Berkeley",
                "stateCode": "CA",
            },
        )
    ]
    assert len(events) == 1
    assert events[0]["name"] == "Radiohead"
    assert events[0]["artist"] == ""
    assert events[0]["tour"] == "A Moon Shaped Pool"
    assert events[0]["venue"] == "Greek Theatre"
    assert events[0]["location"] == "Berkeley, CA"
    assert events[0]["source"] == "setlistfm"
    assert events[0]["date_iso"] == "2017-04-18"
    assert service.has_more() is True


def test_setlist_client_uses_api_key_header_without_bearer_auth():
    service = SetlistService("test-key")

    headers = service.api._headers()

    assert headers["x-api-key"] == "test-key"
    assert headers["Accept"] == "application/json"
    assert "Authorization" not in headers


def test_missing_setlist_key_raises_helpful_error():
    service = SetlistService(None)

    with pytest.raises(MissingSetlistApiKeyError, match="SETLISTFM_API_KEY"):
        service.search(artist="Radiohead")
