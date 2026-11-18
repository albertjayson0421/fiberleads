# Statewide Residential-Address Universe — Final Report

**Regenerated:** 2026-09-12 (corrected). Supersedes the 2026-09-11 version.
**Scope rule:** every residential address in the ENTIRE state, from AUTHORITATIVE sources
(state NG911 / NAD / TxGIO / county E911), FEMA residential screen fail-open (keep
confident-residential + hold uncertain; drop only confident non-residential).
**FCC / carrier "footprint" data NEVER defines or caps the universe.**

## Reconciliation (measured 2026-09-12, `_FINAL_RECONCILE.txt`)

| State | Residential rows | Unique | Counties | Source | Status |
|---|---|---|---|---|---|
| **TX** | **10,878,814** | 10,878,814 (0 dups) | **254/254** ✅ | TxGIO (state) + NAD | COMPLETE — corrected this session |
| **NC** | 6,188,837 | 6,188,630¹ | 100/100 ✅ | NC state NG911 | COMPLETE |
| **GA** | 3,918,854 | 3,918,854 (0 dups) | 0/159² | GA GIO (state) | addresses+coords complete; **county field empty** |
| **SC** | **2,547,900** | 2,547,900 (0 dups) | **33/46** | 33 authoritative county E911 | scope-corrected this session; 13 pending |
| **KY** | 2,173,004 | 2,165,144 (7,860 dups) | 120/120 ✅ | NAD | COMPLETE |
| **IA** | 1,585,868 | 1,585,868 (0 dups) | 99/99 ✅ | IA state | COMPLETE |
| **TOTAL** | **27,293,277** | ~27.3M | | | |

¹ NC uses a different row schema (no `ck` field); dedup verified separately (207 dups, 6,188,630 unique).
² GA rows carry rooftop lat/lon + address but no `county` label — see GA gap below. Coverage was
verified by geo-cell, not county.

## What was corrected this session (2026-09-12)

**TX — was missing 2 entire counties.** The NAD-based TX universe (10,895,750) had **zero** rows
for **Henderson** (~pop 82k) and **Howard** (~pop 35k), and only 27% of **Hale**. Root cause: NAD
county coverage is patchy (it also barely covers SC — see below). Fix: pulled all three counties
from the authoritative **TxGIO** state source (already on disk, 12,140,485 addresses / 254 counties),
FEMA-screened them, and rebuilt → **TX_residential_v2.jsonl, 254/254, 10,878,814 rows.** The rebuild
also removed **~90,000 duplicate rows** that the original OOM-resumed screen had double-written.
A per-county TxGIO-vs-screened comparison confirmed **only those 3 counties** were deficient; all
other 251 counties are in the normal screened/raw band, so no broader TX rebuild is needed.

**SC — 9 counties were footprint-sourced (a scope violation).** The prior SC universe used
**`footprint_county` data for 9 counties (841,281 rows), including Greenville — the largest county
in the state.** That is exactly the banned "footprint caps the universe" pattern. Fix: discovery +
verification found authoritative public county E911 address-point layers for **8 of the 9**
(Greenville, Spartanburg, Lexington, Colleton, Kershaw, Lancaster, Calhoun, Orangeburg), pulled and
FEMA-screened them, and added the new WTH county **Newberry**. Result:
**SC_residential_v2.jsonl — 2,547,900 residential across 33 AUTHORITATIVE counties, ZERO footprint.**
(Authoritative screened counts are slightly lower than raw footprint because the screen drops
non-residential — e.g. Greenville 306,425 footprint → 272,547 residential — which is correct.)

## SC — the 13 counties not yet in the universe (33/46)

- **5 counties via WTH TGIS county E911 — build follow-up (extractor built, canary-verified):**
  Clarendon, Union, Marion, Marlboro, Dillon. These publish address data only through a
  proprietary per-feature viewer with **no in-table coordinates**, so each address needs an
  individual request AND the feature-list endpoint (`getFtrs`) uses offset pagination that
  degrades badly at high offsets — making a full unattended pull impractically slow/fragile this
  session. The per-feature extractor is written and **canary-proves clean output** for all five
  (e.g. Union "301 1ST ST" 34.715,-81.601; Marion "104 2ND AVE"; Dillon "300 N 10TH AVE"), so the
  fix is a **bbox-tiled fetch** (query features by map extent instead of deep offset) or a direct
  county-GIS data request. ~90k addresses; would take SC to 38/46. NOT owner-gated.
  (Newberry, a 6th WTH county, DID complete — 22,610 screened — because it exposes coordinates
  in-table; it is already in the 33.)
- **1 county needs re-sourcing (Chesterfield):** was footprint; no public ArcGIS address-point
  layer found. Available via its WTH parcel-situs viewer (`chesterfieldsc.wthgis.com`, dsid 6796)
  or a data request to Chesterfield County GIS / SC RFA — a short follow-up. NOT owner-gated.
- **7 counties need SCIC secure access (OWNER action 🔒):** McCormick, Williamsburg, Allendale,
  Bamberg, Cherokee, Chester, Fairfield. No public layer; the data lives in the SCIC "Secure Data"
  AGOL group — request drafted in `SC-RECOVERY-2026-09-12/evidence/statewide_access.md`
  (requires submitting the request + confirming commercial-use license).

## GA — one remaining gap

GA has all **3,918,854** residential addresses with rooftop lat/lon, but the `county` field is
**empty** (the GA GIO source/extract never carried it, and coverage was verified by geo-cell rather
than county). This is a downstream **enrichment** gap, not a completeness gap — the addresses and
coordinates are present. Fix: point-in-polygon spatial join of the 3.9M points against the TIGER
`tl_2024_us_county` (GA, FIPS 13) polygons; ~30–60 min once the county shapefile is downloaded.

## Not a universe problem — the carrier pull

Building these universes is the residential-FILTER-first stage. Determining serviceability
(fiber plans populating) requires **authorized Kinetic access** — externally blocked by the 403
behavioral wall, resolvable only by the owner's authorized-access request (see OWNER_WHATS_LEFT §0).
No serviceability numbers are claimed here; this report is the residential universe only.

## Files (on T7)

- `runs/STATEWIDE_NAD_2026-09-12/TX_residential_v2.jsonl` — TX 254/254 (canonical; supersedes `TX_residential_universe.jsonl`)
- `runs/STATEWIDE_NAD_2026-09-12/SC_residential_v2.jsonl` — SC 33/46 authoritative (supersedes `SC_FULL_universe.jsonl`)
- `runs/STATEWIDE_NAD_2026-09-12/GA_residential_universe.jsonl` · `KY_residential_universe.jsonl`
- `runs/NC-statewide-2026-09-03/universe.jsonl` · `iowa_pull/iowa_all.jsonl`
- `runs/STATEWIDE_NAD_2026-09-12/_FINAL_RECONCILE.txt` — the reconciliation above
- SC evidence + endpoints: `runs/SC-RECOVERY-2026-09-12/` (discovered_endpoints.json, evidence/)
