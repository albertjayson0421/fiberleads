"""Merge the remediation results back into the per-ZIP disposition and rebuild the IA-tab
workbook with the corrected counts. After full remediation, an address that STILL_NOMATCH
(every variant tried, Kinetic has no record) is treated as not-serviceable-not-in-footprint;
only genuine infra failures remain 'unresolved'."""
import os, json, collections, glob, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import flpaths

ck_zip = {json.loads(l)["ck"]: str(json.loads(l).get("zip", "")).strip()[:5] for l in open(flpaths.data("iowa_sweep_final.jsonl"))}
# original sweep class+reason
orig = {}
for i in range(12):
    try: fh = open(os.path.join(flpaths.LEDGERS, "vm_%02d.jsonl" % i))
    except Exception: continue
    for l in fh:
        try: r = json.loads(l)
        except Exception: continue
        c = r.get("ck")
        if c and c not in orig: orig[c] = (r.get("class"), r.get("reason"))
# remediation results
remed = {}
try:
    for l in open(flpaths.data("remediation_results.jsonl")):
        try: r = json.loads(l)
        except Exception: continue
        if r.get("ck"): remed[r["ck"]] = r.get("status")
except FileNotFoundError:
    print("no remediation_results.jsonl yet"); raise SystemExit

def bucket(ck):
    if ck in remed:
        s = remed[ck]
        return {"SERVICEABLE": "serviceable", "EXISTING": "existing", "NOT_SERVICEABLE": "not_serviceable",
                "STILL_NOMATCH": "not_serviceable", "UNRESOLVED": "unresolved"}.get(s, "unresolved")
    cls, reason = orig.get(ck, (None, None))
    if cls == "SERVICEABLE_FIBER": return "serviceable"
    if cls == "EXCLUDED_EXISTING": return "existing"
    if cls == "EXCLUDED_PENDING_ORDER": return "pending"
    if cls in ("DEFINITIVE_NONFIBER", "DEFINITIVE_OUT_OF_TERRITORY"): return "not_serviceable"
    if cls == "COMPLETE_REJECTED": return "not_serviceable" if reason == "AddressUnserviceableInTerritory" else "unresolved"
    return "unresolved"

Z = collections.defaultdict(collections.Counter); T = collections.Counter()
for ck, z in ck_zip.items():
    b = bucket(ck); Z[z][b] += 1; Z[z]["residential"] += 1; T[b] += 1; T["residential"] += 1
json.dump({z: dict(c) for z, c in Z.items()}, open(flpaths.data("zip_disposition_final.json"), "w"))
print("=== corrected funnel ===")
for k in ("residential", "serviceable", "existing", "pending", "not_serviceable", "unresolved"):
    print("  %-16s %d" % (k, T[k]))
print("  kinetic fibre-reachable (serv+exist+pend):", T["serviceable"] + T["existing"] + T["pending"])

# rebuild the IA-tab workbook
SRC = glob.glob(flpaths.data("*FTTP Competition by ZIP*.xlsx"))[0]
OUT = flpaths.out("FTTP Competition by ZIP - with Kinetic Homes.xlsx")
wb = openpyxl.load_workbook(SRC); ws = wb["IA"]
hdr_row = 2; headers = {ws.cell(hdr_row, c).value: c for c in range(1, ws.max_column + 1)}
zip_c = headers["ZIP"]; band_c = headers["FTTP competition"]; last = ws.max_column
NEW = ["Total residential homes", "Total kinetic homes in territory", "Existing customers", "Serviceable homes"]
green = PatternFill("solid", fgColor="548235"); HF = Font(bold=True, color="FFFFFF", size=10)
ws.cell(1, last + 1, "Homes — live Kinetic check + remediation 2026-09-08").font = Font(bold=True, italic=True, size=9, color="548235")
for i, name in enumerate(NEW):
    cell = ws.cell(hdr_row, last + 1 + i, name); cell.font = HF; cell.fill = green
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[openpyxl.utils.get_column_letter(last + 1 + i)].width = 13
band_ok = {"0–30%", "31–49%", "0-30%", "31-49%"}
ZERO = {"50012", "50163", "52344"}
filled = 0
for row in range(hdr_row + 1, ws.max_row + 1):
    z = ws.cell(row, zip_c).value
    if z is None: continue
    z = str(z).strip(); bnd = str(ws.cell(row, band_c).value).strip()
    if bnd not in band_ok: continue
    if z in ZERO:
        for i in range(4): ws.cell(row, last + 1 + i, "n/a")
        filled += 1; continue
    d = Z.get(z, {}); serv = d.get("serviceable", 0); ex = d.get("existing", 0); pend = d.get("pending", 0)
    for i, v in enumerate([d.get("residential", 0), serv + ex + pend, ex, serv]):
        cell = ws.cell(row, last + 1 + i, v); cell.number_format = "#,##0"; cell.alignment = Alignment(horizontal="center")
    filled += 1
wb.save(OUT)
print("REBUILT workbook (%d IA rows filled):" % filled, OUT)
