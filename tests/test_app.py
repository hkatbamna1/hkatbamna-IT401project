import pytest

from app import create_app


@pytest.fixture
def client():
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
    client.application.config["TICKETMASTER_API_KEY"] = None

    response = client.get("/?artist=Hozier")

    assert response.status_code == 200
    assert b"Search unavailable" in response.data
    assert b"TICKETMASTER_API_KEY" in response.data


def test_search_results_are_rendered(client, monkeypatch):
    class FakeSearchService:
        def __init__(self, api_key):
            pass

        def search(self, **filters):
            assert filters == {"artist": "Hozier", "venue": "", "location": "Seattle"}
            return [
                {
                    "id": "event-1",
                    "name": "Hozier Live",
                    "artist": "Hozier",
                    "venue": "Climate Pledge Arena",
                    "location": "Seattle, WA",
                    "genre": "Rock",
                    "date_day": "10",
                    "date_month": "OCT",
                    "date_label": "SAT, OCT 10",
                    "time_label": "8:00 PM",
                    "image": "https://example.com/hozier.jpg",
                    "image_alt": "Hozier artist image",
                    "url": "https://example.com/event-1",
                }
            ]

    monkeypatch.setattr("routes.main.SearchService", FakeSearchService)

    response = client.get("/?artist=Hozier&location=Seattle")

    assert response.status_code == 200
    assert b"Hozier Live" in response.data
    assert b"Climate Pledge Arena" in response.data
    assert b'Hozier artist image' in response.data
    assert b"See on Ticketmaster" in response.data
