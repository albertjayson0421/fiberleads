"""STEP 2/3 experiment: a SINGLE STABLE well-behaved session, correct headers, no churn.
Verify US residential routing, then auth -> search -> product in ONE session on the same
route. Capture full evidence per phase (status, CF-Ray, content-type, body head, timing).
This decides: implementation-caused flag (clean session works) vs genuine access control
(clean session still restricted)."""
import os, sys, json, time, base64, hashlib, random
HERE = os.path.expanduser("~/merge_sr"); os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
from curl_cffi import requests as cr
BASE = "https://buy.gokinetic.com"
SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"
PRODUCT = "/api/v2/windstream/product-availability"
BASIC = base64.b64encode(next(l.strip() for l in open(os.path.join(os.environ["FL_CONFIG"], "uqual.txt")) if l.strip()).encode()).decode()
pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]

# Correct browser-equivalent headers (curl_cffi chrome124 supplies UA / sec-ch-ua / accept-encoding)
def hdr(auth):
    return {"Origin": BASE, "Referer": BASE + "/", "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
            "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json", "Authorization": auth}

def ev(r):
    b = ""
    try: b = r.text[:160]
    except Exception: pass
    return {"status": r.status_code, "cf_ray": r.headers.get("cf-ray"),
            "cf_mit": r.headers.get("cf-mitigated"), "ctype": r.headers.get("content-type"),
            "retry_after": r.headers.get("retry-after"), "len": len(b), "body_head": b,
            "body_hash": hashlib.sha256((b or "").encode()).hexdigest()[:12]}

def exit_url():
    p = random.choice(pools)
    u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return f'http://{u}:{p["pass"]}@{p["host"]}:{p["port"]}'

def run_one(addr):
    px = exit_url()
    s = cr.Session(impersonate="chrome124", proxies={"http": px, "https": px})
    out = {"route_hash": hashlib.sha256(px.encode()).hexdigest()[:10]}
    # 0) verify US residential route
    try:
        ipj = s.get("https://ipinfo.io/json", timeout=20).json()
        out["ip"] = ipj.get("ip"); out["country"] = ipj.get("country"); out["org"] = ipj.get("org")
        dc = any(k in (ipj.get("org", "") or "").lower() for k in ("hosting", "datacenter", "data center", "cloud", "llc-hosting", "ovh", "digitalocean", "amazon", "google", "azure"))
        out["is_datacenter_guess"] = dc
        if ipj.get("country") != "US":
            out["refused"] = "NON_US_ROUTE"; return out
    except Exception as e:
        out["ipinfo_err"] = str(e)[:60]
    # 1) warm homepage (same session -> cookie jar)
    try:
        h = s.get(BASE + "/", timeout=25); out["home"] = ev(h); out["cookies_after_home"] = len(s.cookies)
    except Exception as e:
        out["home"] = {"exc": str(e)[:80]}
    # 2) auth (same session)
    t0 = time.time()
    try:
        r = s.post(BASE + SESSION, headers=hdr("Basic " + BASIC), data="{}", timeout=30)
        out["auth"] = ev(r); out["auth_ms"] = int((time.time() - t0) * 1000)
        tok = None
        try: tok = r.json().get("token") or (r.json().get("data") or {}).get("token")
        except Exception: pass
        out["got_token"] = bool(tok)
        if not tok: out["stop"] = "auth_no_token"; return out
    except Exception as e:
        out["auth"] = {"exc": str(e)[:80]}; out["stop"] = "auth_exc"; return out
    # 3) search (SAME session, SAME token, SAME route)
    t0 = time.time()
    try:
        r = s.post(BASE + SEARCH, headers=hdr("Bearer " + tok),
                   data=json.dumps({"addressLine1": addr["line1"], "city": addr.get("city", ""), "state": addr["state"], "zip": addr["zip"]}), timeout=30)
        out["search"] = ev(r); out["search_ms"] = int((time.time() - t0) * 1000)
        j = {}
        try: j = r.json()
        except Exception: pass
        out["exactMatch"] = j.get("exactMatch"); out["maxQual"] = j.get("maxQual")
        # 4) product ONLY for exact fiber candidate
        if j.get("exactMatch") is True and j.get("dfAddressId") and j.get("uqualProvisioningResult"):
            t0 = time.time()
            pr = s.post(BASE + PRODUCT, headers=hdr("Bearer " + tok),
                        data=json.dumps({"dfAddressId": j["dfAddressId"], "accountNumber": "", "moveDate": "", "addAddrReason": "", "uqualProvisioningResult": j["uqualProvisioningResult"]}), timeout=30)
            out["product"] = ev(pr); out["product_ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        out["search"] = {"exc": str(e)[:80]}
    finally:
        s.close()
    return out

if __name__ == "__main__":
    # one real Centerville address + one queued rural one
    tests = [{"line1": "421 N MAIN ST", "city": "CENTERVILLE", "state": "IA", "zip": "52544"}]
    ql = [json.loads(l) for l in open(os.path.expanduser("~/cities_test.jsonl"))] if os.path.exists(os.path.expanduser("~/cities_test.jsonl")) else []
    tests += ql[:1]
    for t in tests:
        print(json.dumps({"addr": t["line1"] + " " + t["zip"], **run_one(t)}, indent=0), flush=True)
        time.sleep(2)
