"""Authenticated recommendation routes and progressive, quota-conscious UI."""
import copy
import html
import re

import pytest
from flask import current_app
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models import RecommendationGeneration, SavedSearch, db
from services.generation_store import add_generation
from services.intelligence import generate_recommendations
from services.recommendations import retrieve_recommendation_candidates
from services.retrieval import parse_options
from tests.test_intelligence import arrange_ai, count_generations, forbid_ai
from tests.test_recommendations import TODAY, response, scope


def client_for(subject="recommend-owner"):
    client = current_app.test_client()
    with client.session_transaction() as session:
        session["user"] = {"sub": subject, "name": "Test listener"}
        session["logout_csrf"] = "recommendation-csrf"
    return client


def form_data(client, saved_id, **values):
    page = client.get("/profile", query_string={"recommend_from": saved_id})
    assert page.status_code == 200
    form = re.search(r'<form[^>]+data-recommendation-form[^>]*>(.*?)</form>', page.get_data(as_text=True), re.S).group(1)
    fields = {name: html.unescape(value) for name, value in re.findall(r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"', form)}
    fields.update(saved_search_id=str(saved_id), **values)
    return fields


def install_ai(monkeypatch, **settings):
    calls = arrange_ai(monkeypatch, **settings)
    monkeypatch.setattr("routes.profile.generate_recommendations", lambda **kwargs: generate_recommendations(**kwargs, today=TODAY))
    return calls


def stage_history(scope, *, foreign=False, preferences="A private preference"):
    user_id = scope["other"] if foreign else scope["owner"]
    saved_id = scope["other_saved"] if foreign else scope["saved"]
    options = parse_options({})
    batch = retrieve_recommendation_candidates(options, saved_id, user_id, today=TODAY)
    generation = add_generation(user_id=user_id, batch=batch, options=options, preferences=preferences,
        output=response(batch), prompt_version="concert-recommendations-v1", model="gemini-test", today=TODAY)
    db.session.commit()
    return generation


def test_library_exposes_form_and_saved_results_link_back_without_ai(scope, monkeypatch):
    forbid_ai(monkeypatch)
    client = client_for()
    fields = form_data(client, scope["saved"], q="Hozier", location="Los Angeles", sort="date_asc", page="2")
    assert fields["q"] == "Hozier" and fields["location"] == "Los Angeles" and fields["page"] == "2"
    assert fields["csrf_token"] == "recommendation-csrf" and fields["recommendation_token"]
    page = client.get("/profile", query_string={"recommend_from": scope["saved"]})
    assert b"Recommend concerts" in page.data and b"Shortlist history" in page.data
    assert b"recommendations.js" in page.data and "no-store" in page.headers["Cache-Control"]
    element_ids = re.findall(r'\bid="([^"]+)"', page.get_data(as_text=True))
    assert len(element_ids) == len(set(element_ids))
    assert f'<option value="{scope["saved"]}" selected>'.encode() in page.data
    source_page = client.get(f"/profile/searches/{scope['saved']}")
    assert b"Recommend concerts in My library" in source_page.data
    assert b"data-recommendation-form" not in source_page.data


def test_success_redirects_to_saved_result_and_replay_refresh_history_use_no_ai(scope, monkeypatch):
    calls = install_ai(monkeypatch)
    client = client_for()
    fields = {**form_data(client, scope["saved"]), "preferences": "Prefer venue variety."}
    url = "/profile/recommendations"
    created = client.post(url, data=fields)
    assert created.status_code == 303
    detail_url = created.headers["Location"]
    page = client.get(detail_url)
    assert page.status_code == 200 and page.data.count(b'class="recommendation-card"') == 3
    assert b"Why this fits" in page.data and b"View structured response" in page.data
    assert b"Generation details" in page.data and b"Prefer venue variety." in page.data
    assert b"View original listing" in page.data and "no-store" in page.headers["Cache-Control"]
    replay = client.post(url, data=fields)
    assert replay.status_code == 303 and replay.headers["Location"] == detail_url
    assert client.get(detail_url).status_code == 200
    assert client.get("/profile/recommendations").status_code == 200
    assert len(calls) == 1 and count_generations() == 1


@pytest.mark.parametrize("csrf", ["", "bad-csrf"])
def test_generation_requires_csrf_before_ai(scope, monkeypatch, csrf):
    forbid_ai(monkeypatch)
    client = client_for()
    fields = {**form_data(client, scope["saved"]), "csrf_token": csrf}
    assert client.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields).status_code == 400
    assert count_generations() == 0


def test_foreign_scope_and_forged_user_id_cannot_change_ownership(scope, monkeypatch):
    calls = install_ai(monkeypatch)
    owner = client_for()
    fields = {**form_data(owner, scope["saved"]), "user_id": str(scope["other"])}
    foreign = client_for("recommend-other")
    assert foreign.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields).status_code == 404
    assert not calls
    result = owner.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields)
    assert result.status_code == 303 and len(calls) == 1
    assert foreign.get(result.headers["Location"]).status_code == 404


@pytest.mark.parametrize("method,path", [("get", "/profile/recommendations"),
                                        ("get", "/profile/recommendations/1"),
                                        ("post", "/profile/searches/1/recommendations")])
def test_recommendation_routes_require_sign_in(scope, monkeypatch, method, path):
    forbid_ai(monkeypatch)
    assert getattr(current_app.test_client(), method)(path).status_code == 302


def test_inflight_request_blocks_other_forms_for_same_search(scope, monkeypatch):
    forbid_ai(monkeypatch)
    client = client_for()
    first = form_data(client, scope["saved"])
    guard = current_app.extensions["recommendation_requests"]
    assert guard.claim(first["recommendation_token"], scope["owner"], scope["saved"]).status == "accepted"
    second = form_data(client, scope["saved"])
    blocked = client.post(f"/profile/searches/{scope['saved']}/recommendations", data=second)
    assert blocked.status_code == 409 and b"already being generated" in blocked.data
    assert b'<article class="history-card' in blocked.data and count_generations() == 0


@pytest.mark.parametrize("token", ["", "forged-token"])
def test_missing_or_unknown_submission_token_uses_no_quota(scope, monkeypatch, token):
    forbid_ai(monkeypatch)
    client = client_for()
    fields = {**form_data(client, scope["saved"]), "recommendation_token": token}
    page = client.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields)
    assert page.status_code == 409 and b"already used or expired" in page.data
    assert b'<article class="history-card' in page.data


@pytest.mark.parametrize("status,message", [(429, b"quota limit"), (504, b"too long"), (503, b"temporarily unavailable")])
def test_provider_failures_preserve_search_and_block_post_replay(scope, monkeypatch, status, message):
    calls = install_ai(monkeypatch, status=status)
    client = client_for()
    fields = {**form_data(client, scope["saved"]), "preferences": "Prefer variety."}
    url = f"/profile/searches/{scope['saved']}/recommendations"
    page = client.post(url, data=fields)
    assert page.status_code == (429 if status == 429 else 503)
    assert message in page.data and b"Prefer variety." in page.data
    assert b'<article class="history-card' in page.data and b'role="alert"' in page.data
    assert b"private provider details" not in page.data
    assert client.post(url, data=fields).status_code == 409
    assert len(calls) == 1 and count_generations() == 0


def test_empty_candidates_and_invalid_preferences_use_no_ai(scope, monkeypatch):
    forbid_ai(monkeypatch)
    client = client_for()
    fields = form_data(client, scope["saved"], date_from="2010-01-01", date_to="2011-01-01")
    page = client.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields)
    assert page.status_code == 200 and b"No eligible saved concerts" in page.data
    fields = {**form_data(client, scope["saved"]), "preferences": "x" * 1001}
    page = client.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields)
    assert page.status_code == 400 and b"at most 1,000" in page.data
    assert b'<article class="history-card' in page.data and count_generations() == 0


def test_storage_failure_shows_verified_cards_without_claiming_saved_history(scope, monkeypatch):
    calls = install_ai(monkeypatch)
    client = client_for()
    fields = form_data(client, scope["saved"])
    def fail(session):
        raise OperationalError("private SQL", {}, Exception("private details"))
    monkeypatch.setattr("services.intelligence.Session.commit", fail)
    page = client.post(f"/profile/searches/{scope['saved']}/recommendations", data=fields)
    assert page.status_code == 503 and b"saving could not be confirmed" in page.data
    assert page.data.count(b'class="recommendation-card"') == 3
    assert b'<article class="history-card' in page.data and b"private details" not in page.data
    assert len(calls) == 1 and count_generations() == 0


def test_history_is_private_paginated_and_can_filter_by_saved_search(scope, monkeypatch):
    forbid_ai(monkeypatch)
    for index in range(13):
        stage_history(scope, preferences=f"Owner preference {index}")
    foreign = stage_history(scope, foreign=True, preferences="Other user's secret preference")
    client = client_for()
    first = client.get("/profile/recommendations")
    assert first.status_code == 200 and first.data.count(b"recommendation-history-card") == 12
    assert b"Other user" not in first.data and b"Page 1 of 2" in first.data
    second = client.get("/profile/recommendations?page=2")
    assert second.status_code == 200 and second.data.count(b"recommendation-history-card") == 1
    filtered = client.get("/profile/recommendations", query_string={"search_id": scope["saved"]})
    assert filtered.status_code == 200 and b"Show all shortlists" in filtered.data
    assert "no-store" in filtered.headers["Cache-Control"]
    assert client.get(f"/profile/recommendations/{foreign.id}").status_code == 404
    assert client.get("/profile/recommendations", query_string={"search_id": scope["other_saved"]}).status_code == 404


@pytest.mark.parametrize("query", [{"page": "bad"}, {"page": "0"}, {"page": "2"},
                                  {"search_id": "bad"}, {"search_id": "-1"}])
def test_history_rejects_invalid_controls(scope, monkeypatch, query):
    forbid_ai(monkeypatch)
    assert client_for().get("/profile/recommendations", query_string=query).status_code == 400


def test_history_handles_empty_state_and_past_source_dates_without_regeneration(scope, monkeypatch):
    forbid_ai(monkeypatch)
    client = client_for()
    assert b"No shortlists saved yet" in client.get("/profile/recommendations").data
    generation = stage_history(scope)
    context = copy.deepcopy(generation.source_context)
    context["as_of_date"] = "2020-01-01"
    for candidate in context["candidates"]:
        candidate["facts"]["event_date"] = "2020-02-01"
    generation.source_context = context
    db.session.commit()
    page = client.get(f"/profile/recommendations/{generation.id}")
    assert page.status_code == 200 and b"Feb 01, 2020" in page.data
    assert b"Dates and availability may have changed" in page.data


def test_stored_text_is_escaped_and_links_come_only_from_safe_sources(scope, monkeypatch):
    forbid_ai(monkeypatch)
    generation = stage_history(scope, preferences='<script>alert("preferences")</script>')
    context = copy.deepcopy(generation.source_context)
    context["candidates"][0]["facts"]["title"] = '<img src=x onerror="alert(1)">'
    context["candidates"][0]["source_url"] = "javascript:alert(1)"
    generation.source_context = context
    output = copy.deepcopy(generation.output)
    output["recommendations"][0]["explanation"] = '<script>alert("explanation")</script>'
    generation.output = output
    db.session.commit()
    page = client_for().get(f"/profile/recommendations/{generation.id}")
    assert page.status_code == 200 and b"&lt;script&gt;" in page.data and b"&lt;img" in page.data
    assert b"<script>alert" not in page.data and b"<img src=x" not in page.data
    assert b"javascript:alert" not in page.data


def test_corrupt_stored_output_is_rejected_before_display(scope, monkeypatch):
    forbid_ai(monkeypatch)
    generation = stage_history(scope)
    generation.output = {"recommendations": [{"source_ref": "C99"}], "limitations": []}
    db.session.commit()
    page = client_for().get(f"/profile/recommendations/{generation.id}")
    assert page.status_code == 503 and b"could not be verified" in page.data
    assert b"C99" not in page.data


def test_older_ten_candidate_history_remains_readable_and_keeps_original_scope_label(scope, monkeypatch):
    forbid_ai(monkeypatch)
    generation = stage_history(scope)
    context = copy.deepcopy(generation.source_context)
    context.update(candidates=context["candidates"][:10], candidate_limit=10, considered_count=10, truncated=True)
    generation.source_context = context
    db.session.commit()
    page = client_for().get(f"/profile/recommendations/{generation.id}")
    assert page.status_code == 200
    assert b"Compared 10 of 17" in page.data and b"only the first 10" in page.data
    assert b"only the first 30" not in page.data


def test_library_generation_filters_are_separate_from_library_search_and_restored_on_return(scope, monkeypatch):
    calls = install_ai(monkeypatch)
    client = client_for()
    fields = form_data(client, scope["saved"], q="Hozier", location="Los Angeles",
                       date_from="2026-11-18", date_to="2026-11-20", sort="date_desc")
    result = client.post("/profile/recommendations?q=Private&sort=recent&page=1", data=fields)
    assert result.status_code == 303 and len(calls) == 1
    assert [item["facts"]["event_date"] for item in calls[0]["candidates"]] == ["2026-11-20", "2026-11-19", "2026-11-18"]
    stored = db.session.scalar(db.select(RecommendationGeneration))
    assert stored.retrieval_options["q"] == "Hozier" and stored.retrieval_options["sort"] == "date_desc"
    detail = client.get(result.headers["Location"]).get_data(as_text=True)
    return_url = html.unescape(re.search(r'<a href="([^"]+)">&larr; Back to My library', detail).group(1))
    assert return_url.startswith("/profile?") and "rec_q=Hozier" in return_url
    returned = client.get(return_url).get_data(as_text=True)
    assert f'<option value="{scope["saved"]}" selected>' in returned
    assert 'name="q" maxlength="160" value="Hozier"' in returned


def test_library_generation_requires_an_owned_selection_and_replay_cannot_switch_sources(scope, monkeypatch):
    calls = install_ai(monkeypatch)
    client = client_for()
    fields = form_data(client, scope["saved"])
    for selection, status in (("", 400), ("bad", 400), (str(scope["other_saved"]), 404)):
        result = client.post("/profile/recommendations", data={**fields, "saved_search_id": selection})
        assert result.status_code == status
    assert not calls
    assert client.post("/profile/recommendations", data=fields).status_code == 303
    assert client.post("/profile/recommendations", data={**fields, "saved_search_id": scope["second"]}).status_code == 409
    assert len(calls) == 1


def test_recommendation_sources_are_private_and_independent_of_library_result_pagination(scope, monkeypatch):
    forbid_ai(monkeypatch)
    foreign = db.session.get(SavedSearch, scope["other_saved"])
    foreign.title = "Other listener's private source"
    for index in range(13):
        db.session.add(SavedSearch(user_id=scope["owner"], title=f"Additional saved source {index}",
            query_key=f"additional-{index}", source="test", source_url="/", filters={}, snapshots={}))
    db.session.commit()
    page = client_for().get("/profile?page=2")
    assert page.status_code == 200
    form = re.search(r'<form[^>]+data-recommendation-form[^>]*>(.*?)</form>', page.get_data(as_text=True), re.S).group(1)
    select = re.search(r'<select id="recommendation-source".*?</select>', form, re.S).group(0)
    assert select.count("<option value=") == 16  # 15 own searches plus the placeholder.
    assert "Other listener" not in select


def test_empty_library_disables_recommendation_submission(scope, monkeypatch):
    forbid_ai(monkeypatch)
    client = client_for("new-listener-with-empty-library")
    page = client.get("/profile")
    assert page.status_code == 200 and b"Your library is empty" in page.data
    assert b"data-recommendation-submit disabled" in page.data
