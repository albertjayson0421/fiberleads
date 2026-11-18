# SC / NC / KY residential-universe inventory (2026-09-10, item-6 recheck)

Per directive: "Count SC/NC/KY residential universes where suitable local inputs exist. Otherwise identify the specific missing source and scope."

## Residential universes (counted premises)

| State | Residential universe | Unique premises | Status |
|---|---|---|---|
| Iowa | `iowa_pull/iowa_statewide_kept.jsonl` | 941,315 | BUILT (universe of record) |
| South Carolina | — not built — | **UNKNOWN** | inputs partial (geo only) |
| North Carolina | — not built — | **UNKNOWN** | inputs partial (geo only) |
| Kentucky | — not built — | **UNKNOWN** | inputs partial (geo only) |

## What DOES exist (T7 `04_Data`, checked 2026-09-10; T7 mounted)

Geographic scaffolding only — **not** a residential-address universe:
- SC: `geo/sc_all_bg.json` (block groups), `geo/sc_places.json`, `geo/sc_cousub.json`, `SC_STATEWIDE_UNIVERSE_FINDING.md` (a finding doc, not counted premises).
- NC: `geo/footprint_kinetic_NC_blocks.json` (Kinetic footprint blocks), `history/redzone_nc_sweep.csv` (a partial sweep, not a universe).
- KY: `geo/ky_zip_city.json`, `geo/ky_places.json`, `history/london_ky_sweep.csv` (partial sweep).

## Specific missing source & scope (to build each universe)

For each of SC/NC/KY the **residential-address universe is not built**. Missing source =
authoritative address points (state/county NG911/E911 + NAD) intersected with the Kinetic
footprint block groups above, then FEMA-USA-Structures residential filter (OCC_CLS) — the same
pipeline that produced Iowa's 941,315. Until built, per-state monthly volume is **UNKNOWN** and any
multi-market aggregate stays a planning scenario (only Iowa is counted; Texas 12M is operator-stated).
