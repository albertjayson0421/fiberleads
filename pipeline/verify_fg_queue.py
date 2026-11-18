"""Independent verification of fairfield_gladbrook_queue.jsonl on 100% of rows.
Cross-checks coordinates against the town ZCTA bbox (a DIFFERENT source than the
FEMA footprint filter that selected the rows), so the check is not circular."""
import json, hashlib, re, collections, sys

QUEUE = "/Users/masani/iowa_pull/fairfield_gladbrook_queue.jsonl"
REQUIRED = ["ck", "line1", "city", "state", "zip", "lat", "lon"]
TOWN = {"52556": "Fairfield", "50136": "Gladbrook"}
# ZCTA bboxes fetched from Census TIGERweb (independent of FEMA); +/-0.03deg slack
# for zip-field rows the postal record places just outside the ZCTA boundary.
BBOX = {"52556": (-92.1208, 40.9011, -91.7749, 41.1485),
        "50136": (-92.3554, 41.4235, -92.2199, 41.5387)}
SLACK = 0.03

def norm(s): return re.sub(r"[^A-Z0-9]", "", (s or "").upper())
def ck24(line1, zip_): return hashlib.sha256((norm(line1) + "|" + zip_).encode()).hexdigest()[:24]

fails = collections.Counter()
n = 0
cks = set()
per_town = collections.Counter()
outside = []
for ln, line in enumerate(open(QUEUE), 1):
    line = line.rstrip("\n")
    if not line.strip():
        fails["blank_line"] += 1; continue
    n += 1
    try:
        r = json.loads(line)
    except Exception:
        fails["not_json"] += 1; continue
    # exact key set, exact order
    if list(r.keys()) != REQUIRED:
        fails["schema_keys"] += 1
        if fails["schema_keys"] <= 3: print("  schema off line", ln, list(r.keys()))
    # types
    for k in REQUIRED:
        if not isinstance(r.get(k), str):
            fails["nonstring_field:%s" % k] += 1
    z = r.get("zip"); city = r.get("city")
    if z not in TOWN: fails["bad_zip"] += 1
    elif city != TOWN[z]: fails["city_zip_mismatch"] += 1
    if r.get("state") != "IA": fails["bad_state"] += 1
    if not (r.get("line1") or "").strip(): fails["empty_line1"] += 1
    # ck recomputed independently
    if r.get("ck") != ck24(r.get("line1", ""), z or ""):
        fails["ck_mismatch"] += 1
    if len(r.get("ck", "")) != 24 or not re.fullmatch(r"[0-9a-f]{24}", r.get("ck", "")):
        fails["ck_format"] += 1
    if r["ck"] in cks: fails["dup_ck"] += 1
    cks.add(r["ck"])
    # real coordinate, and geographic cross-check vs town bbox (independent source)
    try:
        x = float(r["lon"]); y = float(r["lat"])
    except Exception:
        fails["bad_coord"] += 1; continue
    if not (-90 <= y <= 90 and -180 <= x <= 180):
        fails["coord_range"] += 1
    if z in BBOX:
        w, s, e, nn = BBOX[z]
        if not (w - SLACK <= x <= e + SLACK and s - SLACK <= y <= nn + SLACK):
            fails["coord_outside_town"] += 1; outside.append((ln, city, y, x))
    per_town[city] += 1

print("rows verified:", n)
print("per town:", dict(per_town))
print("distinct ck:", len(cks))
if fails:
    print("\nFAILURES:")
    for k, v in fails.most_common(): print("  %-24s %d" % (k, v))
else:
    print("\nALL CHECKS PASS — schema exact, ck recomputes, city/zip consistent, "
          "every coord real and inside town bbox, no dup ck.")
if outside[:5]:
    print("sample outside-bbox:", outside[:5])
