import secrets

from authlib.integrations.base_client import OAuthError
from authlib.integrations.flask_client import OAuth
from joserfc.errors import JoseError
from flask import abort, flash, redirect, request, session, url_for
from requests.exceptions import RequestException
from services.persistence import current_user


def register_auth(app):
    oauth = OAuth(app)
    google = oauth.register(
        "google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "timeout": 15},
    )

    def configured():
        return (
            bool(app.config.get("GOOGLE_CLIENT_ID"))
            and bool(app.config.get("GOOGLE_CLIENT_SECRET"))
            and len(app.config.get("SECRET_KEY") or "") >= 32
        )

    @app.get("/auth/google/login")
    def google_login():
        if not configured():
            flash("Google sign-in is not configured. Set GOOGLE_CLIENT_ID, "
                  "GOOGLE_CLIENT_SECRET, and a random SECRET_KEY of at least "
                  "32 characters in .env, then restart Encore.", "error")
            return redirect(url_for("index"))
        try:
            return google.authorize_redirect(
                url_for("google_callback", _external=True),
                prompt="select_account",
            )
        except (OAuthError, RequestException, ValueError):
            flash("Google sign-in is temporarily unavailable. Please try again.", "error")
            return redirect(url_for("index"))

    @app.get("/auth/google/callback")
    def google_callback():
        if not configured():
            return redirect(url_for("google_login"))
        try:
            # Authlib checks state, signature, issuer, audience, expiry and nonce.
            token = google.authorize_access_token()
            user = token.get("userinfo")
            if not user or not user.get("sub"):
                raise ValueError("Missing validated identity")
        except (OAuthError, JoseError, RequestException, ValueError, KeyError):
            flash("Google sign-in could not be completed. Please try signing in again.", "error")
            return redirect(url_for("index"))

        session.clear()
        session.permanent = True
        # Store only identity details, never Google tokens, in the signed cookie.
        session["user"] = {
            "sub": user["sub"],
            "name": user.get("name") or "Encore listener",
            "email": user.get("email") if user.get("email_verified") is True else None,
        }
        session["logout_csrf"] = secrets.token_urlsafe(32)
        current_user()
        flash("You are signed in to Encore.", "success")
        return redirect(url_for("index"))

    @app.post("/auth/logout")
    def logout():
        expected = session.get("logout_csrf")
        supplied = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            abort(400)
        session.clear()
        flash("You have signed out of Encore.", "success")
        return redirect(url_for("index"))
