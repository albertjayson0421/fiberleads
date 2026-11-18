"""Build the Iowa 0-49% (knockable) competition chart: every IA 0-49% ZIP from the FTTP
Competition sheet, with all its original columns, PLUS four absolute home-count columns
from the live Kinetic sweep — placed right after the competition band. Adds Summary + Legend
tabs. Counts are live-verified (my 262-ZIP sweep), not the FCC filing (which lags)."""
import openpyxl, glob, json, collections
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SRC = glob.glob("/Users/masani/Desktop/*FTTP Competition by ZIP*.xlsx")[0]
OUT = "/Users/masani/iowa_pull/deliverable/09.08.2026 - Iowa 0-49pct Knockable - Homes by ZIP.xlsx"
metrics = json.load(open("/Users/masani/iowa_pull/zip_metrics.json"))

wb_src = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
ws_ia = wb_src["IA"]; rows = list(ws_ia.iter_rows(values_only=True))
hdr = rows[1]; H = {h: i for i, h in enumerate(hdr)}
def gv(r, name): return r[H[name]] if name in H and H[name] < len(r) else None

# collect the 262 0-49% rows
band_ok = {"0–30%", "31–49%", "0-30%", "31-49%"}
data = [r for r in rows[2:] if r[H["ZIP"]] and str(gv(r, "FTTP competition")).strip() in band_ok]
data.sort(key=lambda r: str(r[H["ZIP"]]))

# ---- output ----
wb = openpyxl.Workbook()
ws = wb.active; ws.title = "IA 0-49% Knockable"

NEW = ["Total residential homes", "Total Kinetic homes in territory", "Existing customers", "Serviceable homes"]
# original columns to carry (in a sensible order), band first, then the 4 counts, then the detail cols
ORIG_HEAD = ["ZIP", "City", "County", "FTTP competition"]
ORIG_TAIL = ["Competing FTTP % of homes", "Kinetic fibre % of homes", "Competing % of all fibre",
             "Cable", "Any fibre", "Fixed wireless", "DSL", "Satellite",
             "Competing FTTP providers (% of homes)", "Cable operators (% of homes)", "Other counties in ZIP"]
COLS = ORIG_HEAD + NEW + ORIG_TAIL

# styles
HFILL = PatternFill("solid", fgColor="1F3864"); HFONT = Font(bold=True, color="FFFFFF", size=10)
NFILL = PatternFill("solid", fgColor="C55A11"); SFILL = PatternFill("solid", fgColor="548235")
thin = Side(style="thin", color="D9D9D9"); BORD = Border(left=thin, right=thin, top=thin, bottom=thin)
title = ws.cell(1, 1, "Iowa — Kinetic fibre homes by ZIP (knockable list: FTTP competition 0–49%)")
title.font = Font(bold=True, size=14, color="1F3864")
sub = ws.cell(2, 1, "262 ZIPs · counts are LIVE Kinetic serviceability (exact-address sweep, 2026-09-08), not the FCC filing · competition bands from FCC BDC 12/31/2025")
sub.font = Font(italic=True, size=9, color="808080")
# header row at row 4
for j, c in enumerate(COLS, 1):
    cell = ws.cell(4, j, c); cell.font = HFONT; cell.fill = HFILL
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = BORD
    if c in ("Total Kinetic homes in territory", "Existing customers"): cell.fill = NFILL
    if c == "Serviceable homes": cell.fill = SFILL

miss0 = []
r_out = 5
for r in data:
    z = str(r[H["ZIP"]]).strip()
    m = metrics.get(z, {})
    if not m: miss0.append(z)
    resid = m.get("residential", 0); serv = m.get("serviceable", 0); exist = m.get("existing", 0); pend = m.get("pending", 0)
    kin = serv + exist + pend
    vals = {"ZIP": z, "City": gv(r, "City"), "County": gv(r, "County"), "FTTP competition": gv(r, "FTTP competition"),
            "Total residential homes": resid, "Total Kinetic homes in territory": kin,
            "Existing customers": exist, "Serviceable homes": serv}
    for name in ORIG_TAIL: vals[name] = gv(r, name)
    for j, c in enumerate(COLS, 1):
        cell = ws.cell(r_out, j, vals.get(c)); cell.border = BORD; cell.font = Font(size=10)
        if c in ("Total residential homes", "Total Kinetic homes in territory", "Existing customers", "Serviceable homes"):
            cell.number_format = "#,##0"; cell.alignment = Alignment(horizontal="center")
        if c == "Serviceable homes":
            cell.font = Font(size=10, bold=True, color="548235")
    r_out += 1
ws.freeze_panes = "A5"
ws.auto_filter.ref = "A4:%s%d" % (get_column_letter(len(COLS)), r_out - 1)
widths = {"ZIP": 8, "City": 16, "County": 16, "FTTP competition": 13, "Total residential homes": 12,
          "Total Kinetic homes in territory": 13, "Existing customers": 11, "Serviceable homes": 11,
          "Competing FTTP providers (% of homes)": 40, "Cable operators (% of homes)": 26, "Other counties in ZIP": 22}
for j, c in enumerate(COLS, 1): ws.column_dimensions[get_column_letter(j)].width = widths.get(c, 11)

# ---- SUMMARY ----
tot = collections.Counter()
band_tot = collections.defaultdict(collections.Counter)
for r in data:
    z = str(r[H["ZIP"]]).strip(); m = metrics.get(z, {}); band = str(gv(r, "FTTP competition")).strip()
    serv = m.get("serviceable", 0); exist = m.get("existing", 0); pend = m.get("pending", 0); resid = m.get("residential", 0)
    for k, v in (("residential", resid), ("serviceable", serv), ("existing", exist), ("pending", pend), ("kinetic", serv + exist + pend), ("zips", 1)):
        tot[k] += v; band_tot[band][k] += v

wss = wb.create_sheet("Summary")
wss.cell(1, 1, "Iowa 0–49% Knockable — Summary").font = Font(bold=True, size=14, color="1F3864")
rows_sum = [
    ("", ""),
    ("ZIPs (0–49% FTTP competition)", tot["zips"]),
    ("  · 0–30% band", band_tot["0–30%"]["zips"]),
    ("  · 31–49% band", band_tot["31–49%"]["zips"]),
    ("", ""),
    ("Total residential homes (swept)", tot["residential"]),
    ("Total Kinetic homes in territory (fibre populates)", tot["serviceable"] + tot["existing"] + tot["pending"]),
    ("Existing Kinetic customers", tot["existing"]),
    ("Serviceable homes (NEW-customer fibre leads)", tot["serviceable"]),
    ("  · pending order (excluded from leads)", tot["pending"]),
    ("", ""),
    ("Serviceable as % of residential", "%.1f%%" % (100 * tot["serviceable"] / max(1, tot["residential"]))),
    ("Serviceable as % of Kinetic territory", "%.1f%%" % (100 * tot["serviceable"] / max(1, tot["serviceable"] + tot["existing"] + tot["pending"]))),
]
for i, (k, v) in enumerate(rows_sum, 3):
    a = wss.cell(i, 1, k); b = wss.cell(i, 2, v)
    if isinstance(v, int): b.number_format = "#,##0"
    if k and not k.startswith("  ") and k.strip(): a.font = Font(bold=(v != ""))
wss.column_dimensions["A"].width = 46; wss.column_dimensions["B"].width = 14

# ---- LEGEND ----
wl = wb.create_sheet("Legend & Method")
leg = [
    ("Iowa 0–49% Knockable — column definitions & method", ""),
    ("", ""),
    ("Total residential homes", "Residential addresses qualified in the ZIP by the live pull (National Address Database, screened by FEMA/footprint occupancy; confident non-residential — commercial/industrial/agriculture/etc. — removed). The denominator actually swept."),
    ("Total Kinetic homes in territory", "Homes where a Kinetic FIBER plan populates on an exact-address check = Serviceable + Existing customers + Pending order. This is the LIVE fibre footprint. The sheet's 'Kinetic fibre % of homes' (FCC filing) is ~0 here because the FCC data lags 2025 builds; the live check supersedes it."),
    ("Existing customers", "Exact-address homes that returned an existing-Kinetic-customer status (billing). Excluded from the new-lead list."),
    ("Serviceable homes", "NEW-customer homes where a priced Kinetic fibre plan populates (availability + pricing). These are the deliverable leads."),
    ("", ""),
    ("As of", "Live Kinetic sweep 2026-09-08 (exact-address, anonymous public availability check). FTTP competition band from FCC BDC filing 12/31/2025."),
    ("Coverage", "All 262 IA ZIPs with FTTP competition 0–49% (= 218 at 0–30% + 44 at 31–49%), matching the sheet's IA knockable list exactly."),
    ("Note", "3 ZIPs (PO-box / no residential address points in NAD) show 0 across counts. 7 addresses statewide were infra-incomplete (retryable) and 57 serviceable homes were held for manual rooftop review; neither is counted as serviceable."),
]
for i, (k, v) in enumerate(leg, 1):
    a = wl.cell(i, 1, k); b = wl.cell(i, 2, v); b.alignment = Alignment(wrap_text=True, vertical="top")
    if i == 1: a.font = Font(bold=True, size=13, color="1F3864")
    elif k: a.font = Font(bold=True, size=10)
wl.column_dimensions["A"].width = 32; wl.column_dimensions["B"].width = 95

wb.save(OUT)
print("BUILT:", OUT)
print("rows:", len(data), "| zips_with_zero_counts:", miss0)
print("totals: residential=%d kinetic_territory=%d existing=%d serviceable=%d pending=%d"
      % (tot["residential"], tot["serviceable"] + tot["existing"] + tot["pending"], tot["existing"], tot["serviceable"], tot["pending"]))
