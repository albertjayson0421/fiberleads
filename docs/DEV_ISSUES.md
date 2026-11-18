# Dev Issues — ready to paste into GitHub

> ⚠️ **The P1 "Rebuild TX/GA/KY/SC statewide universes" issue is now largely DONE (2026-09-12)** —
> see [`UNIVERSE_STATUS_CURRENT.md`](UNIVERSE_STATUS_CURRENT.md). TX is 254/254, KY/NC/IA complete,
> GA complete-on-addresses, SC 33/46 authoritative (footprint eliminated). **Recast P1 as the actual
> remaining data work:** (a) GA county-label backfill (point-in-polygon vs TIGER counties),
> (b) the last SC counties (5 WTH via bbox-tiled fetch, Chesterfield, 7 SCIC-secure — owner),
> (c) verification. `build_nc_statewide.py` (the template P1 references) is now vendored at
> `pipeline/build_nc_statewide.py`. The P0 clean-clone issue and everything else below stand.

Six issues for the weekend PRs, in priority order. Each is self-contained; copy the block
into a new GitHub issue (title = the `##` line). File/line references were verified against
the repo on 2026-09-11 — re-check line numbers after the first PR moves code. Time figures
are **projections**, not measurements. **No issue here involves a live carrier or proxy
request** — access is 403-walled and owner-gated (`STATE_OF_THE_SYSTEM.md` §3).

---

## [P0] Make the repo run end-to-end from a clean clone

**Labels:** `P0`, `blocker`, `infra`, `good-first-pr`

### Context
A fresh clone cannot import the engine or run the pipeline. This blocks every other task and
is the highest-leverage weekend win. See `STATE_OF_THE_SYSTEM.md` §2a and
`DEV_DAY1_QUICKSTART.md`.

### Evidence (verified)
- `runtime/fleet_pipeline.py:40` and `runtime/fleet_actor_v2.py:50` read
  `open(os.path.join(os.environ["FL_CONFIG"], "uqual.txt"))` **at module import**. `FL_CONFIG`
  is `setdefault`-ed to `runtime/config/` (`fleet_pipeline.py:32`), which ships only
  `uqual.txt.example` — so `import fleet_pipeline` fails `FileNotFoundError`, and the offline
  suites fail at import.
- `address_source.py` and `residential_filter.py` are now vendored in `runtime/engine/`, but
  pipeline scripts still `sys.path.insert` external dirs, e.g.
  `pipeline/build_delivery.py:15`, `pipeline/verify_deliverable.py:6`,
  `pipeline/prior_delivered.py:6`, and the `iowa_*`/`tx_*` scripts point at
  `/Volumes/T7/.../02_System/engine`.
- **36 of 39** `pipeline/*.py` hardcode absolute paths (`/Users/masani/...`, `/Volumes/T7/...`).
- Two divergent delivery builders: `pipeline/build_delivery.py` writes a **CSV**
  (`Address,City,State,Zip,Latitude,Longitude,FiberPlan`) with Iowa paths hardcoded
  (`/Users/masani/iowa_pull/...`); `pipeline/verify_deliverable.py` reads an **XLSX** and is
  hardcoded to Iowa's bounding box. They don't target the same artifact.

### Tasks
- [ ] Make secret loading **lazy** — move the `uqual.txt` read out of module scope into the
      auth path (or a cached accessor), so `import` works with no secret. Add a
      `uqual.txt.example` fallback for offline. ⚠️ `fleet_pipeline`/`fleet_actor_v2` are
      **hash-gated frozen modules** — change on a branch; the owner re-signs
      `runtime/production_manifest.json` on merge.
- [ ] Repoint every pipeline `sys.path.insert`/import at `runtime/engine/`.
- [ ] Replace hardcoded machine paths with args/env/`market.json` (see the market-config
      issue).
- [ ] Unify the delivery path: one builder, one format, consumed by `verify_deliverable.py`;
      remove the Iowa-bbox hardcode (take the bbox/keyset from config).

### Acceptance criteria
- [ ] From a fresh clone in the pinned devcontainer, **all 7 offline suites** print
      `RESULT: N passed, 0 failed` with no manual secret creation (placeholders auto-fall-back).
- [ ] `build_delivery` output is exactly what `verify_deliverable` validates, for a non-Iowa
      market, driven by config.
- [ ] No new secret is committed (`.gitignore` still covers `uqual.txt`, `residential.json`).

_Projection: ~3–6 dev-days._

---

## [P1] Bounded self-service: package the pull as a Cloud Run Job the owner-approved dev can trigger

**Labels:** `P1`, `infra`, `owner-priority`

### Context
The owner needs to run pulls **without being present** (owner decision 2026-09-12) under the **bounded self-service** model: the developer TRIGGERS the pull
job, but the job runs as the `fleet-runner` service account that holds the secrets — so the dev
never sees a secret value and gets no prod code-change/deploy rights. Depends on [P0] (the pipeline
must run from a clean clone first).

### Tasks
- [ ] Wrap the end-to-end pull (universe → residential filter → dispatch → reconciliation →
      deliverable) as a container entrypoint driven by `market.json` (no interactive steps, no
      hardcoded paths — rides on the [P0] config work).
- [ ] Deploy it as a **Cloud Run Job** (`fleet-pull`) in the PROD project **with
      `--service-account=fleet-runner@$PROD.iam.gserviceaccount.com`** so secrets are injected at
      run time from Secret Manager, not handled by any human.
- [ ] Make it **resumable/idempotent** (re-running continues, never double-charges the carrier or
      duplicates rows) and emit the reconciliation + a clear success/fail exit.
- [ ] Document the one-line trigger the dev uses (`gcloud run jobs execute fleet-pull …`) and the
      owner's Phase-2 grant (`run.invoker` on the job) — covered in `docs/CONTROL_PLANE.md`.
- [ ] (Stretch, "runs with nobody") add a **Cloud Scheduler** monthly trigger so pulls can run with
      no human at all; the dev just maintains it.

### Acceptance criteria
- [ ] The dev, with only `devFleetOperator` (DEV) + `run.invoker` on `fleet-pull` (PROD), can start
      a pull and retrieve the finished list **without any owner action and without any
      secret-reading permission on his own identity**.
- [ ] A secret read during the run appears in the locked audit bucket under the `fleet-runner`
      identity, not the dev's.
- [ ] Re-running a completed/partial job reconciles cleanly (no duplicate carrier calls or rows).

> Note: this makes the pull *runnable by the dev*; it does not bypass **Kinetic authorized access** —
> serviceable-lead pulls still return nothing until Kinetic grants access (owner, external). Universe
> refreshes run without Kinetic.

_Projection: ~2–4 dev-days after [P0]._

---

## [P1] First-ask input-quality fix (collapse the ~45% 1065 bucket)

**Labels:** `P1`, `correctness`, `throughput`

### Context
`AddressNeedsFix`/`AddressNotFound`/`AddressSuggestions` (errorCode **1065**) was **~45% of
the Inman run** — mostly blank/wrong city+ZIP and TIGER-interpolated synthetic strings sent on
the *first* ask. Correcting on the first ask (not only in after-the-fact remediation) turns a
large chunk of UNRESOLVED into definitive answers **and** cuts the call multiplier
(`STATE_OF_THE_SYSTEM.md` §3, Truth 3). This is fully offline work.

### Evidence (verified)
- `pipeline/remediate2.py` already implements the proven escalation ladder (ZIP→USPS city,
  without-city, `AddressSuggestions` candidate mining, street-type/spelling variants).
- `runtime/engine/classifier.py` invariant: 1065 → UNRESOLVED (retryable), **never**
  NOT_FOUND. Do not weaken this.
- NC already builds from authoritative NG911 points (`build_nc_statewide.py`); the other
  states still lean on weaker strings.

### Tasks
- [ ] Extract the cheap, high-yield corrections from `remediate2.py` and apply them **before
      the first carrier call** in the universe/dispatch path: authoritative ZIP→USPS city
      fill, canonical street formatting.
- [ ] Prefer authoritative NG911/E911 address strings over TIGER where the universe carries
      them.
- [ ] Keep `remediate2.py` as the backstop for what the first ask still misses.

### Acceptance criteria
- [ ] Measured **before/after first-ask needs-fix rate on the same fixture/captured set**,
      shown in the PR (no live calls — replay stored responses).
- [ ] No change to the 1065→UNRESOLVED classification; classifier tests still green.
- [ ] Reduction is reproducible from committed fixtures.

_Projection: ~3–6 days._

---

## [P1] Cut wasted carrier calls per durable answer

**Labels:** `P1`, `throughput`, `efficiency`

### Context
Fresh residential exits are the real ceiling (`STATE_OF_THE_SYSTEM.md` §3, Truth 2). Fewer
calls per durable = more addresses resolved per scarce exit = more coverage when a real pull
runs. This is offline/measurement work; it does not need live access.

### Evidence (from prior run notes — re-measure, don't trust the number blindly)
- A sample run spent ~**1.75 searches + 0.46 auths per job** — a ~3.7× call multiplier over
  the ideal 1 search/job.
- The re-auth and repeated `AddressSuggestions` round-trips live in the sweep/actor path
  (`runtime/fleet_pipeline.py`, `runtime/fleet_actor_v2.py`).

### Tasks
- [ ] Instrument calls-per-durable (searches/job, auths/job) as a first-class run metric.
- [ ] Find and cut needless re-auth (reuse a valid session) and redundant `AddressSuggestions`
      round-trips.
- [ ] Fold in the first-ask input-quality fix (fewer 1065s = fewer remediation round-trips).

### Acceptance criteria
- [ ] PR shows **measured** calls-per-durable before vs after on the same offline set.
- [ ] No correctness regression: the classifier still decides serviceability from a priced
      plan; nothing is downgraded to reduce calls.

_Projection: ~2–4 days._

---

## [P1] Fix the dishonest progress meter (count durable answers, not attempts)

**Labels:** `P1`, `correctness`, `observability`

### Context
The project has been burned by a progress counter that reported **attempts** as **settled
work** (e.g. 47.8% real coverage quoted as 95%). Any "% complete" the system shows must be
backed by real, evidence-backed dispositions, or it will mislead the owner — who ships on
these numbers. A failed/blocked/never-asked address is UNRESOLVED, never progress.

### Evidence (verified)
- Conservation ("unaccounted == 0") is **printed, not asserted** as a blocking gate
  (`STATE_OF_THE_SYSTEM.md` §2e).
- Run databases carry the real buckets (`baseline`, `assignments`, `current_disposition`,
  `evidence`, `scope_excluded`) — the meter should read those, not launched-request counts.

### Tasks
- [ ] Define "progress" strictly as durable, evidence-backed dispositions over the reconciled
      denominator (kept vs held vs answered vs unresolved), matching the reconciliation stage.
- [ ] Make the meter refuse to emit a "% complete" that isn't backed by rows (fail closed).
- [ ] Promote conservation to a **blocking assert** at run/delivery boundaries: unaccounted
      must equal 0 or the stage stops.

### Acceptance criteria
- [ ] On a fixture run, the reported % equals an independent count of evidence-backed
      dispositions / reconciled denominator.
- [ ] A deliberately introduced unaccounted row makes the conservation assert **fail the run**.
- [ ] Attempts, retries, and blocked/never-asked requests never count as progress.

_Projection: ~2–4 days._

---

## [P1] Repair the corrupted TX statewide run.db

**Labels:** `P1`, `data-integrity`

### Context
TX is the furthest-swept state, but its statewide run.db is **corrupted**, so no TX progress
number can be trusted until it's repaired (`STATE_OF_THE_SYSTEM.md` §2d). Raw evidence must be
preserved — rebuild dispositions from evidence, never mutate evidence.

### Evidence (verified this pass)
- `runs/TX-statewide-2026-09-03/run.db`: `PRAGMA quick_check` reports a malformed b-tree —
  `Freelist: size is 105 but should be 107`, `Tree 39 page 835377 cell 42: 2nd reference to
  page 424065`, `Child page depth differs`. Its own counts (baseline 1,074,655;
  current_disposition ~1,058,512; evidence 2,587,800) are therefore **not trustworthy**.
- A clean pre-repair backup exists:
  `run.db.pre-queue-repair-20260904T092510Z.sqlite` (+ `.json` sidecar) — `quick_check: ok`,
  baseline 1,074,655, current_disposition **99,383** (= 9.2% swept as of Sep-4), sha256
  recorded in the sidecar.

### Tasks
- [ ] Attempt in-place recovery (`.recover` / dump-and-reload into a fresh schema); if it can't
      reconcile, fall back to the verified Sep-4 backup and re-derive dispositions from the
      preserved `evidence` table.
- [ ] Verify the recovered db passes `PRAGMA integrity_check` and that
      `baseline == residential_screen` and conservation holds (unaccounted == 0).
- [ ] Record the true swept count/% after recovery; do **not** carry forward the corrupted db's
      inflated numbers.

### Acceptance criteria
- [ ] Recovered TX run.db passes `integrity_check`.
- [ ] Row counts reconcile against the preserved evidence and the backup sidecar; the reported
      TX swept % is re-derived, not inherited from the corrupted file.
- [ ] Raw `evidence` rows are unchanged (immutable); only derived dispositions are rebuilt.

_Projection: ~1–2 days._

---

## [P1] Rebuild TX / GA / KY / SC universes to TRUE statewide

**Labels:** `P1`, `data-build`, `scope`

### Context
A "statewide pull" = **every residential address in the entire state**, from authoritative
sources, filtered to residential — **FCC/footprint data never caps the universe**
(`STATE_OF_THE_SYSTEM.md` §0). Only **NC** is built this way today; TX/GA/KY/SC are
footprint/partial and must be rebuilt. `build_nc_statewide.py` is the template
(all 100 counties, keep+hold fail-open, `county_name` on every row, footprint reachable only
via `FL_NC_SCOPE=footprint`). This is offline data work — no carrier calls.

### Evidence (verified this pass)
| State | Built scope | Universe (measured) | Gap |
|---|---|---|---|
| NC ✅ | all 100 counties, NC OneMap NG911 + FEMA | 6,188,837 rows (baseline 5,640,928) | none — the template |
| TX ⚠️ | authoritative **1.84M** (`universe.jsonl` 1,836,461); run.db seeded **KEEP-ONLY** | far short of full TX (~11–12M) | rebuild full-statewide from NAD; seed **keep+hold** fail-open like NC |
| GA ❌ | STAGE 2 keeps "in-footprint rows only… FCC BDC Windstream **131413** blocks" | 691,203 rows | rebuild from the full GA GIO statewide address layer |
| SC ❌ | only footprint counties acquired per-county | 842,845 rows | acquire all 46 counties |
| KY ❌ | "(Windstream/Kinetic footprint)" scoped | 534,032 rows | rebuild full-statewide from KY NG911/KyGovMaps |

Source for full-state builds: `nad_bulk/NAD_TXT.zip` (7.6 GB) + state NG911/E911 layers.

### Tasks
- [ ] Generalize `build_nc_statewide.py` into a config-driven builder (depends on the
      market-config work): per-state authoritative source + full county denominator, FEMA
      keep+hold fail-open, `county_name` on every row, footprint only as an optional filter.
- [ ] Rebuild TX (fix the KEEP-ONLY seed → keep+hold), GA (drop the FCC-131413 cap), SC (all
      46 counties), KY (full statewide).
- [ ] For each state, produce a reconciliation ledger (source records → canonical → residential
      keep/hold/drop → seeded), unaccounted == 0.

### Acceptance criteria
- [ ] Each rebuilt universe covers the **entire state** county set from the official
      denominator, not a footprint/partial subset.
- [ ] `keep + hold` seeded (fail-open); only confident non-residential dropped, with evidence.
- [ ] Every row carries an authoritative rooftop coordinate + `county_name`.
- [ ] A footprint filter is available but **off by default**; turning it on removes zero rows
      from the stored universe (filter, not rebuild).

_Projection: ~1–2 days per state (data-download + FEMA-screen bound), plus the config work._

---

## Not on this list (on purpose)
- **Live pull / carrier access** — external, owner-gated (403 wall). Track separately; it is
  not an engineering issue.
- **Autonomy / orchestrator** — deferred by the owner behind THE PULL (`STATE_OF_THE_SYSTEM.md`
  §2b).
- **Scaling by adding boxes** — measured counterproductive and moot until access is restored
  (Truth 2).
