"""DECOUPLED search/product pipeline — STEP 3+4 of the speed repair (2026-09-09).

The single-actor design blocked each search actor on its own slow product call; at 256 actors
product-stage reads hung (58 watchdog expiries) and throughput collapsed to ~6/s. This splits the
work into three bounded stages so search never waits on product:

  A. SEARCH ACTORS (--search N): persistent actor-owned sticky session/token/route. One primary
     search per job. Non-fiber / existing / not-found / non-exact finalize IMMEDIATELY. An exact
     fiber candidate is packaged with its OWN route+token+device-id and pushed to PRODUCT_QUEUE.
  B. PRODUCT WORKERS (--product M): bounded semaphore. Each pulls a candidate and fires the product
     call on the SAME route (sticky exit) and SAME bearer token as the search that produced it
     (route/token lineage preserved; a route-bound product session is reused, never a new route).
     A product watchdog timeout is INCOMPLETE_RETRYABLE, never a final, never counted.
  C. DURABLE WRITER: one batched async writer, idempotent dedup on ck, no per-row fsync, exact
     cold-reopen reconciliation.

Backpressure: when PRODUCT_QUEUE depth exceeds its bound, search leasing pauses (so product calls
are never allowed to pile up and time out).

Watchdog (STEP 4): per-phase deadlines set ABOVE the measured p99 + a bounded margin (defaults
generous; override via flags). Expiry cancels only that op, preserves phase/elapsed/actor/route,
marks retryable, and never kills unrelated actors.

Native headers + native search schema are the FROZEN proven ones (ported from carrier_uqual
c02e20cf); DO NOT change them. Classifier is fleet_actor_v2.classify_final (one classifier).

Usage: fleet_pipeline.py <addrs.jsonl> <ledger.jsonl> --search N --product M --hold 60 [--label L]
"""
import sys, os, json, asyncio, time, base64, hashlib, argparse, collections
try:
    import resource  # POSIX-only; used only for an rss diagnostic
except ImportError:  # Windows / offline analysis / CI
    resource = None
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
import classifier as CL, product_stage as PS
from curl_cffi.requests import AsyncSession

BASE = "https://buy.gokinetic.com"; SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"; PLANS = "/api/v2/windstream/product-availability"
XHR = {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
       "Content-Type": "application/json", "Referer": BASE + "/", "Origin": BASE}
# Lazy so a clean clone imports without secrets; falls back to the committed
# uqual.txt.example placeholder for offline runs. A real credential is never committed.
_BASIC = None
def _basic():
    global _BASIC
    if _BASIC is None:
        _cfgdir = os.environ["FL_CONFIG"]
        _p = os.path.join(_cfgdir, "uqual.txt")
        if not os.path.exists(_p):
            _p = os.path.join(_cfgdir, "uqual.txt.example")
        with open(_p) as _f:
            _BASIC = base64.b64encode(next(l.strip() for l in _f if l.strip() and not l.strip().startswith("#")).encode()).decode()
    return _BASIC

# --- FROZEN native /api headers (byte-for-byte carrier_uqual c02e20cf) — DO NOT CHANGE ---
import uuid as _uuid
_FP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_FP_STRING = "|".join(["true", _FP_UA, "en-US", "8", "0", "false", "true",
                       "1920", "1080", "1920", "1032", "24", "24", "300"])
def _sha256_hex(s): return hashlib.sha256((s or "").encode("utf-8")).hexdigest()
def _x_meta_info(): return _sha256_hex(_FP_STRING)
def _x_resource_id(token): return _sha256_hex(token or "")
def _jwt_session_id(token):
    try:
        p = (token or "").split(".")[1]; p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("sessionId", "")
    except Exception:
        return ""
def _headers(token, device_id, bearer):
    return {**XHR, "Authorization": ("Bearer " + token if bearer else "Basic " + _basic()),
            "device-id": device_id, "session-id": _jwt_session_id(token or ""),
            "x-meta-info": _x_meta_info(), "x-resource-id": _x_resource_id(token or "")}

# reuse the ONE proven classifier + normalization + priced-plan gate
from fleet_actor_v2 import classify_final, deliverable_plans, usps_normalize, sticky
from failure_handling import parse_retry_after, ActorBreaker
COUNTED = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING"}
OTHER_DURABLE = {"DEFINITIVE_OUT_OF_TERRITORY", "DEFINITIVE_NO_ORDERABLE_FIBER", "EXCLUDED_PENDING_ORDER"}


class M:
    """Windowed + cumulative metrics (all measured, none assumed)."""
    def __init__(self):
        self.counted = 0; self.rejected = 0; self.excluded = 0; self.deliverable = 0
        self.other_durable = 0; self.retryable = 0; self.dups = 0
        self.searches = 0; self.products = 0; self.reauth = 0
        self.s401 = self.s403 = self.s429 = self.transport = 0
        self.search_wd = self.product_wd = 0
        self.slat = collections.deque(maxlen=4000); self.plat = collections.deque(maxlen=4000); self.alat = collections.deque(maxlen=4000)
        self.exact = self.non_exact = 0
        self.seen = {}; self.finals = []
        self.win_counted = 0; self.win_search = 0; self.win_product = 0
        self.reopens = 0; self.broad_fail = 0; self.tripped = False; self.last_prod_t = 0.0


def pctl(a, q):
    if not a: return 0.0
    s = sorted(a); return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 3)


async def guard(coro, deadline):
    """Hard per-op watchdog: cancel and raise on timeout, so a hung read never blocks the stage."""
    return await asyncio.wait_for(coro, timeout=deadline)


_DEF_TERMINAL = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING", "COMPLETE_REJECTED",
                 "DEFINITIVE_OUT_OF_TERRITORY", "DEFINITIVE_NO_ORDERABLE_FIBER", "EXCLUDED_PENDING_ORDER",
                 "COMPLETE_NOT_IN_KINETIC"}


def load_done(ledger_path, terminal_set=_DEF_TERMINAL):
    """Resume/lease-recovery set: canonical keys already carrying a terminal disposition.
    Restart re-sweeps only undecided / INCOMPLETE_RETRYABLE keys; never a duplicate."""
    done = set()
    if os.path.exists(ledger_path):
        for l in open(ledger_path):
            try:
                d = json.loads(l)
                if d.get("class") in terminal_set and d.get("ck"): done.add(d["ck"])
            except Exception:
                pass
    return done


class Actor:
    """A search actor: persistent sticky route + auth token + stable device-id."""
    def __init__(self, idx, pool):
        self.idx = idx; self.pool = pool; self.s = None; self.tok = None
        self.route = None; self.plan = None; self.device_id = str(_uuid.uuid4()); self.alive = False
        self.breaker = ActorBreaker(idx); self.retry_after = None

    def note_block(self, response):
        """Actor-scoped breaker (item 1): a 403/429 policy block quarantines THIS actor only —
        never auto-retried, never replaced. Retains raw+parsed Retry-After (item 3)."""
        self.retry_after = parse_retry_after(getattr(response, "headers", {}) or {})
        self.breaker.record(getattr(response, "status_code", None), self.retry_after)
        self.alive = False
        return self.retry_after

    async def open(self, pools, auth_dl, m):
        self.route, self.plan = sticky(pools, self.pool)
        self.s = AsyncSession(impersonate="chrome124", proxies={"http": self.route, "https": self.route})
        try:
            t = time.monotonic()
            r = await guard(self.s.post(BASE + SESSION, headers=_headers(None, self.device_id, False), data="{}", timeout=30), auth_dl)
            m.alat.append(time.monotonic() - t)
            if r.status_code == 201:
                self.tok = r.json().get("token") or r.json().get("accessToken")
                self.alive = bool(self.tok); return self.alive
        except Exception:
            pass
        return False

    async def reauth(self, auth_dl, m):
        try:
            r = await guard(self.s.post(BASE + SESSION, headers=_headers(None, self.device_id, False), data="{}", timeout=30), auth_dl)
            if r.status_code == 201:
                self.tok = r.json().get("token") or r.json().get("accessToken"); m.reauth += 1; return True
        except Exception:
            pass
        return False

    async def search(self, job, search_dl):
        body = {"addressLine1": usps_normalize(job["line1"]).upper() or str(job["line1"]).upper(), "addressLine2": "",
                "city": str(job.get("city") or "").upper(), "state": str(job.get("state") or "").upper(),
                "postalCode": str(job.get("zip") or ""), "geoLat": "", "geoLong": "", "useSearchV2": False}
        return await guard(self.s.post(BASE + SEARCH, headers=_headers(self.tok, self.device_id, True),
                                       data=json.dumps(body), timeout=30), search_dl)

    async def product(self, det, product_dl):
        # SAME route (self.s = sticky exit) + SAME token + SAME device-id as the search that produced it.
        body = {"dfAddressId": det["dfAddressId"], "accountNumber": "", "moveDate": "",
                "addAddrReason": "", "uqualProvisioningResult": det["uqualProvisioningResult"]}
        return await guard(self.s.post(BASE + PLANS, headers=_headers(self.tok, self.device_id, True),
                                       data=json.dumps(body), timeout=40), product_dl)


def build_det(j, old, new):
    ad = j.get("address") if isinstance(j.get("address"), dict) else {}
    tech = (j.get("techType") or "").upper()
    outcome = {"FIBER": "FIBER", "COPPER": "COPPER", "CABLE": "CABLE"}.get(tech, "") or \
        ("NOFIBER" if j.get("validationResult") == "AddressUnserviceableInTerritory" else
         ("UNRESOLVED" if j.get("validationResult") == "AddressSuggestions" else "NOMATCH"))
    bs = str(ad.get("billingStatus") or "").strip().upper()
    exist = True if bs in old else (False if bs in new else None)
    det = {"techType": tech, "dfAddressId": j.get("dfAddressId"), "existing_customer": exist,
           "customer_mapping_verified": exist is not None,
           "exactMatch": j.get("exactMatch") if isinstance(j.get("exactMatch"), bool) else None,
           "success": j.get("success"), "uqualProvisioningResult": j.get("uqualProvisioningResult"),
           "maxQual": j.get("maxQual"), "validationResult": j.get("validationResult"), "_raw_search": j}
    return outcome, det


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addrs"); ap.add_argument("ledger")
    ap.add_argument("--search", type=int, default=128); ap.add_argument("--product", type=int, default=16)
    ap.add_argument("--hold", type=int, default=60); ap.add_argument("--label", default="pipe")
    ap.add_argument("--stagger", type=float, default=0.03)
    ap.add_argument("--auth-deadline", type=float, default=20.0)
    ap.add_argument("--search-deadline", type=float, default=10.0)   # gate p99 search 2.75s -> generous margin
    ap.add_argument("--product-deadline", type=float, default=22.0)  # gate p99 product 7.56s -> generous margin
    ap.add_argument("--pq-bound", type=int, default=0)               # 0 -> auto (2x product workers)
    ap.add_argument("--pace", type=float, default=0.0)               # per-actor inter-request delay (gentle mode)
    ap.add_argument("--canary", default=None, help="approved end-to-end qualification canary (JSON path); required to start production")
    a = ap.parse_args()
    pq_bound = a.pq_bound or (a.product * 2)
    from failure_handling import ReadinessGate
    _canary = json.load(open(a.canary)) if (getattr(a, "canary", None) and os.path.exists(a.canary)) else {}
    if not ReadinessGate().open_if_ready(_canary):
        print(json.dumps({"ev": "STARTUP_REFUSED", "reason": "readiness gate: an approved end-to-end qualification canary (auth 201 -> search 200 -> product 200 -> terminal) is required; auth 201 alone cannot reopen production"}), flush=True); return

    cfg = json.load(open(os.path.join(os.environ["FL_CONFIG"], "billing_config.json"))).get("billing_status_mapping", {})
    old = {v.strip().upper() for v in cfg.get("existing_values", [])}; new = {v.strip().upper() for v in cfg.get("new_values", [])}
    pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
    raw = [json.loads(l) for l in open(a.addrs)]
    m = M()

    #: RESUME across chained bursts — skip any ck that already carries a DURABLE/REJECTED/NOT-IN-KINETIC
    #: (or completed-reject) decision in the ledger. Only genuinely-undecided addresses are re-swept, so
    #: chaining bursts with fresh exits never re-hits an answered address. INCOMPLETE_RETRYABLE is re-swept.
    done = load_done(a.ledger)     # resume/lease-recovery: skip already-terminal ck (restart-safe)
    raw = [r for r in raw if r.get("ck") not in done]

    jobq = asyncio.Queue(); pq = asyncio.Queue(maxsize=pq_bound); wq = asyncio.Queue()   # bounded product queue -> real backpressure
    for r in raw: jobq.put_nowait(r)
    n_input = len(raw)
    ledger = open(a.ledger, "a")     # APPEND across bursts (resume-safe)

    # ---- prewarm search actors ----
    actors = [Actor(i, i % len(pools)) for i in range(a.search)]
    async def pw(ac, i):
        await asyncio.sleep(i * a.stagger); return await ac.open(pools, a.auth_deadline, m)
    warmed = await asyncio.gather(*[pw(ac, i) for i, ac in enumerate(actors)])
    live = [ac for ac, ok in zip(actors, warmed) if ok]
    print(json.dumps({"ev": "PREWARM", "label": a.label, "requested": a.search, "warmed": len(live),
                      "product_workers": a.product, "pq_bound": pq_bound, "input": n_input,
                      "auth_p50": pctl(m.alat, .5)}), flush=True)
    if not live:
        print(json.dumps({"ev": "FINAL", "error": "no_actors_warmed"})); return

    t0 = time.monotonic(); deadline = t0 + a.hold; stop = asyncio.Event()
    m.last_prod_t = t0

    def commit(ck, cls, ev=None):
        if ck in done:
            m.dups += 1; return
        done.add(ck)
        m.last_prod_t = time.monotonic()   # stall-halt anchor: last time a durable decision landed
        if cls == "SERVICEABLE_FIBER": m.deliverable += 1
        if cls in COUNTED: m.counted += 1; m.win_counted += 1
        elif cls in OTHER_DURABLE: m.other_durable += 1; m.win_counted += 1
        if cls == "EXCLUDED_EXISTING": m.excluded += 1
        if cls == "COMPLETE_REJECTED": m.rejected += 1; m.counted += 1; m.win_counted += 1
        row = {"ck": ck, "class": cls, "t": round(time.monotonic() - t0, 3)}
        if ev: row.update(ev)
        wq.put_nowait(row); m.finals.append(ck)

    def retryable(job, phase, retry_after=None):
        m.retryable += 1
        row = {"ck": job.get("ck"), "class": "INCOMPLETE_RETRYABLE", "phase": phase,
               "t": round(time.monotonic() - t0, 3), "counted": False}
        if retry_after is not None:
            row["retry_after"] = retry_after
        wq.put_nowait(row)

    async def writer():
        buf = []
        while not (stop.is_set() and wq.empty()):
            try:
                row = await asyncio.wait_for(wq.get(), timeout=0.2)
                buf.append(row)
            except asyncio.TimeoutError:
                pass
            if buf and (len(buf) >= 200 or stop.is_set()):
                ledger.write("".join(json.dumps(r) + "\n" for r in buf)); ledger.flush(); buf = []
        if buf:
            ledger.write("".join(json.dumps(r) + "\n" for r in buf)); ledger.flush()

    async def do_product(ac, det, job):
        try:
            t = time.monotonic()
            r = await ac.product(det, a.product_deadline)
            m.products += 1; m.win_product += 1; m.plat.append(time.monotonic() - t)
            if r.status_code in (403, 429):
                (setattr(m, "s429", m.s429 + 1) if r.status_code == 429 else setattr(m, "s403", m.s403 + 1))
                ra = ac.note_block(r); retryable(job, "product-" + str(r.status_code), ra); return
            enr = {**r.json(), "_fl_transport": {"http_status": 200}} if r.status_code == 200 else {"fl_fetch_error": "http_" + str(r.status_code)}
            outcome, det2 = build_det(det["_raw_search"], old, new)
            cls = classify_final("FIBER", det, enr)
            if cls in COUNTED or cls in OTHER_DURABLE:
                ev = {}
                if cls == "SERVICEABLE_FIBER":
                    ev = {"line1": job.get("line1"), "city": job.get("city"), "zip": job.get("zip"),
                          "lat": job.get("lat"), "lon": job.get("lon"), "plans": deliverable_plans(enr)}
                commit(job["ck"], cls, ev)
            else:
                # fiber-exact whose product did not populate a plan -> not a lead but a real completed answer
                commit(job["ck"], "COMPLETE_REJECTED")
        except asyncio.TimeoutError:
            m.product_wd += 1; retryable(job, "product_watchdog")
        except Exception:
            m.transport += 1; retryable(job, "product_transport")

    async def product_worker():
        while not (stop.is_set() and pq.empty()):
            try:
                ac, det, job = await asyncio.wait_for(pq.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            await do_product(ac, det, job)

    async def search_actor(ac):
        while not stop.is_set():
            # backpressure: don't lease new work while the product queue is saturated
            if pq.qsize() >= pq_bound:
                await asyncio.sleep(0.02); continue
            try:
                job = jobq.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.02)
                if jobq.empty(): return
                continue
            try:
                t = time.monotonic()
                r = await ac.search(job, a.search_deadline)
                m.searches += 1; m.win_search += 1; m.slat.append(time.monotonic() - t)
            except asyncio.TimeoutError:
                m.search_wd += 1; retryable(job, "search_watchdog"); continue
            except Exception:
                m.transport += 1; retryable(job, "search_transport"); continue
            if r.status_code == 401:
                m.s401 += 1
                job["_reauth401"] = job.get("_reauth401", 0) + 1
                if job["_reauth401"] <= 3 and await ac.reauth(a.auth_deadline, m):
                    jobq.put_nowait(job); continue
                ac.alive = False; retryable(job, "auth_401" + ("_cap" if job["_reauth401"] > 3 else "")); return
            if r.status_code in (403, 429):
                (setattr(m, "s429", m.s429 + 1) if r.status_code == 429 else setattr(m, "s403", m.s403 + 1))
                ac.note_block(r)                                  # actor-scoped breaker + raw/parsed Retry-After retention
                jobq.put_nowait(job); return                      # quarantine this actor (NO replacement); job stays READY
            if r.status_code != 200:
                retryable(job, "search_http_" + str(r.status_code)); continue
            j = r.json()
            outcome, det = build_det(j, old, new)
            if det["exactMatch"] is True: m.exact += 1
            else: m.non_exact += 1
            fiber_exact = (outcome == "FIBER" and det["exactMatch"] is True and det["success"] is True
                           and det["dfAddressId"] and det["uqualProvisioningResult"]
                           and not (det["existing_customer"] is True and det["customer_mapping_verified"]))
            if fiber_exact:
                await pq.put((ac, det, job))                      # hand to product stage; keep searching
            else:
                cls = classify_final(outcome, det, None)
                if cls in COUNTED or cls in OTHER_DURABLE:
                    commit(job["ck"], cls)
                else:
                    # D1 FIX (classifier v2, 2026-09-10): a search-stage NON-DEFINITIVE result
                    # (NOMATCH / AddressSuggestions / non-exact) is "asked badly", not "the answer
                    # is no". It is UNRESOLVED/retryable and must NEVER be a terminal reject.
                    retryable(job, "unresolved_search_" + str(det.get("validationResult") or outcome))
            if a.pace: await asyncio.sleep(a.pace)                # gentle mode: throttle per-actor request rate

    async def reporter():
        w = 0
        while not stop.is_set():
            await asyncio.sleep(10); w += 1
            el = time.monotonic() - t0
            active = sum(1 for ac in live if ac.alive)
            oldest = 0.0
            print(json.dumps({"ev": "WIN", "label": a.label, "w": w, "t": round(el),
                "durable_10s_per_s": round(m.win_counted / 10.0, 2), "search_10s_per_s": round(m.win_search / 10.0, 2),
                "product_10s_per_s": round(m.win_product / 10.0, 2), "pq_depth": pq.qsize(), "pq_bound": pq_bound,
                "product_wd": m.product_wd, "search_wd": m.search_wd,
                "search_p50": pctl(m.slat[-200:], .5), "search_p95": pctl(m.slat[-200:], .95), "search_p99": pctl(m.slat[-200:], .99),
                "product_p50": pctl(m.plat[-200:], .5), "product_p95": pctl(m.plat[-200:], .95), "product_p99": pctl(m.plat[-200:], .99),
                "s401": m.s401, "s403": m.s403, "s429": m.s429, "transport": m.transport,
                "active": active, "quarantined": len(live) - active,
                "rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0) if resource else 0,
                "cum_durable": m.counted + m.other_durable, "backlog": jobq.qsize()}), flush=True)
            m.win_counted = m.win_search = m.win_product = 0

    wtask = asyncio.create_task(writer())
    ptasks = [asyncio.create_task(product_worker()) for _ in range(a.product)]
    stasks = [asyncio.create_task(search_actor(ac)) for ac in live]
    rtask = asyncio.create_task(reporter())
    # SUPERVISOR (fresh-session replacement): keep `a.search` FRESH actors alive. When a session is
    # retired on a 403 (its search_actor returns), spawn a brand-new actor with a fresh random sticky
    # session (Actor.open -> sticky() -> os.urandom tag). BREAKER: if fresh sessions stop authenticating
    # (broad block), STOP — never hammer. Low fixed a.search = the concurrency ceiling; we never over-scale.
    BREAKER = 6; MAX_SPAWN = 200000
    async def supervisor():
        nxt = a.search
        while not stop.is_set():
            await asyncio.sleep(0.5)
            if stop.is_set(): break
            alive = sum(1 for t in stasks if not t.done())
            if False:  # item 8 (2026-09-10): fresh-session replacement DISABLED — a 403'd actor stops, never replaced
                for _ in range(a.search - alive):
                    if jobq.empty() or stop.is_set(): break
                    ac = Actor(nxt, nxt % len(pools)); nxt += 1
                    if await ac.open(pools, a.auth_deadline, m):
                        m.reopens += 1; m.broad_fail = 0
                        stasks.append(asyncio.create_task(search_actor(ac)))
                    else:
                        m.broad_fail += 1
                        if m.broad_fail >= BREAKER:
                            m.tripped = True; stop.set(); break
                    await asyncio.sleep(a.stagger)
    suptask = asyncio.create_task(supervisor())
    # run for the hold window OR until both queues drain
    STALL_SECS = 120
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        if jobq.empty() and pq.empty() and all(t.done() for t in stasks):
            break
        # STALL-HALT: if no durable decision landed for STALL_SECS while work remains, the egress is
        # refusing fresh sessions too — STOP rather than churn (auth-201-then-search-403 hammering).
        if not jobq.empty() and (time.monotonic() - m.last_prod_t) > STALL_SECS:
            m.tripped = True; break
    stop.set()
    suptask.cancel()
    await asyncio.gather(*stasks, return_exceptions=True)
    # drain product queue
    for _ in range(200):
        if pq.empty(): break
        await asyncio.sleep(0.1)
    await asyncio.gather(*ptasks, return_exceptions=True)
    rtask.cancel()
    await asyncio.gather(wtask, return_exceptions=True)
    ledger.close()

    active_s = time.monotonic() - t0
    durable = m.counted + m.other_durable
    # cold-reopen reconciliation
    reopened = set()
    for l in open(a.ledger):
        try:
            d = json.loads(l)
            if d.get("class") in COUNTED or d.get("class") in OTHER_DURABLE or d.get("class") == "COMPLETE_REJECTED":
                reopened.add(d["ck"])
        except Exception:
            pass
    total_searches = max(1, m.searches)
    print(json.dumps({"ev": "FINAL", "label": a.label, "search_actors": len(live), "product_workers": a.product,
        "active_s": round(active_s, 1),
        "durable_decisions": durable, "durable_per_s": round(durable / active_s, 3),
        "correct_durable_per_s": round(m.counted / active_s, 3),
        "deliverable": m.deliverable, "rejected": m.rejected, "excluded_existing": m.excluded,
        "retryable": m.retryable, "product_wd": m.product_wd, "search_wd": m.search_wd,
        "searches": m.searches, "products": m.products, "reauth": m.reauth, "reopens": m.reopens, "tripped": m.tripped,
        "exact": m.exact, "non_exact": m.non_exact, "exact_frac": round(m.exact / max(1, m.exact + m.non_exact), 3),
        "calls_per_decision": round((m.searches + m.products) / max(1, durable), 2),
        "search_p50": pctl(m.slat, .5), "search_p95": pctl(m.slat, .95), "search_p99": pctl(m.slat, .99),
        "product_p50": pctl(m.plat, .5), "product_p95": pctl(m.plat, .95), "product_p99": pctl(m.plat, .99),
        "s401": m.s401, "s403": m.s403, "s429": m.s429, "transport": m.transport,
        "s403_pct": round(100 * m.s403 / total_searches, 2), "s429_pct": round(100 * m.s429 / total_searches, 2),
        "product_wd_pct": round(100 * m.product_wd / max(1, m.products + m.product_wd), 2),
        "dups": m.dups, "consumed": n_input - jobq.qsize(), "backlog_end": jobq.qsize(),
        "unaccounted": n_input - (durable + m.rejected + m.retryable + jobq.qsize() + (m.excluded if False else 0)),
        "cold_reopen_unique": len(reopened), "cold_reopen_ok": len(reopened) == len(set(m.finals)),
        "hit_eof": jobq.empty()}), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
