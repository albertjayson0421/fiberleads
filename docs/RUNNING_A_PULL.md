# How to run a pull — step by step

This is the operator runbook: how a state pull actually runs, stage by stage, which script does each,
what guardrail protects it, and what "done" looks like. **Read `DEV_SYSTEM_GUIDE.md` first** for the
invariants — every stage below enforces one, and skipping one is how deliverables have been silently
broken.

> **Honest state (2026-09-12):** the live carrier stages **cannot run today** — the serviceability
> endpoint is behind an authorized-access wall (`DEV_SYSTEM_GUIDE.md §3.1`), and the pipeline isn't yet
> runnable end-to-end from a clean clone (that's your [P0] task, `DEV_ISSUES.md`). So today you run
> **Part A** (offline + the simulator) to learn and prove the engine; **Part B** is the real pull
> sequence you build toward and run once P0 is done and Kinetic access is granted. Many `pipeline/*`
> scripts are currently per-market with hardcoded paths — [P0] unifies them behind one `market.json`,
> so treat the script names below as the *stage*, not a finished CLI.

---

## Part A — what you can run TODAY (from a clean clone)

After the devcontainer is up (`DEV_DAY1_QUICKSTART.md`), from the repo root:

```bash
# 1. Offline unit + integration suites — must all print "RESULT: N passed, 0 failed"
python runtime/engine/test_classifier_negatives.py
python runtime/engine/test_reclassify.py
python runtime/engine/test_failure_handling.py
python runtime/engine/test_proxy_config.py          # needs the residential.json example from Day-1 setup
python runtime/test_integration_offline.py
python runtime/test_readiness_entrypoints.py

# 2. The end-to-end SIMULATOR — the real fleet engine against a localhost mock carrier
python runtime/test_sim_scenarios.py
```

`test_sim_scenarios.py` spins a local `ThreadingHTTPServer` mock and drives the **actual**
`fleet_scheduler → search actors → product/pricing → classifier → durable writer` path — no network,
no secrets, no live carrier. It exercises the **carrier-sweep half** of the pipeline (Stage 3–4
below); it does **not** run universe build / residential filter / delivery. That's the safe way to see
the sweep engine work and to test a classifier change before it touches production.

```bash
# 3. Prove the standards gate still fires (all rules present, none silently deleted)
python -c "import runtime.engine  # placeholder — run the standards selftest per DEV_SYSTEM_GUIDE §2"
```

---

## Part B — the full pull, stage by stage (the real sequence)

Order is fixed. Each stage feeds the next; each has a guard that **fails closed** rather than passing
bad data downstream.

### Stage 1 — Build the state's residential-address universe
- **Does:** enumerate *every* residential address in the state from authoritative government sources
  (state NG911 / county E911 / NAD / TxGIO), with an authoritative rooftop coordinate per address.
- **Script:** `pipeline/build_nc_statewide.py` is the reference/template (NC OneMap + FEMA); state
  variants follow it. Iowa's built universe (`iowa_universe.py` lineage) is the counted example.
- **Invariant:** authoritative sources only — a carrier "footprint" never defines or caps the universe.
- **Output:** `<state>_universe.jsonl` (one row per premises, with lat/lon + county).

### Stage 2 — Residential filter (FEMA) — BEFORE any carrier call
- **Does:** screen every universe row against FEMA USA Structures `OCC_CLS` at its coordinate (free,
  no proxy). Drop confident non-residential; keep Residential + Unclassified.
- **Script:** `pipeline/residential_run.py` → `runtime/engine/residential_filter.py`.
- **Guard:** `standards.py` R1 exits the pull (code 6) if a sweep is attempted on an unfiltered queue.
  The screen must reconcile `kept + dropped + held == total` or raise `FilterNotApplied`; if the
  unevaluated or held share is too high the batch is **refused** (do not sweep it), not passed through.
- **Output:** the residential-only queue that Stage 3 is allowed to sweep.

### Stage 3 — Serviceability sweep (the carrier stage) — **gated on authorized access**
- **Does:** for each residential address, ask Kinetic whether a **priced fiber plan populates**.
- **Engine:** `runtime/fleet_scheduler.py` (the async fleet) via a per-market dispatcher
  (`pipeline/tx_dispatch.py`-style). Auth mints a token; `search` qualifies the address; on
  fiber-exact, a `product` call fetches the priced bundle.
- **Guards (all in the engine — never hand-roll a loop around them):**
  - **egress_guard** — all traffic through the configured proxy; a proxy failure aborts, never falls back to direct.
  - **endpoint_guard** — process-wide circuit on sustained transport *silence*; a 403/429/500 is the endpoint talking and never trips it. Treat `EndpointDown` as *unfinished + resumable*, never an empty result.
  - **FETCH_METADATA** XHR headers on every `/api` request; global exit pool; rotate an exit before ~20 searches.
- **Classify:** every raw response → one versioned `classifier.py` disposition. Serviceable **only** if
  a named priced bundle populates. 403/429 = path refused (never a "no"). `1065`/`1700`/suggestions =
  "asked badly" → remediation. Only `1060` → terminal not-found.
- **Readiness gate:** production refuses to start without an approved end-to-end canary (auth 201 →
  search 200 → product 200 → terminal). **This cannot pass while the endpoint is walled** — hence
  Stage 3 is blocked until authorized access exists.
- **Output:** raw responses (immutable) + dispositions in the run's durable store.

### Stage 4 — Remediation (drain the "asked badly" pile)
- **Does:** re-ask everything that came back as needs-fix / no-match / suggestions, using the accepted
  spelling (`matching_candidates → variants → _near`), and normalize the submit per region (abbreviate
  everywhere except GA, which stores spelled-out).
- **Script:** `pipeline/remediate.py` / `remed_dispatch.py` / `finalize_remediation.py`.
- **Invariant:** an unknown address is **labelled, never deleted**. A no-match is a statement about the
  string you submitted, not the premises.

### Stage 5 — Reconciliation (completeness)
- **Does:** confirm every address reached a proven answer. Completeness = `closes_address()` (an
  answer whose meaning is proven), **never** `status in TERMINAL`.
- **Script:** `pipeline/finalize.py` / `finalize2.py`.
- **Guard:** the run does not close until `answered + explicitly-unresolved == total`, with no address
  silently marked done without a usable answer.

### Stage 6 — NEW-only diff
- **Does:** drop any premises delivered in a prior month; keep only brand-new serviceable homes.
- **Script:** `pipeline/prior_delivered.py` + `pipeline/join_new.py`.

### Stage 7 — Coordinate placement (rooftop pins)
- **Does:** attach the authoritative rooftop pin to each lead. `one_roof_one_address`; both axes ≥4
  decimals; no two leads share a pin. Carrier `geoLat/geoLong` fills a hole only — it never overwrites
  an authoritative point.
- **Note:** any market pulled before 2026-08-24 should be **re-placed from disk, not re-swept**
  (`DEV_SYSTEM_GUIDE §3.9`).

### Stage 8 — Deliverable build + verify
- **Does:** produce the client lead list and validate it against the standard.
- **Script:** `pipeline/build_delivery.py` → `pipeline/verify_deliverable.py`.
- **Gate (the 34-point standard):** residential-only, priced-fiber-populated, rooftop pin on the
  correct house, NEW-only vs prior, real city name on every row, reconciliation balances. **100% of
  rows, never a sample.** A list that fails any point does not ship.

---

## What "done" looks like
A pull is complete only when: the universe reconciles, every residential address has a proven
disposition or is explicitly unresolved (no silent losses), serviceable leads carry a priced fiber
plan + an authoritative rooftop pin, the NEW-only diff is applied, and `verify_deliverable` passes the
34-point standard on 100% of rows. Anything short of that is *unfinished*, not a result.

## Your near-term path
You can't run Stage 3–8 live yet (access + [P0]). The highest-leverage work now, all offline and all
moving toward the full pull: **[P0]** make the repo run clean → **package the pull as a Cloud Run Job**
(your bounded self-service trigger) → **first-ask input-quality** to cut the ~45% needs-fix bucket →
finish the remaining state universes. See `DEV_ISSUES.md`.
