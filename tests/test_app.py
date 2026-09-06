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
