"""Self-contained fleet node runner. Uses the proven UqualClient (global
DataImpulse rotation) over THIS box's address partition; appends every real
Kinetic result to results.jsonl. No mp_launch/run.db machinery — just the
carrier client that already works.
Usage: node_run.py <addrs.jsonl> <workers> <results.jsonl>
"""
import sys, os, json, threading, queue, time, itertools
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["FL_CONFIG"] = os.path.join(HERE, "config")
os.environ["FL_PROXY"] = "1"          # never use the box's own IP; always the residential exit
import carrier_uqual as U

POOL = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"]
        if p.get("name", "").startswith("dataimpulse")]
_lk = threading.Lock(); _cyc = itertools.cycle(POOL)
def _next():
    with _lk:
        return next(_cyc)
def factory():
    def fresh():
        p = _next()
        return f"http://{p['user']}:{p['pass']}@{p['host']}:{p['port']}"   # global rotation, fresh exit
    return fresh

ADDRS = sys.argv[1]; W = int(sys.argv[2]) if len(sys.argv) > 2 else 128; OUT = sys.argv[3]
q = queue.Queue()
for line in open(ADDRS):
    line = line.strip()
    if line:
        q.put(json.loads(line))
total = q.qsize()
out = open(OUT, "a"); olk = threading.Lock()
done = [0]; fiber = [0]

def worker(i):
    cl = U.UqualClient(proxy_factory=factory(), worker=i)
    used = 0
    while True:
        try:
            a = q.get_nowait()
        except queue.Empty:
            return
        try:
            o, d = cl.check(a["line1"], a["city"], a["state"], a["zip"])
        except Exception as e:
            o, d = "EXC", {}
        with olk:
            out.write(json.dumps({"ck": a.get("ck"), "o": o,
                                  "plan": (d or {}).get("maxQual")}) + "\n")
            out.flush()
            done[0] += 1
            if o == "FIBER":
                fiber[0] += 1
        used += 1
        if used % 14 == 0:                     # retire bundle -> fresh exit
            try:
                cl = U.UqualClient(proxy_factory=factory(), worker=i)
            except Exception:
                pass

ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(W)]
t0 = time.time()
for t in ths:
    t.start()
while any(t.is_alive() for t in ths):
    time.sleep(10)
    el = time.time() - t0
    print(f"{done[0]}/{total} done, {fiber[0]} fiber, {round(done[0]/el,1)}/s", flush=True)
out.close()
print(f"NODE_DONE {done[0]} results, {fiber[0]} fiber in {round(time.time()-t0)}s", flush=True)
