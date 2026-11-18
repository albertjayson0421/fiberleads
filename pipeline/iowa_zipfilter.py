"""PASS 2 of 2 — filter the captured Iowa universe (iowa_all.jsonl) to the 262 target
ZIPs. NAD_r23 populates ZIP for ~90.7% of Iowa rows, so a ZIP-string filter covers the
vast majority; blank-ZIP rows (Keokuk/Louisa-county NAD null-ZIP case) are held for
point-in-polygon recovery so nothing is silently dropped (completeness rule).

line1 stays RAW NAD (original preserved for evidence); the sweep runs with FL_NORM=abbrev,
which the fleet applies to produce the Kinetic-matching abbreviated form. Output feeds the
residential filter next."""
import json, collections
IN = "/Users/masani/iowa_pull/iowa_all.jsonl"
TARGET_OUT = "/Users/masani/iowa_pull/iowa_target.jsonl"
BLANK_OUT = "/Users/masani/iowa_pull/iowa_blank.jsonl"
target = set(open("/Users/masani/iowa_pull/zips.txt").read().split())

n = kept = blank = nontarget = 0
per_zip = collections.Counter(); per_county = collections.Counter(); blank_county = collections.Counter()
with open(TARGET_OUT, "w") as tout, open(BLANK_OUT, "w") as bout:
    for line in open(IN):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        n += 1
        z = (r.get("zip") or "").strip()
        if z in target:
            kept += 1; per_zip[z] += 1; per_county[r.get("county", "")] += 1
            tout.write(line + "\n")
        elif not (z and z.isdigit() and len(z) == 5):
            blank += 1; blank_county[r.get("county", "")] += 1
            bout.write(line + "\n")
        else:
            nontarget += 1

print("PASS2_DONE total_IA=%d" % n)
print("  in-target (kept) : %d  -> %s" % (kept, TARGET_OUT))
print("  blank-ZIP (held) : %d  -> %s  (point-in-polygon recovery vs 262 ZCTAs)" % (blank, BLANK_OUT))
print("  non-target ZIP   : %d  (outside the 262)" % nontarget)
print("  conservation: kept+blank+nontarget = %d == total %d ? %s" % (kept + blank + nontarget, n, kept + blank + nontarget == n))
print("  distinct target ZIPs hit: %d of %d" % (len(per_zip), len(target)))
missing = sorted(target - set(per_zip.keys()))
print("  target ZIPs with ZERO NAD rows (%d): %s" % (len(missing), missing[:40]))
print("  top target counties:", dict(per_county.most_common(12)))
print("  blank-ZIP rows by county (recovery scope):", dict(blank_county.most_common(12)))
