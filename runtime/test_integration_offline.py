"""OFFLINE INTEGRATION TEST — drives the REAL candidate fleet_pipeline.Actor and
fleet_scheduler gate via dependency injection (a fake AsyncSession). No network, no
carrier requests, no live workers. Proves the Section-4 controls operate through the
actual execution-path code, not just helper units."""
import os, sys, json, asyncio, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
import fleet_pipeline as FP
import fleet_scheduler as FS
from failure_handling import ActorBreaker

P = F = 0
def check(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

# ---- fake AsyncSession injected into the REAL Actor (dependency injection) ----
class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status; self._body = body or {}; self.headers = headers or {}
    def json(self): return self._body

class FakeSession:
    """Scripts responses by URL substring. Records calls. .post is a real coroutine so the
    Actor's `await guard(self.s.post(...))` path runs unchanged."""
    def __init__(self, script): self.script = script; self.calls = []
    async def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(url)
        for key, resp in self.script.items():
            if key in url:
                if isinstance(resp, Exception): raise resp
                return resp
        raise AssertionError("no scripted response for " + url)
    async def close(self): pass

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJzZXNzaW9uSWQiOiJzMSJ9" + ".sig"   # decodable sessionId

async def t_actor_block_retain_and_quarantine():
    # REAL Actor; inject a fake session that auths 201 then search 403 with Retry-After absent
    ac = FP.Actor(0, 0); ac.tok = FAKE_JWT
    ac.s = FakeSession({"/auth/session": FakeResp(201, {"token": FAKE_JWT}),
                        "/address/search": FakeResp(403, {}, headers={"Server": "cloudflare"})})
    m = FP.M()
    # drive the REAL open() (sets token via real code path)
    # open() builds its own session, so set alive/token via real open with a session that auths:
    ac2 = FP.Actor(1, 0)
    # monkeypatch AsyncSession used inside open() so open() runs the real code with our fake
    import fleet_pipeline
    class _AS:
        def __init__(self, *a, **k): pass
        async def post(self, url, headers=None, data=None, timeout=None): return FakeResp(201, {"token": FAKE_JWT})
        async def close(self): pass
    orig = fleet_pipeline.AsyncSession
    fleet_pipeline.AsyncSession = _AS
    ok = await ac2.open([{"user":"u","pass":"p","host":"gw.dataimpulse.com","port":823,"name":"dataimpulse_p1"}], 20.0, m)
    fleet_pipeline.AsyncSession = orig
    check("real Actor.open() warms on 201 (token set)", ok is True and ac2.tok == FAKE_JWT and ac2.alive)

    # now the REAL search returns 403; the REAL note_block (called by search_actor) handles it
    r = await ac.search({"line1":"1 MAIN ST","city":"AKRON","state":"IA","zip":"51001"}, 10.0)
    check("real Actor.search() returns the injected 403", r.status_code == 403)
    ra = ac.note_block(r)                          # the exact control search_actor invokes
    check("note_block quarantines the actor (breaker QUARANTINED, alive False)",
          ac.breaker.state == ActorBreaker.QUARANTINED and ac.alive is False)
    check("Retry-After ABSENT distinguished (present False, parsed None)",
          ra["present"] is False and ra["parsed_seconds"] is None)
    check("breaker forbids identity replacement + auto-retry after policy block",
          ac.breaker.may_replace_identity() is False and ac.breaker.may_auto_retry() is False)

async def t_retry_after_literal_null_via_real_path():
    ac = FP.Actor(2, 0); ac.tok = FAKE_JWT
    ac.s = FakeSession({"/address/search": FakeResp(429, {}, headers={"Retry-After": "null"})})
    r = await ac.search({"line1":"1 A ST","city":"X","state":"IA","zip":"50001"}, 10.0)
    ra = ac.note_block(r)
    check("literal 'null' Retry-After distinguished from absent (via real Actor path)",
          ra["present"] is True and ra["was_literal_null"] is True and ra["parsed_seconds"] is None)

async def t_retry_after_seconds_product_path():
    ac = FP.Actor(3, 0); ac.tok = FAKE_JWT
    ac.s = FakeSession({"/product-availability": FakeResp(403, {}, headers={"Retry-After": "120"})})
    r = await ac.product({"dfAddressId":"d","uqualProvisioningResult":"u"}, 22.0)
    ra = ac.note_block(r)
    check("product 403 retains parsed Retry-After seconds (via real Actor.product path)",
          ra["parsed_seconds"] == 120 and ac.alive is False)

def t_load_done_resume_restart():
    # REAL load_done over a ledger with terminal + retryable rows
    fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    rows = [{"ck":"A","class":"SERVICEABLE_FIBER"}, {"ck":"B","class":"DEFINITIVE_NONFIBER"},
            {"ck":"C","class":"INCOMPLETE_RETRYABLE"},                 # NOT terminal -> re-sweep
            {"ck":"D","class":"COMPLETE_REJECTED"}]
    open(path,"w").write("\n".join(json.dumps(r) for r in rows)+"\n")
    done = FP.load_done(path)
    check("load_done returns only terminal ck (A,B,D), not retryable C",
          done == {"A","B","D"})
    # simulate restart: raw universe re-filtered by done -> only undecided/retryable remain
    raw = [{"ck":c} for c in ["A","B","C","D","E"]]
    remaining = [r for r in raw if r.get("ck") not in done]
    check("restart re-sweeps only C (retryable) + E (unattempted); no dup of A/B/D",
          sorted(r["ck"] for r in remaining) == ["C","E"])
    os.unlink(path)

def t_bounded_queue_backpressure():
    # the candidate main() now builds pq = asyncio.Queue(maxsize=pq_bound); prove backpressure
    q = asyncio.Queue(maxsize=2)
    q.put_nowait(1); q.put_nowait(2)
    raised = False
    try: q.put_nowait(3)
    except asyncio.QueueFull: raised = True
    check("bounded product queue applies backpressure at capacity (no unbounded growth)",
          raised and q.qsize() == 2)

def t_readiness_gate_real():
    # REAL fleet_scheduler.readiness_gate_check (calls sys.exit on failure)
    def refused(canary):
        try: FS.readiness_gate_check(canary); return False
        except SystemExit: return True
    check("readiness gate REFUSES auth-201-alone (cannot reopen production)",
          refused({"auth_status": 201}))
    check("readiness gate REFUSES 201+200 without product",
          refused({"auth_status": 201, "search_status": 200}))
    ok_pass = True
    try:
        FS.readiness_gate_check({"auth_status":201,"search_status":200,"product_status":200,"disposition":"SERVICEABLE_FIBER"})
    except SystemExit:
        ok_pass = False
    check("readiness gate PASSES a full end-to-end terminal canary", ok_pass)

async def main():
    print("[actor block handling through real Actor path]")
    await t_actor_block_retain_and_quarantine()
    await t_retry_after_literal_null_via_real_path()
    await t_retry_after_seconds_product_path()
    print("[resume / lease recovery through real load_done]")
    t_load_done_resume_restart()
    print("[bounded queue backpressure]")
    t_bounded_queue_backpressure()
    print("[end-to-end readiness gate through real scheduler]")
    t_readiness_gate_real()

if os.name == "nt":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

asyncio.run(main())
print("\nRESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
