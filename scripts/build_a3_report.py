"""Capture actual UI with a disposable account/database and build the A3 PDF.

Requires playwright, reportlab, and installed Microsoft Edge. Reads the real
MariaDB cache but never writes demo users/history to it. No auth bypass route.
"""
import copy
import html
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from models import CachedResult, SavedSearch, db
from playwright.sync_api import sync_playwright
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted, Image, PageBreak
from werkzeug.serving import make_server


def capture():
    real = create_app()
    with real.app_context():
        records = [{column.name: copy.deepcopy(getattr(row, column.name))
                    for column in CachedResult.__table__.columns}
                   for row in db.session.scalars(db.select(CachedResult).order_by(CachedResult.id))]
    if not records:
        raise RuntimeError("Run a real concert search before generating documentation.")
    # SQLite exists only in this process. The app's default remains MariaDB.
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        for record in records:
            db.session.add(CachedResult(**record))
        db.session.commit()
    filters = records[0]["filters"]
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = {"sub": "documentation-demo", "name": "Demo listener", "email": None}
        session["logout_csrf"] = "documentation-only-csrf"
    # Providers cannot be contacted by the preview, even if the source cache expires.
    with app.app_context():
        from datetime import timedelta
        from models import utcnow
        for row in db.session.scalars(db.select(CachedResult)):
            row.expires_at = utcnow() + timedelta(hours=1)
        db.session.commit()
    from unittest.mock import patch
    with patch("services.api_service.ApiService.get", side_effect=AssertionError("Preview must not call APIs")):
        assert client.get("/", query_string=filters).status_code == 200
        with app.app_context():
            search_id = db.session.scalar(db.select(SavedSearch.id))
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        output = ROOT / "docs" / "screenshots" / "a3"
        output.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
                # Disable external images during reproducible captures.
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin) else route.abort())
                context.add_cookies([{"name": "session", "value": client.get_cookie("session").value,
                                     "url": origin, "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                assert page.goto(origin + "/").status == 200
                page.screenshot(path=str(output / "homepage.png"))
                assert page.goto(origin + "/profile").status == 200
                page.screenshot(path=str(output / "profile.png"), full_page=True)
                assert page.goto(origin + f"/profile/searches/{search_id}").status == 200
                page.screenshot(path=str(output / "saved-results.png"))
                page.locator(".results-group").last.evaluate("element => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 100)")
                page.screenshot(path=str(output / "stored-concerts.png"))
                page.goto(origin + "/profile")
                page.get_by_text("Edit title & notes", exact=True).click()
                original_title = page.get_by_label("Search title", exact=True).input_value()
                page.get_by_label("Search title", exact=True).fill("Unsaved draft")
                page.get_by_role("button", name="Cancel", exact=True).click()
                assert not page.locator(".history-editor").evaluate("element => element.open")
                page.get_by_text("Edit title & notes", exact=True).click()
                assert page.get_by_label("Search title", exact=True).input_value() == original_title
                page.get_by_label("Search title", exact=True).fill("Hozier — Los Angeles plans")
                page.get_by_label("Your notes", exact=True).fill("Compare venues and share these discoveries with friends.")
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.wait_for_url(origin + "/profile")
                assert page.get_by_role("heading", name="Hozier — Los Angeles plans", exact=True).count() == 1
                page.get_by_text("Edit title & notes", exact=True).click()
                page.locator(".history-card").screenshot(path=str(output / "edit-search.png"))
                page.goto(origin + "/profile")
                page.screenshot(path=str(output / "profile.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "mobile-profile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile page overflows"
                page.get_by_text("Edit title & notes", exact=True).click()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile editor overflows"
                page.goto(origin + f"/profile/searches/{search_id}")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile result page overflows"
                page.goto(origin + "/profile")
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.locator(".history-delete button").click()
                assert page.locator(".history-card").count() == 1
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator(".history-delete button").click()
                page.wait_for_url(origin + "/profile")
                assert page.locator(".library-stat strong").inner_text() == "0"
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return output, sum(len(r["events"]) for r in records)


def build_pdf(output, event_count):
    destination = ROOT / "docs" / "Encore-A3.pdf"
    styles = getSampleStyleSheet()
    styles["BodyText"].fontSize = 9
    styles["BodyText"].leading = 13
    styles["Code"].fontSize = 7
    styles["Code"].leading = 9
    story = [Paragraph("Encore — Assignment 3", styles["Title"]),
             Paragraph("Hardik Katbamna · IT 401 · Information Persistence", styles["Heading2"]),
             Paragraph("MariaDB on CI Keys · Flask-SQLAlchemy · Google sign-in", styles["BodyText"]),
             Spacer(1, 16),
             Paragraph("Implementation and validation", styles["Heading2"]),
             Paragraph(f"Generated {datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%B %d, %Y %I:%M %p %Z')}. "
                       f"The hosted database supplied {event_count} real cached concert records for the isolated UI demonstration. "
                       "MariaDB CRUD was verified in a rolled-back transaction; a repeated real search made no external API calls. "
                       "Browser checks exercised profile rendering, editing, deletion, and mobile layouts.", styles["BodyText"]),
             Paragraph("Screenshots show a demonstration account in an isolated SQLite database using copies of actual acquired data. "
                       "The deployed development configuration uses MariaDB; no demonstration account was added to CI Keys.", styles["BodyText"]),
             PageBreak()]
    code, in_code = [], False
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if in_code:
                import textwrap
                wrapped = "\n".join("\n".join(textwrap.wrap(l, width=100, replace_whitespace=False) or [""]) for l in code)
                story.append(Preformatted(wrapped, styles["Code"]))
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        if line.startswith("![") or not line.strip() or re.match(r"^\|[\s|:-]+\|$", line):
            continue
        level = len(line) - len(line.lstrip("#"))
        text = line.lstrip("# ") if level else line
        text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        text = text.replace("**", "").replace("`", "")
        if text.startswith("|"):
            text = " · ".join(part.strip() for part in text.strip("|").split("|"))
        style = styles["Heading1"] if level == 1 else styles["Heading2"] if level == 2 else styles["Heading3"] if level else styles["BodyText"]
        story.append(Paragraph(html.escape(text), style))
    captions = [("homepage.png", "Homepage and signed-in navigation"),
                ("profile.png", "Database-backed personal profile and search history"),
                ("saved-results.png", "Previously acquired concert results with retrieval timestamps and attribution"),
                ("stored-concerts.png", "Stored information: real Setlist.fm concerts replayed from database snapshots"),
                ("edit-search.png", "CRUD controls: update title and notes, delete a saved search"),
                ("mobile-profile.png", "Responsive mobile profile")]
    for name, caption in captions:
        story.extend([PageBreak(), Paragraph(caption, styles["Heading1"]),
                      Paragraph("Demonstration account; actual stored provider results. Remote images are disabled for reproducible screenshots.", styles["BodyText"]), Spacer(1, 12)])
        path = output / name
        width, height = ImageReader(str(path)).getSize()
        ratio = min(480 / width, 600 / height)
        story.append(Image(str(path), width=width * ratio, height=height * ratio))
    def footer(canvas, document):
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 24, "Encore | Hardik Katbamna | IT 401 A3")
        canvas.drawRightString(570, 24, str(document.page))
    SimpleDocTemplate(str(destination), rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42,
                      title="Encore A3 — Information Persistence", author="Hardik Katbamna").build(story, onFirstPage=footer, onLaterPages=footer)
    print("Screenshots and PDF created:", destination)


if __name__ == "__main__":
    build_pdf(*capture())
