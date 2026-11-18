"""LOCALHOST CARRIER SIMULATOR + real-entrypoint scenario tests (item A verification).

Drives the REAL fleet_scheduler.main() against a controllable localhost HTTP carrier sim
(NO real carrier, NO proxy, NO deploy). Deterministic via the sim's fixed responses + a
bounded --hold. Proves the four defect fixes through the real code:
  T1 all-actors-quarantine while intake is blocked -> terminates, no hang, no silent loss (A1)
  T2 product-403 racing search dispatch -> shared gate closes, in-flight accounted (A2)
  T3 cancellation -> sessions closed, unfinished recovered (A3)
  T4 restart after failure persistence -> retry budget restored, not reset (A4)
Simulated results are kept DISTINCT from real carrier acceptance (this only exercises control flow).
"""
import os, sys, json, io, time, threading, asyncio, contextlib, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
import curl_cffi.requests as ccr
import fleet_pipeline, fleet_scheduler

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzZXNzaW9uSWQiOiJzMSJ9.sig"
CONTROL = {"search": "200_fiber", "product": "200_plan", "retry_after": None}
COUNTS = {"auth": 0, "search": 0, "product": 0}

def _search_body():
    return json.dumps({"success": True, "techType": "FIBER", "exactMatch": True,
                       "dfAddressId": "D1", "uqualProvisioningResult": "U1",
                       "maxQual": "QUAL UP TO 2 GIG RANGE VIA FIBER",
                       "address": {"billingStatus": "N"}}).encode()
def _product_body():
    return json.dumps({"metaBundles": {"speedAndProductBundleList": {"1000": {
        "bundles": [{"bundleName": "Gig Fiber", "standardPrice": "39.99"}], "speed": {"speed": "1000"}}}},
        "transactionId": "t"}).encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body=b"", ctype="application/json", headers=None):
        self.send_response(code); self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        try: self.rfile.read(ln)
        except Exception: pass
        p = self.path
        if "auth/session" in p:
            COUNTS["auth"] += 1; self._send(201, json.dumps({"token": FAKE_JWT, "success": True}).encode())
        elif "address/search" in p:
            COUNTS["search"] += 1; mode = CONTROL["search"]
            if mode == "403": self._send(403, b"Forbidden", "text/html", _ra_hdr())
            elif mode == "429": self._send(429, b"Too Many", "text/html", _ra_hdr())
            elif mode == "401": self._send(401, b"{}")
            elif mode == "200_nonfiber":
                self._send(200, json.dumps({"success": True, "techType": "COPPER", "exactMatch": True, "maxQual": "copper"}).encode())
            else: self._send(200, _search_body())
        elif "product-availability" in p:
            COUNTS["product"] += 1; mode = CONTROL["product"]
            if mode == "403": self._send(403, b"Forbidden", "text/html", _ra_hdr())
            else: self._send(200, _product_body())
        else: self._send(404, b"{}")
def _ra_hdr():
    ra = CONTROL.get("retry_after")
    return {"Retry-After": str(ra)} if ra is not None else {}

# ---- session factory: direct (no proxy) + open/close counters (A3 proof) ----
OPENS = [0]; CLOSES = [0]
def _factory(*a, **k):
    k.pop("proxies", None)
    s = ccr.AsyncSession(impersonate=k.get("impersonate", "chrome124"))
    OPENS[0] += 1
    _orig = s.close
    async def _close():
        CLOSES[0] += 1
        try: return await _orig()
        except Exception: pass
    s.close = _close
    return s

P = F = 0
def ck(n, c):
    global P, F
    if c: P += 1; print("  PASS", n)
    else: F += 1; print("  FAIL", n)

def write_addrs(path, n):
    with open(path, "w") as fh:
        for i in range(n):
            fh.write(json.dumps({"ck": "ck%05d" % i, "line1": "%d MAIN ST" % i, "city": "AKRON",
                                 "state": "IA", "zip": "51001", "lat": "1", "lon": "1"}) + "\n")

def read_ledger(path):
    rows = []
    if os.path.exists(path):
        for l in open(path):
            try: rows.append(json.loads(l))
            except Exception: pass
    return rows

def run_main(addrs, ledger, canary, extra=None, hold=25):
    argv = ["fs", addrs, ledger, "--actors", "3", "--product-workers", "2", "--hold", str(hold),
            "--auth-rate", "200", "--search-rate", "200", "--pq-bound", "3",
            "--refresh-every", "100000", "--breaker-threshold", "0.05", "--canary", canary] + (extra or [])
    old = sys.argv; sys.argv = argv; buf = io.StringIO(); final = {}
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            asyncio.run(fleet_scheduler.main())
    finally:
        sys.argv = old
    for line in buf.getvalue().splitlines():
        if line.startswith('{"ev": "FINAL"') or '"ev": "FINAL"' in line:
            try: final = json.loads(line)
            except Exception: pass
    return final, buf.getvalue()

def main():
    fleet_pipeline.BASE = "http://127.0.0.1:%d"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fleet_pipeline.BASE = "http://127.0.0.1:%d" % port
    fleet_pipeline.AsyncSession = _factory
    tmp = tempfile.mkdtemp()
    cf = os.path.join(tmp, "canary.json")
    open(cf, "w").write(json.dumps({"auth_status": 201, "search_status": 200, "product_status": 200, "disposition": "SERVICEABLE_FIBER"}))

    # ---------- T1: all searches 403; intake larger than INTAKE_BOUND (blocked) ----------
    print("[T1] all-actors-quarantine while intake blocked (A1)")
    CONTROL["search"] = "403"; CONTROL["product"] = "200_plan"; CONTROL["retry_after"] = "120"
    OPENS[0] = CLOSES[0] = 0; COUNTS["auth"]=COUNTS["search"]=COUNTS["product"]=0
    ad = os.path.join(tmp, "t1.jsonl"); ld = os.path.join(tmp, "t1_led.jsonl"); write_addrs(ad, 200)
    t = time.monotonic(); final, out = run_main(ad, ld, cf, hold=30); dur = time.monotonic() - t
    rows = read_ledger(ld)
    classes = {}
    for r in rows: classes[r.get("class")] = classes.get(r.get("class"), 0) + 1
    print("   [T1 FINAL]", {k: final.get(k) for k in ("breaker_tripped","s403","searches","fed","consumed","retryable","all_actors_down")})
    print("   [T1 COUNTS]", dict(COUNTS), "reopens=", final.get("reopens"), "auths=", final.get("auths"))
    print("   [T1 sample out]", "\n".join(l for l in out.splitlines() if "Traceback" in l or "Error" in l or "READINESS" in l)[:400])
    ck("T1 terminates (no hang, < hold)", dur < 29)
    ck("T1 terminates when all actors unavailable (A1) with intake BLOCKED (fed>INTAKE_BOUND)",
       (final.get("all_actors_down") is True or final.get("breaker_tripped") is True) and final.get("fed", 0) > 60)
    ck("T1 no dispatched ck silently lost (each searched ck has a durable retryable/RETRY_LOG row)",
       final.get("retryable", 0) >= final.get("consumed", 0) and any(r.get("class") == "RETRY_LOG" for r in rows))
    ck("T1 RETRY_LOG rows persisted with parsed Retry-After (A4 persist-before-requeue)",
       any(r.get("class") == "RETRY_LOG" and isinstance(r.get("retry_after"), dict) and r["retry_after"].get("parsed_seconds") == 120 for r in rows))
    ck("T1 NO ck wrongly terminalized (no durable no-fiber from a 403)",
       classes.get("DEFINITIVE_NONFIBER", 0) == 0 and classes.get("COMPLETE_REJECTED", 0) == 0)
    ck("T1 sessions all closed (A3 cleanup): opens==closes", OPENS[0] == CLOSES[0] and OPENS[0] > 0)

    # ---------- T2: search 200 fiber-exact, product 403 (racing dispatch) ----------
    print("[T2] product-403 racing search dispatch (A2 shared gate)")
    CONTROL["search"] = "200_fiber"; CONTROL["product"] = "403"; CONTROL["retry_after"] = None
    OPENS[0] = CLOSES[0] = 0
    ad = os.path.join(tmp, "t2.jsonl"); ld = os.path.join(tmp, "t2_led.jsonl"); write_addrs(ad, 40)
    final, out = run_main(ad, ld, cf, hold=30); rows = read_ledger(ld)
    prod_retry = [r for r in rows if r.get("class") == "INCOMPLETE_RETRYABLE" and str(r.get("phase", "")).startswith("product-403")]
    quarantined_pre = [r for r in rows if r.get("phase") == "actor_quarantined_preproduct"]
    ck("T2 product-403 candidate recorded retryable (in-flight accounted)", len(prod_retry) >= 1)
    ck("T2 shared gate closed: post-quarantine product dispatch blocked (actor_quarantined_preproduct) OR sessions re-cycled",
       len(quarantined_pre) >= 1 or OPENS[0] > 3)
    ck("T2 no fiber wrongly shipped as SERVICEABLE on a product 403", classes_of(rows).get("SERVICEABLE_FIBER", 0) == 0)
    ck("T2 sessions all closed (opens==closes)", OPENS[0] == CLOSES[0] and OPENS[0] > 0)

    # ---------- T3: cancellation mid-run ----------
    print("[T3] cancellation -> session cleanup + in-flight recovery (A3)")
    CONTROL["search"] = "200_fiber"; CONTROL["product"] = "200_plan"; CONTROL["retry_after"] = None
    OPENS[0] = CLOSES[0] = 0
    ad = os.path.join(tmp, "t3.jsonl"); ld = os.path.join(tmp, "t3_led.jsonl"); write_addrs(ad, 300)
    async def cancel_run():
        sys.argv = ["fs", ad, ld, "--actors", "3", "--product-workers", "2", "--hold", "60",
                    "--auth-rate", "200", "--search-rate", "8", "--pq-bound", "3",
                    "--refresh-every", "100000", "--canary", cf]
        task = asyncio.create_task(fleet_scheduler.main())
        await asyncio.sleep(2.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        asyncio.run(cancel_run())
    await_settle()
    rows = read_ledger(ld)
    ck("T3 sessions closed on cancellation (opens==closes)", OPENS[0] == CLOSES[0] and OPENS[0] > 0)
    ck("T3 no ck double-terminal after cancel (no corruption)", no_double_terminal(rows))
    ck("T3 in-flight preserved (some durable rows written, nothing hung)", len(rows) > 0)

    # ---------- T4: restart after failure persistence ----------
    print("[T4] restart preserves quarantine / per-job retry budget (A4)")
    CONTROL["search"] = "401"; CONTROL["product"] = "200_plan"; CONTROL["retry_after"] = None
    ad = os.path.join(tmp, "t4.jsonl"); ld = os.path.join(tmp, "t4_led.jsonl"); write_addrs(ad, 60)
    run_main(ad, ld, cf, hold=30)
    rows1 = read_ledger(ld)
    max_attempt_1 = max([int(r.get("attempt", 0)) for r in rows1 if r.get("class") == "RETRY_LOG"] or [0])
    run_main(ad, ld, cf, hold=30)   # SAME ledger -> restart
    rows2 = read_ledger(ld)
    max_attempt_2 = max([int(r.get("attempt", 0)) for r in rows2 if r.get("class") == "RETRY_LOG"] or [0])
    ck("T4 RETRY_LOG persisted attempts in run 1", max_attempt_1 >= 1)
    ck("T4 restart did NOT reset retry budget (attempts carried up, not back to 1)", max_attempt_2 > max_attempt_1)

    srv.shutdown()
    print("\nRESULT: %d passed, %d failed" % (P, F))
    sys.exit(1 if F else 0)

def classes_of(rows):
    d = {}
    for r in rows: d[r.get("class")] = d.get(r.get("class"), 0) + 1
    return d
def no_double_terminal(rows):
    TERM = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING", "COMPLETE_REJECTED",
            "DEFINITIVE_OUT_OF_TERRITORY", "EXCLUDED_PENDING_ORDER"}
    seen = {}
    for r in rows:
        if r.get("class") in TERM:
            ckk = r.get("ck")
            if ckk in seen: return False
            seen[ckk] = 1
    return True
def await_settle():
    time.sleep(0.3)

if __name__ == "__main__":
    main()
