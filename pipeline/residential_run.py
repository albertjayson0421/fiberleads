"""Residential filter (HARD precondition before any sweep): screen an Iowa address
shard down to established residences using FEMA/footprint occupancy (free, no proxy,
no carrier). Drops confident non-residential (Commercial/Industrial/Assembly/Government/
Education/Agriculture/Utility) and OUTBLDG; keeps Residential + Unclassified. Held rows
(evidence gap) are written to a manifest, never dropped and never swept.

Usage: residential_run.py <in.jsonl> <kept.jsonl> <dropped.jsonl> <held.jsonl> [--workers 64]
"""
import sys, os, json, time
# resolve all path args to ABSOLUTE before chdir into the engine (engine loads data files rel to cwd)
_ABS = {i: os.path.abspath(sys.argv[i]) for i in range(1, min(5, len(sys.argv)))}
import flpaths
flpaths.ensure_engine_on_path()   # was: sys.path.insert + os.chdir into a /Volumes/T7 engine dir
import residential_filter as RF

def arg(name, d):
    return type(d)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d

def main():
    IN, KEPT, DROPPED, HELD = _ABS[1], _ABS[2], _ABS[3], _ABS[4]
    workers = arg("--workers", 64)
    addrs = [json.loads(l) for l in open(IN) if l.strip()]
    print("residential filter: %d input addresses, workers=%d" % (len(addrs), workers), flush=True)
    t0 = time.time()
    kept, dropped, held, counts = RF.filter_residential(addrs, workers=workers, progress_log=print)
    for path, rows in ((KEPT, kept), (DROPPED, dropped), (HELD, held)):
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    acc = len(kept) + len(dropped) + len(held)
    print("\nRESIDENTIAL_DONE in %.1f min" % ((time.time() - t0) / 60), flush=True)
    print("  input=%d  kept=%d  dropped=%d  held=%d" % (len(addrs), len(kept), len(dropped), len(held)))
    print("  conservation: kept+dropped+held=%d == input=%d ? %s (unaccounted=%d)"
          % (acc, len(addrs), acc == len(addrs), len(addrs) - acc))
    print("  counts:", dict(counts))

if __name__ == "__main__":
    main()
