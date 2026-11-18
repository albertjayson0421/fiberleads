"""Independent verification of the final deliverable CSV (the build_delivery output) - run before
calling a deliverable ready. Checks, on 100% of rows: Address/City present; rooftop lat/lon present
and numeric; a populated FiberPlan; NONE already-delivered (new-prospects-only); no duplicate
(Address,Zip); no stacked coords (<4m apart = same pin). FAILS first.

Per-market checks (state, target ZIP set, bounding box) are optional and come from market.json when
configured; absent -> that check is skipped, so this works for any market, not just Iowa.

Usage: verify_deliverable.py [deliverable.csv]   (defaults to the newest *.csv under the out dir)
"""
import os
import sys
import csv
import glob
import collections
import math
import flpaths
flpaths.ensure_engine_on_path()
from carrier_uqual import abbreviate

# Optional per-market checks (all optional; absent -> that check is skipped).
_m = flpaths._market
STATE = _m.get("state")                                              # e.g. "IA"; None -> no state check
TARGET_ZIPS = set(str(z).strip()[:5] for z in _m.get("target_zips", []))  # empty -> no zip-target check
BBOX = _m.get("bbox")                                               # [lat_min,lat_max,lon_min,lon_max] or None

csv_path = sys.argv[1] if len(sys.argv) > 1 else None
if not csv_path:
    cands = sorted(glob.glob(os.path.join(flpaths.OUT, "*.csv")))
    csv_path = cands[-1] if cands else None
if not csv_path or not os.path.exists(csv_path):
    print("VERIFY: no deliverable CSV found (pass a path, or put one under %s)" % flpaths.OUT)
    sys.exit(2)

prior_path = os.path.join(flpaths.DATA, "prior_delivered_keys.txt")
prior = set(l.strip() for l in open(prior_path) if l.strip()) if os.path.exists(prior_path) else set()

with open(csv_path, newline="") as fh:
    data = list(csv.DictReader(fh))

fails = collections.Counter()
addr_seen = collections.Counter()
coords = []
for r in data:
    addr = (r.get("Address") or "").strip()
    city = (r.get("City") or "").strip()
    st = (r.get("State") or "").strip()
    zc = str(r.get("Zip") or "").strip()[:5]
    plan = (r.get("FiberPlan") or "").strip()
    if not addr or not city:
        fails["missing_address_or_city"] += 1
    if STATE and st != STATE:
        fails["state_not_%s" % STATE] += 1
    if TARGET_ZIPS and zc not in TARGET_ZIPS:
        fails["zip_not_in_target"] += 1
    try:
        la = float(r.get("Latitude"))
        lo = float(r.get("Longitude"))
        if BBOX and not (BBOX[0] <= la <= BBOX[1] and BBOX[2] <= lo <= BBOX[3]):
            fails["coord_outside_bbox"] += 1
        coords.append((la, lo))
    except Exception:
        fails["coord_missing_or_bad"] += 1
    if not plan:
        fails["no_fiber_plan"] += 1
    if addr and zc and (abbreviate(addr.upper().replace(".", " ").replace(",", " ")).strip() + "|" + zc) in prior:
        fails["already_delivered_leaked"] += 1
    addr_seen[(addr.upper(), zc)] += 1

dups = sum(c - 1 for c in addr_seen.values() if c > 1)
# stacked-coord check: grid-bucket to find near-duplicate pins
bucket = collections.defaultdict(list)
for la, lo in coords:
    bucket[(round(la, 4), round(lo, 4))].append((la, lo))
stacked = 0
for _, pts in bucket.items():
    if len(pts) > 1:
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dy = (pts[j][0] - pts[i][0]) * 111320.0
                dx = (pts[j][1] - pts[i][1]) * 111320.0 * math.cos(math.radians(pts[i][0]))
                if math.hypot(dx, dy) < 4.0:
                    stacked += 1
                    break

print("VERIFY:", os.path.basename(csv_path))
print("  rows:", len(data))
any_fail = False
print("  --- FAILURES (must all be 0) ---")
for k, v in fails.most_common():
    if v:
        print("  FAIL %-28s %d" % (k, v))
        any_fail = True
print("  duplicate (address,zip) rows:", dups, ("FAIL" if dups else "ok"))
print("  stacked coords (<4m apart)  :", stacked, ("FAIL" if stacked else "ok"))
print("  VERDICT:", "FAIL" if (any_fail or dups or stacked) else "PASS - all rows clean")
sys.exit(1 if (any_fail or dups or stacked) else 0)
