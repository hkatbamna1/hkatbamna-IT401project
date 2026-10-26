"""Acquire up to three real George Clanton JamBase pages for A4 screenshots.

Uses the normal shared cache/catalog. No account or private search is created in
the hosted database. The public normalized provider facts are exported as evidence.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from services.persistence import result_page
from services.search_service import SearchService


def acquire():
    app = create_app()
    pages = {}
    with app.app_context():
        for number in range(1, 4):
            page = result_page("jambase", {"artist": "George Clanton", "venue": "", "location": ""}, number,
                               lambda: SearchService(app.config["JAMBASE_API_KEY"]))
            pages[str(number)] = page
            if not page["has_more"]:
                break
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    upcoming = [event for page in pages.values() for event in page["events"]
                if event.get("date_iso", "") >= today]
    if not upcoming:
        raise RuntimeError("JamBase returned no current upcoming George Clanton concerts; do not fabricate screenshots.")
    report = {"artist": "George Clanton", "provider": "JamBase", "capture_date_local": today,
              "timezone": "America/Los_Angeles", "upcoming_loaded": len(upcoming),
              "provider_total": pages["1"]["total"], "pages_loaded": len(pages),
              "corroboration_url": "https://groundcontroltouring.com/artists/george-clanton",
              "pages": pages}
    destination = ROOT / "docs" / "george-clanton-acquisition.json"
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "pages"}, indent=2))
    print("First upcoming cards:", [(event["date_iso"], event["venue"], event.get("location")) for event in upcoming[:3]])
    return report


if __name__ == "__main__":
    acquire()
