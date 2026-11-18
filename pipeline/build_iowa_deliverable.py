"""Assemble the final Iowa deliverable from the priced serviceable leads:
  1. keep only status==PRICED (fresh re-confirmed exact fiber + populated priced plan, not existing)
  2. EXCLUDE already-delivered (new prospects only — FiberLeads req 6)
  3. rooftop-place every lead authoritatively (RP.place, Google OFF; unplaceable -> HELD, never shipped)
  4. export xlsx (address, rooftop lat/lon, fiber plans + pricing) + a HELD manifest + reconciliation

Usage: build_iowa_deliverable.py <priced_leads.jsonl>"""
import os, sys, json, time, collections
_INP = os.path.abspath(sys.argv[1])
PRIOR = "/Users/masani/iowa_pull/prior_delivered_keys.txt"
OUTDIR = "/Users/masani/iowa_pull/deliverable"; os.makedirs(OUTDIR, exist_ok=True)
sys.path.insert(0, "/Users/masani/FLEET_5K/merge/engine")
from carrier_uqual import abbreviate
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"
sys.path.insert(0, ENG); os.chdir(ENG)
import rooftop_place as RP

def key(addr, zc):
    return abbreviate(str(addr).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(zc).strip()[:5]

prior = set(l.strip() for l in open(PRIOR) if l.strip())
rows = [json.loads(l) for l in open(_INP) if l.strip()]
st = collections.Counter(x.get("status") for x in rows)
priced = [x for x in rows if x.get("status") == "PRICED"]

# new-prospects-only exclusion
new_leads = []; excluded_prior = 0
for x in priced:
    if key(x.get("address") or x.get("line1"), x.get("zip")) in prior:
        excluded_prior += 1
    else:
        new_leads.append(x)

print("priced input=%d | status=%s" % (len(rows), dict(st.most_common())), flush=True)
print("PRICED=%d  excluded_already_delivered=%d  NEW=%d" % (len(priced), excluded_prior, len(new_leads)), flush=True)

# rooftop placement (authoritative NG911 structure + FEMA footprints; key="" => Google OFF)
for l in new_leads:
    try: l["lat"] = float(l["lat"]); l["lon"] = float(l["lon"])
    except Exception: pass
print("rooftop-placing %d new leads (NG911 structure / FEMA footprints, Google OFF)..." % len(new_leads), flush=True)
t0 = time.time()
placed, held = RP.place(new_leads, "IA", key="", workers=24)
print("PLACED=%d  HELD(no authoritative rooftop)=%d  in %.1f min" % (len(placed), len(held), (time.time() - t0) / 60), flush=True)

# ---- CLEANUP: city backfill + exact dedup + de-stack (>=4m between distinct bases) ----
import math
zipcity = {}
try: zipcity = json.load(open("/Users/masani/iowa_pull/zip_city_final.json"))
except Exception: pass
def realcity(lead):
    c = (lead.get("city") or "").strip()
    if not c or c.lower() in ("not stated", "unincorporated", "unknown"):
        return zipcity.get(str(lead.get("zip", "")).strip()[:5], c)
    return c
for p in placed:
    p["city"] = realcity(p)
def dist_m(a, b):
    dy = (b[0]-a[0])*111320.0; dx = (b[1]-a[1])*111320.0*math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)
# exact (address,zip) dedup — keep first
seen_addr = set(); dedup = []; exact_dups = 0
for p in placed:
    k = (str(p.get("address") or p.get("line1")).upper().strip(), str(p.get("zip")).strip()[:5])
    if k in seen_addr: exact_dups += 1; held.append({**p, "reason": "exact duplicate address"}); continue
    seen_addr.add(k); dedup.append(p)
# de-stack: pins <4m apart -> try reverting to authoritative NAD src point; still <4m -> hold extra
def latlon(p): return (float(p["lat"]), float(p["lon"]))
grid = collections.defaultdict(list)
for p in dedup:
    try: grid[(round(float(p["lat"]), 3), round(float(p["lon"]), 3))].append(p)
    except Exception: pass
destacked = 0
for cell, members in grid.items():
    if len(members) < 2: continue
    for i in range(len(members)):
        for j in range(i+1, len(members)):
            a, b = members[i], members[j]
            try:
                if dist_m(latlon(a), latlon(b)) < 4.0:
                    # revert the higher-house-number one to its NAD src point
                    hi = b if str(b.get("address","")) > str(a.get("address","")) else a
                    if hi.get("src_lat") and hi.get("src_lon"):
                        hi["lat"] = float(hi["src_lat"]); hi["lon"] = float(hi["src_lon"])
            except Exception: pass
# final stacked re-check -> hold any still <4m
final_ship = []; held_stacked = 0
grid2 = collections.defaultdict(list)
for p in dedup:
    try: grid2[(round(float(p["lat"]), 4), round(float(p["lon"]), 4))].append(p)
    except Exception: pass
too_close = set()
keys = list(grid2.keys())
allpts = [(p, latlon(p)) for p in dedup]
by_cell = collections.defaultdict(list)
for p in dedup:
    try: by_cell[(round(float(p["lat"]), 3), round(float(p["lon"]), 3))].append(p)
    except Exception: pass
held_ids = set()
for cell, members in by_cell.items():
    for i in range(len(members)):
        for j in range(i+1, len(members)):
            a, b = members[i], members[j]
            if id(a) in held_ids or id(b) in held_ids: continue
            try:
                if dist_m(latlon(a), latlon(b)) < 4.0:
                    hi = b if str(b.get("address","")) > str(a.get("address","")) else a
                    held_ids.add(id(hi)); held_stacked += 1
                    held.append({**hi, "reason": "coincident pin <4m from sibling — needs manual separation"})
            except Exception: pass
final_ship = [p for p in dedup if id(p) not in held_ids]
print("CLEANUP: exact_dups_removed=%d  stacked_held=%d  ship=%d" % (exact_dups, held_stacked, len(final_ship)), flush=True)
placed = final_ship

# export
import openpyxl
def plan_cols(lead):
    plans = lead.get("plans") or []
    gig = next((p for p in plans if p["name"].lower().startswith("gig")), None)
    twogig = next((p for p in plans if p["name"].lower().startswith("2gig") or p["name"].lower().startswith("2 gig")), None)
    allp = "; ".join("%s @ $%s" % (p["name"], p["standard"]) for p in plans)
    return (("$" + gig["standard"]) if gig else "", ("$" + twogig["standard"]) if twogig else "", allp)

wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Serviceable"
HDR = ["Lead Owner", "Lead Status", "Address", "City", "State", "Zip", "Latitude", "Longitude",
       "1 Gig", "2 Gig", "Fiber Plans (name @ standard price)", "Pin Source", "Evidence UTC"]
ws.append(HDR)
for p in placed:
    g, tg, allp = plan_cols(p)
    ws.append(["", "", p.get("address") or p.get("line1"), p.get("city"), p.get("state"), p.get("zip"),
               p.get("lat"), p.get("lon"), g, tg, allp,
               p.get("pin_source") or p.get("source") or p.get("rooftop_source") or "", p.get("observed_at_utc", "")])
stamp = time.strftime("%m.%d.%Y")
xlsx = os.path.join(OUTDIR, "%s - Iowa 262-ZIP - Serviceable Fiber (NEW).xlsx" % stamp)
wb.save(xlsx)

with open(os.path.join(OUTDIR, "held_no_rooftop.jsonl"), "w") as hf:
    for h in held:
        hf.write(json.dumps(h) + "\n")

print("\n==== ICONCILIATION ====")
print("  serviceable (sweep)        : %d" % len(rows))
print("  re-confirmed PRICED        : %d" % len(priced))
print("  already-delivered excluded : %d" % excluded_prior)
print("  NEW prospects              : %d" % len(new_leads))
print("  shipped (rooftop-placed)   : %d" % len(placed))
print("  held (no auth rooftop)     : %d  -> deliverable/held_no_rooftop.jsonl" % len(held))
print("  conservation placed+held == new ? %s" % (len(placed) + len(held) == len(new_leads)))
print("  DELIVERABLE -> %s" % xlsx)
