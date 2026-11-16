import secrets
from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload, load_only

from models import RecommendationGeneration, SavedSearch, SearchEvent, db
from services.generation_store import get_generation
from services.intelligence import GenerationOutcome, generate_recommendations
from services.persistence import current_user
from services.catalog import normalize
from services.recommendation_requests import RecommendationRequests
from services.recommendation_views import recommendation_view
from services.recommendations import CANDIDATE_LIMIT, RecommendationScopeNotFound
from services.retrieval import (InvalidSearch, LIBRARY_SORTS, SORTS, SOURCES, concert_card,
                               parse_options, record_search, retrieve_library, retrieve_saved)
from services.validators import InvalidRecommendation


def register_profile(app):
    recommendation_requests = RecommendationRequests()
    app.extensions["recommendation_requests"] = recommendation_requests

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
        return {"csrf_token": csrf_token, "recommendation_candidate_limit": CANDIDATE_LIMIT}

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

    def profile_context(values, *, log_search=False):
        user = current_user()
        try:
            options = parse_options(values, sorts=LIBRARY_SORTS, default_sort="recent")
            searches, counts = retrieve_library(options, user.id)
            recommendation_values = {key: values["rec_" + key] for key in
                ("q", "source", "location", "date_from", "date_to", "sort") if "rec_" + key in values}
            parse_options(recommendation_values)
        except InvalidSearch as error:
            abort(400, description=str(error))
        total = db.session.scalar(db.select(db.func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user.id))
        # Preserve display values if the optional telemetry transaction rolls back.
        rows = []
        for saved in searches.items:
            row_values = {field: getattr(saved, field) for field in ("id", "title", "description", "filters", "last_viewed_at")}
            metadata_match = normalize(options.query) in normalize(saved.title + " " + saved.description)
            row_values["result_url"] = url_for("saved_search", search_id=saved.id, **{
                **options.url_values(), "q": "" if metadata_match else options.query,
                "sort": options.sort if options.sort in SORTS else "relevance"})
            row_values["matching_concerts"] = counts.get(saved.id, 0)
            rows.append(row_values)
        searches.items = rows
        display_user = {"title": user.title, "id": user.id}
        sources_for_recommendations = db.session.execute(db.select(SavedSearch.id, SavedSearch.title)
            .where(SavedSearch.user_id == user.id)
            .order_by(SavedSearch.last_viewed_at.desc(), SavedSearch.id.desc())).all()
        source_choices = [{"id": str(row.id), "title": row.title} for row in sources_for_recommendations]
        recent = db.session.execute(db.select(RecommendationGeneration.id, RecommendationGeneration.generated_at)
            .join(SavedSearch).where(SavedSearch.user_id == user.id)
            .order_by(RecommendationGeneration.generated_at.desc(), RecommendationGeneration.id.desc()).limit(3)).all()
        recent = [{"id": row.id, "generated_at": row.generated_at} for row in recent]
        form_token = recommendation_requests.issue(user.id)
        if log_search and "page" not in values and (normalize(options.query) or any(options.filters.values())):
            record_search(options, searches.total, session.get("user"), scope="library")
        return dict(user=display_user, searches=searches, total=total,
            query=options.query, values=values, options=options, sorts=LIBRARY_SORTS, sources=SOURCES,
            concert_sorts=SORTS, recommendation_sources=source_choices,
            recommendation_selected_id=values.get("recommend_from", ""),
            recommendation_values=recommendation_values, recent_generations=recent,
            recommendation_token=form_token, recommendation_preferences="", recommendation_outcome=None,
            recommendation_result=None, recommendation_metadata=None,
            initial_search=not normalize(options.query) and not any(options.filters.values()),
            page_url=lambda page: url_for("profile", **options.url_values(), page=page))

    @app.get("/profile")
    @login_required
    def profile():
        return render_template("profile.html", **profile_context(request.args, log_search=True))

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

    def saved_context(saved, values, *, log_search=False):
        try:
            options = parse_options(values)
            results = retrieve_saved(options, saved.id)
        except InvalidSearch as error:
            abort(400, description=str(error))
        cards = [concert_card(row) for row in results.items]
        results.items = cards  # Freeze display data before releasing the read transaction.
        display_saved = {field: getattr(saved, field) for field in
                         ("id", "title", "description", "filters", "created_at", "retrieved_at")}
        if log_search and "page" not in values and (normalize(options.query) or any(options.filters.values())):
            record_search(options, results.total, session.get("user"), scope=f"saved:{display_saved['id']}")
        return dict(saved=display_saved, results=results, result_events=cards, values=values,
            options=options, sorts=SORTS, sources=SOURCES,
            page_url=lambda page: url_for("saved_search", search_id=display_saved["id"], **options.url_values(), page=page))

    @app.get("/profile/searches/<int:search_id>")
    @login_required
    def saved_search(search_id):
        saved = owned_search(search_id)
        return render_template("saved_search.html", **saved_context(saved, request.args, log_search=True))

    @app.post("/profile/searches/<int:search_id>/recommendations")
    @app.post("/profile/recommendations")
    @login_required
    def generate_concert_recommendations(search_id=None):
        require_csrf()
        if search_id is None:
            try:
                search_id = int(request.form.get("saved_search_id", ""))
                if search_id < 1:
                    raise ValueError
            except ValueError:
                abort(400, description="Choose a saved search to recommend concerts from.")
        saved = owned_search(search_id)
        user_id = saved.user_id
        context = profile_context(request.args)
        context["recommendation_selected_id"] = str(search_id)
        context["recommendation_values"] = request.form
        preferences = request.form.get("preferences", "")
        context["recommendation_preferences"] = preferences[:1000]
        token = request.form.get("recommendation_token", "")
        submission = recommendation_requests.claim(token, user_id, search_id)
        if submission.status == "complete":
            return redirect(url_for("recommendation_detail", generation_id=submission.generation_id), code=303)
        if submission.status != "accepted":
            message = ("Recommendations for this saved search are already being generated. Please wait, then open Shortlist history to view the result."
                       if submission.status == "pending" else
                       "This recommendation form was already used or expired. Use the new form below to make another request.")
            context["recommendation_outcome"] = GenerationOutcome(
                "in_progress" if submission.status == "pending" else "request_used", message)
            return render_template("profile.html", **context), 409
        db.session.rollback()  # The search cards and headings are now plain display values.
        generation_id = None
        try:
            outcome = generate_recommendations(saved_id=search_id, user_id=user_id,
                values=request.form, preferences=preferences)
            generation_id = outcome.generation_id
        finally:
            recommendation_requests.finish(token, generation_id)
        if outcome.saved:
            return redirect(url_for("recommendation_detail", generation_id=outcome.generation_id), code=303)
        if outcome.code == "not_found":
            abort(404, description="Saved search not found.")
        context["recommendation_outcome"] = outcome
        if outcome.has_output:
            context["recommendation_result"] = recommendation_view(outcome.output, outcome.source_context)
            context["recommendation_metadata"] = dict(model=outcome.model, prompt_version=outcome.prompt_version,
                generated_label=outcome.generated_at[:19].replace("T", " ") + " UTC")
        status = {"invalid_request": 400, "empty_context": 200, "rate_limited": 429}.get(outcome.code, 503)
        return render_template("profile.html", **context), status

    @app.get("/profile/recommendations")
    @login_required
    def recommendation_history():
        user = current_user()
        statement = db.select(RecommendationGeneration).join(SavedSearch).where(SavedSearch.user_id == user.id)
        selected_search = None
        if "search_id" in request.args:
            try:
                search_id = int(request.args["search_id"])
                if search_id < 1:
                    raise ValueError
            except ValueError:
                abort(400, description="Choose a valid saved search.")
            selected_search = owned_search(search_id)
            statement = statement.where(RecommendationGeneration.saved_search_id == search_id)
        try:
            page = parse_options({"page": request.args.get("page", "1")}).page
        except InvalidSearch as error:
            abort(400, description=str(error))
        statement = statement.options(load_only(RecommendationGeneration.id, RecommendationGeneration.saved_search_id,
            RecommendationGeneration.user_request, RecommendationGeneration.generated_at),
            joinedload(RecommendationGeneration.saved_search).load_only(SavedSearch.id, SavedSearch.title))
        generations = db.paginate(statement.order_by(RecommendationGeneration.generated_at.desc(), RecommendationGeneration.id.desc()),
                                  page=page, per_page=12, max_per_page=12, error_out=False)
        if page > max(generations.pages, 1):
            abort(400, description="That recommendation history page does not exist.")
        return render_template("recommendation_history.html", results=generations, selected_search=selected_search,
            page_url=lambda number: url_for("recommendation_history", page=number,
                **({"search_id": selected_search.id} if selected_search else {})))

    @app.get("/profile/recommendations/<int:generation_id>")
    @login_required
    def recommendation_detail(generation_id):
        user = current_user()
        try:
            generation = get_generation(generation_id, user.id)
        except RecommendationScopeNotFound:
            abort(404, description="Generated recommendations not found.")
        try:
            view = recommendation_view(generation.output, generation.source_context)
        except InvalidRecommendation:
            return render_template("error.html", message="This saved recommendation result could not be verified. Return to your library to generate a new shortlist."), 503
        return render_template("recommendation_detail.html", generation=generation, recommendation_result=view,
            recommendation_metadata=dict(model=generation.model, prompt_version=generation.prompt_version,
                generated_label=generation.generated_at.strftime("%b %d, %Y %H:%M UTC")),
            return_url=url_for("profile", recommend_from=generation.saved_search_id,
                **{"rec_" + key: value for key, value in generation.retrieval_options.items()}) + "#recommendations")

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
