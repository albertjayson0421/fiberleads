"""Texas sweep dispatcher — runs remediate2 on the TX universe IN PARALLEL with the Iowa
job (separate tx_in/tx_out files, second process per VM). launch | status | collect."""
import sys, os, json, subprocess, time, collections
BOXES = [l.strip() for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
KEY = os.path.expanduser("~/.ssh/fleet_key")
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-q"]
SHARD = "/Users/masani/iowa_pull/tx_shards"; LED = "/Users/masani/iowa_pull/tx_ledgers"

def _retry(a, to, n=4):
    for k in range(n):
        try: subprocess.run(a, check=True, timeout=to); return True
        except Exception as e:
            if k == n - 1: print("   FAIL:", str(e)[:50], flush=True); return False
            time.sleep(3 * (k + 1))

def launch():
    inp = sys.argv[2]; rows = [l for l in open(inp) if l.strip()]
    os.makedirs(SHARD, exist_ok=True); n = len(BOXES)
    sh = [[] for _ in range(n)]
    for i, r in enumerate(rows): sh[i % n].append(r)
    print("TX: %d addresses across %d VMs (~%d each)" % (len(rows), n, len(rows) // n), flush=True)
    ok = 0
    for i, box in enumerate(BOXES):
        p = os.path.join(SHARD, "tx_%02d.jsonl" % i); open(p, "w").writelines(sh[i])
        _retry(SCP + ["/Users/masani/iowa_pull/remediate2.py", "%s:~/remediate2.py" % box], 60)
        if not _retry(SCP + [p, "%s:~/tx_in.jsonl" % box], 400):
            print("  [%2d] SKIP scp" % i, flush=True); continue
        cmd = ("cd ~ && FL_NORM=abbrev setsid nohup python3 -u remediate2.py ~/tx_in.jsonl ~/tx_out.jsonl "
               "--threads 24 > ~/tx.log 2>&1 </dev/null & disown; sleep 1; exit 0")
        try: subprocess.run(SSH + ["-n", box, cmd], timeout=25)
        except subprocess.TimeoutExpired: pass
        except Exception: pass
        ok += 1; print("  [%2d] %-22s shard=%d launched" % (i, box, len(sh[i])), flush=True)
    print("TX SWEEP LAUNCHED ON %d/%d VMs" % (ok, n), flush=True)

def status():
    tot = 0; alive = 0
    for i, box in enumerate(BOXES):
        r = subprocess.run(SSH + [box, "pgrep -f 'remediate2.py ~/tx_in'>/dev/null&&echo A||echo D; wc -l < ~/tx_out.jsonl 2>/dev/null"],
                           capture_output=True, text=True, timeout=25).stdout.strip().split("\n")
        al = r[0] if r else "?"; dn = r[1].strip() if len(r) > 1 and r[1].strip().isdigit() else "0"
        alive += (al == "A"); tot += int(dn)
        print("  [%2d] %-22s %s done=%s" % (i, box, al, dn), flush=True)
    print("=== TX %d/12 alive, %d resolved ===" % (alive, tot), flush=True)

def collect():
    os.makedirs(LED, exist_ok=True); m = open("/Users/masani/iowa_pull/tx_results.jsonl", "w"); t = collections.Counter()
    for i, box in enumerate(BOXES):
        lp = os.path.join(LED, "tx_%02d.jsonl" % i)
        r = subprocess.run(SCP + ["%s:~/tx_out.jsonl" % box, lp], capture_output=True, text=True)
        if r.returncode != 0: print("  [%2d] no output" % i); continue
        c = 0
        for line in open(lp):
            line = line.strip()
            if line:
                m.write(line + "\n"); c += 1
                try: t[json.loads(line).get("status")] += 1
                except Exception: pass
        print("  [%2d] rows=%d" % (i, c))
    m.close(); print("=== TX merged:", dict(t.most_common()), "===")

if __name__ == "__main__":
    {"launch": launch, "status": status, "collect": collect}[sys.argv[1]]()
