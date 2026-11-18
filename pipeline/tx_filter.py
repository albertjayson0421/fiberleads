"""Extract TX residential address points from NAD in the 201 Kinetic-FTTH ZIPs (the
authoritative universe for the Texas 120K rebuild). Same one-pass in-zip stream as the
Iowa filter. Keeps raw NAD address + rooftop point; residential filter + sweep downstream."""
import os, zipfile, io, csv, json, hashlib, time, collections
NAD_DIR = "/Volumes/T7/Masani Files/nad_bulk"; ZIP = "NAD_TXT.zip"
OUT = "/Users/masani/iowa_pull/tx_fiber_universe.jsonl"
os.chdir(NAD_DIR)
fiber = set(json.load(open("/Users/masani/iowa_pull/tx_kinetic_fiber_zips.json")))
NUM, PREDIR, NAME, POSTYP, POSTDIR, FULLST, UNIT = 4, 6, 9, 10, 11, 13, 16
COUNTY, INCMUNI, POSTCITY, STATE, ZIP_C, LON, LAT, PLACE = 22, 23, 24, 33, 34, 39, 40, 43
z = zipfile.ZipFile(ZIP)
kept = scanned = 0; t0 = time.time(); seen = set(); per_zip = collections.Counter()
with z.open("TXT/NAD_r23.txt") as f, open(OUT, "w") as out:
    tf = io.TextIOWrapper(f, encoding="utf-8", errors="replace"); rd = csv.reader(tf); next(rd)
    for r in rd:
        scanned += 1
        if scanned % 10000000 == 0:
            print("scanned %d, kept_TX %d (%.0fs)" % (scanned, kept, time.time() - t0), flush=True)
        if len(r) <= PLACE: continue
        if r[STATE].strip().upper() not in ("TX", "TEXAS"): continue
        z5 = r[ZIP_C].strip()[:5]
        if z5 not in fiber: continue
        num = r[NUM].strip()
        stname = r[FULLST].strip() or " ".join(x.strip() for x in (r[PREDIR], r[NAME], r[POSTYP], r[POSTDIR]) if x.strip())
        addr = (num + " " + stname).strip()
        if not addr or not num: continue
        unit = r[UNIT].strip(); full = addr + (" UNIT " + unit if unit else "")
        lat = r[LAT].strip(); lon = r[LON].strip()
        ck = hashlib.sha256((full.upper() + "|" + lat + "|" + lon).encode()).hexdigest()
        if ck in seen: continue
        seen.add(ck); per_zip[z5] += 1
        out.write(json.dumps({"ck": ck, "line1": full, "address": full,
                              "city": (r[POSTCITY].strip() or r[INCMUNI].strip()), "state": "TX", "zip": z5,
                              "lat": lat, "lon": lon, "placement": r[PLACE].strip(), "county": r[COUNTY].strip()}) + "\n")
        kept += 1
print("\nTX_FIBER_UNIVERSE_DONE scanned=%d kept=%d in %.1f min -> %s" % (scanned, kept, (time.time() - t0) / 60, OUT))
print("distinct fiber ZIPs with TX NAD rows:", len(per_zip), "of", len(fiber))
