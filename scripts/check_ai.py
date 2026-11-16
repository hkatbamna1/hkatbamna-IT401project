"""Make one live Gemini request at the current candidate limit; never access the DB.

Run from the repository root: python scripts/check_ai.py
This explicit diagnostic consumes API quota. It prints only timing and validation
metadata, never the key, prompt, model output, or any private saved records.
"""
import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config
from models import utcnow
from services.ai_service import AiService, AiServiceError
from services.prompts import build_recommendation_prompt
from services.recommendations import CANDIDATE_LIMIT, RecommendationCandidate, RecommendationCandidates
from services.validators import InvalidRecommendation, validate_recommendation_output


def synthetic_candidates():
    cutoff = utcnow().date()
    retrieved_at = utcnow().isoformat() + "Z"
    candidates = tuple(RecommendationCandidate(
        source_ref=f"C{index + 1:02d}", source="synthetic-test", external_id=f"fixture-{index + 1}",
        source_url="https://example.com/fixture", retrieved_at=retrieved_at,
        facts={"title": f"Synthetic concert {index + 1}", "artist": f"Fixture artist {index + 1}",
               "venue": "Fixture venue A" if index % 2 else "Fixture venue B",
               "location": "Fixture city", "event_date": (cutoff + timedelta(days=30 + index)).isoformat(),
               "status": "Scheduled", "genre": "Rock"}) for index in range(CANDIDATE_LIMIT))
    return RecommendationCandidates(0, cutoff, len(candidates), candidates)


def run_check(settings):
    batch = synthetic_candidates()
    prompt = build_recommendation_prompt(
        batch, "Prefer Fixture venue A. Choose up to three concerts on different dates.")
    service = AiService.from_config(settings)
    started = time.perf_counter()
    result = service.generate(prompt)
    output = validate_recommendation_output(result.text, batch)
    return {"status": "passed", "requested_model": service.model, "returned_model": result.model,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "candidate_count": batch.considered_count,
            "recommendation_count": len(output["recommendations"]), "source_validation": "passed",
            "data_source": "synthetic fixtures; no database access"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Override AI_SERVICE_MODEL for this check only.")
    parser.add_argument("--timeout", type=int, help="Override timeout (1–60 seconds) for this check only.")
    args = parser.parse_args(argv)
    settings = {key: getattr(Config, key) for key in
                ("AI_SERVICE_API_KEY", "AI_SERVICE_MODEL", "AI_SERVICE_TIMEOUT_SECONDS", "AI_SERVICE_MAX_OUTPUT_TOKENS")}
    if args.model is not None:
        settings["AI_SERVICE_MODEL"] = args.model
    if args.timeout is not None:
        settings["AI_SERVICE_TIMEOUT_SECONDS"] = args.timeout
    try:
        report = run_check(settings)
    except AiServiceError as error:
        report = {"status": "failed", "error_code": error.code, "message": str(error)}
    except InvalidRecommendation:
        report = {"status": "failed", "error_code": "invalid_output",
                  "message": "Generated JSON failed local source validation; nothing was stored."}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
