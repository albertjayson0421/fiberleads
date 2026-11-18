"""Offline failure-handling primitives for the Kinetic fleet (2026-09-10).

Implements the directive's Section-4 requirements as standalone, unit-tested units:
  - parse_retry_after(): retain RAW and PARSED Retry-After, and distinguish an
    ABSENT header from a literal "null" string.
  - ActorBreaker: per-actor circuit breaker. A policy block (403/429) quarantines
    ONLY that actor; it never auto-retries and never gets a replacement identity.
    Other actors are unaffected.
  - FleetDependencyBreaker: opens ONLY on a confirmed SHARED failure (a large
    fraction of active actors blocked), which can stop the shared dependency; a
    single/isolated actor failure never opens it.
  - BoundedStageQueue: bounded auth/search/product queue with backpressure (no
    unbounded growth), lease tracking, and shutdown-drain that preserves both
    queued and in-flight (leased-but-incomplete) work as UNRESOLVED.
  - ReadinessGate: production may reopen ONLY on an approved end-to-end
    qualification result; HTTP 201 auth alone can NEVER reopen production.

Pure/offline: no network, no imports of the live client. Wire into fleet_actor_v2
/ fleet_pipeline / fleet_scheduler as the integration step.
"""
from __future__ import annotations


# ---------------------------------------------------------------- Retry-After
def parse_retry_after(headers) -> dict:
    """Return {present, raw, parsed_seconds, was_literal_null}.
    ABSENT (header not sent) => present=False. Literal string "null" => present=True,
    was_literal_null=True, parsed_seconds=None (NOT treated as a real delay)."""
    get = getattr(headers, "get", None)
    raw = get("Retry-After") if get else (headers.get("Retry-After") if isinstance(headers, dict) else None)
    if raw is None:
        return {"present": False, "raw": None, "parsed_seconds": None, "was_literal_null": False}
    s = str(raw).strip()
    if s.lower() == "null":
        return {"present": True, "raw": raw, "parsed_seconds": None, "was_literal_null": True}
    if s.isdigit():
        return {"present": True, "raw": raw, "parsed_seconds": int(s), "was_literal_null": False}
    try:
        from email.utils import parsedate_to_datetime
        import datetime
        dt = parsedate_to_datetime(s)
        now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.utcnow()
        return {"present": True, "raw": raw, "parsed_seconds": max(0, int((dt - now).total_seconds())),
                "was_literal_null": False}
    except Exception:
        return {"present": True, "raw": raw, "parsed_seconds": None, "was_literal_null": False}


# ---------------------------------------------------------------- Actor breaker
class ActorBreaker:
    """Per-actor state. A 403/429 policy block quarantines THIS actor only.
    No auto-retry after a policy block; no replacement identity, ever."""
    HEALTHY = "HEALTHY"; QUARANTINED = "QUARANTINED"

    def __init__(self, actor_id):
        self.actor_id = actor_id
        self.state = self.HEALTHY
        self.block_status = None
        self.retry_after = None

    def record(self, status, retry_after_info=None):
        if status in (403, 429):
            self.state = self.QUARANTINED
            self.block_status = status
            self.retry_after = retry_after_info
        return self.state

    def can_proceed(self) -> bool:
        return self.state == self.HEALTHY

    def may_auto_retry(self) -> bool:
        return False  # never auto-retry a carrier policy block

    def may_replace_identity(self) -> bool:
        return False  # never swap identity to evade a block


# ---------------------------------------------------------------- Fleet breaker
class FleetDependencyBreaker:
    """Opens only when a CONFIRMED shared failure is present: at least `min_actors`
    are active AND the quarantined fraction reaches `threshold`. A single isolated
    actor failure never opens it (that's the ActorBreaker's job)."""
    def __init__(self, threshold=0.5, min_actors=4):
        self.threshold = threshold
        self.min_actors = min_actors
        self.open = False
        self.reason = None

    def evaluate(self, active_actors, quarantined_actors):
        if active_actors < self.min_actors:
            self.open = False; self.reason = "too_few_actors_to_confirm_shared"
            return self.open
        frac = (quarantined_actors / active_actors) if active_actors else 0.0
        self.open = frac >= self.threshold
        self.reason = ("shared_failure_frac_%.2f" % frac) if self.open else ("isolated_frac_%.2f" % frac)
        return self.open


# ---------------------------------------------------------------- Bounded queue
class QueueFull(Exception):
    pass


class BoundedStageQueue:
    """Bounded FIFO with lease tracking. put() raises QueueFull at capacity
    (backpressure — the producer must slow, never grow unbounded). Shutdown drain
    preserves queued AND leased-but-incomplete items as unresolved (nothing lost)."""
    def __init__(self, maxsize):
        assert maxsize > 0
        self.maxsize = maxsize
        self._q = []
        self._leases = {}
        self.backpressure_events = 0
        self._seq = 0

    def qsize(self): return len(self._q)
    def inflight(self): return len(self._leases)

    def put(self, item):
        if len(self._q) >= self.maxsize:
            self.backpressure_events += 1
            raise QueueFull("stage queue at capacity %d" % self.maxsize)
        self._q.append(item)

    def lease(self, worker):
        if not self._q:
            return None
        item = self._q.pop(0)
        self._seq += 1
        self._leases[self._seq] = (worker, item)
        return self._seq, item

    def complete(self, lease_id):
        self._leases.pop(lease_id, None)

    def drain_unresolved(self):
        """Return every not-completed item (queued + leased) exactly once, for
        durable preservation on shutdown. Idempotent across restart."""
        pending = list(self._q) + [v[1] for v in self._leases.values()]
        return pending


# ---------------------------------------------------------------- Readiness gate
class ReadinessGate:
    """Production may reopen ONLY on an approved end-to-end qualification result.
    HTTP 201 auth alone can NEVER reopen production."""
    TERMINAL_OK = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER",
                   "DEFINITIVE_OUT_OF_TERRITORY", "EXCLUDED_EXISTING"}

    def open_if_ready(self, canary) -> bool:
        if not isinstance(canary, dict):
            return False
        if canary.get("auth_status") != 201:
            return False
        # auth 201 ALONE is not enough — require completed search + product + terminal disposition
        if canary.get("search_status") != 200:
            return False
        if canary.get("product_status") != 200:
            return False
        return canary.get("disposition") in self.TERMINAL_OK
