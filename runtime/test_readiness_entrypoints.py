"""Proves the readiness gate is enforced through the REAL main() of every launchable
production entrypoint (not just the scheduler): auth-201-alone can never reopen production.
Runs each main() with NO --canary and asserts it refuses BEFORE any prewarm/network."""
import os, sys, io, asyncio, contextlib, json, tempfile
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[os.path.join(HERE,"engine"),HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE,"config"))
import fleet_pipeline, fleet_actor_v2, fleet_scheduler
P=F=0
def ck(n,c):
    global P,F
    if c: P+=1; print("  PASS",n)
    else: F+=1; print("  FAIL",n)

def run_main(mod, argv):
    old=sys.argv; sys.argv=argv; buf=io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            asyncio.run(mod.main())
    except SystemExit: pass
    except Exception as e: buf.write("EXC:%s:%s"%(type(e).__name__,e))
    finally: sys.argv=old
    return buf.getvalue()

NO="/tmp/__nope_addrs.jsonl"; LED="/tmp/__nope_ledger.jsonl"
out=run_main(fleet_pipeline, ["fp", NO, LED])
ck("fleet_pipeline.main() REFUSES to start without an end-to-end canary", "STARTUP_REFUSED" in out)
ck("fleet_pipeline refusal happens before prewarm/network (no PREWARM emitted)", "PREWARM" not in out)

out2=run_main(fleet_actor_v2, ["fa", NO, LED])
ck("fleet_actor_v2.main() REFUSES to start without an end-to-end canary", "readiness gate" in out2)
ck("fleet_actor_v2 refusal happens before prewarm", "PREWARM" not in out2)

# with a valid end-to-end canary the gate PASSES (won't refuse) — verify it gets PAST the gate
fd,cf=tempfile.mkstemp(suffix=".json"); os.close(fd)
open(cf,"w").write(json.dumps({"auth_status":201,"search_status":200,"product_status":200,"disposition":"SERVICEABLE_FIBER"}))
out3=run_main(fleet_pipeline, ["fp", NO, LED, "--canary", cf])
ck("fleet_pipeline with valid canary does NOT refuse at the gate", "STARTUP_REFUSED" not in out3)
os.unlink(cf)

print("\nRESULT: %d passed, %d failed"%(P,F)); sys.exit(1 if F else 0)
