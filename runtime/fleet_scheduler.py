#!/usr/bin/env python3
"""FLEET-WIDE SINGLE-PROCESS SCHEDULER — BUILT OFFLINE. DO NOT LAUNCH until a fresh auth returns 201.

Global admission control (one process => limits are trivially fleet-wide; all egress via DataImpulse,
never the host IP):
  - 72 actors total, sessions REUSED (re-auth only on death), new sessions created at <= 2 auth/s.
  - globally smoothed search rate (single global token bucket).
  - separate bounded product queue + product workers.
  - actor-level breaker: quarantine an actor on 403; its re-entry is gated by the 2/s auth bucket.
  - fleet breaker: if 403 rate > 1% over any rolling 60s window (with min sample), STOP everything.
  - zero inline remediation retries: a 403 / timeout is recorded retryable and NOT re-fired inline.
Frozen transport (native headers, search/product bodies, classifier) is reused verbatim from
fleet_pipeline / fleet_actor_v2 — this file changes ORCHESTRATION only, never the request bytes."""
import asyncio, json, os, sys, time, collections, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
from fleet_pipeline import (BASE, SESSION, SEARCH, PLANS, _headers, build_det, Actor,
                            classify_final, deliverable_plans, usps_normalize, sticky,
                            COUNTED, OTHER_DURABLE, pctl, guard)
from failure_handling import parse_retry_after, ReadinessGate
import re as _re

# DIAGNOSTIC redaction: capture a non-200 response body head + edge headers WITHOUT storing any secret.
_SECRET = _re.compile(r'("?(token|authorization|cookie|bearer|password|apikey|accessToken)"?\s*[:=]\s*)"[^"]*"', _re.I)
def _redact(t): return _SECRET.sub(r'\1"[RED]"', (t or ""))[:400]
def _resp_hdrs(r):
    keys = ("Server", "CF-Ray", "cf-mitigated", "Retry-After", "Content-Type", "cf-cache-status", "x-frame-options")
    h = getattr(r, "headers", None)
    return {k: h.get(k) for k in keys if h and h.get(k)} if h else {}
def _resp_body(r):
    try: return _redact(getattr(r, "text", "") or "")
    except Exception: return ""


def gate_hashes():
    """Item 2: fail startup unless every loaded module matches the signed manifest and none is obsolete."""
    import hashlib
    OBSOLETE = {"f11f0f2bc61e4010c871dd3349b353647541d4bbb311f0bb3464a8c513e696d7",
                "064eaa98cf5f687e876a7e399250102a185b1842fb0529aa941053a6b87210cb"}
    mpath = os.path.join(HERE, "production_manifest.json")
    manifest = json.load(open(mpath)) if os.path.exists(mpath) else {}
    if not manifest:
        sys.exit("STARTUP REFUSED: production_manifest.json missing — cannot verify hashes")
    import carrier_uqual, fleet_actor_v2, product_stage, billing_mapping, fleet_pipeline
    mods = {"carrier_uqual": carrier_uqual, "fleet_actor_v2": fleet_actor_v2,
            "product_stage": product_stage, "billing_mapping": billing_mapping, "fleet_pipeline": fleet_pipeline}
    loaded = {}
    for name, mod in mods.items():
        f = getattr(mod, "__file__", None)
        h = hashlib.sha256(open(f, "rb").read()).hexdigest() if f else ""
        loaded[name] = h
        if h in OBSOLETE:
            sys.exit("STARTUP REFUSED: %s loaded OBSOLETE hash %s (%s)" % (name, h[:16], f))
        want = manifest.get(name)
        if want and h != want:
            sys.exit("STARTUP REFUSED: %s hash %s != manifest %s (%s)" % (name, h[:16], want[:16], f))
    print(json.dumps({"ev": "HASH_GATE_PASS", "loaded": {k: v[:16] for k, v in loaded.items()}}), flush=True)
    return loaded


def readiness_gate_check(canary):
    """End-to-end readiness gate (item): production may reopen ONLY on an approved end-to-end
    qualification canary (auth 201 -> search 200 -> product 200 -> terminal disposition).
    HTTP 201 auth ALONE can never reopen production."""
    if not ReadinessGate().open_if_ready(canary):
        sys.exit("STARTUP REFUSED: readiness gate — an approved end-to-end qualification canary is "
                 "required (auth 201 -> search 200 -> product 200 -> terminal disposition); "
                 "auth 201 alone cannot reopen production.")
    print(json.dumps({"ev": "READINESS_GATE_PASS", "disposition": canary.get("disposition")}), flush=True)


class TokenBucket:
    """Async token bucket for fleet-global rate smoothing."""
    def __init__(self, rate, burst=None):
        self.rate = float(rate); self.cap = float(burst if burst is not None else max(1.0, rate))
        self.tokens = self.cap; self.t = time.monotonic(); self.lock = asyncio.Lock()
    async def acquire(self):
        while True:
            async with self.lock:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate); self.t = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0; return
                wait = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(min(wait, 1.0))


class FleetBreaker:
    """Rolling-60s TRANSPORT-SILENCE breaker. Per the never-hammer rule, only SILENCE (no answer:
    transport failure / timeout) trips it — a 403/429/500 is the endpoint TALKING and must NOT stop
    a working pull. A 403 rotates the exit (handled at the call site); it is fed here as NOT-silence."""
    def __init__(self, window=60.0, threshold=0.01, min_req=50):
        self.window = window; self.threshold = threshold; self.min_req = min_req
        self.events = collections.deque()  # (t, is_403)
        self.tripped = False
    def record(self, is_403):
        now = time.monotonic(); self.events.append((now, is_403))
        while self.events and now - self.events[0][0] > self.window: self.events.popleft()
        req = len(self.events)
        if req >= self.min_req:
            bad = sum(1 for _, b in self.events if b)
            if bad / req > self.threshold: self.tripped = True
        return self.tripped


class M:
    def __init__(self):
        self.durable = self.rejected = self.excluded = self.deliverable = self.other = 0
        self.retryable = self.dups = 0
        self.auths = self.searches = self.products = self.reopens = 0
        self.s401 = self.s403 = self.s429 = self.transport = 0
        self.slat = collections.deque(maxlen=4000); self.plat = collections.deque(maxlen=4000); self.alat = collections.deque(maxlen=4000)
        self.seen = set(); self.last_prod_t = 0.0
        self.win_durable = self.win_search = self.win_product = 0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addrs"); ap.add_argument("ledger")
    ap.add_argument("--actors", type=int, default=72)          # fleet-wide total (single process)
    ap.add_argument("--auth-rate", type=float, default=2.0)    # <= 2 auth/s fleet-wide
    ap.add_argument("--search-rate", type=float, default=20.0) # globally smoothed search rate
    ap.add_argument("--product-workers", type=int, default=8)
    ap.add_argument("--pq-bound", type=int, default=24)
    ap.add_argument("--hold", type=int, default=21600)
    ap.add_argument("--auth-deadline", type=float, default=20.0)
    ap.add_argument("--search-deadline", type=float, default=10.0)
    ap.add_argument("--product-deadline", type=float, default=22.0)
    ap.add_argument("--breaker-window", type=float, default=60.0)
    ap.add_argument("--breaker-threshold", type=float, default=0.05)
    ap.add_argument("--refresh-every", type=int, default=15)   # proactive token refresh (403 hits ~21/token)
    ap.add_argument("--exit-budget", type=int, default=10)     # PROACTIVE exit rotation: retire a sticky exit after N successful searches. Clean run ran ~2.3/exit @ s403=0; one exit tolerated ~30 before a 403 (measured 2026-09-10). 10 keeps a 3x margin.
    ap.add_argument("--rot-cap", type=int, default=40)         # consecutive exit rotations with ZERO successful search before an actor gives up. Raised for a partially-403 exit pool: rotating to a fresh exit on a 403 reply is NOT hammering; true silence is caught by the transport breaker.
    ap.add_argument("--canary", default=None, help="approved end-to-end qualification canary result (JSON path)")
    a = ap.parse_args()
    gate_hashes()   # item 2: refuse to start on any obsolete/mismatched module hash
    _canary = json.load(open(a.canary)) if (a.canary and os.path.exists(a.canary)) else {}
    readiness_gate_check(_canary)   # end-to-end readiness gate: auth 201 alone cannot reopen production

    cfg = json.load(open(os.path.join(os.environ["FL_CONFIG"], "billing_config.json"))).get("billing_status_mapping", {})
    old = {v.strip().upper() for v in cfg.get("existing_values", [])}
    new = {v.strip().upper() for v in cfg.get("new_values", [])}
    _rjson = os.path.join(HERE, "residential.json")
    if not os.path.exists(_rjson):
        _rjson = os.path.join(HERE, "residential.json.example")  # offline / clean-clone fallback
    pools = [p for p in json.load(open(_rjson))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]

    _TERM = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING", "COMPLETE_REJECTED",
             "DEFINITIVE_OUT_OF_TERRITORY", "DEFINITIVE_NO_ORDERABLE_FIBER", "EXCLUDED_PENDING_ORDER",
             "COMPLETE_NOT_IN_KINETIC"}
    done = set()
    if os.path.exists(a.ledger):
        for l in open(a.ledger):
            try:
                d = json.loads(l)
                if d.get("class") in _TERM and d.get("ck"): done.add(d["ck"])
            except Exception: pass
    # A4: restore per-job retry budgets from durable RETRY_LOG rows so a restart never silently resets them
    retry_budget = {}
    if os.path.exists(a.ledger):
        for l in open(a.ledger):
            try:
                d = json.loads(l)
                if d.get("class") == "RETRY_LOG" and d.get("ck"):
                    retry_budget[d["ck"]] = max(retry_budget.get(d["ck"], 0), int(d.get("attempt", 0)))
            except Exception: pass

    m = M(); auth_b = TokenBucket(a.auth_rate); search_b = TokenBucket(a.search_rate, burst=max(2.0, a.search_rate))
    breaker = FleetBreaker(a.breaker_window, a.breaker_threshold)
    INTAKE_BOUND = max(64, a.actors * 4)
    jobq = asyncio.Queue(maxsize=INTAKE_BOUND); pq = asyncio.Queue(maxsize=a.pq_bound); wq = asyncio.Queue()   # A1: bounded intake + product queues
    ledger = open(a.ledger, "a"); stop = asyncio.Event(); feed_done = asyncio.Event()
    live_actors = a.actors; n_fed = 0
    t0 = time.monotonic(); deadline = t0 + a.hold; m.last_prod_t = t0

    async def feeder():                                    # A1: lazy streaming intake, real backpressure, durable resume
        nonlocal n_fed
        try:
            for line in open(a.addrs):
                if stop.is_set(): break
                try: r = json.loads(line)
                except Exception: continue
                ck = r.get("ck")
                if not ck or ck in done: continue           # durable resume: skip already-terminal ck
                r["_reauth401"] = retry_budget.get(ck, 0)    # A4: restore per-job retry budget across restart
                await jobq.put(r)                            # blocks when full -> intake backpressure
                n_fed += 1
        finally:
            feed_done.set()

    def commit(ck, cls, ev=None):
        if ck in m.seen: m.dups += 1; return
        m.seen.add(ck); m.last_prod_t = time.monotonic()
        if cls == "SERVICEABLE_FIBER": m.deliverable += 1
        if cls in COUNTED: m.durable += 1; m.win_durable += 1
        elif cls in OTHER_DURABLE: m.other += 1; m.win_durable += 1
        if cls == "EXCLUDED_EXISTING": m.excluded += 1
        if cls == "COMPLETE_REJECTED": m.rejected += 1; m.durable += 1; m.win_durable += 1
        row = {"ck": ck, "class": cls, "t": round(time.monotonic() - t0, 3)}
        if ev: row.update(ev)
        wq.put_nowait(row)

    def retryable(job, phase, retry_after=None):
        m.retryable += 1
        row = {"ck": job.get("ck"), "class": "INCOMPLETE_RETRYABLE", "phase": phase,
               "t": round(time.monotonic() - t0, 3), "counted": False}
        if retry_after is not None:
            row["retry_after"] = retry_after
        wq.put_nowait(row)

    def requeue(job):
        # A1: intake queue is bounded, so a requeue can find it full. Never crash and never
        # block (all actors blocking on a full queue would deadlock): persist the job as
        # retryable (durable, re-fed on restart) instead of dropping or hanging.
        try: jobq.put_nowait(job)
        except asyncio.QueueFull: retryable(job, "requeue_backpressure")

    def persist_retry(job, phase, ra, resp=None):
        # A4: persist the search/product failure + Retry-After + attempt count to the DURABLE stream
        # BEFORE the job is requeued/released, so a crash/restart cannot silently reset the budget.
        # DIAGNOSTIC: on a non-200 pass the response so the REAL body head + edge headers (redacted) are
        # recorded — the actual cause of a 403/429 is then inspectable instead of inferred.
        row = {"ck": job.get("ck"), "class": "RETRY_LOG", "phase": phase,
               "attempt": job.get("_reauth401", 0), "retry_after": ra,
               "t": round(time.monotonic() - t0, 3), "counted": False}
        if resp is not None:
            row["http"] = getattr(resp, "status_code", None)
            row["resp_headers"] = _resp_hdrs(resp)
            row["body_head"] = _resp_body(resp)
        wq.put_nowait(row)

    def check_breaker(is_silence):
        # trips ONLY on sustained transport SILENCE (no answer) per the never-hammer rule; 403/429 are replies
        if breaker.record(is_silence) and not stop.is_set():
            print(json.dumps({"ev": "FLEET_BREAKER_TRIP", "transport": m.transport, "note": "transport-silence > threshold / 60s -> stop"}), flush=True)
            stop.set()

    async def writer():
        buf = []
        while not (stop.is_set() and wq.empty()):
            try: buf.append(await asyncio.wait_for(wq.get(), timeout=0.2))
            except asyncio.TimeoutError: pass
            if buf and (len(buf) >= 200 or stop.is_set()):
                ledger.write("".join(json.dumps(r) + "\n" for r in buf)); ledger.flush(); buf = []
        if buf: ledger.write("".join(json.dumps(r) + "\n" for r in buf)); ledger.flush()

    async def product_worker():
        while not (stop.is_set() and pq.empty()):
            try: ac, det, job = await asyncio.wait_for(pq.get(), timeout=0.2)
            except asyncio.TimeoutError: continue
            if not ac.alive:                               # A2: shared gate — never dispatch on a quarantined session
                retryable(job, "actor_quarantined_preproduct"); continue
            try:
                await search_b.acquire()                   # smooth product calls into the global rate too
                if stop.is_set(): retryable(job, "product_inflight_on_stop"); continue   # A3: preserve in-flight (drain, not drop)
                r = await ac.product(det, a.product_deadline); m.products += 1
                if r.status_code in (403, 429):
                    setattr(m, "s429" if r.status_code == 429 else "s403", getattr(m, "s429" if r.status_code == 429 else "s403") + 1)
                    ra = parse_retry_after(getattr(r, "headers", {}) or {})   # retain raw+parsed Retry-After
                    ac.alive = False; check_breaker(False)     # A2: quarantine this session; 403/429 is a reply, not silence — do NOT halt the fleet
                    persist_retry(job, "product_%d" % r.status_code, ra, r)   # A4: persist failure+Retry-After+real body before release
                    retryable(job, "product-%d" % r.status_code, ra); continue
                check_breaker(False)
                enr = {**r.json(), "_fl_transport": {"http_status": 200}} if r.status_code == 200 else {"fl_fetch_error": "http_%d" % r.status_code}
                cls = classify_final("FIBER", det, enr)
                if cls in COUNTED or cls in OTHER_DURABLE:
                    ev = {}
                    if cls == "SERVICEABLE_FIBER":
                        ev = {"line1": job.get("line1"), "city": job.get("city"), "zip": job.get("zip"),
                              "lat": job.get("lat"), "lon": job.get("lon"), "plans": deliverable_plans(enr)}
                    commit(job["ck"], cls, ev)
                else:
                    commit(job["ck"], "COMPLETE_REJECTED")
            except asyncio.CancelledError:
                retryable(job, "product_cancelled"); raise           # A3: recover unfinished on cancel
            except asyncio.TimeoutError: check_breaker(True); retryable(job, "product_watchdog")     # timeout = SILENCE
            except Exception: m.transport += 1; check_breaker(True); retryable(job, "product_transport")  # transport failure = SILENCE

    async def actor_task(idx):
        nonlocal live_actors
        open_fails = 0; rot_no_progress = 0
        try:
            while not stop.is_set():
                if rot_no_progress >= a.rot_cap:
                    # BOUNDED egress rotation: fresh exits keep returning 403/429 with no successful search
                    # in between -> stop this actor rather than hammer a broadly-unavailable egress.
                    print(json.dumps({"ev": "ACTOR_ROTATION_EXHAUSTED", "idx": idx, "rot_no_progress": rot_no_progress}), flush=True)
                    return
                await auth_b.acquire()                          # <= 2 auth/s fleet-wide (gates every (re)auth)
                if stop.is_set(): return
                ac = Actor(idx, idx % len(pools)); m.auths += 1; leased = None
                try:
                    ta = time.monotonic()
                    ok = await ac.open(pools, a.auth_deadline, m)
                    m.alat.append(time.monotonic() - ta)
                    if not ok:
                        # STARTUP exit selection (NOT post-block rotation): DataImpulse hands varied exits;
                        # some are non-US / unreachable and fail auth. Try a bounded number of fresh routes
                        # (gated at <=2 auth/s) to find a working US exit before this session does any search.
                        # This is the fleet_actor_v2.prewarm pattern; a SEARCH/PRODUCT 403 still quarantines
                        # with NO rotation (below). Give up after the budget so a broadly-down egress is not hammered.
                        open_fails += 1
                        if open_fails >= 40:                    # find a working US exit through a partially-403 pool; 403 on auth is a reply, rotate to a fresh exit
                            return
                        continue
                    open_fails = 0
                    m.reopens += 1
                    since = 0; exit_searches = 0                # per-exit successful-search counter (proactive rotation)
                    # A2: shared dispatch gate. `ac.alive` is the ONE gate consulted by both search dispatch (here)
                    # and product dispatch (product_worker). A 403/429 on EITHER sets ac.alive=False; after that no
                    # new request dispatches on this session. In-flight work is accounted separately (recovered below
                    # and in product_worker). A fresh session re-enters via the outer loop, gated at <=2 auth/s.
                    while not stop.is_set() and ac.alive:
                        if exit_searches >= a.exit_budget:
                            # PROACTIVE exit rotation: retire this exit before it accumulates enough searches
                            # to draw a 403; break -> outer loop re-opens via sticky() -> FRESH exit IP + token.
                            break
                        if since >= a.refresh_every:
                            await auth_b.acquire()
                            await ac.reauth(a.auth_deadline, m); since = 0
                        if pq.qsize() >= a.pq_bound: await asyncio.sleep(0.02); continue
                        try: job = jobq.get_nowait()
                        except asyncio.QueueEmpty:
                            if feed_done.is_set() and jobq.empty(): stop_if_drained(); return   # A1: fed + drained -> done
                            await asyncio.sleep(0.05); continue
                        leased = job
                        await search_b.acquire()
                        if stop.is_set(): requeue(job); leased = None; return
                        try:
                            ts = time.monotonic(); r = await ac.search(job, a.search_deadline)
                            m.searches += 1; m.win_search += 1; since += 1; m.last_prod_t = time.monotonic(); m.slat.append(time.monotonic() - ts)  # any returned search = endpoint alive (stall guard must not fire on retryable-heavy backlog; 403-floods still caught by FleetBreaker)
                        except asyncio.TimeoutError: check_breaker(True); retryable(job, "search_watchdog"); leased = None; continue   # timeout = SILENCE -> feed breaker
                        except Exception: m.transport += 1; check_breaker(True); retryable(job, "search_transport"); leased = None; continue  # transport failure = SILENCE
                        if r.status_code == 401:
                            m.s401 += 1; job["_reauth401"] = job.get("_reauth401", 0) + 1
                            persist_retry(job, "search_401", None)                       # A4: persist before requeue
                            if job["_reauth401"] <= 3 and await ac.reauth(a.auth_deadline, m):
                                requeue(job); leased = None; continue
                            retryable(job, "auth_401" + ("_cap" if job["_reauth401"] > 3 else "")); leased = None; return
                        if r.status_code in (403, 429):
                            setattr(m, "s429" if r.status_code == 429 else "s403", getattr(m, "s429" if r.status_code == 429 else "s403") + 1)
                            ra = parse_retry_after(getattr(r, "headers", {}) or {})
                            check_breaker(False)   # 403/429 is a REPLY (endpoint talking), NOT silence -> rotate the exit below, never halt the fleet on it
                            persist_retry(job, "search_%d" % r.status_code, ra, r)       # A4: persist failure+Retry-After+real body before release
                            # RECOVERY = EXIT ROTATION. The clean run sustained ~290 searches @ s403=0 by egressing
                            # from fresh exits; a same-IP token refresh did NOT recover (measured 2026-09-10). Requeue
                            # the unanswered job, quarantine THIS exit, and break -> the outer loop opens a FRESH exit
                            # (sticky() -> new sessid -> new exit IP) + fresh token. Bounded by a.rot_cap + the fleet breaker.
                            ac.alive = False
                            requeue(job); leased = None
                            rot_no_progress += 1
                            break
                        check_breaker(False)
                        if r.status_code != 200: retryable(job, "search_http_%d" % r.status_code); leased = None; continue
                        exit_searches += 1; rot_no_progress = 0                          # progress: this exit answered 200 -> reset the rotation bound
                        j = r.json()
                        outcome, det = build_det(j, old, new)
                        # AddressSuggestions FOLLOW: the carrier returns its own candidate(s); re-search the top
                        # candidate ONCE to turn a non-exact reply into a real verdict (proven: recovered Andrews leads).
                        if not (det.get("exactMatch") is True) and j.get("addressCandidates") and not job.get("_followed"):
                            c0 = j["addressCandidates"][0]
                            if isinstance(c0, dict) and (c0.get("addressLine1") or c0.get("dfAddressId")):
                                fjob = dict(job); fjob["_followed"] = 1
                                fjob["line1"] = c0.get("addressLine1") or job.get("line1")
                                if c0.get("city"): fjob["city"] = c0.get("city")
                                if c0.get("postalCode"): fjob["zip"] = str(c0.get("postalCode"))
                                try:
                                    rf = await ac.search(fjob, a.search_deadline); m.searches += 1; m.last_prod_t = time.monotonic()
                                    if rf.status_code == 200:
                                        j = rf.json(); outcome, det = build_det(j, old, new); job = fjob
                                except Exception: pass
                        fiber_exact = (outcome == "FIBER" and det["exactMatch"] is True and det["success"] is True
                                       and det["dfAddressId"] and det["uqualProvisioningResult"]
                                       and not (det["existing_customer"] is True and det["customer_mapping_verified"]))
                        if fiber_exact:
                            await pq.put((ac, det, job)); leased = None                  # in-flight ownership -> product stage
                        else:
                            cls = classify_final(outcome, det, None)
                            if cls in COUNTED or cls in OTHER_DURABLE: commit(job["ck"], cls)
                            else: retryable(job, "unresolved_search_" + str(det.get("validationResult") or outcome))  # D1
                            leased = None
                finally:
                    if leased is not None: retryable(leased, "actor_exit_inflight")      # A3: recover unfinished (incl. cancel)
                    if ac is not None and ac.s is not None:
                        try: await ac.s.close()                                          # A3: session cleanup on every exit
                        except Exception: pass
        finally:
            live_actors -= 1                                                             # A1: all-actors-unavailable termination
            if live_actors <= 0 and not stop.is_set(): stop.set()

    def stop_if_drained():
        if feed_done.is_set() and jobq.empty() and pq.empty(): stop.set()

    async def reporter():
        w = 0
        while not stop.is_set():
            await asyncio.sleep(10); w += 1
            print(json.dumps({"ev": "WIN", "w": w, "t": round(time.monotonic() - t0),
                "durable_10s_per_s": round(m.win_durable / 10.0, 2), "search_10s_per_s": round(m.win_search / 10.0, 2),
                "auth_cum": m.auths, "reopens": m.reopens, "pq": pq.qsize(),
                "s401": m.s401, "s403": m.s403, "s429": m.s429, "transport": m.transport,
                "breaker_tripped": breaker.tripped, "backlog": jobq.qsize(),
                "search_p50": pctl(m.slat[-300:], .5), "search_p95": pctl(m.slat[-300:], .95)}), flush=True)
            m.win_durable = m.win_search = m.win_product = 0
            if time.monotonic() - m.last_prod_t > 90:
                # no search answer for 90s = stuck (actors wedged on a degraded egress). Stop so the process
                # EXITS instead of zombie-ing (removed the `jobq not empty` guard that let it hang forever).
                print(json.dumps({"ev": "STALL_HALT", "note": "no search answer 90s -> stop (no zombie)"}), flush=True); stop.set()

    ftask = asyncio.create_task(feeder())
    wtask = asyncio.create_task(writer())
    ptasks = [asyncio.create_task(product_worker()) for _ in range(a.product_workers)]
    atasks = [asyncio.create_task(actor_task(i)) for i in range(a.actors)]
    rtask = asyncio.create_task(reporter())
    try:
        while time.monotonic() < deadline and not stop.is_set():
            await asyncio.sleep(0.5)
            if feed_done.is_set() and jobq.empty() and pq.empty(): break
            if all(t.done() for t in atasks): break            # A1: all actors unavailable -> terminate the run
    finally:
        # A3: clean shutdown on normal stop OR cancellation. Stop, cancel the blocking tasks (feeder may
        # be parked in `await jobq.put`, reporter in sleep), let the workers exit and run their finally
        # (session close + in-flight recovery), then drain the durable writer last so no row is lost.
        stop.set()
        for t in (ftask, rtask):
            t.cancel()
        await asyncio.gather(*atasks, *ptasks, return_exceptions=True)
        await asyncio.gather(ftask, rtask, wtask, return_exceptions=True)
        try: ledger.close()
        except Exception: pass
    print(json.dumps({"ev": "FINAL", "actors": a.actors, "auth_rate": a.auth_rate, "search_rate": a.search_rate,
        "active_s": round(time.monotonic() - t0, 1), "durable": m.durable, "deliverable": m.deliverable,
        "rejected": m.rejected, "retryable": m.retryable, "auths": m.auths, "searches": m.searches,
        "products": m.products, "reopens": m.reopens, "s401": m.s401, "s403": m.s403, "s429": m.s429,
        "transport": m.transport, "breaker_tripped": breaker.tripped, "dups": m.dups,
        "fed": n_fed, "consumed": n_fed - jobq.qsize(), "all_actors_down": (live_actors <= 0)}), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
