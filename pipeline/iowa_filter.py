"""PASS 1 of 2 — capture the COMPLETE Iowa address universe from the 41 GB NAD text
(streamed from inside the zip, no extraction). Keep EVERY Iowa row with its rooftop
point + raw ZIP + address parts. We do NOT filter by ZIP here: NAD Iowa has historically
carried a blank Zip_Code, and filtering on it during the 41 GB scan would silently drop
the whole state and force a re-scan. ZIP assignment/filtering to the 262 targets happens
in a fast re-runnable pass 2 over this small output (iowa_zipfilter.py).
Normalization (Kinetic wants ABBREVIATED) is decided at sweep time; raw NAD address kept here."""
import os, zipfile, io, csv, json, hashlib, time, collections
NAD_DIR = "/Volumes/T7/Masani Files/nad_bulk"
ZIP = "NAD_TXT.zip"   # opened relative, from inside NAD_DIR (matches the verified-working pattern)
OUT = "/Users/masani/iowa_pull/iowa_all.jsonl"
os.chdir(NAD_DIR)
# column indices (NAD r23) — verified against header 2026-09-08
NUM, PREDIR, NAME, POSTYP, POSTDIR, FULLST, UNIT = 4, 6, 9, 10, 11, 13, 16
COUNTY, INCMUNI, POSTCITY, STATE, ZIP_C, LON, LAT, PLACE = 22, 23, 24, 33, 34, 39, 40, 43

z = zipfile.ZipFile(ZIP)
kept = scanned = blank_zip = 0
t0 = time.time(); seen = set(); zip_present = collections.Counter(); county_ct = collections.Counter()
with z.open("TXT/NAD_r23.txt") as f, open(OUT, "w") as out:
    tf = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
    rd = csv.reader(tf); header = next(rd)
    for r in rd:
        scanned += 1
        if scanned % 10000000 == 0:
            print("scanned %d, kept_IA %d (%.0fs)" % (scanned, kept, time.time() - t0), flush=True)
        if len(r) <= PLACE:
            continue
        if r[STATE].strip().upper() not in ("IA", "IOWA"):
            continue
        num = r[NUM].strip()
        stname = r[FULLST].strip() or " ".join(x.strip() for x in (r[PREDIR], r[NAME], r[POSTYP], r[POSTDIR]) if x.strip())
        addr = (num + " " + stname).strip()
        if not addr or not num:
            continue
        unit = r[UNIT].strip()
        full = addr + (" UNIT " + unit if unit else "")
        z5 = r[ZIP_C].strip()[:5]
        if z5 and z5.isdigit():
            zip_present["present"] += 1
        else:
            blank_zip += 1; zip_present["blank"] += 1
        lat = r[LAT].strip(); lon = r[LON].strip()
        # dedup by address + coords (coords disambiguate when ZIP is blank)
        ck = hashlib.sha256((full.upper() + "|" + lat + "|" + lon).encode()).hexdigest()
        if ck in seen:
            continue
        seen.add(ck)
        county_ct[r[COUNTY].strip()] += 1
        out.write(json.dumps({"ck": ck, "address": full, "line1": full,
                              "city": (r[POSTCITY].strip() or r[INCMUNI].strip()), "state": "IA", "zip": z5,
                              "lat": lat, "lon": lon,
                              "placement": r[PLACE].strip(), "county": r[COUNTY].strip()}) + "\n")
        kept += 1
print("\nIOWA_ALL_DONE scanned=%d kept_unique_IA=%d in %.1f min -> %s"
      % (scanned, kept, (time.time() - t0) / 60, OUT), flush=True)
print("ZIP populated among kept IA rows:", dict(zip_present))
print("distinct counties:", len(county_ct), "| top:", dict(county_ct.most_common(10)))
