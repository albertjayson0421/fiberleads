#!/usr/bin/env python3
"""BOUNDED RECOVERY CANARY (authorized 2026-09-10, one canary only).

ONE actor, ONE preflight-verified US residential route, its OWN new session/token (session
affinity preserved). Budget: 1 auth, <=5 sequential searches, <=1 product per searched address.
NO automatic retries. HARD STOP on 403/429/egress-preflight-fail. NO identity rotation, NO restart.
Exercises the CANDIDATE'S real request+classification paths (fleet_pipeline.Actor + build_det +
classify_final). Full diagnostics persisted to a restricted file; only redacted evidence printed.
Simulated success is NOT real acceptance; five checks are NOT sustained capacity.
"""
import asyncio, json, os, sys, time, hashlib, re, datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
import fleet_pipeline as FP
from curl_cffi.requests import AsyncSession

DATACENTER_ASN = {"396982","15169","16509","14618","8075","14061","63949","20473","16276","24940"}
# 1 exact-address positive control (stored evidence) + up to 4 real Iowa targets
ADDRS = [
    {"ck": "control_tulsa", "line1": "5958 S 94TH EAST PL", "city": "TULSA", "state": "OK", "zip": "74145", "control": True},
    {"ck": "ia_knox_conrey", "line1": "101 South CONREY Street", "city": "Knoxville", "state": "IA", "zip": "50138"},
    {"ck": "ia_knox_lincoln", "line1": "101 South LINCOLN Street", "city": "Knoxville", "state": "IA", "zip": "50138"},
    {"ck": "ia_akron_south", "line1": "930 SOUTH ST", "city": "AKRON", "state": "IA", "zip": "51001"},
    {"ck": "ia_centerville_maple", "line1": "1000 West Maple Street", "city": "Centerville", "state": "IA", "zip": "52544"},
]
SECRET = re.compile(r'("?(token|authorization|cookie|bearer|password|apikey|accessToken)"?\s*[:=]\s*)"[^"]*"', re.I)
def redact(t): return SECRET.sub(r'\1"[RED]"', (t or ""))[:300]
def hdrs(r):
    keys = ("Server", "CF-Ray", "cf-mitigated", "Retry-After", "Content-Type", "cf-cache-status")
    return {k: r.headers.get(k) for k in keys if getattr(r, "headers", None) and r.headers.get(k)}
def maskip(ip):
    p = str(ip).split("."); return ".".join(p[:2] + ["x", "x"]) if len(p) == 4 else "?"

DIAG = []  # full (secure) records
def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

async def main():
    rel_hash = hashlib.sha256(open(os.path.join(HERE, "MANIFEST.sha256"), "rb").read()).hexdigest()[:16]
    pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
    route, plan = FP.sticky(pools, 0)                       # ONE route (;sessid. sticky)
    # ---- egress preflight on THIS route (fail closed) ----
    s0 = AsyncSession(impersonate="chrome124", proxies={"http": route, "https": route})
    try:
        j = (await s0.get("https://ipinfo.io/json", timeout=15)).json()
    except Exception as e:
        print(json.dumps({"ev": "CANARY_STOP", "reason": "egress_preflight_unreachable", "error": type(e).__name__, "release": rel_hash})); return
    finally:
        try: await s0.close()
        except Exception: pass
    org = str(j.get("org") or ""); country = j.get("country"); asn = org.split()[0].replace("AS", "") if org.startswith("AS") else ""
    exit_id = {"country": country, "org": org[:40], "ip_masked": maskip(j.get("ip")), "ip_hash": hashlib.sha256(str(j.get("ip")).encode()).hexdigest()[:12]}
    if country != "US" or asn in DATACENTER_ASN:
        print(json.dumps({"ev": "CANARY_STOP", "reason": "egress_preflight_failed_not_US_residential", "exit": exit_id, "release": rel_hash})); return
    print(json.dumps({"ev": "PREFLIGHT_OK", "exit": exit_id, "release": rel_hash, "route": "dataimpulse;sessid (verified US residential)"}), flush=True)

    # ---- ONE actor reuses the preflight-verified route (session affinity) ----
    FP.sticky = lambda p, i: (route, plan)                  # pin open() to the preflighted route
    ac = FP.Actor(0, 0); m = FP.M()
    ok = await ac.open(pools, 20.0, m)                      # 1 AUTH via candidate path
    if not ok:
        print(json.dumps({"ev": "CANARY_STOP", "reason": "auth_not_201_or_no_token", "release": rel_hash, "exit": exit_id})); return
    print(json.dumps({"ev": "AUTH_OK", "http": 201, "token": "present", "release": rel_hash}), flush=True)

    cfg = json.load(open(os.path.join(os.environ["FL_CONFIG"], "billing_config.json"))).get("billing_status_mapping", {})
    old = {v.strip().upper() for v in cfg.get("existing_values", [])}; new = {v.strip().upper() for v in cfg.get("new_values", [])}
    results = []
    for i, job in enumerate(ADDRS[:5]):                     # <=5 sequential searches
        try:
            r = await ac.search(job, 12.0)                 # candidate real search path; NO retry
        except Exception as e:
            DIAG.append({"phase": "search", "addr": job["ck"], "exception": type(e).__name__, "utc": now()})
            print(json.dumps({"ev": "CANARY_STOP", "reason": "search_transport_exception", "addr": job["ck"], "error": type(e).__name__, "release": rel_hash})); break
        rec = {"addr": job["ck"], "control": job.get("control", False), "search_http": r.status_code, "headers": hdrs(r), "utc": now()}
        DIAG.append({"phase": "search", "addr": job["ck"], "http": r.status_code, "headers": hdrs(r),
                     "body_redacted": redact(getattr(r, "text", "")), "utc": now()})
        if r.status_code in (403, 429):                    # HARD STOP
            rec["stopped"] = True; results.append(rec)
            print(json.dumps({"ev": "CANARY_STOP", "reason": "search_%d" % r.status_code, "addr": job["ck"],
                              "headers": hdrs(r), "body_head": redact(getattr(r, "text", "")), "release": rel_hash}), flush=True)
            break
        if r.status_code != 200:
            rec["disposition"] = "non200_incomplete"; results.append(rec); continue
        j2 = r.json(); outcome, det = FP.build_det(j2, old, new)
        rec["techType"] = det.get("techType"); rec["exactMatch"] = det.get("exactMatch")
        fiber_exact = (outcome == "FIBER" and det["exactMatch"] is True and det["success"] is True
                       and det["dfAddressId"] and det["uqualProvisioningResult"]
                       and not (det["existing_customer"] is True and det["customer_mapping_verified"]))
        if fiber_exact:
            try:
                pr = await ac.product(det, 22.0)           # <=1 product per address; NO retry
            except Exception as e:
                rec["product"] = "exception:" + type(e).__name__; results.append(rec); continue
            DIAG.append({"phase": "product", "addr": job["ck"], "http": pr.status_code, "headers": hdrs(pr),
                         "body_redacted": redact(getattr(pr, "text", "")), "utc": now()})
            rec["product_http"] = pr.status_code
            if pr.status_code in (403, 429):
                rec["stopped"] = True; results.append(rec)
                print(json.dumps({"ev": "CANARY_STOP", "reason": "product_%d" % pr.status_code, "addr": job["ck"], "headers": hdrs(pr), "release": rel_hash}), flush=True); break
            enr = {**pr.json(), "_fl_transport": {"http_status": 200}} if pr.status_code == 200 else {"fl_fetch_error": "http_%d" % pr.status_code}
            cls = FP.classify_final("FIBER", det, enr)
            rec["disposition"] = cls or "unresolved"
        else:
            cls = FP.classify_final(outcome, det, None)
            rec["disposition"] = cls if (cls in FP.COUNTED or cls in FP.OTHER_DURABLE) else "unresolved_search_" + str(det.get("validationResult") or outcome)
        results.append(rec)
    try: await ac.s.close()
    except Exception: pass
    print("CANARY_RESULTS " + json.dumps({"release": rel_hash, "exit": exit_id, "results": results}), flush=True)
    # persist full diagnostics securely
    dpath = os.path.join(HERE, "canary_diag_%s.json" % datetime.datetime.now().strftime("%H%M%S"))
    json.dump({"utc": now(), "release": rel_hash, "exit": exit_id, "diag": DIAG}, open(dpath, "w"), indent=1)
    os.chmod(dpath, 0o600)
    print("DIAG_FILE " + dpath, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
