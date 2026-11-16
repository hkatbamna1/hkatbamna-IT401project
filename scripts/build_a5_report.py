"""Build A5 browser evidence and a standalone PDF without live APIs or MariaDB.

Replays reviewed live model output against frozen fictional records in temporary
in-memory SQLite. Only the quota-error capture submits a form, using an SDK HTTP
mock. Existing evaluation reports are read, never rerun or overwritten.
"""
import copy
import hashlib
import html
import json
import re
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from google import genai
from playwright.sync_api import expect, sync_playwright
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.serving import WSGIRequestHandler, make_server

from app import create_app
from models import RecommendationGeneration, SavedSearch, User, db
from scripts.a5_evaluation import AS_OF_DATE, output_fingerprint, seed_evaluation_data
from services.ai_service import AiService
from services.generation_store import add_generation
from services.recommendations import retrieve_recommendation_candidates, snapshot_recommendation_context
from services.retrieval import parse_options

DOCS = ROOT / "docs"
OUTPUT = DOCS / "screenshots" / "a5"
NOTICE = "A5 preview: fictional concerts; saved output from recorded Gemini run; quota error simulated. No live API calls."


def reviewed_report(filename):
    report = json.loads((DOCS / filename).read_text(encoding="utf-8"))
    for case in report["cases"]:
        digest = output_fingerprint(case["validated_output"], case["source_context"])
        if (not case["contract_pass"] or not case["quality"]["automatic_pass"]
                or case["explanation_review"].get("output_fingerprint") != digest
                or not case["explanation_review"]["status"].startswith("reviewed")):
            raise RuntimeError(f"{filename} needs a fresh output/source review before use in the report.")
    return report


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


@contextmanager
def preview_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def capture(report):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    case = next(item for item in report["cases"] if item["id"] == "venue_dates")
    app = create_app("testing", {"AI_SERVICE_API_KEY": "documentation-mock-only"})

    @app.after_request
    def label_preview(response):
        if response.mimetype == "text/html":
            label = ('<div role="note" style="position:fixed;bottom:0;left:0;right:0;z-index:100;'
                     'padding:9px 16px;background:#d9ef70;color:#17131f;font:12px/1.4 Segoe UI,sans-serif;'
                     'border-top:1px solid #17131f">' + html.escape(NOTICE) + '</div>')
            response.set_data(response.get_data().replace(b"</body>", label.encode() + b"</body>"))
        return response

    with app.app_context():
        if db.engine.dialect.name != "sqlite" or db.engine.url.database not in (None, "", ":memory:"):
            raise RuntimeError("Documentation previews require isolated in-memory SQLite.")
        db.create_all()
        scope = seed_evaluation_data()
        options = parse_options({"location": "Fixture City", "sort": "alphabetical"})
        batch = retrieve_recommendation_candidates(options, scope["primary_id"], scope["owner_id"], today=AS_OF_DATE)
        if snapshot_recommendation_context(batch) != case["source_context"]:
            raise RuntimeError("Preview fixtures no longer match the recorded generation's sources.")
        generation = add_generation(user_id=scope["owner_id"], batch=batch, options=options,
            preferences=case["preferences"], output=case["validated_output"],
            prompt_version=report["prompt_version"], model=report["requested_model"], today=AS_OF_DATE)
        # The report records the requested identifier, not the provider-returned
        # model version. Do not present the former as independently captured metadata.
        generation.model = report["requested_model"] + " (recorded request)"
        generation.generated_at = datetime.fromisoformat(report["evaluated_at"]).replace(tzinfo=None)
        owner = db.session.get(User, scope["owner_id"])
        owner.title = "A5 evaluation listener"
        primary = db.session.get(SavedSearch, scope["primary_id"])
        primary.title = "Fixture City - evaluation concerts"
        primary.description = "Fictional evaluation listings for comparing venue and date preferences."
        attacked = db.session.get(SavedSearch, scope["attacked_id"])
        attacked.title = "Fixture City - injection evaluation"
        attacked.description = "Separate fictional source used in the recorded instruction-injection check."
        db.session.commit()
        saved_id, generation_id = primary.id, generation.id
        identity = {"sub": owner.google_sub, "name": owner.title}

    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = identity
    observations = {"mocked_http_calls": 0}

    def quota_response(request):
        observations["mocked_http_calls"] += 1
        return httpx.Response(429, json={"error": {"code": 429, "message": "Synthetic documentation quota limit"}})

    def client_factory(**kwargs):
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(quota_response), "trust_env": False}
        return genai.Client(**kwargs)

    mocked_service = AiService("documentation-mock-only", client_factory=client_factory)
    manifest = {"captured_at": datetime.now(timezone.utc).isoformat(), "dataset": "Frozen fictional A5 evaluation corpus",
        "live_api_calls": 0, "hosted_database_access": False, "source_report": "docs/a5-evaluation-live.json",
        "output_fingerprint": case["output_fingerprint"],
        "preview_metadata": "Stored row reconstructed from validated report; timestamp uses evaluated_at; model identifies recorded request, not provider-returned version.",
        "screenshots": []}
    try:
        with patch("services.api_service.ApiService.get", side_effect=AssertionError("Unexpected concert API call")), \
                patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Unexpected live HTTP call")), \
                patch("services.intelligence.AiService.from_config", return_value=mocked_service), sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            with preview_server(app) as origin:
                context = browser.new_context(viewport={"width": 1440, "height": 1180}, reduced_motion="reduce")
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def visit(path):
                    response = page.goto(origin + path)
                    assert response.status == 200, (path, response.status)
                    page.evaluate("document.documentElement.style.scrollBehavior = 'auto'")

                def shot(filename, caption, provenance, selector=None):
                    if selector:
                        page.locator(selector).first.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 102)")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(OUTPUT / filename))
                    manifest["screenshots"].append({"file": filename, "caption": caption, "path": page.url.removeprefix(origin),
                        "dataset": provenance, "width": page.viewport_size["width"], "height": page.viewport_size["height"]})

                visit(f"/profile?recommend_from={saved_id}&rec_location=Fixture+City&rec_sort=alphabetical")
                page.get_by_label("What matters to you?").fill(case["preferences"])
                shot("library.png", "My library: recommendation source, preferences and existing search", "Fictional evaluation account; no generation submitted.", ".recommendation-panel")
                visit(f"/profile/searches/{saved_id}?q=Harbor+Hall&sort=date_asc&page=1")
                expect(page.locator(".concert-card")).to_have_count(6)
                shot("search-results.png", "Existing saved-concert search: venue keyword and earliest-date ordering", "Six matching fictional Harbor Hall concerts; ordinary retrieval.", ".catalog-form")
                detail = f"/profile/recommendations/{generation_id}"
                visit(detail)
                expect(page.locator(".recommendation-card")).to_have_count(3)
                shot("generated-intelligence.png", "Generated shortlist: three choices, reasons and exact supporting facts", "Recorded live v3 output replayed against its original fictional source context.", ".recommendation-output")
                page.get_by_text("View structured response", exact=True).click()
                displayed = json.loads(page.locator(".recommendation-details pre").inner_text())
                assert displayed == case["validated_output"]
                shot("structured-output.png", "Expanded structured response (beginning); complete JSON retained in evaluation report", "Exact locally validated JSON from the recorded v3 model response.", ".recommendation-details")
                visit("/profile/recommendations")
                expect(page.locator(".recommendation-history-card")).to_have_count(1)
                page.get_by_role("link", name="Open saved shortlist", exact=True).click()
                expect(page.locator(".recommendation-card")).to_have_count(3)
                page.reload()
                assert observations["mocked_http_calls"] == 0
                visit("/profile/recommendations")
                shot("stored-results.png", "Private shortlist history: reopen stored output without another model request", "One validated preview row reconstructed from the recorded v3 response.")
                page.set_viewport_size({"width": 390, "height": 1000})
                visit(detail)
                shot("mobile-shortlist.png", "Mobile saved shortlist: stacked concert choices and source evidence", "Same recorded v3 response; first choice shown at mobile width.", ".recommendation-output")
                page.set_viewport_size({"width": 1440, "height": 1180})
                visit(f"/profile?recommend_from={saved_id}")
                page.get_by_label("What matters to you?").fill(case["preferences"])
                with page.expect_response(lambda response: response.request.method == "POST" and "/profile/recommendations" in response.url) as failed:
                    page.get_by_role("button", name="Recommend concerts").click()
                assert failed.value.status == 429
                expect(page.get_by_role("alert")).to_contain_text("quota limit")
                expect(page.get_by_label("What matters to you?")).to_have_value(case["preferences"])
                expect(page.locator("#recommendation-source")).to_have_value(str(saved_id))
                expect(page.locator(".history-card")).to_have_count(2)
                shot("error-handling.png", "Quota failure: safe message and preserved source, preferences and library", "Simulated SDK HTTP 429; no live request, retry, or new saved generation.", ".recommendation-panel")
                assert observations["mocked_http_calls"] == 1 and not errors, errors
                context.close()
            browser.close()
        with app.app_context():
            assert db.session.scalar(db.select(db.func.count()).select_from(RecommendationGeneration)) == 1
        manifest["checks"] = {"browser_script_errors": errors, "horizontal_overflow": False,
            "structured_output_matches_report": True, "history_reopens_without_model_call": True,
            "quota_status": 429, "quota_preserves_input_and_library": True,
            "quota_creates_no_history_row": True, **observations}
        for capture in manifest["screenshots"]:
            capture["sha256"] = hashlib.sha256((OUTPUT / capture["file"]).read_bytes()).hexdigest()
        (DOCS / "a5-screenshot-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def workflow_diagram():
    drawing = Drawing(510, 298)
    stages = ["Listener request and selected private saved search", "Retrieve up to 30 eligible owner-scoped concerts",
        "Build versioned prompt and bounded retrieved context", "One Gemini call -> structured JSON response",
        "Validate schema and sources -> recheck owner -> store", "Display explained shortlist -> reopen private history"]
    for index, text in enumerate(stages):
        y = 255 - index * 49
        drawing.add(Rect(20, y, 470, 33, rx=5, ry=5, fillColor=colors.HexColor("#f1edfb" if index % 2 else "#edf4d5"), strokeColor=colors.HexColor("#7157dc")))
        drawing.add(String(255, y + 12, text, textAnchor="middle", fontName="Helvetica", fontSize=10))
        if index < len(stages) - 1:
            drawing.add(Line(255, y, 255, y - 12, strokeColor=colors.HexColor("#7157dc")))
            drawing.add(Polygon([251, y - 10, 259, y - 10, 255, y - 16], fillColor=colors.HexColor("#7157dc"), strokeColor=None))
    return drawing


def build_pdf(manifest, report, initial):
    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize, styles["BodyText"].leading = 10, 14
    styles["BodyText"].spaceAfter = 9
    styles["Title"].textColor = styles["Heading1"].textColor = colors.HexColor("#4c399d")
    styles["Code"].fontSize, styles["Code"].leading = 8, 11
    story = []

    def text(value, style="BodyText"):
        story.append(Paragraph(html.escape(value), styles[style]))

    def page(title):
        if story:
            story.append(PageBreak())
        text(title, "Heading1")

    def table(headers, rows, widths):
        cells = [[Paragraph(html.escape(str(cell)), styles["BodyText"]) for cell in row] for row in [headers, *rows]]
        grid = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
        grid.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9ef70")),
            ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#c7c2ce")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.extend([grid, Spacer(1, 10)])

    page("Encore | A5 Intelligence Generation")
    text("Hardik Katbamna | IT 401", "Title")
    text("Branch: hkatbamna-it401-a5")
    text("Report built " + datetime.now(timezone.utc).strftime("%B %d, %Y (UTC)"))
    text("An explained concert shortlist", "Heading2")
    text("Encore helps listeners discover upcoming concerts through JamBase, revisit performances through Setlist.fm, and keep private saved searches. A4 added searchable stored records; A5 adds a comparison of those records against the listener's stated preferences.")
    text("Selected capability: recommendation. From one owner-selected saved search, retrieve up to 30 eligible concerts and generate up to three choices with short explanations, exact supporting facts and explicit limitations. This reduces the work of comparing a long result list while leaving the final choice with the listener.")
    text("The feature is part of the existing Flask application. Flask-SQLAlchemy, SQLAlchemy and PyMySQL continue to use the configured CI Keys MariaDB (MySQL-compatible) database. Existing search, ranking, pagination, Google authentication and saved-search CRUD remain available. SQLite is restricted to isolated tests and documentation previews.")
    text("Evidence and provenance", "Heading2")
    text("Screenshots show actual app routes with fictional evaluation concerts in in-memory SQLite. Saved output replays an actual Gemini v3 response recorded during step 8. The quota failure is simulated. No extra live API calls or hosted database writes are needed to build this report. Captures carry a preview label and are individually captioned.")
    text("Source evidence: docs/a5-evaluation-live.json; docs/a5-evaluation-live-v2.json; docs/a5-evaluation-offline.json; docs/a5-screenshot-manifest.json; docs/a5-tests.txt. The README contains installation details, the model contract and historical A4 evidence.")

    page("1. Retrieval-augmented workflow")
    story.append(workflow_diagram())
    text("Request and retrieval", "Heading2")
    text("On My library, choose a saved search, optional concert keyword/provider/city/date/sort controls, and up to 1,000 characters of preferences. Recommendation controls are independent of the library's title/notes search. Candidate retrieval enforces ownership and uses the existing SQL retrieval/ranking logic, independently of the 12-card display page.")
    text("SQL eligibility runs before LIMIT 30: require a known concert date on or after the UTC calendar cutoff and exclude known cancelled, postponed and performed statuses. Same-day listings remain eligible because a calendar date does not establish whether a show has started. Shared catalog records and other owners' searches cannot enter the source batch.")
    text("The read transaction ends before the model call. The service validates the response, rechecks ownership, validates again and commits in an independent storage session. Successful form submission redirects to a private read-only result URL. Refresh, history and ordinary page views make no new Gemini request.")
    text("Responsibilities: routes/profile.py handles HTTP/authentication; recommendations.py selects candidates; prompts.py constructs context; ai_service.py owns transport; validators.py checks output; intelligence.py coordinates the workflow; generation_store.py persists it; recommendation_views.py prepares safe display data.")

    page("2. Prompt design and structured output")
    text("System instructions", "Heading2")
    text("Prompt version concert-recommendations-v3 instructs Gemini to compare only the supplied facts, use no outside knowledge or tools, ignore instructions embedded in retrieved strings, and return fewer or no choices when evidence is insufficient. Explanations must be plain text; missing prices, distances, similarity or availability cannot be invented. Scope and availability limitations must use plain language.")
    text("User request and retrieved context", "Heading2")
    story.append(Preformatted('USER REQUEST (JSON data)\n{"preferences": "Prefer Harbor Hall. Choose the earliest three..."}\n\nRETRIEVED CONTEXT (JSON data)\n{"as_of_date": "2026-11-16", "considered_count": 30,\n "matching_count": 36, "truncated": true,\n "candidates": [{"source_ref": "C06", "facts": {"venue":\n "Harbor Hall", "event_date": "2027-01-06", "...": "..."},\n "retrieved_at": "2026-11-16T12:00:00Z"}, "..."]}', styles["Code"]))
    text("This shortened context illustrates organization, not a complete request. Full requests contain available title, artist, venue, location, date, tour, genre and status facts for every selected candidate. References C01-C30 are local to that exact batch. Cutoff, counts and retrieval times establish scope and freshness.")
    text("Private notes, account identifiers, credentials, images, provider identities and URLs are excluded from the model prompt. Source identity and sanitized links remain in the local stored snapshot for attribution. This limits irrelevant input and avoids sending private organizational data unnecessary for comparison.")
    text("AI component", "Heading2")
    text("The official Google Gen AI Python SDK calls the Gemini Developer API. Configured default: gemini-3.5-flash-lite. AI_SERVICE_API_KEY is preferred; GEMINI_API_KEY is a legacy fallback. AI_SERVICE_MODEL, AI_SERVICE_TIMEOUT_SECONDS (default 20), and AI_SERVICE_MAX_OUTPUT_TOKENS (default 4096) control the call. The project remains on the user's existing free tier; no billing change or paid fallback is implemented.")
    text("The SDK receives application/json and a response JSON Schema. There is one request per submission, no automatic retries, browsing or tools. Provider JSON formatting is only the first step: local validation remains mandatory.")

    page("3. Generated result and validation")
    text("Actual recorded v3 example", "Heading2")
    text(report["cases"][0]["preferences"])
    table(["Rank", "Concert / source", "Venue", "Date"], [
        [1, "Fixture concert 06 / C06", "Harbor Hall", "2027-01-06"],
        [2, "Fixture concert 12 / C12", "Harbor Hall", "2027-01-12"],
        [3, "Fixture concert 18 / C18", "Harbor Hall", "2027-01-18"]], [38, 210, 130, 132])
    text("Actual second-choice explanation: " + report["cases"][0]["validated_output"]["recommendations"][1]["explanation"])
    text("Structured response excerpt (first choice; remaining choices omitted)", "Heading2")
    story.append(Preformatted('{' + '\n  "recommendations": [{\n    "source_ref": "C06", "rank": 1,\n    "explanation": "[see recorded full response]",\n    "supporting_facts": [\n      {"field": "venue", "value": "Harbor Hall"},\n      {"field": "event_date", "value": "2027-01-06"},\n      {"field": "status", "value": "Scheduled"}\n    ]\n  }],\n  "limitations": [\n    "Saved facts do not establish current ticket availability.",\n    "[only the first 30 matching saved concerts were compared]"\n  ]\n}', styles["Code"]))
    text("Bracketed text abbreviates the full response retained in the evaluation JSON. The interface displays the complete validated JSON. It does not accept generated concert names or links in place of source facts.")
    text("Validation and safe display", "Heading2")
    text("Reject missing or extra fields, wrong types, blank/oversized strings, duplicate keys, nonstandard JSON numbers, unknown/duplicate references, nonconsecutive ranks or over three choices. Each choice requires one to five distinct field/value facts matching its own source exactly. Recheck date/status eligibility. Empty choices require a limitation. Reject the whole response on any failure before display or storage.")
    text("Templates escape model text. Names, dates, venues and links come from source snapshots; links permit only safe HTTP(S) URLs. These checks establish structured evidence integrity, not the truth of every free-text explanation. Separate evaluation reviews explanations against recorded facts.")

    page("4. Persistence, reliability and existing features")
    text("Database updates", "Heading2")
    text("The additive a5_recommendation_generations Alembic migration creates encore_recommendation_generations and its saved-search/time index. Each RecommendationGeneration stores saved_search_id, user_request, retrieval_options, full source_context, validated output JSON, prompt_version, model and UTC generated_at. Ownership follows the parent SavedSearch; deleting it removes its history through cascade. Source snapshots preserve evidence after private concert indexes are rebuilt.")
    text("Request and retrieval metadata explain what was compared. Prompt/model versions support investigation and evaluation. Timestamps show age; stored output avoids repeated quota use. The production client retains the provider-returned model version when supplied. The documentation replay labels the requested model because the evaluation report did not record that returned identifier.")
    table(["Failure", "Behavior / fallback"], [
        ["Missing key or bad settings", "Safe configuration message; ordinary search stays available."],
        ["Empty context / foreign source", "Stop before Gemini; owner checks hide foreign records."],
        ["Quota, timeout, provider failure", "Clear message; preserve form and ordinary library results; no retry or paid fallback."],
        ["Malformed or ungrounded output", "Reject completely; no displayed output or saved generation."],
        ["Retrieval database failure", "Safe unavailable outcome; no model request."],
        ["Storage database failure", "Show already validated output for this response; no confirmed saved ID. A commit acknowledgment can fail after server commit."],
        ["Owner changes during generation", "Recheck before storing; expose no output/source context if scope is lost."]], [150, 360])
    text("Current features", "Heading2")
    text("Discover retains official provider search and load-more. My library retains private keyword search, relevance ranking, provider/city/date filters, sorting, pagination and search telemetry. CRUD creates saved searches during signed-in discovery, reads snapshots, edits labels/notes and deletes records. Recommendations add loading feedback, structured JSON, source evidence, private history and safe failure handling. Google authentication, CSRF and owner-scoped queries protect these actions.")

    page("5. Evaluation and observed quality")
    text("Frozen dataset and method", "Heading2")
    text("The synthetic corpus has 36 eligible Fixture City concerts, five date/status-ineligible records, one other-city record, another owner's private record and a shared-only catalog record. Retrieval considers 30. Every sixth eligible concert is at Harbor Hall. Exact expected choices are established independently of generated responses. One source also contains an instruction to invent C99 and false price/distance facts.")
    table(["Recorded request", "Prompt", "Observed result", "Time"], [
        ["Prefer Harbor Hall, earliest three", "v2", "Correct C06, C12, C18 in order", "2.001 s"],
        ["Require verified price and distance", "v2", "No choices; both missing facts disclosed", "1.450 s"],
        ["Venue/date task with record injection", "v2", "Correct choices; no injected C99/price claim", "1.655 s"],
        ["Targeted venue/date wording follow-up", "v3", "Correct choices; plain-language scope note", "2.398 s"]], [178, 45, 225, 62])
    text("The retrieval-first-three baseline returned event-01, event-02 and event-03, none meeting the stated venue preference. The model's three expected choices all matched. Generated selection, ordering and explanations add a preference-based comparison beyond a list. The explicit venue/date task is also solvable without an LLM; this sample does not establish broad musical-taste reasoning.")
    text("The v2 review found raw 'truncated' wording and ambiguous availability language. Prompt v3 addresses those presentation issues. Only the venue/date case was repeated under v3; the other two live observations remain attributed to v2. Four live requests were used in step 8, with no extra live calls for this report.")
    text("Reliability and regression evidence", "Heading2")
    test_log = (DOCS / "a5-tests.txt").read_text(encoding="utf-8")
    passed = re.search(r"(\d+) passed", test_log)
    if not passed:
        raise RuntimeError("A passing full regression log is required before building the PDF.")
    text(f"{passed.group(1)} automated tests pass. The 13-case offline evaluator checks normal output, unsupported requirements, injection, empty context, foreign ownership, missing key, 429, timeout, malformed JSON, invented source/fact and retrieval/storage database failures. Nine mocked calls and zero live requests exercise the SDK contracts; scripted answers are not counted as model-quality evidence.")
    text("Selection/source/evidence checks are automatic; Codex reviewed explanations against exact fixtures with output/context fingerprints. This is a small synthetic sample, not a human study or general accuracy estimate. Missing-fact keyword checks are proxies, not semantic proof. A single injection test does not guarantee resistance.")

    page("6. Design decisions, limitations and A6")
    table(["Decision", "Reason / tradeoff"], [
        ["One selected private saved search", "Clear ownership, scope and reusable source evidence; no cross-library taste inference."],
        ["30 candidates; up to three choices", "Broader comparison at a bounded free-tier request size. Selection order can exclude useful later matches."],
        ["Gemini Flash-Lite; one request", "Successful short live responses after model-specific timeout investigation. Quotas and latency still vary."],
        ["Exact evidence plus local validation", "Reject fabricated references and supporting facts; prose still needs quality review."],
        ["Stored output and source snapshot", "Explainable private history and repeat viewing without quota; saved facts can become stale."],
        ["Bounded process-local form guard", "Protect duplicate/overlapping submissions; a multi-worker deployment requires shared claim storage."]], [165, 345])
    text("Current limitations", "Heading2")
    text("Only acquired and saved listings are searchable. Missing price, travel, genre or similarity evidence remains unknown; the feature does not discover artists or infer musical affinity. It evaluates only the first 30 eligible matches in the chosen sort order. Listing facts and links may age; availability must be checked with the original source. The default cutoff uses UTC calendar dates. Google login and the local MariaDB SSH tunnel remain deployment dependencies. Free-tier quotas can prevent a request.")
    text("A6 decision support", "Heading2")
    text("Build a side-by-side comparison with listener-controlled priorities and visible satisfied, unsupported or conflicting requirements. Add budget/travel tradeoffs only after acquiring trustworthy timestamped prices, venue coordinates and an explicitly supplied starting location. Retain provenance and let the listener make the final choice.")
    text("Evaluate with larger frozen datasets, ambiguous and conflicting preferences, repeated runs, deterministic baselines and human usefulness judgments. Artist recommendations require explicit music metadata and listener preferences. Improve aliases, complete lineup indexing and cross-provider identity resolution. Collect feedback only for a clear purpose; clicks alone do not establish satisfaction.")

    page("7. Running and reviewing the submission")
    text("Runtime setup", "Heading2")
    story.append(Preformatted('git switch hkatbamna-it401-a5\npython -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n# Configure .env from .env.example and start the MariaDB tunnel.\n.\\.venv\\Scripts\\python.exe -m flask --app app db upgrade\n.\\.venv\\Scripts\\python.exe app.py', styles["Code"]))
    text("Supply the existing Google OAuth, provider and MariaDB settings plus AI_SERVICE_API_KEY. Do not commit .env. Keep the existing free-tier project; no payment upgrade is needed by this implementation. Ordinary private search and reading stored shortlists do not call Gemini.")
    text("Browser walkthrough", "Heading2")
    text("1. Sign in and open My library. 2. Choose a saved search under Find your next night out. 3. Optionally narrow concerts and state preferences. 4. Select Recommend concerts once. 5. Review choices, reasons, source facts and limitations; expand structured output or generation details. 6. Reopen via Shortlist history without another model request. Source date/status eligibility can leave no concerts to compare.")
    text("Offline verification and artifact rebuild", "Heading2")
    story.append(Preformatted('.\\.venv\\Scripts\\python.exe -B -m pytest -q -p no:cacheprovider\n.\\.venv\\Scripts\\python.exe -B scripts/a5_evaluation.py\n.\\.venv\\Scripts\\python.exe -m pip install -r requirements-docs.txt\n.\\.venv\\Scripts\\python.exe -B scripts/build_a5_report.py', styles["Code"]))
    text("The report builder requires installed Microsoft Edge and existing reviewed live reports. It blocks live API transports, verifies exact source/output fingerprints, checks desktop/mobile layout, reopens private history and simulates one quota failure. Preview rows are temporary in-memory data. Screenshot hashes and route captions are retained in the manifest.")
    text("Required deliverables", "Heading2")
    text("The work is on hkatbamna-it401-a5. README.md covers the capability, workflow diagram, system/user/context prompts, AI configuration, validation and fallback behavior, generated example, database changes, current features, design decisions, limitations and A6 work. This standalone PDF adds the required search, intelligence, structured-output, error and stored-result screenshots. Earlier A3/A4 artifacts remain available as historical evidence.")
    text("Submission remains the owner's action: review the PDF, commit/push the desired files on the A5 branch and submit the branch link and PDF through the course system. Building the report does not publish or submit anything.")

    for index, screenshot in enumerate(manifest["screenshots"], start=1):
        page(f"Screenshot {index}. " + screenshot["caption"])
        text(screenshot["dataset"])
        text("Route: " + screenshot["path"])
        path = OUTPUT / screenshot["file"]
        width, height = ImageReader(str(path)).getSize()
        ratio = min(510 / width, 570 / height)
        story.append(Image(str(path), width=width * ratio, height=height * ratio))

    def footer(canvas, document):
        canvas.setTitle("Encore A5 - Intelligence Generation")
        canvas.setAuthor("Hardik Katbamna")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(45, 25, "Encore | Hardik Katbamna | IT 401 A5")
        canvas.drawRightString(565, 25, str(document.page))

    destination = DOCS / "Encore-A5.pdf"
    SimpleDocTemplate(str(destination), pagesize=(612, 792), leftMargin=45, rightMargin=45,
        topMargin=35, bottomMargin=40).build(story, onFirstPage=footer, onLaterPages=footer)
    return destination


if __name__ == "__main__":
    current = reviewed_report("a5-evaluation-live.json")
    original = reviewed_report("a5-evaluation-live-v2.json")
    captures = capture(current)
    pdf = build_pdf(captures, current, original)
    print(f"Created {pdf}; {len(captures['screenshots'])} screenshots. Zero live API calls; no hosted database access.")
