"""Distribute the remediation re-sweep across the 12 VMs. launch | status | collect."""
import sys, os, json, subprocess
BOXES = [l.strip() for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
KEY = os.path.expanduser("~/.ssh/fleet_key")
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-q"]
SHARD = "/Users/masani/iowa_pull/remed_shards"; LED = "/Users/masani/iowa_pull/remed_ledgers"

def launch():
    inp = sys.argv[2]; rows = [l for l in open(inp) if l.strip()]
    os.makedirs(SHARD, exist_ok=True); n = len(BOXES)
    shards = [[] for _ in range(n)]
    for i, r in enumerate(rows): shards[i % n].append(r)
    print("remediating %d addresses across %d VMs (~%d each)" % (len(rows), n, len(rows) // n), flush=True)
    import time
    def _retry(args, timeout, tries=4):
        for k in range(tries):
            try:
                subprocess.run(args, check=True, timeout=timeout); return True
            except Exception as e:
                if k == tries - 1: print("    FAILED after %d: %s" % (tries, str(e)[:50]), flush=True); return False
                time.sleep(3 * (k + 1))
    ok = 0
    for i, box in enumerate(BOXES):
        sp = os.path.join(SHARD, "rem_%02d.jsonl" % i)
        open(sp, "w").writelines(shards[i])
        if not _retry(SCP + ["/Users/masani/iowa_pull/remediate2.py", "%s:~/remediate2.py" % box], 60):
            print("  [%2d] %-22s SKIP (scp remediate2.py failed)" % (i, box), flush=True); continue
        _retry(SCP + ["/Users/masani/iowa_pull/zip_city_final.json", "%s:~/zip_city_final.json" % box], 60)
        if not _retry(SCP + [sp, "%s:~/rem_in.jsonl" % box], 300):
            print("  [%2d] %-22s SKIP (scp shard failed)" % (i, box), flush=True); continue
        cmd = ("cd ~ && FL_NORM=abbrev setsid nohup python3 -u remediate2.py ~/rem_in.jsonl ~/rem_out.jsonl "
               "--threads 32 > ~/rem.log 2>&1 </dev/null & disown; sleep 1; exit 0")
        try: subprocess.run(SSH + ["-n", box, cmd], timeout=25)
        except subprocess.TimeoutExpired: pass
        except Exception as e: print("    launch warn: %s" % str(e)[:40], flush=True)
        ok += 1; print("  [%2d] %-22s shard=%d launched" % (i, box, len(shards[i])), flush=True)
    print("REMEDIATION SWEEPS LAUNCHED ON %d/%d VMs" % (ok, n), flush=True)

def status():
    ts = collections.Counter(); alive = 0
    for i, box in enumerate(BOXES):
        out = subprocess.run(SSH + [box, "pgrep -f remediate2.py >/dev/null && echo A || echo D; wc -l < ~/rem_out.jsonl 2>/dev/null; tail -1 ~/rem.log 2>/dev/null"],
                             capture_output=True, text=True, timeout=30).stdout.strip().split("\n")
        al = out[0] if out else "?"; done = out[1] if len(out) > 1 else "0"
        alive += (al == "A")
        print("  [%2d] %-22s %s done=%s | %s" % (i, box, al, done.strip(), (out[2][:70] if len(out) > 2 else "")), flush=True)
    print("=== %d/12 alive ===" % alive, flush=True)

def collect():
    os.makedirs(LED, exist_ok=True); merged = open("/Users/masani/iowa_pull/remediation2_results.jsonl", "w"); tot = collections.Counter()
    for i, box in enumerate(BOXES):
        lp = os.path.join(LED, "rem2_%02d.jsonl" % i)
        r = subprocess.run(SCP + ["%s:~/rem_out.jsonl" % box, lp], capture_output=True, text=True)
        if r.returncode != 0: print("  [%2d] no output (%s)" % (i, r.stderr.strip()[:40])); continue
        c = 0
        for line in open(lp):
            line = line.strip()
            if line: merged.write(line + "\n"); c += 1
            try: tot[json.loads(line).get("status")] += 1
            except Exception: pass
        print("  [%2d] %-22s rows=%d" % (i, box, c))
    merged.close(); print("=== merged remediation results:", dict(tot.most_common()), "===")

import collections
if __name__ == "__main__":
    {"launch": launch, "status": status, "collect": collect}[sys.argv[1]]()
