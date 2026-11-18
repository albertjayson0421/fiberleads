"""Post-sweep pricing/evidence capture for the SERVICEABLE_FIBER leads. For each lead,
re-run search + product through a fresh US-sticky DataImpulse exit, re-confirm exact +
fiber + not-existing, and capture the REAL priced fiber plans (name + standard/promo) with
fresh reproducible evidence (observed_at_utc, response_sha256). This is the carrier
invariant: a shipped lead carries the populated fiber plan WITH pricing. Uses the proven
extraction (enr_probe validated: Gig $39.99, 2Gig $59.99 on real Iowa enr).

Usage: capture_pricing.py <serviceable_leads.jsonl> <priced_out.jsonl> [--threads 48]
Runs AFTER the sweep (pool is free). A lead that no longer re-confirms is written with
status != PRICED and kept out of the deliverable (not silently dropped)."""
import os, sys, json, hashlib, base64, time, random, threading, queue
from urllib.parse import quote
_INP = os.path.abspath(sys.argv[1]); _OUT = os.path.abspath(sys.argv[2])   # before chdir
HERE = "/Users/masani/FLEET_5K/merge"; sys.path[:0] = [HERE, os.path.join(HERE, "engine")]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
os.chdir(os.path.join(HERE, "engine"))
from curl_cffi import requests as cr
import product_stage as PS, classifier as CL
sys.path.insert(0, HERE)
from carrier_uqual import abbreviate

BASE = "https://buy.gokinetic.com"; SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"; PLANS = "/api/v2/windstream/product-availability"
XHR = {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
       "Content-Type": "application/json", "Accept": "application/json"}
BASIC = base64.b64encode(next(l.strip() for l in open(os.path.join(os.environ["FL_CONFIG"], "uqual.txt")) if l.strip()).encode()).decode()
pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
bc = json.load(open(os.path.join(HERE, "billing_config.json"))).get("billing_status_mapping", {})
EXIST = {v.strip().upper() for v in bc.get("existing_values", [])}
NEWC = {v.strip().upper() for v in bc.get("new_values", [])}

def arg(name, d):
    return type(d)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d
INP, OUT = _INP, _OUT; THREADS = arg("--threads", 48)

def exit_url():
    p = random.choice(pools)
    u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

def plans_of(enr):
    out = {}
    names = set(PS.priced_fiber_bundles(enr))
    for b, _t in CL._iter_bundles(enr):
        n = CL._bundle_name(b)
        if n in names and n not in out:
            a = b.get("attributes") if isinstance(b.get("attributes"), dict) else b
            std = PS._money(a.get("standardPrice")); pro = PS._money(a.get("promoPrice"))
            out[n] = {"name": n, "standard": str(std) if std is not None else "", "promo": str(pro) if pro is not None else ""}
    return list(out.values())

PER_EXIT = 16   # reuse one authed exit for up to 16 leads (stay under the ~21-search/exit ceiling)

def new_authed_session(tries=6):
    """Mint fresh exits until one auths (201). Returns (session, token) or (None, None)."""
    for _ in range(tries):
        px = exit_url(); s = cr.Session(impersonate="chrome124", proxies={"http": px, "https": px})
        try:
            r = s.post(BASE + SESSION, headers={**XHR, "Authorization": "Basic " + BASIC}, data="{}", timeout=30)
            if r.status_code in (200, 201):
                tok = r.json().get("token") or (r.json().get("data") or {}).get("token")
                if tok:
                    return s, tok
            s.close()
        except Exception:
            try: s.close()
            except Exception: pass
    return None, None

def price_one(s, tok, lead):
    """One lead on an already-authed session. Returns (result, rotate) — rotate=True means
    this exit hit a 403/429 and must be replaced (the lead is retryable, not decided)."""
    try:
        line1 = abbreviate(str(lead["line1"]).upper())
        sr = s.post(BASE + SEARCH, headers={**XHR, "Authorization": "Bearer " + tok},
                    data=json.dumps({"addressLine1": line1, "city": lead["city"], "state": lead["state"], "zip": lead["zip"]}), timeout=30)
        if sr.status_code in (403, 429):
            return None, True
        if sr.status_code == 401:
            return None, True   # token died; rotate (fresh auth)
        j = sr.json()
        bs = str((j.get("address") or {}).get("billingStatus") or j.get("billingStatus") or "").strip().upper()
        exist = True if bs in EXIST else (False if bs in NEWC else None)
        if exist is True:
            return {**lead, "status": "EXISTING_CUSTOMER"}, False
        if not (j.get("exactMatch") is True and (j.get("maxQual") or "").upper().find("FIBER") >= 0 and j.get("dfAddressId") and j.get("uqualProvisioningResult")):
            return {**lead, "status": "NO_EXACT_FIBER", "maxQual": j.get("maxQual")}, False
        pr = s.post(BASE + PLANS, headers={**XHR, "Authorization": "Bearer " + tok},
                    data=json.dumps({"dfAddressId": j["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": j["uqualProvisioningResult"]}), timeout=30)
        if pr.status_code in (403, 429):
            return None, True
        enr = pr.json()
        plans = plans_of(enr)
        if not plans:
            return {**lead, "status": "NO_PRICED_PLAN"}, False
        sha = hashlib.sha256((json.dumps(j, sort_keys=True) + json.dumps(enr, sort_keys=True)).encode()).hexdigest()
        return {**lead, "status": "PRICED", "sent_line1": line1, "exactMatch": True, "maxQual": j.get("maxQual"),
                "billingStatus": bs, "plans": plans, "response_sha256": sha,
                "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, False
    except Exception as e:
        return {**lead, "status": "ERR_%s" % type(e).__name__}, False

leads = [json.loads(l) for l in open(INP) if l.strip()]
q = queue.Queue(); [q.put(x) for x in leads]
outq = queue.Queue(); done = [0]; lock = threading.Lock(); t0 = time.time()
def worker():
    s = tok = None; used = 0
    while True:
        try: lead = q.get_nowait()
        except queue.Empty:
            break
        # (re)authenticate a reused exit as needed
        if s is None or used >= PER_EXIT:
            if s is not None:
                try: s.close()
                except Exception: pass
            s, tok = new_authed_session(); used = 0
            if s is None:
                outq.put({**lead, "status": "AUTH_403"})  # pool depleted right now; retryable
                with lock: done[0] += 1
                continue
        res, rotate = price_one(s, tok, lead)
        used += 1
        if rotate:
            try: s.close()
            except Exception: pass
            s = tok = None
            lead["_att"] = lead.get("_att", 0) + 1
            if lead["_att"] < 4:
                q.put(lead)      # blocked exit, not a decision — retry on a fresh exit
            else:
                outq.put({**lead, "status": "BLOCKED_403"})
                with lock: done[0] += 1
            continue
        outq.put(res)
        with lock:
            done[0] += 1
            if done[0] % 500 == 0:
                print("  captured %d/%d (%.0fs)" % (done[0], len(leads), time.time() - t0), flush=True)
    if s is not None:
        try: s.close()
        except Exception: pass
ths = [threading.Thread(target=worker) for _ in range(THREADS)]
[t.start() for t in ths]; [t.join() for t in ths]
import collections
st = collections.Counter(); priced = 0
with open(OUT, "w") as f:
    while not outq.empty():
        r = outq.get(); st[r["status"]] += 1
        if r["status"] == "PRICED": priced += 1
        f.write(json.dumps(r) + "\n")
print("\nPRICING_DONE %d leads in %.1f min -> %s" % (len(leads), (time.time() - t0) / 60, OUT))
print("  PRICED (shippable): %d" % priced)
print("  status breakdown:", dict(st.most_common()))
