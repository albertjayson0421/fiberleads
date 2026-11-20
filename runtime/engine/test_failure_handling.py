"""Offline tests for failure_handling.py — clearly-labeled fixtures, no network."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from failure_handling import (parse_retry_after, ActorBreaker, FleetDependencyBreaker,
                              BoundedStageQueue, QueueFull, ReadinessGate)

P = F = 0
def check(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

print("[retry-after: absent vs literal null vs int vs date]")
ra_absent = parse_retry_after({})                      # header not present
ra_null = parse_retry_after({"Retry-After": "null"})   # literal string "null"
ra_int = parse_retry_after({"Retry-After": "120"})
ra_date = parse_retry_after({"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})
check("absent -> present False", ra_absent["present"] is False and ra_absent["parsed_seconds"] is None)
check("literal 'null' distinguished from absent", ra_null["present"] is True and ra_null["was_literal_null"] is True and ra_null["parsed_seconds"] is None)
check("int seconds parsed", ra_int["parsed_seconds"] == 120 and ra_int["was_literal_null"] is False)
check("http-date parsed to positive seconds", ra_date["parsed_seconds"] and ra_date["parsed_seconds"] > 0)

print("[actor breaker: isolated block quarantines only that actor; no retry/replace]")
a0 = ActorBreaker(0); a1 = ActorBreaker(1)
a0.record(403, ra_null)
check("blocked actor quarantined", a0.state == ActorBreaker.QUARANTINED and not a0.can_proceed())
check("unrelated healthy actor unaffected", a1.state == ActorBreaker.HEALTHY and a1.can_proceed())
check("no auto-retry after policy block", a0.may_auto_retry() is False)
check("no identity replacement after policy block", a0.may_replace_identity() is False)
check("retry_after retained on breaker", a0.retry_after is ra_null)
a1.record(200)
check("200 does not quarantine", a1.can_proceed())

print("[fleet breaker: isolated vs confirmed-shared]")
fb = FleetDependencyBreaker(threshold=0.5, min_actors=4)
check("1 of 10 blocked -> NOT open (isolated)", fb.evaluate(active_actors=10, quarantined_actors=1) is False)
check("6 of 10 blocked -> open (shared)", fb.evaluate(active_actors=10, quarantined_actors=6) is True)
check("1 of 2 blocked but < min_actors -> NOT open", fb.evaluate(active_actors=2, quarantined_actors=1) is False)

print("[bounded queue: backpressure, no unbounded growth]")
q = BoundedStageQueue(maxsize=3)
for i in range(3): q.put({"ck": i})
raised = False
try: q.put({"ck": 99})
except QueueFull: raised = True
check("put beyond capacity raises QueueFull (backpressure)", raised and q.backpressure_events == 1)
check("queue never grew beyond maxsize", q.qsize() == 3)

print("[bounded queue: shutdown drain preserves queued + leased (unresolved)]")
lease = q.lease("w1")                    # leases ck=0, 2 remain queued
check("lease returns an item and tracks in-flight", lease is not None and q.inflight() == 1 and q.qsize() == 2)
pending = q.drain_unresolved()           # simulate shutdown
cks = sorted(p["ck"] for p in pending)
check("drain preserves all 3 (2 queued + 1 leased), none lost", cks == [0, 1, 2] and len(pending) == 3)
# simulate restart: re-enqueue drained; complete the previously leased one -> no duplicate
q2 = BoundedStageQueue(maxsize=10)
for p in pending: q2.put(p)
check("restart re-enqueues all preserved work", q2.qsize() == 3)

print("[readiness gate: auth-201-alone cannot reopen; end-to-end required]")
g = ReadinessGate()
check("auth 201 alone -> NOT ready", g.open_if_ready({"auth_status": 201}) is False)
check("201+search200 but no product -> NOT ready", g.open_if_ready({"auth_status": 201, "search_status": 200}) is False)
check("full 201/200/200 + terminal disposition -> ready",
      g.open_if_ready({"auth_status": 201, "search_status": 200, "product_status": 200,
                       "disposition": "SERVICEABLE_FIBER"}) is True)
check("201/200/200 but non-terminal disposition -> NOT ready",
      g.open_if_ready({"auth_status": 201, "search_status": 200, "product_status": 200,
                       "disposition": "UNRESOLVED_NOMATCH"}) is False)

print("\nRESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
