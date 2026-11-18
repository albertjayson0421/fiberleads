"""Isolate the deliverable_plans stall + validate pricing extraction on a REAL enrichment.
Does a real search+product for fresh addresses until it hits an exact-fiber one, dumps the
enr shape, then runs deliverable_plans under a hard SIGALRM so a hang cannot trap us."""
import os, sys, json, hashlib, base64, signal, time, random
from urllib.parse import quote
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

def exit_url():
    p = random.choice(pools)
    u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

def deliverable_plans(enr):
    out = {}
    try:
        names = set(PS.priced_fiber_bundles(enr))
        for b, _tier in CL._iter_bundles(enr):
            n = CL._bundle_name(b)
            if n in names and n not in out:
                a = b.get("attributes") if isinstance(b.get("attributes"), dict) else b
                std = PS._money(a.get("standardPrice")); pro = PS._money(a.get("promoPrice"))
                out[n] = {"name": n, "standard": str(std) if std is not None else "", "promo": str(pro) if pro is not None else ""}
    except Exception as e:
        print("  deliverable_plans EXC:", e)
    return list(out.values())

class Timeout(Exception): pass
def _alarm(s, f): raise Timeout()
signal.signal(signal.SIGALRM, _alarm)

leads = [json.loads(l) for l in open("/Users/masani/iowa_pull/fresh500.jsonl")]
random.shuffle(leads)
found = 0
for lead in leads:
    if found >= 2: break
    px = exit_url(); s = cr.Session(impersonate="chrome124", proxies={"http": px, "https": px})
    try:
        r = s.post(BASE + SESSION, headers={**XHR, "Authorization": "Basic " + BASIC}, data="{}", timeout=30)
        tok = r.json().get("token") or r.json().get("accessToken") or (r.json().get("data") or {}).get("token")
        if not tok:
            # token may be in a header or nested; dump once
            print("auth keys:", list(r.json().keys())[:8]); s.close(); continue
        line1 = abbreviate(str(lead["line1"]).upper())
        sr = s.post(BASE + SEARCH, headers={**XHR, "Authorization": "Bearer " + tok},
                    data=json.dumps({"addressLine1": line1, "city": lead["city"], "state": lead["state"], "zip": lead["zip"]}), timeout=30)
        j = sr.json()
        if not (j.get("exactMatch") is True and (j.get("maxQual") or "").upper().find("FIBER") >= 0 and j.get("dfAddressId") and j.get("uqualProvisioningResult")):
            s.close(); continue
        pr = s.post(BASE + PLANS, headers={**XHR, "Authorization": "Bearer " + tok},
                    data=json.dumps({"dfAddressId": j["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": j["uqualProvisioningResult"]}), timeout=30)
        enr = pr.json()
        found += 1
        print("\n==== FIBER address:", line1, lead["city"], lead["zip"], "====")
        print("enr top-level keys:", list(enr.keys())[:15])
        print("priced_fiber_bundles:", PS.priced_fiber_bundles(enr))
        t0 = time.time(); signal.alarm(8)
        try:
            plans = deliverable_plans(enr)
            signal.alarm(0)
            print("deliverable_plans OK in %.3fs -> %s" % (time.time() - t0, plans))
        except Timeout:
            print("*** deliverable_plans HUNG (>8s) — this is the stall ***")
    except Exception as e:
        print("  probe err:", type(e).__name__, str(e)[:60])
    finally:
        try: s.close()
        except Exception: pass
print("\ndone, found=%d" % found)
