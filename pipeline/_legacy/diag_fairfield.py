"""Diagnose Fairfield's 64% held share: is it a footprint-download gap (held rows
cluster in an un-downloaded sub-region) or genuine rural sparseness (held spread
across the ZCTA, dominated by 'no building'/'>10m from pin')?"""
import sys, os, json, collections
_ABS = "/Users/masani/iowa_pull/fairfield_candidates.jsonl"
ENG = "/Volumes/T7/Masani Files/FiberLeads/02_System/engine"
os.environ["FL_ACCEPT_HELD_SHARE"] = "1"   # only stops the refuse-to-certify raise; kept is unchanged
sys.path.insert(0, ENG); os.chdir(ENG)
import residential_filter as RF

addrs = [json.loads(l) for l in open(_ABS) if l.strip()]
kept, dropped, held, counts = RF.filter_residential(addrs, workers=48, progress_log=lambda *a: None)
for path, rows in (("/Users/masani/iowa_pull/fairfield_kept.jsonl", kept),
                   ("/Users/masani/iowa_pull/fairfield_dropped.jsonl", dropped),
                   ("/Users/masani/iowa_pull/fairfield_held.jsonl", held)):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
print("input=%d kept=%d dropped=%d held=%d  cons=%s" %
      (len(addrs), len(kept), len(dropped), len(held), len(kept)+len(dropped)+len(held)==len(addrs)))
print("\ncounts:")
for k, v in sorted(counts.items(), key=lambda kv: -kv[1]): print("  %5d  %s" % (v, k))

# geographic clustering test: 0.02deg (~1.5km) grid cells, held-share per cell
def cell(r): return (round(float(r["lon"])/0.02), round(float(r["lat"])/0.02))
held_ck = {r.get("_ck") for r in held}
allcells = collections.Counter(); heldcells = collections.Counter()
for r in addrs:
    c = cell(r); allcells[c]+=1
    if r.get("_ck") in held_ck: heldcells[c]+=1
print("\nheld-share by ~1.5km cell (cells with >=20 candidates), worst 15:")
rows=[]
for c,tot in allcells.items():
    if tot>=20:
        h=heldcells[c]; rows.append((h/tot, h, tot, c))
for share,h,tot,c in sorted(rows, reverse=True)[:15]:
    print("  cell %-16s held %3d/%3d = %3.0f%%" % (c, h, tot, share*100))
allshare = sum(heldcells.values())/max(1,sum(allcells.values()))
n_hi = sum(1 for share,_,_,_ in rows if share>0.8); n_lo = sum(1 for share,_,_,_ in rows if share<0.3)
print("\noverall held-share=%.0f%%  dense-cells>80%%held=%d  dense-cells<30%%held=%d  (of %d dense cells)"
      % (allshare*100, n_hi, n_lo, len(rows)))
