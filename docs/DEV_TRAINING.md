# Developer Training & Onboarding Path

This is the **guided path** from zero to productive — not more reference, a curriculum. Work the
phases in order; each ends with a checkpoint you should be able to pass before moving on. Pair it with
the reference docs (it tells you *when* to read each). The glossary at the bottom decodes the jargon.

**Mental model to hold the whole time:** this system finds *every home in a state*, keeps only the
ones where Kinetic shows a **priced fiber plan**, keeps only the **brand-new** ones, and ships them
with a **rooftop pin**. Everything you'll read is in service of doing that **correctly, at 100%, every
month, with no agent in the loop.**

---

## Phase 0 — Orientation (read before touching code · ~1–2h)
- Read **`DEV_SYSTEM_GUIDE.md`** front to back. This is the master doc: the system, every real issue
  we've hit + how it was resolved, the invariants, and the honest full-state-in-2–4h analysis.
- Skim **`README.md`** (repo map).

**✅ Checkpoint:** in two sentences, say (a) what makes an address "serviceable" and (b) why a live
pull returns nothing today. *(Answers: a priced fiber plan populates — not fiber tech alone; and the
serviceability endpoint is behind an authorized-access wall, not a code bug.)*

## Phase 1 — Get it running (~1–2h)
- Follow **`DEV_DAY1_QUICKSTART.md`**: open the pinned devcontainer, run all 7 offline suites, run the
  simulator (`python runtime/test_sim_scenarios.py`).
- **Exercise:** read the simulator's output and identify which stages it exercised (scheduler → search
  actors → product/pricing → classifier → durable writer) and which it did **not** (universe build,
  residential filter, delivery).

**✅ Checkpoint:** all 7 suites print `RESULT: N passed, 0 failed`, and you can say what the simulator
proved (the carrier-sweep engine works end to end against a mock) and what it didn't (the data stages).

## Phase 2 — The engine and the rules (~half a day)
- Read **`ARCHITECTURE.md`** (runner model, call flow, the 5 frozen/hash-gated modules) and
  **`DEV_SYSTEM_GUIDE.md §2`** (the invariants).
- **Exercises (read the code, don't guess):**
  1. In `runtime/engine/classifier.py`, trace how a response with `errorCode 1065` is classified, and
     one carrying a priced bundle. Confirm `1065` becomes remediation, not "no."
  2. Find where **residential-first** is enforced (`standards.py` R1) and where the **endpoint circuit**
     lives (`endpoint_guard.py`). Note: only transport *silence* trips it — a 403 never does.
  3. Find the **5 hash-gated modules** and what `runtime/sign_manifest.py` does after a reviewed edit.

**✅ Checkpoint:** you can name the **four buckets** a response falls into (failed-to-ask, asked-badly,
refused, the-answer-is-no), explain why `1065 ≠ 1060`, and point at where each invariant is enforced.

## Phase 3 — The history: don't repeat dead ends (~1–2h)
- Read **`DEV_SYSTEM_GUIDE.md §3`** (all 14 issues) and especially **§3.1 + §3.1a** (the access wall
  and the tried-and-failed list).

**✅ Checkpoint:** list **3 things NOT to try** against the access wall and why, and state the
difference between the behavioral-wall 403 (`Retry-After` absent, behavioral, no exit fixes it) and
the per-exit 403 (`Retry-After: 86400`, fixed by the global pool + rotation).

## Phase 4 — How a pull runs (~1–2h)
- Read **`RUNNING_A_PULL.md`** (the 8-stage runbook).
- **Exercise:** map each of the 8 stages to the `pipeline/*` script that implements it, and mark which
  run today vs which are gated on access / [P0].

**✅ Checkpoint:** you can walk the 8 stages in order (universe → residential filter → serviceability →
classify → remediate → reconcile → NEW-only → deliver) and name the guard that fails closed on each.

## Phase 5 — Your first contribution (the real work begins)
- Read **`ONBOARDING.md`** (the branch → signed-PR → owner-merge workflow, signing commits) and
  **`CONTROL_PLANE.md`** (how you'll run pulls in the cloud).
- Pick up **`DEV_ISSUES.md` [P0]** — make the repo run end-to-end from a clean clone. It's the highest-
  leverage first win and unblocks everything.

**✅ Checkpoint:** you've opened a **signed PR** (`git commit -S`) that makes a suite pass from a clean
clone, with a description that states what changed and the numbers/tests you ran.

---

## "You understand the system when you can…"
- Explain what makes a lead (a **priced fiber plan populates**) and name the dispositions.
- Run the 7 suites and the simulator from a clean clone.
- Point at where each invariant is enforced in code.
- List the access-wall dead ends and the one real path forward (authorized/bulk carrier access).
- Walk the 8 pull stages and the guard on each.
- Open a signed PR the owner can review and merge.

If all six are true, you're ready to take on the input-quality, efficiency, and Cloud-Run-Job tasks
that move the system toward the full-state-in-hours goal.

---

## Glossary — the jargon, decoded

**Product / data**
- **Universe** — every residential address in a state, from authoritative gov sources (never a carrier footprint).
- **Residential filter** — screening each address by FEMA **`OCC_CLS`** (occupancy class); fail-open (keep confident-residential + hold uncertain; drop confident non-residential).
- **kept / held / dropped** — the three residential-screen outcomes. Reconcile `kept + dropped + held == total`.
- **NEW-only** — leads not delivered in any prior month.
- **Rooftop pin** — an authoritative lat/lon on the correct building (≥4 decimals, one pin per lead).

**Carrier / serviceability**
- **uqual** — Kinetic's address-qualification auth (the secret in `kinetic-uqual` / `uqual.txt`).
- **serviceable** — a **priced fiber plan populates**: a named bundle (`bundle_detail`) with a real `standardPrice`/`promoPrice`. The bar for a lead.
- **techType** — the carrier's reported tech (FIBER/FTTH/FTTP vs COPPER). Establishes *technology*, **not** a lead.
- **exactMatch** — whether the carrier matched *this exact premises*. Required before any terminal answer.
- **metaBundles / bundle_detail** — the plan payload. `metaBundles` is a **dict** keyed `speedAndProductBundleList` (a classic bug source — iterating it yields keys, not plans).
- **errorCodes:** `0000` exact match · `1003` fuzzy-candidate list · `1020` existing customer (rule-out) · `1060` out-of-territory (the **only** terminal "not-found") · `1065` AddressNeedsFix (remediation, **not** a no) · `1080` pending-order (rule-out) · `1700` AddressNotFound (remediation).
- **dispositions** — the classifier's verdicts: `CURRENT_SERVICEABLE`, `CURRENT_NOT_FOUND`, and the `CURRENT_UNRESOLVED_*` family (`ENRICHMENT_UNREAD` = couldn't read plans, retryable; `NO_PLAN_POPULATED` = read it, no priced plan; `ADDRESS_NEEDS_FIX`; `UNKNOWN_RESPONSE`; `IN_TERRITORY_UNSERVICEABLE`).
- **FOUR_BUCKETS** — the exhaustive table separating *failed-to-ask / asked-badly / service-refused / answer-is-no*. Collapsing any two of these is how deliverables get silently broken.
- **`closes_address()`** — the completeness test (an answer whose meaning is *proven*), used for reconciliation instead of `status in TERMINAL`.
- **classifier_version** — stamped on every disposition. **One** versioned classifier; any parser change means bump the version + replay stored responses.

**Infrastructure / guards**
- **the fleet / `fleet_scheduler` / actors** — the async carrier-sweep engine (auth ≤2/s, search ≤20/s, exit rotation).
- **`endpoint_guard`** — process-wide circuit that opens only on sustained transport *silence* (a 403/429/500 is the endpoint talking, never trips it).
- **`egress_guard`** — fail-closed proxy egress: all carrier traffic through the configured proxy; a proxy failure aborts rather than going direct.
- **FETCH_METADATA** — the XHR `Sec-Fetch-*` headers sent on every `/api` call so the request isn't fingerprinted as a browser navigation (which the endpoint refuses).
- **exit / exit pool** — proxy exit IPs; rotate before ~20 searches, never revisit inside 24h.
- **hash gate / manifest** — the 5 frozen modules + `production_manifest.json`; prod refuses to run a changed one until it's re-signed (`sign_manifest.py`).
- **readiness canary** — the approved end-to-end run (auth 201 → search 200 → product 200 → terminal) prod requires before it will start. Can't pass while the endpoint is walled.
- **the behavioral wall** — the PerimeterX/HUMAN 403 on `address/search` needing `x-interaction-quality`/`x-quality-flag` a real browser produces. The current hard blocker.
