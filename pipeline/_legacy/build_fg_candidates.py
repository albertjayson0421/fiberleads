"""Build residential-CANDIDATE universes for Fairfield 52556 (Jefferson Co) and
Gladbrook 50136 (Tama Co) from the National Address Database (iowa_all.jsonl).

Membership (UNION for completeness -- never silently omit an in-town address):
  a NAD row belongs to town T (zip Z, ZCTA polygon P_Z) if
     NAD.zip == Z   OR   point(lon,lat) inside P_Z.
NAD zip is blank on most rural Iowa rows, so ZCTA point-in-polygon (outSR=4326,
exact method from iowa_recover_blank.py) is what recovers them; the zip-field arm
catches any row the ZCTA boundary clips just outside. zip is then set to the
town's Z and city to the town name.

Dedupe by canonical key ck = sha256(NORM(line1)|Z)[:24], NORM=strip to A-Z0-9
uppercase. Duplicate rows (same ck) are DUPLICATE_ROWS, counted, not shipped
twice. No coordinate is fabricated -- a row with no parseable lon/lat is dropped
from candidacy and counted (it cannot be point-tested or placed on a roof).

Writes <town>_candidates.jsonl carrying every NAD field (lat/lon needed by the
FEMA residential filter downstream) plus city/zip/zip_source stamped.
"""
import json, hashlib, re, urllib.request, urllib.parse, time, sys

NAD = "/Users/masani/iowa_pull/iowa_all.jsonl"
BASE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2/query"
TOWNS = {  # zip -> (city, out_path)
    "52556": ("Fairfield", "/Users/masani/iowa_pull/fairfield_candidates.jsonl"),
    "50136": ("Gladbrook", "/Users/masani/iowa_pull/gladbrook_candidates.jsonl"),
}

def norm(s): return re.sub(r"[^A-Z0-9]", "", (s or "").upper())
def ck24(line1, zip_): return hashlib.sha256((norm(line1) + "|" + zip_).encode()).hexdigest()[:24]

def fetch_zcta(z):
    qs = urllib.parse.urlencode({"where": "BASENAME='%s'" % z, "outFields": "BASENAME",
                                 "returnGeometry": "true", "outSR": "4326", "f": "json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(BASE + "?" + qs, timeout=90) as r:
                d = json.loads(r.read())
            rings = [ring for f in d.get("features", []) for ring in f["geometry"].get("rings", [])]
            if not rings:
                raise RuntimeError("no rings returned for %s" % z)
            xs = [p[0] for ring in rings for p in ring]; ys = [p[1] for ring in rings for p in ring]
            return (min(xs), min(ys), max(xs), max(ys)), rings
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2)

def inside(rings, x, y):
    c = False
    for ring in rings:
        n = len(ring); j = n - 1
        for k in range(n):
            xi, yi = ring[k][0], ring[k][1]; xj, yj = ring[j][0], ring[j][1]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi):
                c = not c
            j = k
    return c

print("fetching ZCTA polygons ...", flush=True)
poly = {}
for z in TOWNS:
    bb, rings = fetch_zcta(z)
    poly[z] = (bb, rings)
    print("  %s bbox=%s rings=%d verts=%d" % (z, bb, len(rings), sum(len(r) for r in rings)), flush=True)

# per town: ck -> row ; plus accounting
kept = {z: {} for z in TOWNS}
by_zipfield = {z: 0 for z in TOWNS}
by_geometry = {z: 0 for z in TOWNS}
by_both = {z: 0 for z in TOWNS}
dup_rows = {z: 0 for z in TOWNS}
no_coord = {z: 0 for z in TOWNS}
n = 0; t0 = time.time()
for line in open(NAD):
    line = line.strip()
    if not line:
        continue
    n += 1
    if n % 200000 == 0:
        print("  scanned %d NAD rows (%.0fs)" % (n, time.time() - t0), flush=True)
    try:
        r = json.loads(line)
    except Exception:
        continue
    zf = (r.get("zip") or "").strip()
    for z, (bb, rings) in poly.items():
        zip_match = (zf == z)
        geo_match = False
        try:
            x = float(r["lon"]); y = float(r["lat"])
            if bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3] and inside(rings, x, y):
                geo_match = True
        except (KeyError, TypeError, ValueError):
            if zip_match:  # zip says in-town but no usable coordinate -> cannot place/screen
                no_coord[z] += 1
            continue
        if not (zip_match or geo_match):
            continue
        city, _ = TOWNS[z]
        src = "zip+zcta" if (zip_match and geo_match) else ("zipfield" if zip_match else "zcta")
        if zip_match and geo_match: by_both[z] += 1
        elif zip_match: by_zipfield[z] += 1
        else: by_geometry[z] += 1
        key = ck24(r.get("line1", ""), z)
        row = {**r, "city": city, "zip": z, "state": "IA", "zip_source": src, "_ck": key}
        if key in kept[z]:
            dup_rows[z] += 1
        else:
            kept[z][key] = row
        break  # towns are geographically disjoint; a row belongs to at most one

for z, (city, outp) in TOWNS.items():
    with open(outp, "w") as f:
        for row in kept[z].values():
            f.write(json.dumps(row) + "\n")
    print("\n%s %s -> %s" % (z, city, outp))
    print("  unique candidates (by ck): %d" % len(kept[z]))
    print("  membership: zipfield-only=%d  zcta-only=%d  both=%d"
          % (by_zipfield[z], by_geometry[z], by_both[z]))
    print("  DUPLICATE_ROWS collapsed: %d" % dup_rows[z])
    print("  zip-field rows with no usable coordinate (excluded, counted): %d" % no_coord[z])

print("\nCANDIDATES_DONE scanned=%d rows in %.1f min" % (n, (time.time() - t0) / 60), flush=True)
