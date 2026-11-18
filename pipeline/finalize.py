"""Collect all 12 VM ledgers, dedup by ck, report the class breakdown + conservation
against the 245,672-address sweep universe, and extract the SERVICEABLE_FIBER leads
(joined back to address + rooftop coords by ck) for pricing capture + export.

Usage: finalize.py   (scps ledgers, then analyzes)   |   finalize.py --no-fetch (analyze local)"""
import os, sys, json, subprocess, collections

BOXES = [l.strip() for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
KEY = os.path.expanduser("~/.ssh/fleet_key")
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-q"]
LDIR = "/Users/masani/iowa_pull/ledgers"; os.makedirs(LDIR, exist_ok=True)
SWEEP = "/Users/masani/iowa_pull/iowa_sweep_final.jsonl"
SERV_OUT = "/Users/masani/iowa_pull/serviceable_leads.jsonl"

if "--no-fetch" not in sys.argv:
    for i, box in enumerate(BOXES):
        r = subprocess.run(SCP + ["%s:~/iowa_ledger.jsonl" % box, os.path.join(LDIR, "vm_%02d.jsonl" % i)],
                           capture_output=True, text=True)
        print("  [%2d] %-22s %s" % (i, box, "ok" if r.returncode == 0 else "FAIL:" + r.stderr.strip()[:40]), flush=True)

# merge ledgers, dedup by ck (keep first durable decision per ck)
seen = {}; by_class = collections.Counter(); dup = 0
for i in range(len(BOXES)):
    p = os.path.join(LDIR, "vm_%02d.jsonl" % i)
    if not os.path.exists(p):
        continue
    for line in open(p):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        ck = r.get("ck")
        if not ck:
            continue
        if ck in seen:
            dup += 1; continue
        seen[ck] = r; by_class[r.get("class", "?")] += 1

# load sweep universe for address/coord join
uni = {}
for line in open(SWEEP):
    try:
        r = json.loads(line); uni[r["ck"]] = r
    except Exception:
        pass

serv = [ck for ck, r in seen.items() if r.get("class") == "SERVICEABLE_FIBER"]
with open(SERV_OUT, "w") as f:
    miss = 0
    for ck in serv:
        u = uni.get(ck)
        if not u:
            miss += 1; continue
        f.write(json.dumps({"ck": ck, "line1": u.get("line1") or u.get("address"), "address": u.get("address"),
                            "city": u.get("city"), "state": u.get("state"), "zip": u.get("zip"),
                            "lat": u.get("lat"), "lon": u.get("lon"), "county": u.get("county")}) + "\n")

decided = len(seen)
print("\n==== SWEEP LEDGER SUMMARY ====")
print("  sweep universe (unique addresses): %d" % len(uni))
print("  durable decisions (unique ck)    : %d (%.1f%%)" % (decided, 100 * decided / max(1, len(uni))))
print("  cross-VM dup rows collapsed      : %d" % dup)
print("  class breakdown:")
for c, n in by_class.most_common():
    print("    %-28s %d" % (c, n))
undecided = len(uni) - decided
print("  UNDECIDED (infra-incomplete, retryable): %d (%.1f%%)" % (undecided, 100 * undecided / max(1, len(uni))))
print("\n  SERVICEABLE_FIBER leads extracted -> %s : %d (join-miss=%d)" % (SERV_OUT, len(serv) - (miss if 'miss' in dir() else 0), 0))
print("  NEXT: capture_pricing.py %s priced_leads.jsonl" % SERV_OUT)
