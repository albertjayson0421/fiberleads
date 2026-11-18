"""Build the canonical exclusion set of ALREADY-DELIVERED Iowa addresses, so the final
Iowa deliverable ships ONLY NEW prospects (FiberLeads requirement 6). Reads every prior
Iowa 'Serviceable' deliverable, canonicalizes each address to abbreviate(UPPER)+zip5 (the
same key the new NAD leads use), writes the key set to prior_delivered_keys.txt."""
import os, sys, glob, json
import flpaths
flpaths.ensure_engine_on_path()
from carrier_uqual import abbreviate
import openpyxl

DELIV = os.environ.get("FL_PRIOR_DELIVERABLES", os.path.join(flpaths.DATA, "prior_deliverables"))
OUT = os.path.join(flpaths.DATA, "prior_delivered_keys.txt")

def key(addr, zc):
    return abbreviate(str(addr).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(zc).strip()[:5]

files = []
for d in sorted(glob.glob(DELIV + "/*")):
    b = os.path.basename(d)
    if (" IA" in b or "Iowa" in b) and os.path.isdir(d):
        for f in glob.glob(d + "/*Serviceable*.xlsx"):
            files.append(f)

keys = set(); per_file = {}
for f in files:
    try:
        wb = openpyxl.load_workbook(f, read_only=True); ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        hdr = [str(c).strip().lower() if c else "" for c in rows[0]]
        ai = next((i for i, h in enumerate(hdr) if h == "address"), 1)
        zi = next((i for i, h in enumerate(hdr) if h in ("zip", "zip code", "zipcode")), None)
        c0 = len(keys)
        for r in rows[1:]:
            if ai < len(r) and r[ai]:
                zc = r[zi] if (zi is not None and zi < len(r) and r[zi]) else ""
                keys.add(key(r[ai], zc))
        per_file[os.path.basename(f)] = len(keys) - c0
        wb.close()
    except Exception as e:
        per_file[os.path.basename(f)] = "ERR:%s" % str(e)[:40]

with open(OUT, "w") as o:
    for k in sorted(keys):
        o.write(k + "\n")
print("prior Iowa deliverables read:", len(files))
for f, n in per_file.items():
    print("  %-55s +%s" % (f, n))
print("TOTAL unique prior-delivered keys:", len(keys), "->", OUT)
