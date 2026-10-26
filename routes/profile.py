import secrets
from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for

from models import SavedSearch, SearchEvent, db
from services.persistence import current_user
from services.catalog import normalize
from services.retrieval import (InvalidSearch, LIBRARY_SORTS, SORTS, SOURCES, concert_card,
                               parse_options, record_search, retrieve_library, retrieve_saved)


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
        try:
            options = parse_options(request.args, sorts=LIBRARY_SORTS, default_sort="recent")
            searches, counts = retrieve_library(options, user.id)
        except InvalidSearch as error:
            abort(400, description=str(error))
        total = db.session.scalar(db.select(db.func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user.id))
        # Preserve display values if the optional telemetry transaction rolls back.
        rows = []
        for saved in searches.items:
            values = {field: getattr(saved, field) for field in ("id", "title", "description", "filters", "last_viewed_at")}
            metadata_match = normalize(options.query) in normalize(saved.title + " " + saved.description)
            values["result_url"] = url_for("saved_search", search_id=saved.id, **{
                **options.url_values(), "q": "" if metadata_match else options.query,
                "sort": options.sort if options.sort in SORTS else "relevance"})
            values["matching_concerts"] = counts.get(saved.id, 0)
            rows.append(values)
        searches.items = rows
        display_user = {"title": user.title}
        if "page" not in request.args and (normalize(options.query) or any(options.filters.values())):
            record_search(options, searches.total, session.get("user"), scope="library")
        return render_template("profile.html", user=display_user, searches=searches, total=total,
            query=options.query, values=request.args, options=options, sorts=LIBRARY_SORTS, sources=SOURCES,
            initial_search=not normalize(options.query) and not any(options.filters.values()),
            page_url=lambda page: url_for("profile", **options.url_values(), page=page))

    @app.get("/profile/search-activity")
    @login_required
    def search_activity():
        user = current_user()
        try:
            page = int(request.args.get("page", 1))
            if not 1 <= page <= 1000000:
                raise ValueError
        except ValueError:
            abort(400, description="Enter a valid search activity page number.")
        events = db.paginate(db.select(SearchEvent).where(SearchEvent.user_id == user.id)
                             .order_by(SearchEvent.created_at.desc(), SearchEvent.id.desc()),
                             page=page, per_page=20, max_per_page=20, error_out=False)
        if page > max(events.pages, 1):
            abort(400, description="That search activity page does not exist.")
        return render_template("search_activity.html", events=events)

    @app.get("/profile/searches/<int:search_id>")
    @login_required
    def saved_search(search_id):
        saved = owned_search(search_id)
        try:
            options = parse_options(request.args)
            results = retrieve_saved(options, saved.id)
        except InvalidSearch as error:
            abort(400, description=str(error))
        cards = [concert_card(row) for row in results.items]
        display_saved = {field: getattr(saved, field) for field in
                         ("id", "title", "description", "filters", "created_at", "retrieved_at")}
        if "page" not in request.args and (normalize(options.query) or any(options.filters.values())):
            record_search(options, results.total, session.get("user"), scope=f"saved:{search_id}")
        return render_template("saved_search.html", saved=display_saved, results=results,
            result_events=cards, values=request.args, options=options, sorts=SORTS, sources=SOURCES,
            page_url=lambda page: url_for("saved_search", search_id=search_id, **options.url_values(), page=page))

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
