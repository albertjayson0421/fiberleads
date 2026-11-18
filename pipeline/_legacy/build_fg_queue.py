"""Assemble the final sweep queue from the residential-KEPT rows of both towns,
in the EXACT pipeline schema {ck,line1,city,state,zip,lat,lon}. ck is recomputed
from scratch = sha256(NORM(line1)|zip)[:24]. Kept rows are the engine-certified
residences (pin inside a footprint, or within 10 m of one); held/dropped rows are
NOT shipped -- same operating point as the reference Iowa run. Every shipped row
must carry a real, parseable lat/lon (no fabrication)."""
import json, hashlib, re, collections

def norm(s): return re.sub(r"[^A-Z0-9]", "", (s or "").upper())
def ck24(line1, zip_): return hashlib.sha256((norm(line1) + "|" + zip_).encode()).hexdigest()[:24]

SRC = {"52556": ("Fairfield", "/Users/masani/iowa_pull/fairfield_kept.jsonl"),
       "50136": ("Gladbrook", "/Users/masani/iowa_pull/gladbrook_kept.jsonl")}
OUT = "/Users/masani/iowa_pull/fairfield_gladbrook_queue.jsonl"

rows = []
qual = {z: collections.Counter() for z in SRC}
per_town = collections.Counter()
seen = set()          # global ck dedupe (towns disjoint, but prove it)
dupe = 0
badcoord = 0
for zip_, (city, path) in SRC.items():
    for l in open(path):
        l = l.strip()
        if not l: continue
        r = json.loads(l)
        # real coordinate is mandatory
        try:
            float(r["lat"]); float(r["lon"])
        except (KeyError, TypeError, ValueError):
            badcoord += 1; continue
        ev = r.get("residential_evidence", "")
        qual[zip_]["on_structure" if "inside the footprint" in ev else "near"] += 1
        ck = ck24(r["line1"], zip_)
        if ck in seen:
            dupe += 1; continue
        seen.add(ck)
        rows.append({"ck": ck, "line1": r["line1"], "city": city, "state": "IA",
                     "zip": zip_, "lat": str(r["lat"]), "lon": str(r["lon"])})
        per_town[city] += 1

with open(OUT, "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")

print("WROTE %s  lines=%d" % (OUT, len(rows)))
print("per town:", dict(per_town))
print("coord quality (shipped): ", {SRC[z][0]: dict(qual[z]) for z in SRC})
print("dropped for bad coord:", badcoord, " cross-town dup ck collisions:", dupe)
