"""Build the PARTIAL Texas deliverable from the 35,215 serviceable found before the carrier's
Cloudflare search-challenge armed (209,526 of 609,580 swept = 34%). Real-city backfill,
dedup vs the prior TX delivery (60,526), authoritative rooftop pins. Labeled PARTIAL."""
import os, sys, json, csv, collections
sys.path.insert(0, "/Users/masani/FLEET_5K/merge/engine")
from carrier_uqual import abbreviate
import openpyxl

ZC = json.load(open("/Users/masani/iowa_pull/tx_zip_city.json"))
PRIOR_CSV = "/Volumes/T7/Masani Files/Delivered Leads/09.06.2026 - TX ALL RUNS - Fiber Prospects (60526 unique, 37832 with coords).csv"
def key(a, z): return abbreviate(str(a).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(z).strip()[:5]
def badcity(c): return "COUNTY" in str(c).upper() or str(c).strip().upper() in ("", "NOT STATED", "UNKNOWN", "UNINCORPORATED")

serv = [json.loads(l) for l in open("/Users/masani/iowa_pull/tx_done.jsonl") if json.loads(l).get("status") == "SERVICEABLE"]
print("serviceable found:", len(serv))

prior = set()
with open(PRIOR_CSV) as f:
    for r in csv.DictReader(f):
        a = r.get("Address") or r.get("address"); z = r.get("Zip") or r.get("zip")
        if a and z: prior.add(key(a, z))
print("prior TX delivered keys:", len(prior))

new = []; dup = nocity = 0; seen = set()
for a in serv:
    addr = a.get("sent") or a.get("line1")
    k = key(addr, a.get("zip"))
    if k in prior: dup += 1; continue
    if k in seen: continue
    seen.add(k)
    c = a.get("city")
    if badcity(c): c = ZC.get(str(a.get("zip"))[:5])
    if not c or badcity(c): nocity += 1; continue   # never ship county/blank city
    a["_addr"] = addr; a["_city"] = c; new.append(a)
print("after dedup vs prior + city gate: NEW=%d (dup=%d, unresolved-city=%d)" % (len(new), dup, nocity))

# authoritative rooftop placement
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"; sys.path.insert(0, ENG); os.chdir(ENG)
import rooftop_place as RP
for a in new:
    try: a["lat"] = float(a["lat"]); a["lon"] = float(a["lon"])
    except Exception: pass
    a["address"] = a["_addr"]; a["line1"] = a["_addr"]; a["city"] = a["_city"]
placed, held = RP.place(new, "TX", key="", workers=16)
print("rooftop: placed=%d held(no authoritative footprint)=%d" % (len(placed), len(held)))

HDR = ["Lead Owner","Lead Status","Address","City","State","Zip","Latitude","Longitude","Plan","Max Speed","Pin Source","Rooftop State"]
def plan(a):
    pl = a.get("plans") or []
    return "; ".join(pl) if pl else "fibre plan populated (price not recorded)"
rows = []
for p in placed:
    rows.append(["","",p.get("address"),p.get("city"),"TX",str(p.get("zip"))[:5],p.get("lat"),p.get("lon"),
                 plan(p), p.get("maxQual",""), p.get("pin_source",""), p.get("rooftop_state","")])
OUTBASE = "/Volumes/T7/Masani Files/Delivered Leads/09.09.2026 - TX Statewide - New Fiber Prospects (%d serviceable + pinned, PARTIAL - carrier search behind Cloudflare, 34%% of universe swept)" % len(rows)
with open(OUTBASE + ".csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(HDR); w.writerows(rows)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "TX Serviceable (partial)"; ws.append(HDR)
for r in rows: ws.append(r)
wb.save(OUTBASE + ".xlsx")
print("WROTE %d rows -> %s.csv/.xlsx" % (len(rows), OUTBASE.split("/")[-1]))
# held manifest (never silently dropped)
if held:
    with open("/Users/masani/iowa_pull/tx_partial_held_pins.jsonl", "w") as f:
        for h in held: f.write(json.dumps({"address":h.get("address"),"zip":h.get("zip"),"reason":h.get("hold_reason") or h.get("reason")})+"\n")
    print("held-for-pin manifest:", len(held), "-> tx_partial_held_pins.jsonl")
