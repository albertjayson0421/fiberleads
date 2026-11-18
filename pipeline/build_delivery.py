"""Iowa FiberLeads delivery build: fleet ledgers -> standard-compliant Sales Rabbit CSV + reconciliation.

Turns the 12 VM fleet ledgers (+ any slice ledgers) into the DELIVERED product:
  1. merge all ledger rows, dedup by canonical key (ck) -> one terminal/unresolved state each
  2. extract SERVICEABLE_FIBER only (classifier already gates on a populated priced fiber bundle)
  3. lead standards: real city (never blank/"Unincorporated"), rooftop coords present (NG911+FEMA footprint),
     NEW-only vs ALL prior deliveries (frozen abbreviate anti-join)
  4. conservation reconciliation: every ck -> exactly one bucket, unaccounted must be 0
  5. write Sales Rabbit CSV + a reconciliation report

Run AFTER the sweep completes (and after re-sweep of recovered non-exact / enriched rows are merged in).
Usage: build_delivery.py <ledger_glob_or_dir> <out_csv> <out_recon_json>
"""
import json, sys, os, glob, csv, hashlib, datetime, collections
import flpaths
flpaths.ensure_engine_on_path()
from carrier_uqual import abbreviate

PRIOR = os.path.join(flpaths.DATA, "prior_delivered_keys.txt")
ZIP_CITY = os.path.join(flpaths.DATA, "zip_city_final.json")   # authoritative ZIP->city map (recovers blank city)
BAD_CITY = {"", "UNINCORPORATED", "NOT STATED", "NONE", "UNKNOWN"}
TERMINAL = {"SERVICEABLE_FIBER","DEFINITIVE_NONFIBER","DEFINITIVE_OUT_OF_TERRITORY",
            "DEFINITIVE_NO_ORDERABLE_FIBER","EXCLUDED_EXISTING","EXCLUDED_PENDING_ORDER",
            "COMPLETE_REJECTED","COMPLETE_NOT_IN_KINETIC"}
RETRYABLE = {"INCOMPLETE_RETRYABLE","RETRY_LOG"}

def dkey(addr, z):
    return abbreviate(str(addr).upper().replace(".", " ").replace(",", " ")).strip() + "|" + str(z).strip()[:5]

def main():
    src, out_csv, out_recon = sys.argv[1], sys.argv[2], sys.argv[3]
    files = ([src] if os.path.isfile(src) else
             sorted(glob.glob(os.path.join(src, "**", "*.jsonl"), recursive=True)) if os.path.isdir(src)
             else sorted(glob.glob(src)))
    delivered = set(x for x in open(PRIOR).read().split("\n") if x) if os.path.exists(PRIOR) else set()
    zip_city = {str(k).strip()[:5]: str(v).strip() for k, v in json.load(open(ZIP_CITY)).items()} if os.path.exists(ZIP_CITY) else {}
    # AUTHORITATIVE USPS ZIP->city (zippopotam.us) overrides the small/flawed 262-map
    AUTH_TSV = os.environ.get("AUTH_ZIPCITY_TSV", "")  # optional authoritative ZIP->city TSV; set via env
    if os.path.exists(AUTH_TSV):
        for line in open(AUTH_TSV):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 2 and parts[1].strip():
                zip_city[parts[0].strip()[:5]] = parts[1].strip()

    # dedup by ck: terminal wins over retryable; among terminals, last write wins
    state = {}   # ck -> (class, evidence_row)
    rows_seen = 0
    for f in files:
        for ln in open(f):
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except: continue
            ck = d.get("ck")
            if not ck: continue
            rows_seen += 1
            cls = d.get("class") or d.get("disposition")
            prev = state.get(ck)
            # terminal always supersedes retryable; terminal over terminal = keep (idempotent)
            if prev is None or (prev[0] in RETRYABLE and cls in TERMINAL):
                state[ck] = (cls, d)

    buckets = collections.Counter(c for c, _ in state.values())
    # serviceable leads with standard checks
    leads = []; dropped_city = 0; dropped_coords = 0; dropped_delivered = 0; recovered_city = 0
    for ck, (cls, d) in state.items():
        if cls != "SERVICEABLE_FIBER": continue
        city = str(d.get("city") or "").strip()
        zc = str(d.get("zip") or "").strip()[:5]
        if city.upper() in BAD_CITY:                     # recover blank/bad city from the authoritative ZIP->city map
            if zc in zip_city and zip_city[zc].upper() not in BAD_CITY:
                city = zip_city[zc]; recovered_city += 1
            else:
                dropped_city += 1; continue
        lat, lon = d.get("lat"), d.get("lon")
        if not (lat and lon): dropped_coords += 1; continue
        z = str(d.get("zip") or "").strip()[:5]
        if dkey(d.get("line1") or "", z) in delivered: dropped_delivered += 1; continue
        plans = d.get("plans") or []
        planname = plans[0]["name"] if plans and isinstance(plans[0], dict) else ""
        leads.append({"Address": d.get("line1"), "City": city, "State": d.get("state") or "IA",
                      "Zip": z, "Latitude": lat, "Longitude": lon, "FiberPlan": planname})

    # write Sales Rabbit CSV
    with open(out_csv, "w", newline="") as o:
        w = csv.DictWriter(o, fieldnames=["Address","City","State","Zip","Latitude","Longitude","FiberPlan"])
        w.writeheader(); w.writerows(leads)

    # conservation reconciliation
    unique_ck = len(state)
    terminal_ct = sum(v for k, v in buckets.items() if k in TERMINAL)
    retry_ct = sum(v for k, v in buckets.items() if k in RETRYABLE)
    other_ct = unique_ck - terminal_ct - retry_ct
    recon = {"built_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "ledger_files": len(files), "physical_rows_seen": rows_seen, "unique_ck": unique_ck,
             "buckets": dict(buckets), "terminal": terminal_ct, "retryable_unresolved": retry_ct,
             "unaccounted (must be 0)": other_ct,
             "serviceable_fiber_raw": buckets.get("SERVICEABLE_FIBER", 0),
             "leads_delivered": len(leads), "city_recovered_from_zip": recovered_city,
             "dropped_bad_city_unrecoverable": dropped_city, "dropped_no_coords": dropped_coords,
             "dropped_already_delivered": dropped_delivered,
             "csv": out_csv}
    json.dump(recon, open(out_recon, "w"), indent=1)
    print(json.dumps(recon, indent=1))

if __name__ == "__main__":
    main()
