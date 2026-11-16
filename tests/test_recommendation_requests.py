"""One-time local form claims protect the free-tier budget from repeated submits."""
from concurrent.futures import ThreadPoolExecutor

from services.recommendation_requests import RecommendationRequests


def test_form_replays_and_overlapping_forms_do_not_start_more_work():
    guard = RecommendationRequests()
    first, second = guard.issue(1, 2), guard.issue(1, 2)
    assert guard.claim(first, 9, 2).status == "expired"
    assert guard.claim(first, 1, 3).status == "expired"
    assert guard.claim(first, 1, 2).status == "accepted"
    assert guard.claim(first, 1, 2).status == "pending"
    assert guard.claim(second, 1, 2).status == "pending"
    guard.finish(first, generation_id=42)
    replay = guard.claim(first, 1, 2)
    assert replay.status == "complete" and replay.generation_id == 42
    assert guard.claim(second, 1, 2).status == "accepted"
    guard.finish(second)
    assert guard.claim(second, 1, 2).status == "used"


def test_claim_is_atomic_across_simultaneous_requests():
    guard = RecommendationRequests()
    token = guard.issue(1, 2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: guard.claim(token, 1, 2).status, range(8)))
    assert results.count("accepted") == 1 and results.count("pending") == 7


def test_expiry_and_capacity_preserve_pending_claims_and_bound_memory():
    now = [0]
    guard = RecommendationRequests(clock=lambda: now[0], ttl=100, capacity=2)
    pending = guard.issue(1, 2)
    assert guard.claim(pending, 1, 2).status == "accepted"
    expired_ready = guard.issue(1, 3)
    replacement = guard.issue(1, 4)
    assert guard.claim(expired_ready, 1, 3).status == "expired"
    assert guard.claim(pending, 1, 2).status == "pending"
    assert guard.claim(replacement, 1, 4).status == "accepted"
    assert guard.issue(1, 5) == "" and len(guard.entries) == 2
    now[0] = 101
    assert guard.claim(pending, 1, 2).status == "expired"
    assert guard.issue(1, 5)


def test_library_form_binds_to_first_chosen_source_and_respects_other_inflight_claims():
    guard = RecommendationRequests()
    first, second = guard.issue(1), guard.issue(1)
    assert guard.claim(first, 9, 2).status == "expired"
    assert guard.claim(first, 1, 2).status == "accepted"
    assert guard.claim(second, 1, 2).status == "pending"
    assert guard.claim(first, 1, 3).status == "expired"
    guard.finish(first, generation_id=42)
    assert guard.claim(first, 1, 2).generation_id == 42
    assert guard.claim(second, 1, 3).status == "accepted"
