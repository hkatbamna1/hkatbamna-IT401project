import time
from urllib.parse import parse_qs, urlparse

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from requests.exceptions import ConnectionError

from app import create_app


@pytest.fixture
def auth(monkeypatch):
    app = create_app("testing")
    from models import db
    with app.app_context():
        db.create_all()
    app.config.update(TESTING=True, SECRET_KEY="test-secret-" * 4,
                      GOOGLE_CLIENT_ID="test-client", GOOGLE_CLIENT_SECRET="test-secret")
    google = app.extensions["authlib.integrations.flask_client"].google
    # Client was registered before the fixture supplied its configuration.
    google.client_id = "test-client"
    google.client_secret = "test-secret"
    key = RSAKey.generate_key(2048)
    google.server_metadata.update(
        issuer="https://accounts.google.com",
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        id_token_signing_alg_values_supported=["RS256"],
        jwks={"keys": [key.as_dict(private=False)]},
    )
    monkeypatch.setattr(google, "load_server_metadata", lambda: google.server_metadata)
    return app.test_client(), google, key


def complete_login(auth, monkeypatch, **overrides):
    client, google, key = auth
    response = client.get("/auth/google/login")
    params = parse_qs(urlparse(response.location).query)
    claims = dict(iss="https://accounts.google.com", aud="test-client",
                  sub="google-user-123", name="Test Listener", email="user@example.com",
                  email_verified=True, nonce=params["nonce"][0],
                  iat=int(time.time()), exp=int(time.time()) + 300)
    claims.update(overrides)
    encoded = jwt.encode({"alg": "RS256"}, claims, key)
    monkeypatch.setattr(google, "fetch_access_token", lambda **kwargs: {
        "id_token": encoded, "access_token": "private-access-token", "token_type": "Bearer"})
    return client.get("/auth/google/callback", query_string={
        "state": params["state"][0], "code": "test-code"}, follow_redirects=True)


def test_login_redirect_has_state_nonce_and_callback(auth):
    client, _, _ = auth
    response = client.get("/auth/google/login", base_url="http://127.0.0.1:5000")
    params = parse_qs(urlparse(response.location).query)
    assert urlparse(response.location).hostname == "accounts.google.com"
    assert params["redirect_uri"] == ["http://127.0.0.1:5000/auth/google/callback"]
    assert params["state"] and params["nonce"]
    assert set(params["scope"][0].split()) == {"openid", "email", "profile"}


def test_valid_login_stores_identity_without_tokens(auth, monkeypatch):
    response = complete_login(auth, monkeypatch)
    assert b"Test Listener" in response.data and b"Sign out" in response.data
    with auth[0].session_transaction() as session:
        assert session["user"]["sub"] == "google-user-123"
        assert session["user"]["email"] == "user@example.com"
        assert "private-access-token" not in str(dict(session))
        assert "id_token" not in session


@pytest.mark.parametrize("claims", [
    {"nonce": "wrong"}, {"aud": "wrong"}, {"iss": "https://attacker.example"},
    {"exp": 1}, {"sub": ""},
])
def test_invalid_identity_is_rejected(auth, monkeypatch, claims):
    response = complete_login(auth, monkeypatch, **claims)
    assert b"could not be completed" in response.data
    with auth[0].session_transaction() as session:
        assert "user" not in session


@pytest.mark.parametrize("query", [{"state": "wrong", "code": "fake"},
                                       {"error": "access_denied"}, {}])
def test_invalid_or_cancelled_callback(auth, query):
    response = auth[0].get("/auth/google/callback", query_string=query, follow_redirects=True)
    assert response.status_code == 200
    assert b"could not be completed" in response.data


@pytest.mark.parametrize("setting,value", [("GOOGLE_CLIENT_ID", None),
    ("GOOGLE_CLIENT_SECRET", None), ("SECRET_KEY", "dev")])
def test_missing_configuration(auth, setting, value):
    auth[0].application.config[setting] = value
    response = auth[0].get("/auth/google/login", follow_redirects=True)
    assert b"Google sign-in is not configured" in response.data


def test_google_unavailable(auth, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("Offline")
    monkeypatch.setattr(auth[1], "authorize_redirect", unavailable)
    response = auth[0].get("/auth/google/login", follow_redirects=True)
    assert b"temporarily unavailable" in response.data


def test_logout_requires_csrf_and_clears_session(auth, monkeypatch):
    complete_login(auth, monkeypatch)
    client = auth[0]
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").status_code == 400
    assert client.post("/auth/logout", data={"csrf_token": "wrong"}).status_code == 400
    with client.session_transaction() as session:
        csrf = session["logout_csrf"]
        assert "user" in session
    response = client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=True)
    assert b"Sign in with Google" in response.data
    with client.session_transaction() as session:
        assert "user" not in session


def test_production_cookies_are_secure():
    app = create_app("production")
    assert app.config["SESSION_COOKIE_SECURE"]
    assert app.config["SESSION_COOKIE_HTTPONLY"]
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
