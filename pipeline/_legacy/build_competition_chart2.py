"""Iowa 0-49% knockable competition chart — v2, corrected after adversarial review.
Honest buckets: PROVEN serviceable/existing/not-serviceable are floors; AddressNotFound/
Suggestions (75%) are UNRESOLVED (single-shot sweep, no remediation), NOT proven no-fibre —
surfaced as their own column, never folded into 'not serviceable'. FCC % columns keep their
% format under a separate 'FCC — lags' banner. Reconciles the 10,589 proven-serviceable market
floor to the 9,105 shipped leads. Live counts are a FLOOR pending a remediation re-sweep."""
import openpyxl, glob, json, collections
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SRC = glob.glob("/Users/masani/Desktop/*FTTP Competition by ZIP*.xlsx")[0]
OUT = "/Users/masani/iowa_pull/deliverable/09.08.2026 - Iowa 0-49pct Knockable - Kinetic Homes by ZIP.xlsx"
disp = json.load(open("/Users/masani/iowa_pull/zip_disposition.json"))
shipped = json.load(open("/Users/masani/iowa_pull/zip_shipped.json"))
ZERO_ZIPS = {"50012", "50163", "52344"}

wb_src = openpyxl.load_workbook(SRC, read_only=True, data_only=True); ws_ia = wb_src["IA"]
rows = list(ws_ia.iter_rows(values_only=True)); H = {h: i for i, h in enumerate(rows[1])}
def gv(r, name): return r[H[name]] if name in H and H[name] < len(r) else None
band_ok = {"0–30%", "31–49%", "0-30%", "31-49%"}
data = [r for r in rows[2:] if r[H["ZIP"]] and str(gv(r, "FTTP competition")).strip() in band_ok]
data.sort(key=lambda r: str(r[H["ZIP"]]))

wb = openpyxl.Workbook(); ws = wb.active; ws.title = "IA 0-49% Knockable"
LIVE = ["Residential homes swept", "Kinetic fibre-reachable (live, proven)", "Existing Kinetic customers",
        "Serviceable homes (proven new-customer)", "New leads shipped this pull",
        "Not serviceable (proven)", "Unresolved — address unmatched (retryable)"]
FCCPCT = ["Competing FTTP % of homes", "Kinetic fibre % of homes", "Competing % of all fibre",
          "Cable", "Any fibre", "Fixed wireless", "DSL", "Satellite"]
FCCTXT = ["Competing FTTP providers (% of homes)", "Cable operators (% of homes)", "Other counties in ZIP"]
IDCOLS = ["ZIP", "City", "County", "FTTP competition"]
COLS = IDCOLS + LIVE + FCCPCT + FCCTXT + ["Data note"]

navy = PatternFill("solid", fgColor="1F3864"); green = PatternFill("solid", fgColor="548235")
orange = PatternFill("solid", fgColor="C55A11"); grey = PatternFill("solid", fgColor="808080")
red = PatternFill("solid", fgColor="843C39"); zerofill = PatternFill("solid", fgColor="F2DCDB")
HFONT = Font(bold=True, color="FFFFFF", size=10)
thin = Side(style="thin", color="D9D9D9"); BORD = Border(left=thin, right=thin, top=thin, bottom=thin)

ws.cell(1, 1, "Iowa — Kinetic fibre homes by ZIP · knockable list (FTTP competition 0–49%)").font = Font(bold=True, size=14, color="1F3864")
ws.cell(2, 1, "262 ZIPs. LIVE counts = exact-address Kinetic sweep 2026-09-08; they are PROVEN FLOORS — 75% of addresses returned AddressNotFound on a single-shot (no-remediation) sweep and are UNRESOLVED (not proven no-fibre). FCC % columns are the 12/31/2025 filing and LAG 2025 builds. Trust the LIVE count columns, not the FCC %.").font = Font(italic=True, size=9, color="C55A11")

# group banner (row 3)
def band(c1, c2, text, fill):
    ws.merge_cells(start_row=3, start_column=c1, end_row=3, end_column=c2)
    cell = ws.cell(3, c1, text); cell.fill = fill; cell.font = HFONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
li = len(IDCOLS)
band(li + 1, li + len(LIVE), "LIVE Kinetic sweep 2026-09-08 — home counts (proven floors)", green)
band(li + len(LIVE) + 1, li + len(LIVE) + len(FCCPCT) + len(FCCTXT), "FCC BDC filing 12/31/2025 — % of homes (LAGS 2025 fibre builds)", grey)

# header (row 4)
for j, c in enumerate(COLS, 1):
    cell = ws.cell(4, j, c); cell.font = HFONT; cell.fill = navy
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = BORD
    if c == "Serviceable homes (proven new-customer)": cell.fill = green
    if c == "New leads shipped this pull": cell.fill = green
    if c == "Unresolved — address unmatched (retryable)": cell.fill = orange
    if c == "Not serviceable (proven)": cell.fill = red

r_out = 5
for r in data:
    z = str(r[H["ZIP"]]).strip(); d = disp.get(z, {})
    resid = d.get("residential", 0)
    serv = d.get("serviceable", 0); exist = d.get("existing", 0); pend = d.get("pending", 0)
    kin = serv + exist + pend
    notserv = d.get("proven_copper", 0) + d.get("proven_outterr", 0) + d.get("proven_unserviceable", 0)
    unres = d.get("unresolved_unmatched", 0) + d.get("unresolved_infra", 0)
    ship = shipped.get(z, 0)
    note = ""
    if z in ZERO_ZIPS: note = "No NAD residential address points — NOT swept (homes exist per FCC); source gap, re-acquire"
    elif resid and unres / max(1, resid) > 0.6 and str(gv(r, "Kinetic fibre % of homes") or 0) not in ("0", "None"):
        note = "Many addresses unresolved + FCC shows Kinetic fibre — live count is a floor; remediation re-sweep pending"
    vals = {"ZIP": z, "City": gv(r, "City"), "County": gv(r, "County"), "FTTP competition": gv(r, "FTTP competition"),
            "Residential homes swept": resid, "Kinetic fibre-reachable (live, proven)": kin,
            "Existing Kinetic customers": exist, "Serviceable homes (proven new-customer)": serv,
            "New leads shipped this pull": ship, "Not serviceable (proven)": notserv,
            "Unresolved — address unmatched (retryable)": unres, "Data note": note}
    for name in FCCPCT + FCCTXT: vals[name] = gv(r, name)
    for j, c in enumerate(COLS, 1):
        cell = ws.cell(r_out, j, vals.get(c)); cell.border = BORD; cell.font = Font(size=10)
        if c in LIVE:
            cell.number_format = "#,##0"; cell.alignment = Alignment(horizontal="center")
        if c in FCCPCT:
            cell.number_format = '0"%"'; cell.alignment = Alignment(horizontal="center")
        if c == "Serviceable homes (proven new-customer)": cell.font = Font(size=10, bold=True, color="548235")
        if z in ZERO_ZIPS and c in LIVE: cell.value = None; cell.fill = zerofill
    if z in ZERO_ZIPS:
        ws.cell(r_out, len(COLS)).font = Font(size=9, italic=True, color="843C39")
    r_out += 1

ws.freeze_panes = "E5"
ws.auto_filter.ref = "A4:%s%d" % (get_column_letter(len(COLS)), r_out - 1)
W = {"ZIP": 8, "City": 15, "County": 15, "FTTP competition": 12, "Residential homes swept": 11,
     "Kinetic fibre-reachable (live, proven)": 13, "Existing Kinetic customers": 12,
     "Serviceable homes (proven new-customer)": 14, "New leads shipped this pull": 12,
     "Not serviceable (proven)": 12, "Unresolved — address unmatched (retryable)": 14,
     "Competing FTTP providers (% of homes)": 34, "Cable operators (% of homes)": 22, "Other counties in ZIP": 18, "Data note": 42}
for j, c in enumerate(COLS, 1): ws.column_dimensions[get_column_letter(j)].width = W.get(c, 9)

# ---- SUMMARY ----
T = collections.Counter()
for z, d in disp.items():
    for k in ("residential", "serviceable", "existing", "pending", "proven_copper", "proven_outterr", "proven_unserviceable", "unresolved_unmatched", "unresolved_infra"):
        T[k] += d.get(k, 0)
proven_ns = T["proven_copper"] + T["proven_outterr"] + T["proven_unserviceable"]
unres = T["unresolved_unmatched"] + T["unresolved_infra"]
kin = T["serviceable"] + T["existing"] + T["pending"]
wss = wb.create_sheet("Summary")
wss.cell(1, 1, "Iowa 0–49% Knockable — Summary (honest funnel)").font = Font(bold=True, size=14, color="1F3864")
S = [
    ("ZIPs (0–49% FTTP competition)", 262), ("  · 0–30% band", 218), ("  · 31–49% band", 44),
    ("  · with any proven serviceable homes", 108), ("  · zero-serviceable (Kinetic fibre not yet reaching / unresolved)", 154),
    ("", ""),
    ("Residential homes swept", T["residential"]),
    ("", ""),
    ("RESOLVED (definitive carrier answer)", T["serviceable"] + T["existing"] + T["pending"] + proven_ns),
    ("  · Serviceable — proven new-customer fibre (FLOOR)", T["serviceable"]),
    ("  · Existing Kinetic customers", T["existing"]),
    ("  · Pending order", T["pending"]),
    ("  · Not serviceable — proven (copper / out-of-territory / in-territory-unserviceable)", proven_ns),
    ("UNRESOLVED — AddressNotFound/Suggestions, single-shot sweep, NO remediation (retryable)", unres),
    ("", ""),
    ("Kinetic fibre-reachable (proven floor = serviceable+existing+pending)", kin),
    ("", ""),
    ("RECONCILIATION — proven serviceable 10,589 → shipped leads", ""),
    ("  Serviceable homes (proven)", T["serviceable"]),
    ("  − already delivered in a prior pull", 1423),
    ("  − held for manual rooftop review", 57),
    ("  − pricing not re-confirmed (retryable)", 4),
    ("  = NEW leads shipped this pull", 9105),
    ("", ""),
    ("KNOWN GAP — the serviceable numbers are a FLOOR", ""),
    ("  75% of addresses returned AddressNotFound on a no-remediation sweep and are UNRESOLVED.", ""),
    ("  A remediation re-sweep (address variants/candidate matching) is required to resolve them;", ""),
    ("  a 40-address probe recovered ~7.5% as serviceable, so the true market is materially higher.", ""),
]
i = 3
for k, v in S:
    a = wss.cell(i, 1, k); b = wss.cell(i, 2, v)
    if isinstance(v, int): b.number_format = "#,##0"
    if k and not k.startswith("  "): a.font = Font(bold=True)
    i += 1
wss.column_dimensions["A"].width = 66; wss.column_dimensions["B"].width = 14

# ---- LEGEND ----
wl = wb.create_sheet("Legend & Method"); wl.cell(1, 1, "Column definitions & method").font = Font(bold=True, size=13, color="1F3864")
L = [
 ("Residential homes swept", "Residential addresses qualified in the ZIP (National Address Database, screened by FEMA/footprint occupancy; confident non-residential removed). A swept lower bound, not a census total."),
 ("Kinetic fibre-reachable (live, proven)", "Homes where a Kinetic FIBRE plan populated on an exact-address check = Serviceable + Existing + Pending. Excludes Kinetic's DSL/copper footprint. A PROVEN FLOOR — see the unresolved caveat."),
 ("Existing Kinetic customers", "Exact-address homes returning an existing-Kinetic-customer billing status. Not sellable-to; excluded from leads."),
 ("Serviceable homes (proven new-customer)", "NEW-customer homes where a priced Kinetic fibre plan populated (availability + pricing). The full proven market floor = 10,589. NOT the shipped file — see reconciliation."),
 ("New leads shipped this pull", "Serviceable homes that were new (not already delivered) AND received an authoritative rooftop pin = 9,105 (the delivered lead file)."),
 ("Not serviceable (proven)", "Homes with a definitive carrier answer of no fibre: copper/DSL only, out-of-territory (errorCode 1060), or in-territory-unserviceable. These are proven."),
 ("Unresolved — address unmatched (retryable)", "Homes where the exact-address search returned AddressNotFound / AddressSuggestions on a SINGLE-SHOT sweep with no remediation. Per policy this is NOT proof of no-fibre ('we asked' vs 'the answer is no'). ~75% of swept homes. A remediation re-sweep is required; a probe recovered ~7.5% as serviceable."),
 ("FCC % columns (grey banner)", "From the FCC BDC filing dated 12/31/2025. 'Kinetic fibre % of homes' reads 0 in many ZIPs that our live check finds serviceable (the filing lags 2025 builds); in ~20 ZIPs the FCC shows 30–97% Kinetic fibre where our live count is lower — those ZIPs are flagged (Data note) for a remediation re-sweep. FCC and live disagree in BOTH directions; the live exact-address check is authoritative for freshness, the FCC filing for planned/CAF footprint."),
 ("As of", "Live Kinetic sweep 2026-09-08 (exact-address, anonymous public availability). Bands from FCC BDC 12/31/2025."),
 ("Coverage", "All 262 IA ZIPs at FTTP competition 0–49% (218 at 0–30% + 44 at 31–49%) — matches the source's IA knockable list exactly."),
 ("3 ZIPs with no counts (pink)", "50012 Ames, 50163 Melcher Dallas, 52344 Troy Mills: no NAD residential address points acquired (source gap, homes DO exist per FCC) — UNMEASURED, not zero. Re-acquire from parcels/E911."),
]
i = 2
for k, v in L:
    wl.cell(i, 1, k).font = Font(bold=True, size=10); c = wl.cell(i, 2, v); c.alignment = Alignment(wrap_text=True, vertical="top"); i += 1
wl.column_dimensions["A"].width = 34; wl.column_dimensions["B"].width = 100

wb.save(OUT)
print("BUILT:", OUT)
print("funnel: swept=%d serviceable=%d existing=%d proven_not=%d unresolved=%d kinetic_reachable=%d shipped=%d"
      % (T["residential"], T["serviceable"], T["existing"], proven_ns, unres, kin, sum(shipped.values())))
