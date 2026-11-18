"""Clean recovery estimate: re-check AddressNotFound/Suggestions through the ENGINE's full
workflow (UqualClient.check does address variants + candidate matching — the remediation the
single-shot fleet sweep skipped). Exit-reuse to cut AUTH_403/UNREAD noise. Measures what
fraction of the 'unresolved' bucket is actually recoverable serviceable fibre."""
import os, sys, json, hashlib, random, threading, queue, collections, time
from urllib.parse import quote
_N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
HERE = "/Users/masani/FLEET_5K/merge"; sys.path[:0] = [HERE, os.path.join(HERE, "engine")]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
os.chdir(os.path.join(HERE, "engine"))
from carrier_uqual import UqualClient, abbreviate
pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"] if str(p.get("name", "")).startswith("dataimpulse")]
def exiturl():
    p = random.choice(pools); u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

leads = [json.loads(l) for l in open("/Users/masani/iowa_pull/remediation_canary2000.jsonl")][:_N]
q = queue.Queue(); [q.put(x) for x in leads]
out = collections.Counter(); lock = threading.Lock(); done = [0]; t0 = time.time()
PER_EXIT = 8
def worker():
    cl = None; used = 0
    while True:
        try: a = q.get_nowait()
        except queue.Empty: break
        if cl is None or used >= PER_EXIT:
            if cl is not None:
                try: cl.close()
                except Exception: pass
            px = exiturl(); cl = UqualClient(proxy_factory=lambda px=px: px); cl.proxy = px; used = 0
        try:
            oc, det = cl.check(abbreviate(str(a["line1"]).upper()), a["city"], a["state"], a["zip"])
            oc = str(oc)
        except Exception as e:
            oc = "ERR"
        used += 1
        with lock:
            out[oc] += 1; done[0] += 1
            if done[0] % 100 == 0: print("  %d/%d (%.0fs) %s" % (done[0], len(leads), time.time() - t0, dict(out.most_common(6))), flush=True)
ths = [threading.Thread(target=worker) for _ in range(24)]
[t.start() for t in ths]; [t.join() for t in ths]
fiber = out.get("FIBER", 0); n = len(leads)
completed = sum(v for k, v in out.items() if k in ("FIBER", "NOMATCH", "COPPER", "CABLE", "WIRELESS", "NOFIBER"))
print("\nREMEDIATION_CANARY n=%d in %.1f min" % (n, (time.time() - t0) / 60))
print("outcomes:", dict(out.most_common()))
print("FIBER recovered: %d/%d = %.1f%% of all; %.1f%% of completed(%d)" % (fiber, n, 100 * fiber / n, 100 * fiber / max(1, completed), completed))
