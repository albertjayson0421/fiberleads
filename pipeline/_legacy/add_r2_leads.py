"""Add the CITY-FIX (remediation2) recovered SERVICEABLE homes to the delivered Iowa lead
file. Same gate/placement as add_recovered_leads.py: plan-populated (no price capture),
dedup vs prior + already-shipped, real USPS city backfilled (never blank/'Not stated'),
authoritative rooftop placement. Base = the +remediation file (10,911 with r1 recoveries)."""
import os, sys, json, collections
sys.path.insert(0, "/Users/masani/FLEET_5K/merge/engine")
from carrier_uqual import abbreviate
import openpyxl

BASE = "/Users/masani/iowa_pull/deliverable/09.09.2026 - Iowa 262-ZIP - Serviceable Fiber (NEW, +remediation).xlsx"
PRIOR = set(l.strip() for l in open("/Users/masani/iowa_pull/prior_delivered_keys.txt") if l.strip())
ZC = json.load(open("/Users/masani/iowa_pull/zip_city_final.json"))
def key(a, z): return abbreviate(str(a).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(z).strip()[:5]
def real_city(a):
    c = str(a.get("city", "")).strip()
    if c.upper() in ("", "NOT STATED", "UNKNOWN", "UNINCORPORATED"):
        return ZC.get(str(a.get("zip", "")).strip()[:5], c)
    return c

rec = [json.loads(l) for l in open("/Users/masani/iowa_pull/remediation2_results.jsonl")
       if l.strip() and json.loads(l).get("status") == "SERVICEABLE"]
print("r2 recovered serviceable:", len(rec))

wb = openpyxl.load_workbook(BASE); ws = wb.active
rows = list(ws.iter_rows(values_only=True)); H = {h: i for i, h in enumerate(rows[0])}
shipped_keys = set(key(r[H["Address"]], r[H["Zip"]]) for r in rows[1:])
print("already shipped (base):", len(shipped_keys))

new = []; dup_prior = dup_ship = nocity = 0
for a in rec:
    addr = a.get("sent") or a.get("line1")
    k = key(addr, a.get("zip"))
    if k in PRIOR: dup_prior += 1; continue
    if k in shipped_keys: dup_ship += 1; continue
    c = real_city(a)
    if not c or c.strip().upper() in ("", "NOT STATED", "UNKNOWN", "UNINCORPORATED"):
        nocity += 1; continue   # never ship a blank-city lead (lead standard)
    a["_addr"] = addr; a["_city"] = c; new.append(a); shipped_keys.add(k)
print("after dedup: new=%d (excluded prior=%d, already-shipped=%d, unresolved-city=%d)" % (len(new), dup_prior, dup_ship, nocity))

# authoritative rooftop placement
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"; sys.path.insert(0, ENG); os.chdir(ENG)
import rooftop_place as RP
for a in new:
    try: a["lat"] = float(a["lat"]); a["lon"] = float(a["lon"])
    except Exception: pass
    a["address"] = a["_addr"]; a["line1"] = a["_addr"]; a["city"] = a["_city"]
placed, held = RP.place(new, "IA", key="", workers=16)
print("rooftop: placed=%d held=%d" % (len(placed), len(held)))
if held:
    print("  HELD sample:", [{"a": h.get("address"), "z": h.get("zip")} for h in held[:5]])

def planstr(a):
    pl = a.get("plans") or []
    return "; ".join(pl) if pl else "fibre plan populated (price not recorded)"
# placed rows ARE the enriched input dicts, so plans/observed_at_utc ride along on p
for p in placed:
    ws.append(["", "", p.get("address"), p.get("city"), p.get("state"), p.get("zip"),
               p.get("lat"), p.get("lon"), "", "", planstr(p),
               p.get("pin_source", ""), p.get("observed_at_utc", "")])
OUT = "/Users/masani/iowa_pull/deliverable/09.09.2026 - Iowa 262-ZIP - Serviceable Fiber (NEW, +city-fix).xlsx"
wb.save(OUT)
total = len(rows) - 1 + len(placed)
print("APPENDED %d rooftop-placed city-fix leads -> total %d leads -> %s" % (len(placed), total, OUT))
