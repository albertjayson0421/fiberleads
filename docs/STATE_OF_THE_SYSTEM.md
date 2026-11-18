# State of the System — Developer Handoff

> ⚠️ **UNIVERSE NUMBERS BELOW ARE SUPERSEDED (as of 2026-09-12).** The statewide universes
> were rebuilt/corrected after this doc was written. **For current universe status, read
> [`UNIVERSE_STATUS_CURRENT.md`](UNIVERSE_STATUS_CURRENT.md) — it is the source of truth.**
> In short: TX is now **254/254 counties** (10,878,814), NC/KY/IA complete, GA complete on
> addresses (county-label backfill pending), SC **33/46 authoritative** counties (2,547,900,
> footprint sources eliminated). The §2d/§4 "rebuild TX/GA/KY/SC from footprint/partial" work
> below is **largely DONE** — the real remaining data work is GA county backfill + the last
> SC counties, not a four-state rebuild. Everything about the *code, security, and access
> model* in this doc is still accurate.

**Read this before you touch anything.** It is the honest, evidence-based state of the
FiberLeads system as of **2026-09-11**, from a direct audit of the code in this repo and
the universe/run databases on the data drive. Every number below was measured this pass;
where a number can't be trusted, that is said out loud.

The short version: **the engine core is genuinely strong (the owner estimates the software
is ~90% built). What is NOT done breaks into three separate piles that must not be confused:**

1. **Software gaps you can fix** — the repo doesn't run end-to-end from a clean clone, there
   is no orchestrator/autonomy, it isn't config-driven multi-market, and some correctness
   gates aren't enforced.
2. **A data-build gap you can fix** — only **one** of the five target states (NC) has a
   *true statewide* universe. TX, GA, KY, SC (and the older IA pilot) exist only as
   **footprint / partial subsets** and must be rebuilt to full-statewide from authoritative
   sources.
3. **One external blocker you CANNOT fix in code** — the live carrier pull is parked behind
   a Kinetic/Cloudflare **403 behavioral wall**; authorized access has been requested and
   **not** granted. This is owner/business, not an engineering task.

> **Owner-set priority: the immediate goal is THE PULL** — take every residential address in
> each target state to a definitive carrier answer at full deliverable standard. **Autonomy
> is explicitly deferred.** Focus order: make the pipeline runnable from a clean clone (§4 P0)
> → correctness + input-quality + true-statewide universes so the pull is repeatable and
> complete (§4 P1) → *then* scaling within reality (§4 P3). Do **not** make any carrier or
> proxy request while access is 403-walled — that is external and owner-gated.

---

## 0. The universe-scope rule (read this first — it governs everything below)

**A "statewide pull" means EVERY residential address in the ENTIRE state.** The universe is
built from **authoritative address sources only** — state/county NG911/E911 address points,
the National Address Database (NAD), parcel/address-point layers — then filtered to
residential (FEMA `OCC_CLS`, fail-open: keep confident-residential + hold uncertain, drop
only confident non-residential).

**FCC BDC data and Kinetic/Windstream "footprint" or "serviceable-territory" data NEVER
define or cap the universe.** A footprint may be used to *order* the work (sweep likely
areas first) but it may never *remove* an address. A footprint-scoped or partial-county
universe is **INCOMPLETE and is NOT statewide** — it silently drops exactly the rural
addresses this rural-ILEC product exists to find. `build_nc_statewide.py` is the correct
pattern: it builds all 100 NC counties from the official state denominator and keeps the
27-county Windstream footprint reachable only as an *optional filter* (`FL_NC_SCOPE=footprint`),
never as the default. Every other state must match it.

The five target states are **TX, NC, KY, SC, GA.**

---

## 1. What actually works (build on this, don't rewrite it)

- **The carrier sweep engine** (`runtime/fleet_scheduler.py` → `fleet_pipeline.py` →
  `fleet_actor_v2.py` → `product_stage.py`). Single-process, async actors, global
  token-bucket rate limiting, durable ledger resume, in-flight recovery, a transport-silence
  circuit breaker, exit-rotation recovery on 403/429. Market-agnostic. **This is the mature
  part of the system.**
- **The classifier** (`runtime/engine/classifier.py`) — the single classification authority.
  Honest invariants: 1065 → UNRESOLVED, only verified 1060 → NOT_FOUND, fail-closed on
  unknown, serviceable ⇔ a priced fiber plan populates.
- **The guards** — `endpoint_guard` (process-wide anti-hammer, born of a real
  self-firewalling incident), `egress_guard` (fail-closed proxy egress, never the machine's
  own IP), `fetch_guard` (a refused request is never an answer).
- **The startup gates** — `fleet_scheduler.gate_hashes()` freezes the 5 production modules
  against `runtime/production_manifest.json`; the readiness gate won't reopen prod on a bare
  auth-201.
- **The universe + residential-filter modules are now vendored into the repo**
  (`runtime/engine/address_source.py`, `runtime/engine/residential_filter.py`) — see §2a for
  the wiring that is still missing.
- **Offline tests** — 7 deterministic suites (3 in `runtime/`, 4 in `runtime/engine/`)
  covering the exact places past incidents hit. *(Not run in this audit — the host has only
  Python 3.14; they must be run in the pinned devcontainer. Getting them green on a clean
  clone is the Day-1 task — see `DEV_DAY1_QUICKSTART.md`.)*
- **The docs** — README / ONBOARDING / ARCHITECTURE / SECURITY are accurate on the model and
  the correctness invariants.

## 2. What does NOT work / is missing

### 2a. The repo is not self-contained (P0 — blocks you on day one)
- **Secrets load at module import.** `fleet_pipeline.py:40` and `fleet_actor_v2.py:50` do
  `open(os.path.join(os.environ["FL_CONFIG"], "uqual.txt"))` at import time. `FL_CONFIG`
  defaults (via `setdefault`) to `runtime/config/`, which ships only `uqual.txt.example` —
  so on a clean clone `import fleet_pipeline` **fails with `FileNotFoundError` on
  `uqual.txt`**, and the offline tests fail at import. Day-1 workaround: copy the example to
  `uqual.txt`. Real fix: load the secret lazily (inside the auth path). ⚠️ These are
  **hash-gated frozen modules** — edit on a branch; the owner re-signs the manifest on merge.
- **The vendored engine modules are not wired in.** `address_source.py` and
  `residential_filter.py` now live in `runtime/engine/`, but the pipeline scripts still
  `sys.path.insert` the OLD external locations (`/Users/masani/FLEET_5K/merge/engine`,
  `/Volumes/T7/.../02_System/engine`). They must be repointed at the repo copies.
- **36 of 39 `pipeline/` scripts hardcode absolute machine paths** (`/Users/masani/...`,
  `/Volumes/T7/...`). Universe build, residential filter, remediation, and delivery cannot
  run from a clean clone until these are parameterized.
- **Two divergent delivery builders.** `build_delivery.py` writes a **CSV**
  (`Address,City,State,Zip,Latitude,Longitude,FiberPlan`) and is hardcoded to Iowa paths
  (`/Users/masani/iowa_pull/...`); `verify_deliverable.py` reads an **XLSX** and is hardcoded
  to Iowa's bounding box. They are not wired to the same artifact. (The dead
  `/tmp/claude-501/...` path previously reported in `build_delivery.py` has since been
  removed.) The reconciliation "stage" is markdown notes, not runnable code.
- The `deploy/` scripts deploy the **legacy search-only runner** (`node_run.py`, no
  product/pricing call), **not** the frozen async engine the architecture calls production.
  There is **no** working script that deploys/runs `runtime/`.

### 2b. No autonomy (P2 — deferred by the owner, but it is the biggest build)
- No orchestrator chains the stages. No scheduler (cron/n8n/Cloud Run). No alerting, no
  cross-stage health monitoring, no self-healing, no auto-provision/teardown, no per-market
  run-state machine. `CONTROL_PLANE.md`'s Cloud Run Job is **designed, not built.**
- Result: today a human walks ~9 manual stages per pull. That violates the project's
  "no agent in the loop" rule and is the largest single build in front of you — but the
  owner has deferred it behind THE PULL.

### 2c. Not config-driven / not multi-market
- Market, bbox, ZIP whitelist, paths, even canary addresses are **hardcoded per market**;
  `iowa_*` and `tx_*` are copy-forked. Adding a state today means copying and editing
  scripts — the opposite of the standard ("adding a state touches only that state's config").

### 2d. Universes — only NC is truly statewide (the data-build gap)
Every target state must be **EVERY residential address in the entire state** (§0). Measured
this pass against the run databases and universe files on the data drive:

| State | Scope actually built | Universe size (measured) | Swept to a disposition | Notes |
|---|---|---|---|---|
| **NC** | ✅ **Full statewide** — all 100 counties, NC OneMap NG911 + FEMA, keep+hold fail-open | `universe.jsonl` **6,188,837** rows; run.db baseline **5,640,928** | **0** (0.0%) | The gold-standard build (`build_nc_statewide.py`). Ready to sweep. |
| **TX** | ⚠️ **Partial** — authoritative universe **1.84M**, far short of full TX (~11–12M); statewide run.db seeded **KEEP-ONLY** (a fail-open defect vs NC) | `TX-authoritative` `universe.jsonl` **1,836,461** rows; statewide run.db baseline **1,074,655** | **corrupted db** — see below | Furthest-swept state, but **run.db is corrupted** (`PRAGMA quick_check` reports malformed b-tree: duplicate page references, child-depth mismatch). Last *clean* snapshot (Sep-4 pre-repair backup, quick_check ok): **99,383 / 1,074,655 = 9.2%** swept. Owner's working figure is ~low-teens %. **The current live number cannot be trusted until the db is repaired.** Needs full-statewide rebuild from NAD + keep+hold seed. |
| **GA** | ❌ **Footprint subset** — STAGE 2 keeps "in-footprint rows only… scoped to the FCC BDC Windstream 131413 blocks" | `universe.jsonl` **691,203** rows; run.db baseline **653,077** | **22,549 (3.4%)** | **Violates §0** — the universe is capped to the FCC filing. Needs rebuild from the full GA GIO statewide address layer. |
| **SC** | ❌ **Footprint / partial** — only footprint counties acquired per-county (E911/NG911) | `universe.jsonl` **842,845** rows; run.db assignments **773,736** | **0** (0.0%) | Not all 46 counties. Needs all-county acquisition. |
| **KY** | ❌ **Footprint subset** — "(Windstream/Kinetic footprint)" scoped | run.db baseline **534,032** (no `universe.jsonl`) | **12,052 (2.3%)** | Needs full-statewide rebuild. |
| **IA** *(pilot, not a target state)* | Partial pilot market | `iowa_all.jsonl` **1,585,868** rows | extensively worked earlier | The first market; useful as a worked example, not part of the 5-state deliverable. |

**Bottom line: 1 of 5 target states (NC) has a true statewide universe; the other four
(and IA) are footprint/partial and must be rebuilt full-statewide before "complete" can mean
anything.** The `NAD_TXT.zip` (7.6 GB) on the data drive is the authoritative national source
for the full-state rebuilds.

### 2e. Test/gate gaps that let you break correctness silently
- `classifier.py` — the most correctness-critical file — is **NOT in the enforced startup
  hash gate.** `gate_hashes()` checks only the 5 modules in `production_manifest.json`
  (`carrier_uqual`, `fleet_actor_v2`, `product_stage`, `billing_mapping`, `fleet_pipeline`).
  You can edit `classifier.py` and still pass startup. *(Note: `runtime/MANIFEST.sha256` is a
  broader release-candidate manifest that DOES list classifier + all guards — so the fix may
  be as small as pointing `gate_hashes()` at that manifest.)*
- **No CI.** The 7 suites run only when a human remembers.
- `endpoint_guard`, `egress_guard`, `fetch_guard`, `billing_mapping` have **thin or no
  dedicated tests** — the exact guards whose silent breakage caused past incidents.
- Conservation ("unaccounted == 0") is **printed, not asserted** as a run/delivery-blocking
  gate.

---

## 3. The three hard truths (internalize these before you plan your work)

**Truth 1 — The 403 wall is NOT a code problem. The carrier pull is parked, and it's not
yours to unpark.**
Live `search`/`product` return HTTP 403 (Cloudflare "Forbidden") on fresh US residential
exits, even with valid auth. Evidence in `pipeline/carrier_incident/` shows the working
requests carry a **PerimeterX/HUMAN behavioral SDK** signature the client cannot legitimately
produce. **The durable fix is authorized carrier access from Kinetic/Windstream** (a trusted
skip/token for the approved paths, or a carrier-run qualification batch — the incident packet
already asks for exactly this). It has been **requested and not granted**. Do **not** try to
forge behavioral tokens — futile and prohibited. **Do not make any carrier/proxy request
while walled.** ⚠️ Honesty defect to fix: `carrier_uqual.py` around L81–114 claims these
headers are "PROVEN 200 / not behavioral," which the repo's own captured evidence
contradicts — don't let it mislead you into thinking access is solved.

**Truth 2 — Scaling by adding boxes is measured to be counterproductive.**
The binding constraint is the carrier's per-exit tolerance and the supply of fresh
residential exit IPs — not CPU, bandwidth, threads, or box count. Measured previously: 2 proxy
lanes (21/min) < 1 lane (59/min); a 12-box run produced 0/609,580. The only
evidence-supported throughput levers are **fresh-exit supply** and **cutting wasted calls per
durable**. A "4x by scaling boxes" story is unproven and may be physically unreachable against
this endpoint. (This matters only *after* access is restored; it does not block weekend work.)

**Truth 3 — The 1065 wall is ~45% of a run, and it's largely an input-quality problem you
CAN fix now, offline.**
`AddressNeedsFix`/`AddressNotFound`/`AddressSuggestions` (errorCode 1065) were ~45% of the
Inman run — driven by TIGER-interpolated synthetic address strings and blank/wrong city/ZIP
sent on the *first* ask. The fix (the project's own mandate): build the universe from
**authoritative NG911/E911 address points** (NC already does this), and apply the ZIP→USPS
city fix + authoritative string on the **first** carrier ask, not only in the after-the-fact
remediation ladder. This both raises the definitive-answer rate and cuts the call multiplier.
You can build and prove this against captured/fixture responses without touching the live
endpoint.

---

## 4. Your prioritized roadmap (estimates are labeled projections, not measurements)

**P0 — Make the system runnable from a clean clone (before anything else).**
- Lazy secret loading so `import` + offline tests work with no real secret; repoint the
  pipeline imports at the vendored `runtime/engine/` modules; de-hardcode the pipeline paths;
  unify the two delivery builders. **~3–6 dev-days (projection).**
- (Owner/business, in parallel) pursue **authorized carrier access** — the gate on everything
  *live*. External, unknown duration.

**P1 — Correctness + generality + true-statewide universes (so a pull is repeatable and
complete).**
- One market-config abstraction; collapse the `iowa_*`/`tx_*` forks into one parameterized
  pipeline. **~4–7 days.**
- First-ask input-quality fix + NG911/E911-first universe (Truth 3). **~3–6 days.**
- **Rebuild TX/GA/KY/SC to full statewide** from authoritative sources (NAD + state layers),
  matching `build_nc_statewide.py` (keep+hold fail-open, `county_name` on every row, footprint
  only as an optional filter). Repair the corrupted TX run.db first. **~1–2 days per state
  (projection).**
- Bring `classifier.py` + guards under the enforced hash gate; add CI; test the untested
  guards; make conservation a blocking assert. **~4–7 days.**

**P2 — Autonomy (deferred by the owner).**
- End-to-end orchestrator (provision → universe → filter → canary → sweep → remediate →
  reconcile → deliver → teardown) with per-market run-state + fleet auto-provision/teardown;
  scheduling; alerting; self-healing; the `CONTROL_PLANE.md` Cloud Run Job.
  **~3–6 engineer-weeks (projection).**

**P3 — Scaling, within reality (Truth 2), and only after access is restored.**
- Measure fresh-exit supply; cut per-durable call churn; only then provision more exits.
  **~1–2 weeks — and multiplied scaling remains unproven.**

## 5. Correctness invariants you must never break
Serviceable ⇔ a named, priced fiber-tier bundle populates (not techType, not call-to-order,
not pending 1080). A failed/blocked/ambiguous/never-asked request is UNRESOLVED, never "not
serviceable." 1065 → UNRESOLVED (retryable), only verified 1060 → NOT_FOUND, unknown → fail
closed. **Residential filter before serviceability. Universe = every residential address in
the whole state from authoritative sources; footprint never caps it.** Rooftop coords,
NEW-only, real city. Never narrate a carrier "cap/limit/throttle" — dump the real response
body. Breaker trips only on transport silence (a 403/429/500 means rotate the exit, not
stop). Raw evidence immutable; one versioned classifier per lane; replay stored responses
after any classifier change.

## 6. Your first week
- **Day 1:** NDA signed → accounts → devcontainer up → read this + ARCHITECTURE + SECURITY →
  **get the offline tests green on a clean clone** (that alone surfaces the P0 breakage) →
  run one small pull on fixtures. Follow **`DEV_DAY1_QUICKSTART.md`** step by step.
- **Sat/Sun:** land the weekend PRs in `WEEKEND_PLAN.md` / `DEV_ISSUES.md`. Making the repo
  runnable end-to-end from a clean clone is the highest-leverage weekend win. Do **not**
  attempt a live pull — access is 403-walled and owner-gated.
