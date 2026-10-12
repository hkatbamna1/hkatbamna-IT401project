import secrets
from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for

from models import SavedSearch, db
from services.persistence import current_user


def register_profile(app):
    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user"):
                flash("Sign in with Google to view your search library.", "error")
                return redirect(url_for("google_login"))
            return view(*args, **kwargs)
        return wrapped

    @app.context_processor
    def csrf_context():
        def csrf_token():
            if "logout_csrf" not in session:
                session["logout_csrf"] = secrets.token_urlsafe(32)
            return session["logout_csrf"]
        return {"csrf_token": csrf_token}

    @app.after_request
    def private_response(response):
        if request.path.startswith("/profile") or session.get("user"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def require_csrf():
        expected = session.get("logout_csrf", "")
        if not expected or not secrets.compare_digest(expected.encode(), request.form.get("csrf_token", "").encode()):
            abort(400, description="Your form expired. Reload the page and try again.")

    def owned_search(search_id):
        user = current_user()
        return db.first_or_404(db.select(SavedSearch).where(
            SavedSearch.id == search_id, SavedSearch.user_id == user.id))

    @app.get("/profile")
    @login_required
    def profile():
        user = current_user()
        query = request.args.get("q", "").strip()
        if len(query) > 160:
            abort(400, description="Library search must be at most 160 characters.")
        stmt = db.select(SavedSearch).where(SavedSearch.user_id == user.id)
        if query:
            stmt = stmt.where(SavedSearch.title.contains(query, autoescape=True))
        searches = db.paginate(stmt.order_by(SavedSearch.last_viewed_at.desc(), SavedSearch.id.desc()),
                               per_page=12, max_per_page=12, error_out=False)
        total = db.session.scalar(db.select(db.func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user.id))
        return render_template("profile.html", user=user, searches=searches, total=total, query=query)

    @app.get("/profile/searches/<int:search_id>")
    @login_required
    def saved_search(search_id):
        saved = owned_search(search_id)
        groups = []
        for source, pages in saved.snapshots.items():
            events, seen = [], set()
            for page_number in sorted(pages, key=int):
                for event in pages[page_number]["events"]:
                    if event["id"] not in seen:
                        seen.add(event["id"])
                        events.append(event)
            groups.append({"source": source, "events": events,
                           "retrieved_at": max(p["retrieved_at"] for p in pages.values()),
                           "page_count": len(pages)})
        return render_template("saved_search.html", saved=saved, groups=groups)

    @app.post("/profile/searches/<int:search_id>/edit")
    @login_required
    def edit_search(search_id):
        require_csrf()
        saved = owned_search(search_id)
        title = request.form.get("title", "").strip()
        notes = request.form.get("description", "").strip()
        if not title or len(title) > 200 or len(notes) > 4000 or any(ord(c) < 32 for c in title):
            abort(400, description="Enter a title of 1–200 characters and notes of at most 4,000 characters.")
        saved.title, saved.description = title, notes
        db.session.commit()
        flash("Saved search updated.", "success")
        if request.form.get("return_to") == "profile":
            return redirect(url_for("profile"))
        return redirect(url_for("saved_search", search_id=saved.id))

    @app.post("/profile/searches/<int:search_id>/delete")
    @login_required
    def delete_search(search_id):
        require_csrf()
        saved = owned_search(search_id)
        db.session.delete(saved)
        db.session.commit()
        flash("Search removed from your library.", "success")
        return redirect(url_for("profile"))
