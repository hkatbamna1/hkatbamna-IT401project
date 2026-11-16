"""Bounded, process-local submission guard for the local Flask application."""
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class SubmissionState:
    status: str
    generation_id: int | None = None


class RecommendationRequests:
    """Consume each form token once and allow one in-flight call per saved search.

    Only scope IDs, monotonic times, states and confirmed result IDs are retained.
    Multiple production workers would require a shared store for these claims.
    """
    def __init__(self, *, clock=time.monotonic, ttl=900, capacity=512):
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def _expire(self):
        now = self.clock()
        for token in list(self.entries):
            if now - self.entries[token]["created"] >= self.ttl:
                del self.entries[token]

    def issue(self, user_id, saved_id=None):
        with self.lock:
            self._expire()
            while len(self.entries) >= self.capacity:
                discard = next((token for token, entry in self.entries.items() if entry["status"] != "pending"), None)
                if discard is None:
                    return ""  # Preserve in-flight claims rather than admitting overlapping work.
                del self.entries[discard]
            token = secrets.token_urlsafe(32)
            self.entries[token] = {"scope": (user_id, saved_id), "created": self.clock(),
                                   "status": "ready", "generation_id": None}
            return token

    def claim(self, token, user_id, saved_id):
        with self.lock:
            self._expire()
            entry = self.entries.get(token)
            if entry is None or entry["scope"][0] != user_id or entry["scope"][1] not in (None, saved_id):
                return SubmissionState("expired")
            if entry["status"] != "ready":
                return SubmissionState(entry["status"], entry["generation_id"])
            chosen_scope = (user_id, saved_id)
            if any(value["scope"] == chosen_scope and value["status"] == "pending"
                   for value in self.entries.values()):
                return SubmissionState("pending")
            entry.update(scope=chosen_scope, status="pending")
            return SubmissionState("accepted")

    def finish(self, token, generation_id=None):
        with self.lock:
            entry = self.entries.get(token)
            if entry is not None:
                entry.update(status="complete" if generation_id is not None else "used",
                             generation_id=generation_id)
