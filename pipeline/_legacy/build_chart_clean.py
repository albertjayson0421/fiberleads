"""Iowa 0-49% knockable — the sheet, with the 4 requested columns added. Clean, no extras."""
import openpyxl, glob, json, collections
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SRC = glob.glob("/Users/masani/Desktop/*FTTP Competition by ZIP*.xlsx")[0]
OUT = "/Users/masani/iowa_pull/deliverable/09.08.2026 - Iowa 0-49pct - Homes by ZIP.xlsx"
disp = json.load(open("/Users/masani/iowa_pull/zip_disposition.json"))

wb_src = openpyxl.load_workbook(SRC, read_only=True, data_only=True); ws_ia = wb_src["IA"]
rows = list(ws_ia.iter_rows(values_only=True)); H = {h: i for i, h in enumerate(rows[1])}
def gv(r, name): return r[H[name]] if name in H and H[name] < len(r) else None
band_ok = {"0–30%", "31–49%", "0-30%", "31-49%"}
data = [r for r in rows[2:] if r[H["ZIP"]] and str(gv(r, "FTTP competition")).strip() in band_ok]
data.sort(key=lambda r: str(r[H["ZIP"]]))

# the 4 requested columns (exact names), then the original sheet columns
NEW = ["Total residential homes", "Total kinetic homes in territory", "Existing customers", "Serviceable homes"]
ORIG = ["ZIP", "City", "FTTP competition", "Competing FTTP % of homes", "Kinetic fibre % of homes",
        "Competing % of all fibre", "Cable", "Any fibre", "Fixed wireless", "DSL", "Satellite",
        "Competing FTTP providers (% of homes)", "Cable operators (% of homes)", "County", "Other counties in ZIP"]
COLS = ORIG[:3] + NEW + ORIG[3:]
PCT = {"Competing FTTP % of homes", "Kinetic fibre % of homes", "Competing % of all fibre",
       "Cable", "Any fibre", "Fixed wireless", "DSL", "Satellite"}

wb = openpyxl.Workbook(); ws = wb.active; ws.title = "IA 0-49%"
navy = PatternFill("solid", fgColor="1F3864"); green = PatternFill("solid", fgColor="548235")
HFONT = Font(bold=True, color="FFFFFF", size=10)
thin = Side(style="thin", color="D9D9D9"); BORD = Border(left=thin, right=thin, top=thin, bottom=thin)
for j, c in enumerate(COLS, 1):
    cell = ws.cell(1, j, c); cell.font = HFONT; cell.fill = green if c in NEW else navy
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = BORD
r_out = 2
for r in data:
    z = str(r[H["ZIP"]]).strip(); d = disp.get(z, {})
    serv = d.get("serviceable", 0); exist = d.get("existing", 0); pend = d.get("pending", 0)
    vals = {"Total residential homes": d.get("residential", 0),
            "Total kinetic homes in territory": serv + exist + pend,
            "Existing customers": exist, "Serviceable homes": serv}
    for c in ORIG: vals[c] = gv(r, c)
    for j, c in enumerate(COLS, 1):
        cell = ws.cell(r_out, j, vals.get(c)); cell.border = BORD; cell.font = Font(size=10)
        if c in NEW: cell.number_format = "#,##0"; cell.alignment = Alignment(horizontal="center")
        if c in PCT: cell.number_format = '0"%"'; cell.alignment = Alignment(horizontal="center")
        if c == "Serviceable homes": cell.font = Font(size=10, bold=True, color="548235")
    r_out += 1
ws.freeze_panes = "D2"
ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(COLS)), r_out - 1)
W = {"ZIP": 8, "City": 15, "County": 15, "FTTP competition": 12, "Total residential homes": 12,
     "Total kinetic homes in territory": 13, "Existing customers": 11, "Serviceable homes": 11,
     "Competing FTTP providers (% of homes)": 34, "Cable operators (% of homes)": 22, "Other counties in ZIP": 18}
for j, c in enumerate(COLS, 1): ws.column_dimensions[get_column_letter(j)].width = W.get(c, 10)

# totals row on a small summary
T = collections.Counter()
for z, d in disp.items():
    T["res"] += d.get("residential", 0); T["srv"] += d.get("serviceable", 0)
    T["ex"] += d.get("existing", 0); T["pend"] += d.get("pending", 0)
wss = wb.create_sheet("Totals")
for i, (k, v) in enumerate([
    ("Iowa 0–49% ZIPs", 262), ("Total residential homes", T["res"]),
    ("Total kinetic homes in territory", T["srv"] + T["ex"] + T["pend"]),
    ("Existing customers", T["ex"]), ("Serviceable homes", T["srv"])], 1):
    wss.cell(i, 1, k).font = Font(bold=True); c = wss.cell(i, 2, v); c.number_format = "#,##0" if isinstance(v, int) else "General"
wss.column_dimensions["A"].width = 34; wss.column_dimensions["B"].width = 12
# short definitions
wss.cell(7, 1, "Definitions (live Kinetic check, 2026-09-08):").font = Font(bold=True)
defs = [("Total residential homes", "residential addresses in the ZIP (non-residential removed)"),
        ("Total kinetic homes in territory", "homes where a Kinetic fibre plan populates = serviceable + existing customers"),
        ("Existing customers", "homes already on Kinetic"),
        ("Serviceable homes", "new-customer homes where a priced Kinetic fibre plan populates")]
for i, (k, v) in enumerate(defs, 8):
    wss.cell(i, 1, k).font = Font(bold=True, size=9); wss.cell(i, 2, v).font = Font(size=9)

wb.save(OUT)
print("BUILT:", OUT, "| rows:", len(data))
print("totals: residential=%d kinetic=%d existing=%d serviceable=%d" % (T["res"], T["srv"] + T["ex"] + T["pend"], T["ex"], T["srv"]))
