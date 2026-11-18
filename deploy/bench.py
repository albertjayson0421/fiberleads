"""Concurrency-scaling canary. Same UqualClient + global DataImpulse rotation as
node_run, but time-boxed at a given thread count, reporting the FULL outcome mix
and latency percentiles so we can see whether throughput scales with concurrency
or the proxy pool starts refusing connections (UNREAD/EXC climb).
Usage: bench.py <threads> <seconds>
"""
import sys, os, json, threading, queue, time, itertools, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get("FL_ENGINE", "engine")   # "engine_fast" tests short timeout
sys.path.insert(0, os.path.join(HERE, ENGINE))
os.environ["FL_CONFIG"] = os.path.join(HERE, "config")
os.environ["FL_PROXY"] = "1"
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
        return f"http://{p['user']}:{p['pass']}@{p['host']}:{p['port']}"
    return fresh

W = int(sys.argv[1]); DUR = int(sys.argv[2]) if len(sys.argv) > 2 else 60
addrs = [json.loads(l) for l in open("part.jsonl") if l.strip()]
q = queue.Queue()
for a in addrs:
    q.put(a)
c = collections.Counter(); lat = []; clk = threading.Lock()
stop = time.time() + DUR

def worker(i):
    try:
        cl = U.UqualClient(proxy_factory=factory(), worker=i)
    except Exception as e:
        with clk: c["INIT_EXC:" + type(e).__name__] += 1
        return
    used = 0
    while time.time() < stop:
        try:
            a = q.get_nowait()
        except queue.Empty:
            break
        t0 = time.time()
        try:
            o, d = cl.check(a["line1"], a["city"], a["state"], a["zip"])
        except Exception as e:
            o = "EXC:" + type(e).__name__
        dt = time.time() - t0
        with clk:
            c[o] += 1; lat.append(dt)
        used += 1
        if used % 14 == 0:
            try:
                cl = U.UqualClient(proxy_factory=factory(), worker=i)
            except Exception:
                pass

ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(W)]
t0 = time.time()
for t in ths:
    t.start()
for t in ths:
    t.join(DUR + 40)
el = time.time() - t0
ANS = ("FIBER", "NOFIBER", "COPPER", "CABLE", "WIRELESS", "NOMATCH")
TERMv = ("FIBER", "NOFIBER", "COPPER", "CABLE", "WIRELESS")
done = sum(c.values())
answered = sum(v for k, v in c.items() if k in ANS)
term = sum(v for k, v in c.items() if k in TERMv)
unread = sum(v for k, v in c.items() if k in ("UNREAD", "UNASKED", "UNRESOLVED")) \
         + sum(v for k, v in c.items() if str(k).startswith("EXC") or str(k).startswith("INIT_EXC"))
lat.sort()
p = lambda f: round(lat[min(len(lat) - 1, int(len(lat) * f))], 2) if lat else 0
print(json.dumps({"W": W, "el": round(el, 1),
                  "answered_per_s": round(answered / el, 1),
                  "term_per_s": round(term / el, 2),
                  "fail_per_s": round(unread / el, 1),
                  "p50": p(0.5), "p95": p(0.95),
                  "done": done, "answered": answered, "term": term, "fail": unread,
                  "mix": dict(c)}))
