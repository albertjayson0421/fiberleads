"""Rebuild the Iowa 0-49% chart from the FULLY-RESOLVED dispositions (sweep -> remediate1
-> remediate2 city-fix). Every address now has a real terminal answer. 4 live columns:
 Total residential homes; Total residential IN KINETIC TERRITORY (= residential - out-of-
 territory - still-nomatch, i.e. homes Kinetic has ANY record for); Existing customers;
 Serviceable homes (priced fibre plan populates)."""
import json, collections, glob, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

ck_zip = {json.loads(l)["ck"]: str(json.loads(l).get("zip", "")).strip()[:5] for l in open("/Users/masani/iowa_pull/iowa_sweep_final.jsonl")}
orig = {}
for i in range(12):
    try: fh = open("/Users/masani/iowa_pull/ledgers/vm_%02d.jsonl" % i)
    except Exception: continue
    for l in fh:
        try: r = json.loads(l)
        except Exception: continue
        c = r.get("ck")
        if c and c not in orig: orig[c] = (r.get("class"), r.get("reason"))
def load(path):
    d = {}
    try:
        for l in open(path):
            try: r = json.loads(l)
            except Exception: continue
            if r.get("ck"): d[r["ck"]] = r.get("status")
    except FileNotFoundError: pass
    return d
r1 = load("/Users/masani/iowa_pull/remediation_results.jsonl")
r2 = load("/Users/masani/iowa_pull/remediation2_results.jsonl")

def final_bucket(ck):
    # latest resolution wins: remediate2 (city-fix) > remediate1 > sweep
    if ck in r2:
        s = r2[ck]
        return {"SERVICEABLE": "serviceable", "EXISTING": "existing", "NOT_SERVICEABLE": "not_serv",
                "STILL_NOMATCH": "nomatch", "UNRESOLVED": "nomatch"}.get(s, "nomatch")
    if ck in r1:
        s = r1[ck]
        if s == "STILL_NOMATCH": return "nomatch"   # shouldn't happen (all sent to r2) but guard
        return {"SERVICEABLE": "serviceable", "EXISTING": "existing", "NOT_SERVICEABLE": "not_serv"}.get(s, "nomatch")
    cls, reason = orig.get(ck, (None, None))
    if cls == "SERVICEABLE_FIBER": return "serviceable"
    if cls == "EXCLUDED_EXISTING": return "existing"
    if cls == "EXCLUDED_PENDING_ORDER": return "pending"
    if cls == "DEFINITIVE_NONFIBER": return "not_serv"
    if cls == "DEFINITIVE_OUT_OF_TERRITORY": return "out_terr"
    if cls == "COMPLETE_REJECTED":
        return "unserv_in_terr" if reason == "AddressUnserviceableInTerritory" else "nomatch"
    return "nomatch"

Z = collections.defaultdict(collections.Counter); T = collections.Counter()
for ck, z in ck_zip.items():
    b = final_bucket(ck); Z[z][b] += 1; Z[z]["residential"] += 1; T[b] += 1; T["residential"] += 1
# in Kinetic territory = residential - out_of_territory - nomatch (homes Kinetic has any record for)
json.dump({z: dict(c) for z, c in Z.items()}, open("/Users/masani/iowa_pull/zip_disposition_v2.json", "w"))
def interr(d): return d.get("serviceable",0)+d.get("existing",0)+d.get("pending",0)+d.get("not_serv",0)+d.get("unserv_in_terr",0)
print("=== FULLY-RESOLVED IA funnel ===")
for k in ("residential","serviceable","existing","pending","not_serv","unserv_in_terr","out_terr","nomatch"):
    print("  %-16s %d" % (k, T[k]))
tot_interr=T["serviceable"]+T["existing"]+T["pending"]+T["not_serv"]+T["unserv_in_terr"]
print("  IN KINETIC TERRITORY:", tot_interr, "| serviceable:", T["serviceable"])

# rebuild workbook
SRC = glob.glob("/Users/masani/Desktop/*FTTP Competition by ZIP  TX GA IA KY SC NC.xlsx")[0]
OUT = "/Users/masani/Desktop/09.09.2026 - FTTP Competition + Kinetic Homes (IA) - RESOLVED.xlsx"
wb = openpyxl.load_workbook(SRC); ws = wb["IA"]
hr = 2; headers = {ws.cell(hr, c).value: c for c in range(1, ws.max_column + 1)}
zc = headers["ZIP"]; bc = headers["FTTP competition"]; last = ws.max_column
NEW = ["Total residential homes", "Total kinetic homes in territory", "Existing customers", "Serviceable homes"]
green = PatternFill("solid", fgColor="548235"); HF = Font(bold=True, color="FFFFFF", size=10)
ws.cell(1, last + 1, "Homes — live Kinetic check, fully resolved (city-fix remediation) 2026-09-09").font = Font(bold=True, italic=True, size=9, color="548235")
for i, n in enumerate(NEW):
    cell = ws.cell(hr, last + 1 + i, n); cell.font = HF; cell.fill = green; cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[openpyxl.utils.get_column_letter(last + 1 + i)].width = 13
band_ok = {"0–30%", "31–49%", "0-30%", "31-49%"}; ZERO = {"50012", "50163", "52344"}; filled = 0
for row in range(hr + 1, ws.max_row + 1):
    z = ws.cell(row, zc).value
    if z is None: continue
    z = str(z).strip(); b = str(ws.cell(row, bc).value).strip()
    if b not in band_ok: continue
    if z in ZERO:
        for i in range(4): ws.cell(row, last + 1 + i, "n/a")
        filled += 1; continue
    d = Z.get(z, {}); serv = d.get("serviceable", 0); ex = d.get("existing", 0)
    for i, v in enumerate([d.get("residential", 0), interr(d), ex, serv]):
        cell = ws.cell(row, last + 1 + i, v); cell.number_format = "#,##0"; cell.alignment = Alignment(horizontal="center")
    filled += 1
wb.save(OUT)
print("REBUILT (%d IA rows):" % filled, OUT)
