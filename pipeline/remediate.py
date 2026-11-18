"""Remediation re-sweep of the UNRESOLVED (AddressNotFound/Suggestions) addresses the
single-shot fleet sweep gave up on. For each address, try the abbreviated form AND the
engine's address variants (spell-out, appended street types like '12'->'12 ST', candidate
suggestions) until Kinetic returns an exact match — then classify serviceable/existing/
not-serviceable with the priced-plan gate. Exit-reuse to avoid AUTH_403.

Usage: remediate.py <in.jsonl> <out.jsonl> [--threads 32]
Outputs one row per ck: status in {SERVICEABLE, EXISTING, NOT_SERVICEABLE, STILL_NOMATCH, UNRESOLVED}."""
import os, sys, json, hashlib, base64, time, random, threading, queue, collections
from urllib.parse import quote
_INP = os.path.abspath(sys.argv[1]); _OUT = os.path.abspath(sys.argv[2])
HERE = "/Users/masani/FLEET_5K/merge"
if not os.path.isdir(HERE): HERE = os.path.expanduser("~/merge_sr")   # on a VM
sys.path[:0] = [HERE, os.path.join(HERE, "engine")]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
os.chdir(os.path.join(HERE, "engine"))
from curl_cffi import requests as cr
import product_stage as PS, classifier as CL
from carrier_uqual import abbreviate, spell_out, variants

BASE = "https://buy.gokinetic.com"; SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"; PLANS = "/api/v2/windstream/product-availability"
XHR = {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
       "Content-Type": "application/json", "Accept": "application/json"}
BASIC = base64.b64encode(next(l.strip() for l in open(os.path.join(os.environ["FL_CONFIG"], "uqual.txt")) if l.strip()).encode()).decode()
pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
bc = json.load(open(os.path.join(HERE, "billing_config.json"))).get("billing_status_mapping", {})
EXIST = {v.strip().upper() for v in bc.get("existing_values", [])}; NEWC = {v.strip().upper() for v in bc.get("new_values", [])}
def arg(n, d): return type(d)(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
THREADS = arg("--threads", 32)

def exit_url():
    p = random.choice(pools); u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

def candidates(raw):
    """Ordered, deduped address spellings to try (abbreviated first, then variants)."""
    up = str(raw).upper().replace(".", " ").replace(",", " "); up = " ".join(up.split())
    out = []
    for c in [abbreviate(up), up, spell_out(up)] + list(variants(abbreviate(up))) + list(variants(up)):
        c = (c or "").strip()
        if c and c not in out: out.append(c)
    return out[:5]

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

def search(s, tok, line1, city, state, zc):
    r = s.post(BASE + SEARCH, headers={**XHR, "Authorization": "Bearer " + tok},
               data=json.dumps({"addressLine1": line1, "city": city, "state": state, "zip": zc}), timeout=30)
    return r

def resolve(s, tok, lead):
    """Try candidates until exact match; classify. Returns (status, extra, rotate)."""
    for line1 in candidates(lead["line1"]):
        try:
            sr = search(s, tok, line1, lead["city"], lead["state"], lead["zip"])
        except Exception:
            return "UNRESOLVED", {}, True
        if sr.status_code in (401, 403, 429): return "UNRESOLVED", {}, True
        try: j = sr.json()
        except Exception: continue
        if j.get("exactMatch") is not True:
            continue   # try next variant
        bs = str((j.get("address") or {}).get("billingStatus") or j.get("billingStatus") or "").strip().upper()
        exist = True if bs in EXIST else (False if bs in NEWC else None)
        tech = (j.get("maxQual") or "").upper()
        if "FIBER" not in tech:
            return "NOT_SERVICEABLE", {"sent": line1, "why": "exact_nonfiber"}, False
        if exist is True:
            return "EXISTING", {"sent": line1}, False
        if not (j.get("dfAddressId") and j.get("uqualProvisioningResult")):
            return "NOT_SERVICEABLE", {"sent": line1, "why": "exact_fiber_no_prov"}, False
        # priced-plan gate
        try:
            pr = s.post(BASE + PLANS, headers={**XHR, "Authorization": "Bearer " + tok},
                        data=json.dumps({"dfAddressId": j["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": j["uqualProvisioningResult"]}), timeout=30)
        except Exception:
            return "UNRESOLVED", {}, True
        if pr.status_code in (403, 429): return "UNRESOLVED", {}, True
        enr = pr.json(); plans = PS.priced_fiber_bundles(enr)
        if plans:
            def money(nm):
                for b, _t in CL._iter_bundles(enr):
                    if CL._bundle_name(b) == nm:
                        a = b.get("attributes") if isinstance(b.get("attributes"), dict) else b
                        return str(PS._money(a.get("standardPrice")) or "")
                return ""
            return "SERVICEABLE", {"sent": line1, "plans": [{"name": n, "standard": money(n)} for n in plans],
                                   "maxQual": j.get("maxQual"), "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, False
        return "NOT_SERVICEABLE", {"sent": line1, "why": "fiber_no_priced_plan"}, False
    return "STILL_NOMATCH", {}, False   # no variant matched

leads = [json.loads(l) for l in open(_INP) if l.strip()]
q = queue.Queue(); [q.put(x) for x in leads]
outf = open(_OUT, "w"); wlock = threading.Lock(); dlock = threading.Lock(); done = [0]; t0 = time.time()
st = collections.Counter(); PER_EXIT = 3
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
                (q.put(lead) if lead["_a"] < 5 else _write({**lead, "status": "UNRESOLVED"}))
                continue
        status, extra, rotate = resolve(s, tok, lead); used += 1
        if rotate:
            try: s.close()
            except Exception: pass
            s = tok = None
            lead["_a"] = lead.get("_a", 0) + 1
            if lead["_a"] < 5: q.put(lead)
            else: _write({**lead, "status": "UNRESOLVED"})
            continue
        _write({**{k: lead[k] for k in ("ck", "line1", "city", "state", "zip", "lat", "lon", "county") if k in lead}, "status": status, **extra})
def _write(row):
    with wlock: outf.write(json.dumps(row) + "\n"); outf.flush()
    with dlock:
        st[row["status"]] += 1; done[0] += 1
        if done[0] % 1000 == 0: print("  %d/%d (%.0fs) %s" % (done[0], len(leads), time.time() - t0, dict(st.most_common())), flush=True)
ths = [threading.Thread(target=worker) for _ in range(THREADS)]
[t.start() for t in ths]; [t.join() for t in ths]; outf.close()
print("REMEDIATE_DONE %d in %.1f min -> %s" % (len(leads), (time.time() - t0) / 60, _OUT))
print("status:", dict(st.most_common()))
