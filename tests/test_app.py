import pytest

from app import create_app


@pytest.fixture
def client(monkeypatch):
    class EmptySetlistService:
        def __init__(self, api_key):
            self.pagination = {"totalItems": 0}

        def search(self, **filters):
            return []

        def has_more(self):
            return False

    monkeypatch.setattr("routes.main.SetlistService", EmptySetlistService)
    app = create_app("development")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Your next favorite" in response.data


def test_about(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert b"Great music is closer" in response.data


def test_search_without_api_key_shows_setup_message(client):
    client.application.config["JAMBASE_API_KEY"] = None

    response = client.get("/?artist=Hozier")

    assert response.status_code == 200
    assert b"Upcoming search unavailable" in response.data
    assert b"JAMBASE_API_KEY" in response.data


def test_search_results_are_rendered(client, monkeypatch):
    class FakeSearchService:
        def __init__(self, api_key):
            self.pagination = {
                "totalItems": 25,
                "nextPage": "https://api.data.jambase.com/v3/events?page=2",
            }

        def search(self, **filters):
            assert filters == {
                "artist": "Hozier",
                "venue": "",
                "location": "Seattle",
                "page": 1,
            }
            return [
                {
                    "id": "event-1",
                    "name": "Hozier Live",
                    "artist": "Hozier",
                    "supporting_artists": ["Opening Artist"],
                    "supporting_label": "With",
                    "additional_artist_count": 0,
                    "venue": "Climate Pledge Arena",
                    "location": "Seattle, WA",
                    "genre": "Rock",
                    "date_iso": "2026-10-10",
                    "date_day": "10",
                    "date_month": "OCT",
                    "date_label": "SAT, OCT 10, 2026",
                    "time_label": "8:00 PM",
                    "image": "https://example.com/hozier.jpg",
                    "image_alt": "Hozier artist image",
                    "url": "https://example.com/event-1",
                    "ticket_url": "https://tickets.example.com/event-1",
                    "status": "Scheduled",
                    "source": "jambase",
                    "source_label": "JamBase",
                    "link_label": "View on JamBase",
                },
            ]

    monkeypatch.setattr("routes.main.SearchService", FakeSearchService)

    response = client.get("/?artist=Hozier&location=Seattle")

    assert response.status_code == 200
    assert b"Hozier Live" in response.data
    assert b"Climate Pledge Arena" in response.data
    assert b'Hozier artist image' in response.data
    assert b"Opening Artist" in response.data
    assert b"Upcoming concerts" in response.data
    assert b"Concerts matching your search" in response.data
    assert b"hero-search-results" in response.data
    assert b"View on JamBase" in response.data
    assert b"Tickets" in response.data
    assert b"Showing 1 of 25" in response.data
    assert b"Load more concerts" in response.data
    assert b"page=2" in response.data


def test_load_more_events_returns_rendered_cards(client, monkeypatch):
    class FakeSearchService:
        def __init__(self, api_key):
            self.pagination = {}

        def search(self, **filters):
            assert filters == {
                "artist": "Hozier",
                "venue": "",
                "location": "Seattle",
                "page": 2,
            }
            return [
                {
                    "name": "Hozier Encore",
                    "artist": "Hozier",
                    "supporting_artists": [],
                    "additional_artist_count": 0,
                    "venue": "Paramount Theatre",
                    "location": "Seattle, WA",
                    "genre": "Rock",
                    "date_iso": "2026-10-11",
                    "date_day": "11",
                    "date_month": "OCT",
                    "date_label": "SUN, OCT 11, 2026",
                    "time_label": "8:00 PM",
                    "image": "",
                    "status": "Scheduled",
                    "source": "jambase",
                    "source_label": "JamBase",
                    "url": "https://example.com/event-2",
                    "link_label": "View on JamBase",
                    "ticket_url": "",
                }
            ]

    monkeypatch.setattr("routes.main.SearchService", FakeSearchService)

    response = client.get(
        "/api/events?artist=Hozier&location=Seattle&page=2",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert "Hozier Encore" in payload["html"]
    assert "Paramount Theatre" in payload["html"]
    assert payload["has_more"] is False
    assert payload["next_page"] == 3


def test_past_results_have_a_separate_setlist_section(client, monkeypatch):
    class EmptySearchService:
        def __init__(self, api_key):
            self.pagination = {"totalItems": 0}

        def search(self, **filters):
            return []

    class FakeSetlistService:
        def __init__(self, api_key):
            self.pagination = {"totalItems": 42}

        def search(self, **filters):
            assert filters == {
                "artist": "Radiohead",
                "venue": "",
                "location": "",
                "page": 1,
            }
            return [
                {
                    "name": "Radiohead",
                    "artist": "",
                    "tour": "A Moon Shaped Pool",
                    "supporting_artists": [],
                    "venue": "Greek Theatre",
                    "location": "Berkeley, CA",
                    "genre": "Past show",
                    "date_iso": "2017-04-18",
                    "date_day": "18",
                    "date_month": "APR",
                    "date_label": "TUE, APR 18, 2017",
                    "time_label": "TIME NOT LISTED",
                    "image": "",
                    "status": "Performed",
                    "source": "setlistfm",
                    "source_label": "Setlist.fm",
                    "url": "https://www.setlist.fm/setlist/example",
                    "link_label": "View setlist",
                    "ticket_url": "",
                }
            ]

        def has_more(self):
            return True

    monkeypatch.setattr("routes.main.SearchService", EmptySearchService)
    monkeypatch.setattr("routes.main.SetlistService", FakeSetlistService)

    response = client.get("/?artist=Radiohead")

    assert response.status_code == 200
    assert b"Past concerts" in response.data
    assert b"Powered by Setlist.fm" in response.data
    assert b"Greek Theatre" in response.data
    assert b"Tour:</strong> A Moon Shaped Pool" in response.data
    assert b"Load more past concerts" in response.data
    assert b"Upcoming concerts" not in response.data


def test_load_more_past_events_returns_setlist_cards(client, monkeypatch):
    class FakeSetlistService:
        def __init__(self, api_key):
            self.pagination = {}

        def search(self, **filters):
            assert filters["page"] == 2
            return [
                {
                    "name": "Radiohead",
                    "artist": "",
                    "supporting_artists": [],
                    "venue": "Roundhouse",
                    "location": "London, United Kingdom",
                    "genre": "Past show",
                    "date_iso": "2016-05-27",
                    "date_day": "27",
                    "date_month": "MAY",
                    "date_label": "FRI, MAY 27, 2016",
                    "time_label": "TIME NOT LISTED",
                    "image": "",
                    "status": "Performed",
                    "source": "setlistfm",
                    "source_label": "Setlist.fm",
                    "url": "https://www.setlist.fm/setlist/example-2",
                    "link_label": "View setlist",
                    "ticket_url": "",
                }
            ]

        def has_more(self):
            return False

    monkeypatch.setattr("routes.main.SetlistService", FakeSetlistService)

    response = client.get("/api/past-events?artist=Radiohead&page=2")

    assert response.status_code == 200
    payload = response.get_json()
    assert "Roundhouse" in payload["html"]
    assert payload["has_more"] is False
    assert payload["next_page"] == 3
