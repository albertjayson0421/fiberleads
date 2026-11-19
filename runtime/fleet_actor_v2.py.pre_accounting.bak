"""fleet_actor_v2 — LIVE Kinetic async session-actor fleet (single production build).

This is THE production implementation (patched in place, per directive). One immutable
build deploys to every VM; VMs are compute-only and all Kinetic traffic egresses via the
verified DataImpulse US-sticky route.

PROVEN FIXES (kept):
  - US-only egress (verify US before Kinetic auth; reject a definitively non-US exit).
  - One sticky exit + session + cookie jar + token + search context per actor.
  - HTTP 401 -> re-authenticate on the SAME exit and retry (token refresh, not a block).
  - Session reuse: never destroy a session after HTTP 200 or 5xx.
  - Exact-match + priced-fiber-plan + existing-customer correctness.
  - No identity replacement/rotation after 403/429 (quarantine the actor only).
  - Certified durable ledger (dedup on canonical_key) + conservation + cold-reopen.

MAJOR SPEED CHANGES:
  1. Async session actors: curl_cffi AsyncSession; hundreds of actors await Kinetic
     concurrently on one event loop (no blocked threads). Each workflow stays sequential
     and pinned to its owner actor.
  2. Auth once per actor at staggered prewarm (excluded from active timing); reuse token
     until an explicit 401; on 401 re-auth same exit + retry once. auth/1000-addr reported.
  3. Fast path: one primary search on the normalized county/E911 address ->
       exact nonfiber -> final; verified existing -> final exclusion;
       exact fiber/new -> one product call; non-exact/suggestion -> bounded remediation.
     Remediation capped at REMEDIATION_CAP of total searches. primary/remediation/product
     searches reported separately.
  4. Per-actor quarantine only: a 403/429 quarantines that actor (retain Retry-After);
     every other already-running actor continues. A fleet AdmissionController reduces
     admission when the quarantined fraction or latency rises.
  5. Hard monotonic watchdog on every auth/search/product op -> UNRESOLVED_RETRYABLE,
     lease recoverable, evidence preserved, never a hung actor.
  6. Fleet-of-VMs ready: disjoint partitions, local observation queue, durable ledger
     writer per node; deploy one immutable hash to every VM.

Usage: fleet_actor_v2.py <addrs.jsonl> <ledger.jsonl> --actors N --hold 60 --label L
        [--stagger S] [--auth-deadline 20] [--search-deadline 12] [--product-deadline 25]
        [--retry-ledger P] [--remediation-cap 0.15]
"""
import sys, os, json, asyncio, time, base64, hashlib, argparse, collections
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
import classifier as CL, product_stage as PS
import datetime as _dt

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

# --- native /api deterministic headers (ported byte-for-byte from frozen carrier_uqual c02e20cf, 2026-09-09).
# buy.gokinetic.com/api gates on 4 headers the site JS computes: device-id/session-id (session store),
# x-meta-info = SHA256 of the device fingerprint string, x-resource-id = SHA256 of the bearer token.
# NOT behavioral/Cloudflare tokens — deterministic hashes of THIS client's own state.
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

from failure_handling import parse_retry_after, ReadinessGate
COUNTED = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING"}
OTHER_DURABLE = {"DEFINITIVE_OUT_OF_TERRITORY", "DEFINITIVE_NO_ORDERABLE_FIBER", "EXCLUDED_PENDING_ORDER"}
WATCHDOG_STALL_LIMIT = 3

# --- USPS Publication 28 normalization (applied to the primary search; original preserved) ---
import re as _re
try:
    from carrier_uqual import spell_out as _spell_out, abbreviate as _abbreviate
except Exception:
    def _spell_out(x): return ""
    def _abbreviate(x): return x
_MULTISPACE = _re.compile(r"\s+")
# Per-market carrier spelling. Default "spellout" (Texas: carrier wants AVE->AVENUE).
# "abbrev" for Iowa/Kinetic, whose matcher wants the abbreviated form (STREET->ST,
# NORTHEAST->NE); measured 2026-09-08, abbrev recovered 21/24 vs 18/24 spelled-out with
# 0 vs 2 NOMATCH on known-serviceable Knoxville addresses. Set FL_NORM=abbrev to enable.
_NORM_MODE = os.environ.get("FL_NORM", "spellout").strip().lower()


def usps_normalize(line1):
    """Canonical form matching the carrier's own DB spelling: uppercase, collapse
    whitespace, drop stray punctuation (keep # for units), then apply the market's
    directional/suffix convention (FL_NORM: spellout AVE->AVENUE, or abbrev STREET->ST).
    The ORIGINAL county/E911 string is kept beside it for evidence."""
    if not line1:
        return line1
    s = str(line1).upper().replace(".", " ").replace(",", " ")
    s = _MULTISPACE.sub(" ", s).strip()
    if _NORM_MODE == "abbrev":
        out = _abbreviate(s)
    else:
        out = _spell_out(s) or s          # spell_out() returns "" when nothing changed
    return _MULTISPACE.sub(" ", out).strip()


class WatchdogTimeout(Exception):
    def __init__(self, phase, deadline):
        super().__init__(f"{phase} exceeded {deadline}s"); self.phase = phase; self.deadline = deadline


async def guard(coro, phase, deadline):
    try:
        return await asyncio.wait_for(coro, timeout=deadline)
    except asyncio.TimeoutError:
        raise WatchdogTimeout(phase, deadline)


def sticky(pools, i):
    p = pools[i % len(pools)]
    # VERIFIED sticky grammar (2026-09-10): DataImpulse pins the exit IP only with ";sessid.<id>".
    # The prior "__sid.<id>" was IGNORED -> a rotating exit on every request (no egress continuity).
    u = str(p["user"]) + "__cr.us;sessid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"]), p.get("name")


def classify_final(outcome, det, enr):
    ec = det.get("existing_customer"); ver = det.get("customer_mapping_verified") is True
    if outcome == "FIBER":
        if ec is True and ver: return "EXCLUDED_EXISTING"
        if not (det.get("success") is True and det.get("exactMatch") is True and det.get("dfAddressId") and det.get("uqualProvisioningResult")): return None
        if isinstance(enr, dict) and enr.get("fl_fetch_error"): return None
        res = CL.classify("FIBER", det.get("_raw_search"), enrichment=enr)
        if res.get("sub_status") == CL.ENRICHMENT_UNREAD: return None
        if res.get("status") != CL.FIBER_PLAN_POPULATED:
            if res.get("sub_status") == CL.PENDING_ORDER: return "EXCLUDED_PENDING_ORDER"
            if res.get("status") == CL.NONFIBER: return "DEFINITIVE_NONFIBER"
            return "DEFINITIVE_NO_ORDERABLE_FIBER" if res.get("sub_status") == CL.NO_PLAN else None
        return "SERVICEABLE_FIBER" if (ec is False and ver and PS.priced_fiber_bundles(enr)) else None
    res = CL.classify(outcome, det.get("_raw_search"), enrichment=None)
    if res.get("status") == CL.NONFIBER: return "DEFINITIVE_NONFIBER"
    if res.get("status") == CL.NOT_FOUND: return "DEFINITIVE_OUT_OF_TERRITORY"
    return None


def deliverable_plans(enr):
    """Priced fiber-tier bundles with their real recurring/promo prices, for the ledger.
    A SERVICEABLE_FIBER lead ships with the actual populated plan + pricing (carrier
    invariant). Names come from the classifier's own priced-fiber rule."""
    out = {}
    try:
        names = set(PS.priced_fiber_bundles(enr))
        for b, _tier in CL._iter_bundles(enr):
            n = CL._bundle_name(b)
            if n in names and n not in out:
                a = b.get("attributes") if isinstance(b.get("attributes"), dict) else b
                std = PS._money(a.get("standardPrice")); pro = PS._money(a.get("promoPrice"))
                out[n] = {"name": n, "standard": str(std) if std is not None else "",
                          "promo": str(pro) if pro is not None else ""}
    except Exception:
        pass
    return list(out.values())


class M:
    consumed = counted = other_durable = unresolved = retryable = dups = 0
    rejected = 0                       # COMPLETE_REJECTED: completed HTTP-200 carrier answers that are not deliverable leads
    prewarm_auth = reauth = primary_search = remediation_search = product = 0
    exact_match = non_exact = 0
    s401 = s403 = s429 = transport = watchdog = 0
    active = warmed = completed = quarantined = watchdog_stalled = 0
    slat = collections.deque(maxlen=4000); plat = collections.deque(maxlen=4000); alat = collections.deque(maxlen=4000)
    by_class = {}; seen = {}; per_actor = []
    ledger_fp = None; retry_fp = None

    @classmethod
    def total_search(cls): return cls.primary_search + cls.remediation_search


def record_retryable(job, phase, actor, exc):
    M.retryable += 1
    if M.retry_fp is not None:
        M.retry_fp.write(json.dumps({"ck": job.get("ck"), "status": "UNRESOLVED_RETRYABLE", "phase": phase,
                                     "actor": actor.idx, "plan": actor.plan,
                                     "exception": type(exc).__name__ if isinstance(exc, BaseException) else str(exc)}) + "\n")
        M.retry_fp.flush()


def record_final(ck, cls, actor, ev):
    if ck in M.seen:
        M.dups += 1; return
    M.seen[ck] = cls; M.by_class[cls] = M.by_class.get(cls, 0) + 1
    if cls in COUNTED: M.counted += 1
    else: M.other_durable += 1
    if M.ledger_fp is not None:
        row = {"ck": ck, "class": cls, "counted": cls in COUNTED,
               "actor": actor.idx, "plan": actor.plan, "t": round(ev["t"], 3),
               "evidence_sha256": ev["evidence_sha256"]}
        if ev.get("lead") is not None:
            row["lead"] = ev["lead"]
            row["observed_at_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        M.ledger_fp.write(json.dumps(row) + "\n")
        M.ledger_fp.flush()


class AdmissionController:
    """Fleet-wide throttle: raises a per-search delay when the quarantined fraction or
    recent search latency rises. Normally zero delay (maximise rate); engages only under
    measured stress. Reduces the concurrent request rate without replacing any actor."""
    Q_HI = 0.25          # quarantined/warmed fraction that triggers throttle
    L_HI = 4.0           # recent search p95 (s) that triggers throttle
    STEP = 0.05          # seconds of added per-search delay per stress increment
    MAX_DELAY = 1.0

    def __init__(self): self.delay = 0.0; self._stop = False

    async def gate(self):
        if self.delay > 0: await asyncio.sleep(self.delay)

    async def monitor(self):
        while not self._stop:
            await asyncio.sleep(1.0)
            qf = (M.quarantined / M.warmed) if M.warmed else 0.0
            recent = M.slat[-50:]
            p95 = sorted(recent)[int(0.95 * (len(recent) - 1))] if len(recent) > 5 else 0.0
            if qf > self.Q_HI or p95 > self.L_HI:
                self.delay = min(self.MAX_DELAY, self.delay + self.STEP)
            else:
                self.delay = max(0.0, self.delay - self.STEP)

    def stop(self): self._stop = True


class Actor:
    def __init__(self, idx, plan_idx):
        self.idx = idx; self.plan_idx = plan_idx; self.s = None; self.tok = None; self.plan = None; self.alive = False
        self.device_id = str(_uuid.uuid4())     # one stable device identity for this actor's lifetime

    def _h(self, bearer):
        """XHR base + Authorization + the 4 native deterministic headers (x-resource-id/session-id
        follow self.tok, so a 401 reauth that replaces the token recomputes them on the next call)."""
        return {**XHR, "Authorization": ("Bearer " + self.tok if bearer else "Basic " + _basic()),
                "device-id": self.device_id, "session-id": _jwt_session_id(self.tok or ""),
                "x-meta-info": _x_meta_info(), "x-resource-id": _x_resource_id(self.tok or "")}

    async def auth_op(self):
        return await self.s.post(BASE + SESSION, headers=self._h(False), data="{}", timeout=30)

    async def search_op(self, line1, city, state, zipc):
        #: native search schema (postalCode/addressLine2/geoLat/geoLong/useSearchV2), UPPER — matches frozen client
        body = {"addressLine1": str(line1 or "").upper(), "addressLine2": "",
                "city": str(city or "").upper(), "state": str(state or "").upper(),
                "postalCode": str(zipc or ""), "geoLat": "", "geoLong": "", "useSearchV2": False}
        return await self.s.post(BASE + SEARCH, headers=self._h(True), data=json.dumps(body), timeout=30)

    async def product_op(self, det):
        return await self.s.post(BASE + PLANS, headers=self._h(True),
                                 data=json.dumps({"dfAddressId": det["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": det["uqualProvisioningResult"]}), timeout=30)


async def egress_country(s):
    for _ in range(2):
        try:
            c = (await s.get("https://ipinfo.io/json", timeout=8)).json().get("country")
            if c: return c
        except Exception:
            continue
    return None


async def prewarm(actor, pools, ad):
    from curl_cffi.requests import AsyncSession
    for _ in range(6):
        url, actor.plan = sticky(pools, actor.plan_idx)
        s = AsyncSession(impersonate="chrome124", proxies={"http": url, "https": url})
        c = await egress_country(s)
        if c is not None and c != "US":
            try: await s.close()
            except Exception: pass
            continue
        actor.s = s
        try: await s.get(BASE + "/", timeout=15)
        except Exception: pass
        try:
            t = time.monotonic()
            r = await guard(actor.auth_op(), "auth", ad); M.prewarm_auth += 1; M.alat.append(time.monotonic() - t)
            if r.status_code == 201:
                tok = (r.json() or {}).get("token")
                if tok:
                    actor.tok = tok; actor.alive = True; M.active += 1; M.warmed += 1; return True
        except WatchdogTimeout:
            M.watchdog += 1
        except Exception:
            pass
        actor.s = None
        try: await s.close()
        except Exception: pass
    return False


def _candidate_line(j, want_line):
    """Bounded remediation: from an AddressSuggestions payload, return the single candidate
    addressLine1 whose normalized form equals the requested line (never substitute another
    address). Returns None if there is no unambiguous exact-normalized candidate."""
    import re
    def norm(s): return re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    want = norm(want_line)
    for key in ("addressSuggestions", "suggestions", "candidates", "addresses"):
        arr = j.get(key)
        if isinstance(arr, list):
            hits = [c.get("addressLine1") for c in arr if isinstance(c, dict) and norm(c.get("addressLine1")) == want]
            hits = [h for h in hits if h]
            if len(set(norm(h) for h in hits)) == 1 and hits:
                return hits[0]
    return None


async def run_one(actor, job, billing, ad, sd, pd_, ctl, rem_cap):
    old, new = billing
    M.consumed += 1
    nl = usps_normalize(job["line1"])          # Pub-28 normalized form for the carrier; original kept in job
    # ---- PRIMARY SEARCH (watchdog-guarded), same-exit 401 re-auth up to 3x ----
    await ctl.gate()
    t = time.monotonic()
    try:
        r = await guard(actor.search_op(nl, job["city"], job["state"], job.get("zip", "")), "search", sd)
    except WatchdogTimeout as e:
        M.watchdog += 1; record_retryable(job, "search", actor, e); actor.wd_streak += 1
        return "watchdog_stalled" if actor.wd_streak >= WATCHDOG_STALL_LIMIT else None
    except Exception as e:
        M.transport += 1; record_retryable(job, "search", actor, e); return None
    actor.wd_streak = 0
    attempts = 0
    while r.status_code == 401 and attempts < 3:
        attempts += 1
        try:
            ra = await guard(actor.auth_op(), "auth", ad); M.reauth += 1
        except WatchdogTimeout as e:
            M.watchdog += 1; record_retryable(job, "auth", actor, e); return None
        except Exception as e:
            M.transport += 1; record_retryable(job, "auth", actor, e); return None
        if ra.status_code != 201:
            record_retryable(job, "auth", actor, "auth_" + str(ra.status_code)); return None
        tok = (ra.json() or {}).get("token")
        if not tok:
            record_retryable(job, "auth", actor, "auth_no_token"); return None
        actor.tok = tok; actor.reauths += 1; M.s401 += 1
        t = time.monotonic()
        try:
            r = await guard(actor.search_op(nl, job["city"], job["state"], job.get("zip", "")), "search", sd)
        except WatchdogTimeout as e:
            M.watchdog += 1; record_retryable(job, "search", actor, e); return None
        except Exception as e:
            M.transport += 1; record_retryable(job, "search", actor, e); return None
    M.primary_search += 1; actor.searches += 1; M.slat.append(time.monotonic() - t)
    if r.status_code in (403, 429):
        if r.status_code == 429: M.s429 += 1
        else: M.s403 += 1
        actor.retry_after = parse_retry_after(r.headers)
        record_retryable(job, "search-" + str(r.status_code), actor, "blocked")
        return "quarantined_" + str(r.status_code)
    if r.status_code == 401:
        record_retryable(job, "search-401persist", actor, "401_persist"); return None
    if r.status_code != 200:
        record_retryable(job, "search-http" + str(r.status_code), actor, "non200_incomplete"); return None
    try: j = r.json()
    except Exception as e:
        record_retryable(job, "search-json", actor, e); return None
    # ---- classify primary; bounded remediation on non-exact suggestion ----
    terminal = await _handle_search(actor, job, j, old, new, sd, pd_, ctl, is_remediation=False)
    if terminal:
        return terminal
    if j.get("validationResult") == "AddressSuggestions" and (M.remediation_search < rem_cap * max(1, M.total_search())):
        cand = _candidate_line(j, job["line1"])
        if cand:
            await ctl.gate(); t = time.monotonic()
            try:
                rr = await guard(actor.search_op(cand, job["city"], job["state"], job.get("zip", "")), "search", sd)
            except WatchdogTimeout as e:
                M.watchdog += 1; record_retryable(job, "remediation", actor, e); M.unresolved += 1; return None
            except Exception as e:
                M.transport += 1; record_retryable(job, "remediation", actor, e); M.unresolved += 1; return None
            M.remediation_search += 1; actor.searches += 1; M.slat.append(time.monotonic() - t)
            if rr.status_code in (403, 429):
                if rr.status_code == 429: M.s429 += 1
                else: M.s403 += 1
                actor.retry_after = parse_retry_after(rr.headers); record_retryable(job, "remediation-" + str(rr.status_code), actor, "blocked")
                return "quarantined_" + str(rr.status_code)
            if rr.status_code == 200:
                try: rj = rr.json()
                except Exception:
                    if not actor.made_final: M.unresolved += 1
                    return None
                term2 = await _handle_search(actor, job, rj, old, new, sd, pd_, ctl, is_remediation=True)
                if term2: return term2
                if not actor.made_final: M.unresolved += 1
                return None
            if not actor.made_final: M.unresolved += 1
            return None
    if not actor.made_final: M.unresolved += 1
    return None


async def _handle_search(actor, job, j, old, new, sd, pd_, ctl, is_remediation):
    """Classify one search payload; may fire ONE product call. Records a final if terminal.
    Returns a quarantine reason string if a 403/429 hit the product call, else None. Does
    NOT increment M.unresolved (caller decides), except it records finals directly."""
    ad_obj = j.get("address") if isinstance(j.get("address"), dict) else {}
    tech = (j.get("techType") or "").upper()
    outcome = {"FIBER": "FIBER", "COPPER": "COPPER", "CABLE": "CABLE"}.get(tech, "") or \
        ("NOFIBER" if j.get("validationResult") == "AddressUnserviceableInTerritory" else
         ("UNRESOLVED" if j.get("validationResult") == "AddressSuggestions" else "NOMATCH"))
    bs = str(ad_obj.get("billingStatus") or "").strip().upper()
    exist = True if bs in old else (False if bs in new else None)
    det = {"techType": tech, "dfAddressId": j.get("dfAddressId"), "existing_customer": exist,
           "customer_mapping_verified": exist is not None,
           "exactMatch": j.get("exactMatch") if isinstance(j.get("exactMatch"), bool) else None,
           "success": j.get("success"), "uqualProvisioningResult": j.get("uqualProvisioningResult"),
           "maxQual": j.get("maxQual"), "validationResult": j.get("validationResult"), "_raw_search": j}
    if not is_remediation:
        if det["exactMatch"] is True: M.exact_match += 1
        else: M.non_exact += 1
    enr = None
    if outcome == "FIBER" and det["exactMatch"] is True and det["success"] is True and det["dfAddressId"] and det["uqualProvisioningResult"] and not (exist is True and det["customer_mapping_verified"]):
        await ctl.gate(); tp = time.monotonic()
        try:
            rr = await guard(actor.product_op(det), "product", pd_)
            M.product += 1; M.plat.append(time.monotonic() - tp)
            if rr.status_code in (403, 429):
                if rr.status_code == 429: M.s429 += 1
                else: M.s403 += 1
                actor.retry_after = parse_retry_after(rr.headers)
                record_retryable(job, "product-" + str(rr.status_code), actor, "blocked")
                return "quarantined_" + str(rr.status_code)
            enr = {**rr.json(), "_fl_transport": {"http_status": 200}} if rr.status_code == 200 else {"fl_fetch_error": "http_" + str(rr.status_code)}
        except WatchdogTimeout as e:
            M.watchdog += 1; record_retryable(job, "product", actor, e); return None
        except Exception as e:
            M.transport += 1; record_retryable(job, "product", actor, e); return None
    cls = classify_final(outcome, det, enr)
    if cls in COUNTED or cls in OTHER_DURABLE:
        ev = {"t": time.monotonic() - actor.t0,
              "evidence_sha256": hashlib.sha256(json.dumps(j, sort_keys=True).encode()).hexdigest()}
        if cls == "SERVICEABLE_FIBER" and os.environ.get("FL_CAPTURE_PLANS") == "1":   # self-contained deliverable row
            ev["lead"] = {"line1": job.get("line1"), "city": job.get("city"), "state": job.get("state"),
                          "zip": job.get("zip"), "lat": job.get("lat"), "lon": job.get("lon"),
                          "county": job.get("county"), "sent_line1": nl,
                          "exactMatch": det.get("exactMatch"), "maxQual": det.get("maxQual"),
                          "plans": deliverable_plans(enr)}
        record_final(job["ck"], cls, actor, ev)
        actor.made_final = True
    else:
        # cls is None. Two very different cases the OLD metric wrongly lumped into "unresolved":
        #  (a) a fiber-exact whose PRODUCT/enrichment call did not complete -> INCOMPLETE_RETRYABLE
        #      (infrastructure; the workflow did not finish; retry — never a rejection).
        #  (b) a real completed HTTP-200 carrier answer that is simply not a deliverable lead
        #      (non-exact / nomatch / suggestions / no populated plan) -> COMPLETE_REJECTED,
        #      a finalized lead-eligibility decision (NOT proof of no network service).
        fiber_exact = (outcome == "FIBER" and det["exactMatch"] is True and det["success"] is True
                       and det["dfAddressId"] and det["uqualProvisioningResult"])
        product_failed = fiber_exact and isinstance(enr, dict) and bool(enr.get("fl_fetch_error"))
        if product_failed:
            record_retryable(job, "product_incomplete", actor, "product_failed_before_complete")
            actor.made_final = True
        else:
            # D1 FIX (classifier v2, 2026-09-10): distinguish "asked badly" from "the answer is no".
            if not fiber_exact:
                # search-stage NON-DEFINITIVE (NOMATCH / AddressSuggestions / non-exact) -> UNRESOLVED, retryable
                record_retryable(job, "unresolved_search_" + str(det.get("validationResult") or outcome), actor, "non_definitive")
                actor.made_final = True
            elif job["ck"] in M.seen:
                M.dups += 1
            else:
                # fiber-exact whose product READ FINE but populated no priced plan -> a real completed
                # non-lead answer (NOT a "we asked badly" case). Retained as COMPLETE_REJECTED.
                M.seen[job["ck"]] = "COMPLETE_REJECTED"; M.rejected += 1
                M.by_class["COMPLETE_REJECTED"] = M.by_class.get("COMPLETE_REJECTED", 0) + 1
                if M.ledger_fp is not None:
                    reason = det.get("validationResult") or outcome
                    M.ledger_fp.write(json.dumps({"ck": job["ck"], "class": "COMPLETE_REJECTED", "counted": False,
                                                  "reason": reason, "actor": actor.idx, "plan": actor.plan,
                                                  "t": round(time.monotonic() - actor.t0, 3)}) + "\n")
                    M.ledger_fp.flush()
                actor.made_final = True
    return None


async def actor_task(actor, q, billing, deadline, ad, sd, pd_, ctl, t0, rem_cap):
    actor.searches = 0; actor.reauths = 0; actor.wd_streak = 0; actor.retry_after = None
    actor.t0 = t0; actor.born = time.monotonic()
    reason = None
    while time.monotonic() < deadline and actor.alive:
        try: job = q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.02); continue
        actor.made_final = False
        out = await run_one(actor, job, billing, ad, sd, pd_, ctl, rem_cap)
        if out:
            reason = out; break
    if reason is None:
        reason = "deadline"
    actor.alive = False
    if reason.startswith("quarantined"): M.quarantined += 1
    elif reason == "watchdog_stalled": M.watchdog_stalled += 1
    else: M.completed += 1
    M.active -= 1
    try:
        if actor.s: await actor.s.close()
    except Exception: pass
    M.per_actor.append({"actor": actor.idx, "plan": actor.plan, "searches": actor.searches,
                        "reauths": actor.reauths, "lifetime_s": round(time.monotonic() - actor.born, 2),
                        "terminal": reason, "retry_after": actor.retry_after})


def pct(xs, p):
    if not xs: return None
    s = sorted(xs); return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)


def cold_reopen(ledger_path):
    seen = set(); counted = other = fdups = 0
    try:
        for line in open(ledger_path):
            line = line.strip()
            if not line: continue
            r = json.loads(line); ck = r.get("ck")
            if ck in seen: fdups += 1; continue
            seen.add(ck)
            if r.get("counted"): counted += 1
            else: other += 1
    except FileNotFoundError:
        pass
    return {"reopened_unique": len(seen), "reopened_counted": counted, "reopened_other": other,
            "reopened_file_dups": fdups,
            "matches_memory": bool(len(seen) == len(M.seen) and counted == M.counted and other == (M.other_durable + M.rejected) and fdups == 0)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addrs"); ap.add_argument("ledger"); ap.add_argument("--actors", type=int, default=32)
    ap.add_argument("--hold", type=int, default=60); ap.add_argument("--label", default="v2")
    ap.add_argument("--stagger", type=float, default=0.05)
    ap.add_argument("--auth-deadline", type=float, default=20.0)
    ap.add_argument("--search-deadline", type=float, default=12.0)
    ap.add_argument("--product-deadline", type=float, default=25.0)
    ap.add_argument("--retry-ledger", default=None); ap.add_argument("--remediation-cap", type=float, default=0.15)
    ap.add_argument("--canary", default=None, help="approved end-to-end qualification canary (JSON path); required to start production")
    a = ap.parse_args()
    _canary = json.load(open(a.canary)) if (getattr(a, "canary", None) and os.path.exists(a.canary)) else {}
    if not ReadinessGate().open_if_ready(_canary):
        print("FINAL " + json.dumps({"label": a.label, "fatal": "readiness gate: an approved end-to-end qualification canary (auth 201 -> search 200 -> product 200 -> terminal) is required; auth 201 alone cannot reopen production"}), flush=True); return
    cfg = json.load(open(os.path.join(os.environ["FL_CONFIG"], "billing_config.json"))).get("billing_status_mapping", {})
    billing = ({v.strip().upper() for v in cfg.get("existing_values", [])}, {v.strip().upper() for v in cfg.get("new_values", [])})
    _rjson = os.path.join(HERE, "residential.json")
    if not os.path.exists(_rjson):
        _rjson = os.path.join(HERE, "residential.json.example")  # offline / clean-clone fallback
    pools = [p for p in json.load(open(_rjson))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
    raw = [json.loads(l) for l in open(a.addrs)]
    # GLOBAL DEDUP before any HTTP: exactly one actor may own a canonical address.
    seen_ck = set(); addrs = []
    for r in raw:
        ck = r.get("ck")
        if ck in seen_ck: continue
        seen_ck.add(ck); addrs.append(r)
    dup_input = len(raw) - len(addrs)
    q = asyncio.Queue()
    for r in addrs: q.put_nowait(r)
    M.ledger_fp = open(a.ledger, "w"); M.retry_fp = open(a.retry_ledger or (a.ledger + ".retry"), "w")
    # ---- PREWARM (staggered, US-verified, auth-once; excluded from active timing) ----
    tpw = time.monotonic()
    actors = [Actor(i, i % len(pools)) for i in range(a.actors)]
    async def pw(ac, i): await asyncio.sleep(i * a.stagger); return await prewarm(ac, pools, a.auth_deadline)
    warmed = await asyncio.gather(*[pw(ac, i) for i, ac in enumerate(actors)])
    live = [ac for ac, ok in zip(actors, warmed) if ok]
    print("PREWARM " + json.dumps({"label": a.label, "requested": a.actors, "warmed": len(live),
          "prewarm_auth": M.prewarm_auth, "prewarm_s": round(time.monotonic() - tpw, 1),
          "auth_p50": pct(M.alat, 0.5), "input_addrs": len(addrs)}), flush=True)
    if not live:
        print("FINAL " + json.dumps({"label": a.label, "fatal": "no actors warmed", "requested": a.actors, "prewarm_auth": M.prewarm_auth}), flush=True)
        print("LEVEL_DONE", flush=True); return
    # ---- ACTIVE RUN ----
    ctl = AdmissionController(); mon = asyncio.create_task(ctl.monitor())
    t0 = time.monotonic(); deadline = t0 + a.hold
    tasks = [asyncio.create_task(actor_task(ac, q, billing, deadline, a.auth_deadline, a.search_deadline, a.product_deadline, ctl, t0, a.remediation_cap)) for ac in live]
    last = 0; wi = 0; backlog0 = q.qsize()
    while time.monotonic() < deadline:
        await asyncio.sleep(10); now = time.monotonic(); wi += 1
        print("WIN " + json.dumps({"label": a.label, "w": wi, "t": round(now - t0), "counted_10s": M.counted - last,
              "counted_s": round((M.counted - last) / 10.0, 2), "active": M.active, "quarantined": M.quarantined,
              "watchdog_stalled": M.watchdog_stalled, "completed": M.completed, "s401": M.s401, "s403": M.s403,
              "s429": M.s429, "watchdog": M.watchdog, "transport": M.transport, "throttle_delay": round(ctl.delay, 2),
              "backlog": q.qsize(), "cum_counted": M.counted}), flush=True)
        last = M.counted
    ctl.stop(); mon.cancel()
    for t in tasks: t.cancel()
    await asyncio.gather(*tasks, mon, return_exceptions=True)
    active_s = time.monotonic() - t0
    M.ledger_fp.close(); M.retry_fp.close()
    recon = cold_reopen(a.ledger)
    # In-flight-at-cutoff: an address whose actor was cancelled mid-workflow at the deadline
    # is NOT lost — it is retryable/unresolved. Reconcile it so nothing is unaccounted.
    in_flight = max(0, M.consumed - (M.counted + M.other_durable + M.rejected + M.unresolved + M.retryable + M.dups))
    M.retryable += in_flight
    accounted = M.counted + M.other_durable + M.rejected + M.unresolved + M.retryable + M.dups
    in_run_calls = M.reauth + M.total_search() + M.product
    # CORRECTED metric (directive): a durable decision = COMPLETE_DELIVERABLE + COMPLETE_REJECTED.
    deliverable = M.by_class.get("SERVICEABLE_FIBER", 0)
    rejected_total = (M.counted - deliverable) + M.other_durable + M.rejected   # nonfiber/existing/oot/no-plan/pending + non-exact/nomatch
    durable = deliverable + rejected_total
    incomplete = M.retryable + M.unresolved
    ts = M.total_search()
    final = {"label": a.label, "actors_warmed": len(live), "active_s": round(active_s, 1),
             # ---- CORRECTED success metric: new unique durable (deliverable + rejected) / active s ----
             "durable_decisions": durable, "durable_per_s": round(durable / active_s, 3) if active_s else 0,
             "complete_deliverable": deliverable, "complete_rejected": rejected_total,
             "incomplete_retryable": incomplete,
             "corrected_decisions_per_search": round(durable / max(1, ts), 4),
             "deliverable_per_s": round(deliverable / active_s, 3) if active_s else 0,
             # ---- OLD (deliverable-lead-only) metric, for the OLD/NEW comparison ----
             "rate_counted_per_s": round(M.counted / active_s, 3) if active_s else 0,
             "counted_final_decisions": M.counted, "counted_per_actor": round(M.counted / len(live), 3),
             "old_decisions_per_search": round(M.counted / max(1, ts), 4),
             "by_class": M.by_class, "other_durable_not_counted": M.other_durable,
             "unresolved": M.unresolved, "retryable": M.retryable,
             "prewarm_auth": M.prewarm_auth, "reauth_calls": M.reauth,
             "primary_searches": M.primary_search, "remediation_searches": M.remediation_search, "product_calls": M.product,
             "remediation_fraction": round(M.remediation_search / max(1, M.total_search()), 3),
             "exact_matches": M.exact_match, "non_exact": M.non_exact,
             "exact_fraction": round(M.exact_match / max(1, M.exact_match + M.non_exact), 3),
             "input_dedup_removed": dup_input, "in_flight_at_cutoff": in_flight, "usps_normalized": True,
             "calls_per_counted_decision": round(in_run_calls / M.counted, 1) if M.counted else None,
             "auth_per_1000_addr": round(1000.0 * (M.prewarm_auth + M.reauth) / max(1, M.consumed), 1),
             "s401": M.s401, "s403": M.s403, "s429": M.s429, "watchdog_expiries": M.watchdog, "transport": M.transport,
             "search_p50": pct(M.slat, 0.50), "search_p95": pct(M.slat, 0.95), "search_p99": pct(M.slat, 0.99),
             "product_p50": pct(M.plat, 0.50), "product_p95": pct(M.plat, 0.95), "product_p99": pct(M.plat, 0.99),
             "auth_p50": pct(M.alat, 0.50), "auth_p95": pct(M.alat, 0.95),
             "actors_active_end": M.active, "actors_completed": M.completed, "actors_quarantined": M.quarantined,
             "actors_watchdog_stalled": M.watchdog_stalled,
             "consumed": M.consumed, "accounted": accounted, "unaccounted": M.consumed - accounted,
             "dups": M.dups, "cold_reopen": recon, "backlog_start": backlog0, "backlog_end": q.qsize(),
             "searches_before_restriction": sorted([p["searches"] for p in M.per_actor if str(p["terminal"] or "").startswith("quarantined")]),
             "per_actor_sample": M.per_actor[:40]}
    print("FINAL " + json.dumps(final), flush=True)
    print("LEVEL_DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
