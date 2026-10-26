"""Rebuild current A4 evaluation, screenshots and PDF.

Real George Clanton provider pages are copied into an isolated preview database.
Synthetic relevance fixtures use a second isolated app. No demonstration accounts
or fixture data are written to MariaDB, and browser captures cannot call APIs.
"""
import copy
import hashlib
import html
import json
import re
import sys
import textwrap
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from app import create_app
from models import CachedResult, SavedSearch, User, db, utcnow
from services.persistence import query_key
from services.retrieval import parse_options, retrieve_saved
from scripts.a4_evaluation import evaluate, seed_private_corpus
from playwright.sync_api import sync_playwright
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.serving import make_server

OUTPUT = ROOT / "docs" / "screenshots" / "a4"


def label_preview(app, notice):
    @app.after_request
    def label(response):
        if response.mimetype == "text/html":
            response.set_data(response.get_data().replace(b"<main>",
                ('<main><div class="auth-message">' + html.escape(notice) + '</div>').encode(), 1))
        return response


@contextmanager
def preview_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def signed_client(app, subject, name):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = {"sub": subject, "name": name}
        session["logout_csrf"] = "a4-documentation-only"
    return client


def cache_images(evidence):
    images = {}
    directory = ROOT / "instance" / "a4-images"
    directory.mkdir(parents=True, exist_ok=True)
    urls = {event.get("image") for page in evidence["pages"].values() for event in page["events"] if event.get("image")}
    for url in urls:
        destination = directory / (hashlib.sha256(url.encode()).hexdigest() + ".img")
        try:
            if not destination.exists():
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                if not response.headers.get("Content-Type", "").startswith("image/"):
                    continue
                destination.write_bytes(response.content)
            images[url] = destination
        except requests.RequestException:
            print("An optional provider image was unavailable; using Encore's image fallback.")
    return images


def browser_context(browser, origin, client, images=None):
    images = images or {}
    context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
    def route(request):
        url = request.request.url
        if url.startswith(origin):
            request.continue_()
        elif url in images:
            request.fulfill(path=str(images[url]), content_type="image/jpeg")
        else:
            request.abort()
    context.route("**/*", route)
    context.add_cookies([{"name": "session", "value": client.get_cookie("session").value, "url": origin}])
    return context


def capture():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    evidence_path = ROOT / "docs" / "george-clanton-acquisition.json"
    if not evidence_path.exists():
        raise RuntimeError("Run scripts/acquire_a4_examples.py first to acquire real George Clanton concerts.")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    images = cache_images(evidence)
    app = create_app("testing")
    label_preview(app, f"A4 preview: demo account with real JamBase results collected {evidence['capture_date_local']}.")
    filters = {"artist": "George Clanton", "venue": "", "location": ""}
    with app.app_context():
        db.create_all()
        db.session.add(User(google_sub="a4-real-preview", title="A4 preview listener", source="google", source_url="https://accounts.google.com"))
        for number, page in evidence["pages"].items():
            cards = copy.deepcopy(page["events"])
            for card in cards:
                if card.get("image") not in images:
                    card["image"] = ""
            db.session.add(CachedResult(cache_key=query_key(filters, "jambase", int(number)),
                title="George Clanton", description="Actual acquired JamBase page", source="jambase",
                source_url="https://www.jambase.com/band/george-clanton", filters=filters, page=int(number),
                events=cards, total=page["total"], has_more=page["has_more"],
                retrieved_at=datetime.fromisoformat(page["retrieved_at"]), expires_at=utcnow() + timedelta(hours=1)))
        db.session.commit()
    client = signed_client(app, "a4-real-preview", "A4 preview listener")
    today = evidence["capture_date_local"]
    with patch("services.api_service.ApiService.get", side_effect=AssertionError("Screenshot preview attempted a concert API call")):
        assert client.get("/", query_string={**filters, "source": "jambase"}).status_code == 200
        for number in evidence["pages"]:
            if number != "1":
                assert client.get("/api/events", query_string={**filters, "source": "jambase", "page": number}).status_code == 200
        with app.app_context():
            saved_id = db.session.scalar(db.select(SavedSearch.id))
        assert client.post(f"/profile/searches/{saved_id}/edit", data={"csrf_token": "a4-documentation-only",
            "title": "George Clanton — upcoming concerts", "description": "Compare cities, venues, and dates for the upcoming shows."}).status_code == 302
    with app.app_context():
        found, number = [], 1
        while True:
            result = retrieve_saved(parse_options({"q": "George Clanton", "page": str(number)}), saved_id)
            found.extend(row.external_id for row in result.items)
            if not result.has_next:
                break
            number += 1
    expected = {event["id"]: event for page in evidence["pages"].values() for event in page["events"]}
    real_observation = {"query": "George Clanton", "acquired_artist_query_records": len(expected),
        "saved_concert_keyword_matches": len(found),
        "missed_acquired_records": [{"id": key, "title": event["name"], "artist": event.get("artist"),
            "venue": event["venue"], "supporting_artists": event.get("supporting_artists", [])}
            for key, event in expected.items() if key not in set(found)],
        "note": "Artist-query acquisitions can contain a searched performer only in the supporting lineup, which is not a searchable field."}
    upcoming = [event for page in evidence["pages"].values() for event in page["events"] if event.get("date_iso", "") >= today]
    selected = next((event for event in upcoming if event.get("location", "").startswith("Los Angeles")), upcoming[0])
    city = selected["location"].split(",")[0]
    saved_path = f"/profile/searches/{saved_id}"
    manifest = {"captured_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
                "real_data": {key: evidence[key] for key in ("artist", "provider", "capture_date_local", "upcoming_loaded", "pages_loaded", "corroboration_url")},
                "screenshots": []}
    with patch("services.api_service.ApiService.get", side_effect=AssertionError("Unexpected provider request")):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            with preview_server(app) as origin:
                context = browser_context(browser, origin, client, images)
                page, errors = context.new_page(), []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def visit(path):
                    response = page.goto(origin + path)
                    assert response.status == 200, (path, response.status)
                    page.locator(".site-header").wait_for()
                def shot(name, caption, selector=None, full=False, dataset="Real George Clanton JamBase results"):
                    if selector:
                        page.locator(selector).first.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 110)")
                    page.screenshot(path=str(OUTPUT / name), full_page=full)
                    manifest["screenshots"].append({"file": name, "caption": caption, "dataset": dataset,
                                                     "path": page.url.removeprefix(origin)})
                visit("/")
                page.get_by_label("Artist name", exact=True).fill("George Clanton")
                page.get_by_label("Provider", exact=True).select_option("jambase")
                page.get_by_label("From date", exact=True).fill(today)
                page.get_by_label("Sort by", exact=True).select_option("date_asc")
                page.get_by_role("button", name="Find concerts", exact=False).click()
                assert "George Clanton" in page.locator(".concert-card").first.inner_text()
                shot("george-clanton-upcoming.png", "Discover: George Clanton's upcoming concerts", ".results-group-heading")
                visit("/profile")
                assert page.get_by_text("Enter a keyword or use filters", exact=False).count() == 1
                shot("search-form.png", "My library: dedicated GET search form and initial guidance", ".catalog-form")
                page.get_by_label("Search your library", exact=True).fill("George Clanton")
                page.get_by_role("button", name="Search library", exact=True).click()
                assert page.locator(".history-card").count() == 1
                shot("library.png", "Private library keyword search finds the saved George Clanton search", ".library-toolbar")
                page.get_by_role("link", name="View saved results", exact=False).click()
                assert page.locator(".concert-card").count() == 12
                page.get_by_label("Search these saved concerts", exact=True).fill("George Clanton")
                page.get_by_label("From date", exact=True).fill(today)
                page.get_by_label("Sort by", exact=True).select_option("date_asc")
                page.get_by_role("button", name="Apply filters", exact=True).click()
                shot("search-results.png", "Search stored concerts: George Clanton, upcoming dates, earliest first", ".catalog-results-heading")
                page.get_by_role("link", name="Next", exact=False).click()
                assert "page=2" in page.url and f"date_from={today}" in page.url and "q=George+Clanton" in page.url
                page.locator(".library-pagination").scroll_into_view_if_needed()
                shot("pagination.png", "Second result page preserves keyword, date filter, and sorting")
                visit(saved_path + f"?q=George+Clanton&source=jambase&location={city.replace(' ', '+')}&date_from={today}&date_to={selected['date_iso']}&sort=date_asc")
                assert page.locator(".concert-card").count() >= 1
                shot("filtered-results.png", f"Provider, city ({city}) and date filters combined with a keyword", ".catalog-form")
                visit(saved_path + f"?q=George+Clanton&date_from={today}&sort=date_desc")
                shot("sorted-results.png", "Alternate ordering: latest George Clanton concert dates first", ".catalog-results-heading")
                visit(saved_path + "?q=zzzx-no-concert")
                assert page.get_by_text("No matching records were found.", exact=False).count() == 1
                shot("no-results.png", "Completed zero-result search with refinement guidance", ".catalog-results-heading")
                visit("/profile/search-activity")
                assert page.locator(".activity-list article").count() >= 5
                shot("search-activity.png", "Persisted private telemetry includes a zero-result search", ".activity-list")
                visit("/profile")
                page.get_by_text("Edit title & notes", exact=True).click()
                page.locator(".history-card").screenshot(path=str(OUTPUT / "edit-search.png"))
                manifest["screenshots"].append({"file": "edit-search.png", "caption": "Existing CRUD: saved title, personal notes and delete controls", "dataset": "Real concert results; demonstration title/notes", "path": "/profile"})
                page.set_viewport_size({"width": 390, "height": 844})
                for path in ("/", "/profile", saved_path, "/profile/search-activity"):
                    visit(path)
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
                visit("/profile?q=George+Clanton")
                shot("mobile-search.png", "Responsive private library search", full=True)
                assert not errors, errors
                context.close()

            fixture_app = create_app("testing")
            label_preview(fixture_app, "A4 evaluation: synthetic concert fixtures in an isolated database.")
            with fixture_app.app_context():
                db.create_all()
                scope = seed_private_corpus()
                fixture_client = signed_client(fixture_app, scope["owner_sub"], "Evaluation listener")
                evaluation = evaluate(scope["owner_id"], scope["saved_id"], fixture_client)
                evaluation["real_george_clanton_observation"] = real_observation
            with preview_server(fixture_app) as fixture_origin:
                context = browser_context(browser, fixture_origin, fixture_client)
                page = context.new_page()
                assert page.goto(fixture_origin + f"/profile/searches/{scope['saved_id']}?q=Hozier&sort=relevance").status == 200
                assert page.locator(".concert-card").first.get_attribute("data-event-id") == "h01"
                page.locator(".catalog-results-heading").evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 110)")
                page.screenshot(path=str(OUTPUT / "ranked-results.png"))
                manifest["screenshots"].append({"file": "ranked-results.png", "caption": "Controlled relevance evaluation: exact performer matches outrank a tribute title", "dataset": "Synthetic evaluation fixtures; not real event listings", "path": f"/profile/searches/{scope['saved_id']}?q=Hozier&sort=relevance"})
                context.close()
            browser.close()
    (ROOT / "docs" / "a4-evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    (ROOT / "docs" / "a4-screenshot-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Captured {len(manifest['screenshots'])} current screenshots; private evaluation and browser checks passed.")
    return manifest


def build_pdf(manifest):
    """Include the complete current README and full-resolution screenshot evidence."""
    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 13
    styles["BodyText"].spaceBefore, styles["BodyText"].spaceAfter = 0, 7
    styles["Code"].fontSize, styles["Code"].leading = 7, 9
    story = []
    def inline(value):
        value = html.escape(value)
        value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
        value = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', value)
        return re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", value)
    def paragraph(value, style="BodyText"):
        story.append(Paragraph(inline(value), styles[style]))
    paragraph("Encore - Assignment 4", "Title")
    paragraph("Hardik Katbamna | IT 401 | Information Retrieval", "Heading2")
    paragraph("Generated " + datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%B %d, %Y %I:%M %p %Z"))
    paragraph(f"Real screenshot search: George Clanton. JamBase supplied {manifest['real_data']['upcoming_loaded']} upcoming concerts on {manifest['real_data']['capture_date_local']}. Accounts and notes in screenshots are demonstrations in an isolated preview; no demo account or synthetic concert was inserted into MariaDB.")
    paragraph("The relevance evaluation separately uses 18 synthetic fixtures in a private saved search and checks the current SQLAlchemy services and rendered HTTP order. Its screenshot is explicitly labeled. Other screenshots use actual acquired George Clanton concert facts.")
    verification = json.loads((ROOT / "docs" / "a4-database-verification.json").read_text(encoding="utf-8"))
    paragraph(f"Validation: {verification['automated_tests_passed']} automated tests passed; current private-library and saved-result HTTP/database checks passed against MariaDB. Verification time (UTC): {verification['verified_at']}. A test telemetry insert was rolled back.")
    paragraph("The following pages reproduce the current project documentation, followed by individually captioned screenshot evidence.")
    story.append(PageBreak())
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            block = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.extend(textwrap.wrap(lines[index], width=100, replace_whitespace=False) or [""])
                index += 1
            story.append(Preformatted("\n".join(block), styles["Code"]))
            story.append(Spacer(1, 8))
        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                if not re.match(r"^\|[\s|:-]+\|$", lines[index]):
                    rows.append([Paragraph(inline(cell.strip()), styles["BodyText"]) for cell in lines[index].strip("|").split("|")])
                index += 1
            table = Table(rows, colWidths=[510 / len(rows[0])] * len(rows[0]), repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9ef70")),
                                      ("GRID", (0, 0), (-1, -1), .4, colors.lightgrey),
                                      ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            story.extend([table, Spacer(1, 10)])
            continue
        elif line.startswith("![") or not line.strip():
            pass
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            paragraph(line.lstrip("# "), "Heading1" if level < 3 else "Heading2")
        elif line.startswith("- "):
            paragraph("• " + line[2:])
        else:
            paragraph(line)
        index += 1
    for screenshot in manifest["screenshots"]:
        story.append(PageBreak())
        paragraph(screenshot["caption"], "Heading1")
        paragraph(screenshot["dataset"])
        paragraph("Interface path: " + screenshot["path"])
        path = OUTPUT / screenshot["file"]
        width, height = ImageReader(str(path)).getSize()
        ratio = min(510 / width, 590 / height)
        story.append(Image(str(path), width=width * ratio, height=height * ratio))
    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(45, 26, "Encore | Hardik Katbamna | IT 401 A4")
        canvas.drawRightString(565, 26, str(document.page))
    destination = ROOT / "docs" / "Encore-A4.pdf"
    SimpleDocTemplate(str(destination), leftMargin=45, rightMargin=45, topMargin=40, bottomMargin=40).build(
        story, onFirstPage=footer, onLaterPages=footer)
    print("Created", destination)


if __name__ == "__main__":
    build_pdf(capture())
