"""Add the remediation-recovered SERVICEABLE homes to the delivered lead file.
Plan-populated gate already passed in remediate.py (no price capture). Dedup vs prior
deliveries and vs the already-shipped 9,105, rooftop-place authoritatively, append."""
import os, sys, json, glob, collections
sys.path.insert(0, "/Users/masani/FLEET_5K/merge/engine")
from carrier_uqual import abbreviate
import openpyxl

LEADFILE = "/Users/masani/iowa_pull/deliverable/09.08.2026 - Iowa 262-ZIP - Serviceable Fiber (NEW).xlsx"
PRIOR = set(l.strip() for l in open("/Users/masani/iowa_pull/prior_delivered_keys.txt") if l.strip())
def key(a, z): return abbreviate(str(a).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(z).strip()[:5]

# recovered serviceable
rec = [json.loads(l) for l in open("/Users/masani/iowa_pull/remediation_results.jsonl") if l.strip() and json.loads(l).get("status") == "SERVICEABLE"]
print("recovered serviceable:", len(rec))

# existing shipped leads (to dedup + append onto)
wb = openpyxl.load_workbook(LEADFILE); ws = wb.active
rows = list(ws.iter_rows(values_only=True)); H = {h: i for i, h in enumerate(rows[0])}
shipped_keys = set()
for r in rows[1:]:
    shipped_keys.add(key(r[H["Address"]], r[H["Zip"]]))
print("already shipped:", len(shipped_keys))

# dedup recovered
new = []; dup_prior = dup_ship = 0
for a in rec:
    addr = a.get("sent") or a.get("line1")   # the form Kinetic accepted
    k = key(addr, a.get("zip"))
    if k in PRIOR: dup_prior += 1; continue
    if k in shipped_keys: dup_ship += 1; continue
    a["_addr"] = addr; new.append(a); shipped_keys.add(k)
print("after dedup: new=%d (excluded prior=%d, already-shipped=%d)" % (len(new), dup_prior, dup_ship))

# rooftop placement
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"; sys.path.insert(0, ENG); os.chdir(ENG)
import rooftop_place as RP
for a in new:
    try: a["lat"] = float(a["lat"]); a["lon"] = float(a["lon"])
    except Exception: pass
    a["address"] = a["_addr"]; a["line1"] = a["_addr"]
placed, held = RP.place(new, "IA", key="", workers=16)
print("rooftop: placed=%d held=%d" % (len(placed), len(held)))

# append to lead file (price columns left blank per 2026-09-09 rule)
for p in placed:
    ws.append(["", "", p.get("address"), p.get("city"), p.get("state"), p.get("zip"),
               p.get("lat"), p.get("lon"), "", "", "fibre plan populated (price not recorded)",
               p.get("pin_source", ""), ""])
OUT = "/Users/masani/iowa_pull/deliverable/09.09.2026 - Iowa 262-ZIP - Serviceable Fiber (NEW, +remediation).xlsx"
wb.save(OUT)
total = len(rows) - 1 + len(placed)
print("APPENDED %d rooftop-placed recovered leads -> total %d leads -> %s" % (len(placed), total, OUT))
