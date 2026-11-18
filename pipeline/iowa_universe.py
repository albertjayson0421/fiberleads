"""Iowa universe assembly for a ZIP list — fetch NAD (National Address Database)
address points via recursive bbox tiling, filter to the target ZIPs, dedup.
Residential filtering + normalization happen downstream. Resumable per progress.

Usage: iowa_universe.py <zips.txt> <out.jsonl> [--test ZIP]
"""
import sys, os, json, time
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"
sys.path.insert(0, ENG); os.chdir(ENG)
import address_source as A

# Full-state Iowa bounding box (lon_min, lat_min, lon_max, lat_max)
IOWA_BBOX = (-96.72, 40.35, -90.10, 43.55)

def main():
    zips_file = os.path.abspath(sys.argv[1]); out_path = os.path.abspath(sys.argv[2])
    bbox = IOWA_BBOX
    if "--bbox" in sys.argv:
        bbox = tuple(float(x) for x in sys.argv[sys.argv.index("--bbox") + 1].split(","))
    zips = open(zips_file).read().split()
    target = set(zips)
    where = "1=1"
    src = A.NADAddressSource(A.NAD_URL, field_map=A.NAD_FIELDS, name="NAD Iowa")
    t0 = time.time()
    seen = {}
    def prog(n):
        print("  fetched ~%d ..." % n, flush=True)
    rows = src.fetch_bbox(where, bbox, on_progress=prog)
    print("NAD raw points:", len(rows), "in %.0fs" % (time.time() - t0), flush=True)
    kept = 0
    with open(out_path, "w") as f:
        for r in rows:
            z = str(r.get("zip") or "").strip()[:5]
            addr = (r.get("address") or "").strip()
            if not addr or z not in target:
                continue
            k = (addr.upper(), z)
            if k in seen:
                continue
            seen[k] = 1
            city = (r.get("city") or r.get("post_city") or "").strip()
            f.write(json.dumps({"address": addr, "city": city, "state": "IA", "zip": z}) + "\n")
            kept += 1
    print("UNIVERSE_DONE kept=%d unique in-target addresses -> %s (%.0fs)" % (kept, out_path, time.time() - t0), flush=True)

if __name__ == "__main__":
    main()
