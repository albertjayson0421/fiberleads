"""Step 2 (authorized 2026-09-10): prepare the next targeted recovery slice from the
PREVIOUSLY VERIFIED fiber cities, NEW-only vs the delivery baseline, provenance preserved.

RULES honored:
- Residential filter FIRST: READY slice draws ONLY from rooftop-grade, already-residential-
  filtered sources (the *_kept files carrying residential_evidence + footprint placement).
- NEW-only: anti-join vs prior_delivered_keys.txt using the FROZEN carrier_uqual.abbreviate
  (c02e20cf) exactly as prior_delivered.py built the baseline. A row is DELIVERED if EITHER its
  line1-key OR address-key is in the baseline (conservative; never re-deliver).
- Do NOT infer serviceability from city membership: every row is a CANDIDATE TO CHECK.
- Preserve provenance (source file), ZIP, exact premise identity (ck/line1/unit), coords, evidence.
"""
import json, os, sys, hashlib, collections
sys.path.insert(0, "/Users/masani/FLEET_5K/merge/engine")   # read-only import of FROZEN engine
from carrier_uqual import abbreviate

PROVEN = {"CENTERVILLE","KNOXVILLE","TAMA","TOLEDO","GARWIN","MYSTIC","FAIRFIELD",
          "TRAER","MORAVIA","EXLINE","LE GRAND","SEYMOUR"}
# rooftop-grade, residential-filtered sources (carry residential_evidence)
SRC = ["target_kept.jsonl", "canary_kept.jsonl", "fairfield_kept.jsonl",
       "surrounding_kept.jsonl", "cities_kept.jsonl"]
BASE = "/Users/masani/iowa_pull"
DELIVERED = set(x for x in open(os.path.join(BASE,"prior_delivered_keys.txt")).read().split("\n") if x)

def key(addr, zc):
    return abbreviate(str(addr).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(zc).strip()[:5]

seen_ck = {}
rows = []
for f in SRC:
    p = os.path.join(BASE, f)
    if not os.path.exists(p): continue
    for ln in open(p):
        ln = ln.strip()
        if not ln: continue
        try: d = json.loads(ln)
        except: continue
        if (d.get("state") or "").upper() != "IA": continue
        city = (d.get("city") or "").strip().upper()
        if city not in PROVEN: continue
        z = str(d.get("zip") or "").strip()
        if not (z and d.get("lat") and d.get("lon") and d.get("residential_evidence")): continue
        ck = d.get("ck")
        if ck in seen_ck: continue
        seen_ck[ck] = 1
        d["_source_file"] = f
        rows.append(d)

# NEW-only anti-join
new_rows = []; per_city = collections.defaultdict(lambda: {"new":0,"delivered":0})
for d in rows:
    z = str(d.get("zip") or "").strip()[:5]
    k_line1 = key(d.get("line1") or "", z)
    k_addr  = key(d.get("address") or d.get("line1") or "", z)
    delivered = (k_line1 in DELIVERED) or (k_addr in DELIVERED)
    city = (d.get("city") or "").strip().upper()
    if delivered:
        per_city[city]["delivered"] += 1
    else:
        per_city[city]["new"] += 1
        d["_new_key"] = k_line1
        d["_slice"] = "verified-fiber-cities NEW candidates 2026-09-10"
        d["_note"] = "CANDIDATE TO CHECK; serviceability NOT inferred from city membership"
        new_rows.append(d)

OUT = os.path.join(BASE, "iowa_fiber_city_slice_2026-09-10.jsonl")
with open(OUT, "w") as o:
    for d in new_rows:
        o.write(json.dumps(d) + "\n")
sha = hashlib.sha256(open(OUT,"rb").read()).hexdigest()[:16]

print("=== READY slice (rooftop-grade, residential-filtered, NEW-only) ===")
tot_new = tot_del = 0
for c in sorted(per_city):
    n = per_city[c]["new"]; dl = per_city[c]["delivered"]; tot_new += n; tot_del += dl
    print(f"  {c:12s} NEW={n:5d}  already-delivered(excluded)={dl:5d}")
print(f"  {'TOTAL':12s} NEW={tot_new:5d}  already-delivered(excluded)={tot_del:5d}")
print(f"rooftop-grade candidate rows scanned: {len(rows)}")
print(f"OUT: {OUT}")
print(f"sha256_16: {sha}")
