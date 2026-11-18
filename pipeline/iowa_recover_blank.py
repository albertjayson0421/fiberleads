"""Recover blank-ZIP Iowa NAD rows (Keokuk/Louisa/Lee/Appanoose null-ZIP case) by
point-in-polygon against the 262 target ZCTA polygons (Census TIGERweb layer 2). NAD
gives 100% coords, so a blank-ZIP row still has a rooftop point we can place in a ZCTA.
Nothing is dropped: a row inside a target ZCTA is recovered with its assigned ZIP; a row
outside all 262 is genuinely out of scope. Completeness rule — blank != out-of-scope."""
import json, time, urllib.request, urllib.parse

BASE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2/query"
BLANK = "/Users/masani/iowa_pull/iowa_blank.jsonl"
OUT = "/Users/masani/iowa_pull/iowa_recovered.jsonl"
target = sorted(set(open("/Users/masani/iowa_pull/zips.txt").read().split()))

def fetch_zctas(zips):
    polys = {}   # zip -> list of rings
    for i in range(0, len(zips), 40):
        chunk = zips[i:i + 40]
        where = "BASENAME IN (%s)" % ",".join("'%s'" % z for z in chunk)
        qs = urllib.parse.urlencode({"where": where, "outFields": "BASENAME",
                                     "returnGeometry": "true", "outSR": "4326", "f": "json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(BASE + "?" + qs, timeout=60) as r:
                    d = json.loads(r.read())
                break
            except Exception as e:
                if attempt == 2:
                    print("  chunk %d FAILED: %s" % (i, e)); d = {"features": []}
                time.sleep(2)
        for f in d.get("features", []):
            z = f["attributes"]["BASENAME"]
            polys.setdefault(z, []).extend(f["geometry"].get("rings", []))
        print("  fetched ZCTAs %d..%d (have %d polys)" % (i, i + len(chunk), len(polys)), flush=True)
    return polys

def bbox(rings):
    xs = [p[0] for r in rings for p in r]; ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)

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

print("fetching %d target ZCTA polygons ..." % len(target), flush=True)
polys = fetch_zctas(target)
idx = [(z, bbox(rings), rings) for z, rings in polys.items() if rings]
print("built index: %d ZCTA polygons" % len(idx), flush=True)

n = rec = 0; t0 = time.time(); per_zip = {}
with open(OUT, "w") as out:
    for line in open(BLANK):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line); x = float(r["lon"]); y = float(r["lat"])
        except Exception:
            continue
        n += 1
        if n % 50000 == 0:
            print("  scanned %d blank rows, recovered %d (%.0fs)" % (n, rec, time.time() - t0), flush=True)
        for z, (x0, y0, x1, y1), rings in idx:
            if x0 <= x <= x1 and y0 <= y <= y1 and inside(rings, x, y):
                r["zip"] = z; r["zip_source"] = "zcta_recovered"
                out.write(json.dumps(r) + "\n"); rec += 1
                per_zip[z] = per_zip.get(z, 0) + 1
                break
print("\nRECOVER_DONE scanned=%d recovered_in_target=%d in %.1f min -> %s"
      % (n, rec, (time.time() - t0) / 60, OUT), flush=True)
print("distinct ZIPs recovered:", len(per_zip))
top = sorted(per_zip.items(), key=lambda kv: -kv[1])[:15]
print("top recovered ZIPs:", top)
