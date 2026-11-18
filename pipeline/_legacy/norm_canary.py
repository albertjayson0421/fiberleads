"""LIVE normalization canary — resolve the Iowa abbreviate-vs-spell-out conflict with evidence.

Ground truth: 24 Knoxville-area addresses Kinetic returned as serviceable (fiber+pricing) in
July, stored ABBREVIATED ("514 NE 3RD ST"). For each, check three spellings live through a
fresh US-sticky DataImpulse exit and record the primary outcome + exactMatch:
  1. abbrev  : as delivered              ("514 NE 3RD ST")
  2. fleet   : usps_normalize() output   (suffix spelled out -> "514 NE 3RD STREET")  << current fleet
  3. nad_full: dir + suffix spelled out  ("514 NORTHEAST 3RD STREET")  << raw NAD shape
Whichever maximizes exactMatch+FIBER on known-serviceable addresses is the correct Iowa form.
"""
import os, sys, json, hashlib, time, random
from urllib.parse import quote
HERE = "/Users/masani/FLEET_5K/merge"
sys.path[:0] = [HERE, os.path.join(HERE, "engine")]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))
os.chdir(os.path.join(HERE, "engine"))
import carrier_uqual as CU
from carrier_uqual import UqualClient, STREET_TYPES, DIRECTIONALS, USPS_SUFFIXES, spell_out

# inverse maps: full -> abbrev
SUF_ABBR = {v: k for k, v in STREET_TYPES.items()}
SUF_ABBR.update({v: k for k, v in USPS_SUFFIXES.items()})
DIR_ABBR = dict(DIRECTIONALS)                      # NORTH->N already
DIR_FULL = {v: k for k, v in DIRECTIONALS.items()} # N->NORTH

def abbreviate(s):
    out = []
    for w in s.upper().split():
        u = w.strip(".,")
        out.append(DIR_ABBR.get(u, SUF_ABBR.get(u, w)))
    return " ".join(out)

def spell_full(s):
    """Full spell-out of directionals AND suffixes (simulates raw NAD)."""
    out = []
    for w in s.upper().split():
        u = w.strip(".,")
        out.append(DIR_FULL.get(u, STREET_TYPES.get(u, u)))
    return " ".join(out)

def usps_norm_fleet(s):
    """Exactly what fleet_actor_v2.usps_normalize does: suffix spell-out only."""
    up = s.upper().replace(".", " ").replace(",", " ")
    up = " ".join(up.split())
    return spell_out(up) or up

pools = [p for p in json.load(open(os.path.join(HERE, "residential.json")))["pools"]
         if str(p.get("name", "")).startswith("dataimpulse")]

def fresh_exit():
    p = random.choice(pools)
    u = str(p["user"]) + "__cr.us__sid." + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    return "http://" + quote(u, safe="") + ":" + quote(str(p["pass"]), safe="") + "@" + p["host"] + ":" + str(p["port"])

import openpyxl
f = "/Volumes/T7/Masani Files/FiberLeads/01_Deliverables/07.23.2026 - Knoxville IA/07.23.2026 - Knoxville IA - Serviceable.xlsx"
wb = openpyxl.load_workbook(f, read_only=True); ws = wb.active
rows = list(ws.iter_rows(values_only=True))[1:]
# take a spread of 24 distinct streets (Knoxville proper, 50138) so we test varied street types
samp = []
seen_st = set()
for r in rows:
    addr, city, st, zc = r[1], r[2], r[3], str(r[4])
    if not addr:
        continue
    key = " ".join(addr.split()[1:])  # street without house number
    if key in seen_st:
        continue
    seen_st.add(key); samp.append((addr.strip(), city.strip(), st.strip(), zc.strip()))
    if len(samp) >= 24:
        break

print("canary on %d distinct-street known-serviceable addresses\n" % len(samp), flush=True)
tally = {"abbrev": {}, "fleet": {}, "nad_full": {}}
t0 = time.time()
for i, (addr, city, st, zc) in enumerate(samp):
    forms = {"abbrev": abbreviate(addr), "fleet": usps_norm_fleet(addr), "nad_full": spell_full(addr)}
    px = fresh_exit()
    cl = UqualClient(proxy_factory=lambda px=px: px, worker=i)
    cl.proxy = px
    line = "[%2d] %-22s" % (i + 1, addr)
    for fname in ("abbrev", "fleet", "nad_full"):
        try:
            outcome, det = cl.check(forms[fname], city, st, zc)
        except Exception as e:
            outcome, det = "ERR", {"err": str(e)[:40]}
        det = det or {}
        exact = det.get("exactMatch")
        tag = "%s%s" % (outcome, "+exact" if exact else "")
        tally[fname][tag] = tally[fname].get(tag, 0) + 1
        line += " | %-8s=%-16s" % (fname, tag)
    print(line, flush=True)
    try: cl.close()
    except Exception: pass

print("\n==== TALLY (known-serviceable addresses, so FIBER/FIBER+exact = correct form) ==== %.0fs" % (time.time() - t0))
for fname in ("abbrev", "fleet", "nad_full"):
    print("  %-9s : %s" % (fname, dict(sorted(tally[fname].items(), key=lambda x: -x[1]))))
