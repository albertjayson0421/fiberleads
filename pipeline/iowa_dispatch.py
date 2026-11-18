"""Distributed Iowa Kinetic sweep across the 12 GCP VMs. Carrier egress is DataImpulse
US-sticky only (VM direct egress is blocked); the fleet honors each exit's own 403 and
re-waves undecided addresses. FL_NORM=abbrev is set so the fleet sends Kinetic the
abbreviated form (measured mandatory for Iowa).

Subcommands:
  launch  <input.jsonl> [--actors 300]   split round-robin -> 12 shards, scp, start sweeps
  status                                  per-VM: shard size, ledger decided, sweep alive, log tail
  collect                                 scp every VM ledger back -> ledgers/vmNN.jsonl, merge+dedup
"""
import sys, os, json, subprocess, time

BOXES = [l.strip() for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
KEY = os.path.expanduser("~/.ssh/fleet_key")
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-q"]
SHARD_DIR = "/Users/masani/iowa_pull/shards"
LEDGER_DIR = "/Users/masani/iowa_pull/ledgers"

def arg(name, d):
    return type(d)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d

def launch():
    inp = sys.argv[2]; actors = arg("--actors", 300)
    rows = [l for l in open(inp) if l.strip()]
    os.makedirs(SHARD_DIR, exist_ok=True)
    n = len(BOXES)
    shards = [[] for _ in range(n)]
    for i, r in enumerate(rows):
        shards[i % n].append(r)               # round-robin -> geographic mix per VM
    print("dispatching %d addresses across %d VMs (~%d each), actors=%d"
          % (len(rows), n, len(rows) // n, actors), flush=True)
    for i, box in enumerate(BOXES):
        sp = os.path.join(SHARD_DIR, "shard_%02d.jsonl" % i)
        with open(sp, "w") as f:
            f.writelines(shards[i])
        subprocess.run(SCP + [sp, "%s:~/iowa_shard.jsonl" % box], check=True, timeout=120)
        cmd = ("cd ~/merge_sr && FL_NORM=abbrev setsid nohup python3 -u sweep_loop.py "
               "~/iowa_shard.jsonl ~/iowa_ledger.jsonl --actors %d --wave-hold 70 --max-waves 40 "
               "> ~/iowa_sweep.log 2>&1 </dev/null & disown; sleep 1; exit 0") % actors
        try:
            subprocess.run(SSH + ["-n", box, cmd], timeout=20)   # job detaches; ignore if ssh channel lingers
        except subprocess.TimeoutExpired:
            pass
        print("  [%2d] %-22s shard=%d launched" % (i, box, len(shards[i])), flush=True)
    print("ALL 12 SWEEPS LAUNCHED. Poll with: python3 iowa_dispatch.py status", flush=True)

def status():
    tot_shard = tot_dec = alive = 0
    for i, box in enumerate(BOXES):
        out = subprocess.run(SSH + [box,
            "wc -l < ~/iowa_shard.jsonl 2>/dev/null; wc -l < ~/iowa_ledger.jsonl 2>/dev/null; "
            "pgrep -f sweep_loop.py >/dev/null && echo ALIVE || echo done; tail -1 ~/iowa_sweep.log 2>/dev/null"],
            capture_output=True, text=True, timeout=30).stdout.strip().split("\n")
        sh = int(out[0]) if len(out) > 0 and out[0].strip().isdigit() else 0
        dec = int(out[1]) if len(out) > 1 and out[1].strip().isdigit() else 0
        st = out[2] if len(out) > 2 else "?"
        tail = out[3] if len(out) > 3 else ""
        tot_shard += sh; tot_dec += dec; alive += (st == "ALIVE")
        print("  [%2d] %-22s shard=%-6d decided=%-6d %-5s | %s" % (i, box, sh, dec, st, tail[:70]), flush=True)
    print("=== TOTAL shard=%d decided=%d (%.1f%%) | %d/%d sweeps alive ==="
          % (tot_shard, tot_dec, 100 * tot_dec / max(1, tot_shard), alive, len(BOXES)), flush=True)

def collect():
    os.makedirs(LEDGER_DIR, exist_ok=True)
    seen = set(); merged = 0
    mp = os.path.join(LEDGER_DIR, "iowa_merged_ledger.jsonl")
    with open(mp, "w") as mf:
        for i, box in enumerate(BOXES):
            lp = os.path.join(LEDGER_DIR, "vm_%02d.jsonl" % i)
            r = subprocess.run(SCP + ["%s:~/iowa_ledger.jsonl" % box, lp], capture_output=True, text=True)
            if r.returncode != 0:
                print("  [%2d] %-22s NO LEDGER (%s)" % (i, box, r.stderr.strip()[:40])); continue
            cnt = 0
            for line in open(lp):
                line = line.strip()
                if not line:
                    continue
                try:
                    ck = json.loads(line).get("ck")
                except Exception:
                    continue
                if ck and ck not in seen:
                    seen.add(ck); mf.write(line + "\n"); merged += 1; cnt += 1
            print("  [%2d] %-22s ledger_rows_new=%d" % (i, box, cnt))
    print("=== MERGED %d unique durable decisions -> %s ===" % (merged, mp))

if __name__ == "__main__":
    {"launch": launch, "status": status, "collect": collect}[sys.argv[1]]()
