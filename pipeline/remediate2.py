"""Remediation v2 — fixes the real bug: a garbage city ('Not stated') sent verbatim.
Ladder per address, escalating only on a miss: (1) CITY-FIX (USPS city from ZIP when the
city is blank/'Not stated'), (2) WITHOUT-CITY, (3) carrier candidate-list mining on
AddressSuggestions, (4) street-type/spell variants. On an exact match, apply the priced-
fibre-plan gate (no call-to-order/pending/no-plan). AddressNotFound is NEVER terminal until
the whole ladder is exhausted -> only then STILL_NOMATCH (genuine no-record).

Usage: remediate2.py <in.jsonl> <out.jsonl> [--threads 32]"""
import os, sys, json, hashlib, base64, time, random, threading, queue, collections
from urllib.parse import quote
_INP = os.path.abspath(sys.argv[1]); _OUT = os.path.abspath(sys.argv[2])
import flpaths
flpaths.ensure_engine_on_path()   # was: HERE=/Users/masani/FLEET_5K/merge sys.path + os.chdir
from curl_cffi import requests as cr
import product_stage as PS, classifier as CL
from carrier_uqual import abbreviate, spell_out, variants
_RUNTIME = os.path.dirname(flpaths.CONFIG)   # runtime/ (holds residential.json[.example])
BASE = "https://buy.gokinetic.com"; SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"; PLANS = "/api/v2/windstream/product-availability"
XHR = {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
       "Content-Type": "application/json", "Accept": "application/json"}
_uq = os.path.join(flpaths.CONFIG, "uqual.txt")
if not os.path.exists(_uq): _uq = os.path.join(flpaths.CONFIG, "uqual.txt.example")   # clean-clone fallback
BASIC = base64.b64encode(next(l.strip() for l in open(_uq) if l.strip() and not l.strip().startswith("#")).encode()).decode()
_rj = os.path.join(_RUNTIME, "residential.json")
if not os.path.exists(_rj): _rj = os.path.join(_RUNTIME, "residential.json.example")   # clean-clone fallback
pools = [p for p in json.load(open(_rj))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
bc = json.load(open(os.path.join(flpaths.CONFIG, "billing_config.json"))).get("billing_status_mapping", {})
EXIST = {v.strip().upper() for v in bc.get("existing_values", [])}; NEWC = {v.strip().upper() for v in bc.get("new_values", [])}
# zip -> USPS city (works on VM too: try local paths)
ZC = {}
for p in ("/Users/masani/iowa_pull/zip_city_final.json", os.path.expanduser("~/zip_city_final.json")):
    if os.path.exists(p): ZC = json.load(open(p)); break
BADCITY = {"", "NOT STATED", "UNKNOWN", "UNINCORPORATED"}
def arg(n, d): return type(d)(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
THREADS = arg("--threads", 32)

def exit_url():
    p = random.choice(pools); u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

def city_for(lead):
    c = str(lead.get("city") or "").strip()
    if c.upper() in BADCITY:
        return ZC.get(str(lead.get("zip") or "").strip()[:5], c)
    return c

def forms(lead):
    """Ordered (addressLine1, city) attempts."""
    raw = str(lead["line1"]).upper().replace(".", " ").replace(",", " "); raw = " ".join(raw.split())
    fc = city_for(lead); out = []
    def add(a, c):
        a = (a or "").strip()
        if a and (a, c) not in out: out.append((a, c))
    add(abbreviate(raw), fc)                 # 1. abbreviated + fixed city  (THE lever)
    add(abbreviate(raw), "")                 # 2. without city
    add(spell_out(raw) or raw, fc)           # 3. spelled-out + fixed city
    for v in variants(abbreviate(raw)): add(v, fc)   # 4. street-type variants
    for v in variants(raw): add(v, "")
    return out[:6]

def new_session(tries=6):
    for _ in range(tries):
        px = exit_url(); s = cr.Session(impersonate="chrome124", proxies={"http": px, "https": px})
        try:
            r = s.post(BASE + SESSION, headers={**XHR, "Authorization": "Basic " + BASIC}, data="{}", timeout=30)
            if r.status_code in (200, 201):
                tok = r.json().get("token") or (r.json().get("data") or {}).get("token")
                if tok: return s, tok
            s.close()
        except Exception:
            try: s.close()
            except Exception: pass
    return None, None

def classify_exact(s, tok, j, sent):
    bs = str((j.get("address") or {}).get("billingStatus") or j.get("billingStatus") or "").strip().upper()
    exist = True if bs in EXIST else (False if bs in NEWC else None)
    tech = (j.get("maxQual") or "").upper()
    if "FIBER" not in tech:
        return {"status": "NOT_SERVICEABLE", "sent": sent, "why": "exact_nonfiber"}
    if exist is True:
        return {"status": "EXISTING", "sent": sent}
    if not (j.get("dfAddressId") and j.get("uqualProvisioningResult")):
        return {"status": "NOT_SERVICEABLE", "sent": sent, "why": "exact_fiber_no_prov"}
    try:
        pr = s.post(BASE + PLANS, headers={**XHR, "Authorization": "Bearer " + tok},
                    data=json.dumps({"dfAddressId": j["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": j["uqualProvisioningResult"]}), timeout=30)
    except Exception:
        return {"_rotate": True}
    if pr.status_code in (403, 429): return {"_rotate": True}
    plans = PS.priced_fiber_bundles(pr.json())
    if plans:
        return {"status": "SERVICEABLE", "sent": sent, "plans": plans[:3], "maxQual": j.get("maxQual"),
                "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return {"status": "NOT_SERVICEABLE", "sent": sent, "why": "fiber_no_priced_plan"}

def mine_candidate(j, house):
    for key in ("addressSuggestions", "suggestions", "candidates", "addresses"):
        arr = j.get(key)
        if isinstance(arr, list):
            for c in arr:
                if isinstance(c, dict):
                    l1 = str(c.get("addressLine1") or c.get("line1") or "").strip()
                    if l1 and l1.split()[:1] == [house]:
                        return l1, str(c.get("city") or "").strip(), str(c.get("zip") or c.get("postalCode") or "").strip()[:5]
    return None

def resolve(s, tok, lead):
    house = str(lead["line1"]).split()[0] if lead.get("line1") else ""
    for line1, city in forms(lead):
        try:
            sr = s.post(BASE + SEARCH, headers={**XHR, "Authorization": "Bearer " + tok},
                        data=json.dumps({"addressLine1": line1, "city": city, "state": lead["state"], "zip": lead["zip"]}), timeout=30)
        except Exception:
            return {"_rotate": True}
        if sr.status_code in (401, 403, 429): return {"_rotate": True}
        try: j = sr.json()
        except Exception: continue
        if j.get("exactMatch") is True:
            r = classify_exact(s, tok, j, line1)
            if r.get("_rotate"): return r
            return r
        if j.get("validationResult") == "AddressSuggestions":
            cand = mine_candidate(j, house)
            if cand:
                cl1, cc, cz = cand
                try:
                    sr2 = s.post(BASE + SEARCH, headers={**XHR, "Authorization": "Bearer " + tok},
                                 data=json.dumps({"addressLine1": cl1, "city": cc or city, "state": lead["state"], "zip": cz or lead["zip"]}), timeout=30)
                    j2 = sr2.json()
                    if j2.get("exactMatch") is True:
                        r = classify_exact(s, tok, j2, cl1)
                        if not r.get("_rotate"): return r
                except Exception:
                    pass
    return {"status": "STILL_NOMATCH"}

leads = [json.loads(l) for l in open(_INP) if l.strip()]
q = queue.Queue(); [q.put(x) for x in leads]
outf = open(_OUT, "w"); wlock = threading.Lock(); dlock = threading.Lock(); done = [0]; t0 = time.time(); st = collections.Counter(); PER_EXIT = 3
def _write(row):
    with wlock: outf.write(json.dumps(row) + "\n"); outf.flush()
    with dlock:
        st[row["status"]] += 1; done[0] += 1
        if done[0] % 1000 == 0: print("  %d/%d (%.0fs) %s" % (done[0], len(leads), time.time() - t0, dict(st.most_common())), flush=True)
def worker():
    s = tok = None; used = 0
    while True:
        try: lead = q.get_nowait()
        except queue.Empty: break
        if s is None or used >= PER_EXIT:
            if s is not None:
                try: s.close()
                except Exception: pass
            s, tok = new_session(); used = 0
            if s is None:
                lead["_a"] = lead.get("_a", 0) + 1
                (q.put(lead) if lead["_a"] < 5 else _write({**base(lead), "status": "UNRESOLVED"})); continue
        r = resolve(s, tok, lead); used += 1
        if r.get("_rotate"):
            try: s.close()
            except Exception: pass
            s = tok = None; lead["_a"] = lead.get("_a", 0) + 1
            (q.put(lead) if lead["_a"] < 6 else _write({**base(lead), "status": "STILL_NOMATCH"})); continue
        _write({**base(lead), **r})
def base(lead): return {k: lead[k] for k in ("ck", "line1", "city", "state", "zip", "lat", "lon", "county") if k in lead}
ths = [threading.Thread(target=worker) for _ in range(THREADS)]
[t.start() for t in ths]; [t.join() for t in ths]; outf.close()
print("REMEDIATE2_DONE %d in %.1f min -> %s" % (len(leads), (time.time() - t0) / 60, _OUT))
print("status:", dict(st.most_common()))
