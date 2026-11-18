# FiberLeads — Architecture

## Big picture
A pull takes a market's **residential address universe**, checks each exact address
against Kinetic's anonymous serviceability endpoints, and emits **only** addresses
where a **priced fiber plan populates** — as NEW-only, rooftop-pinned Sales Rabbit leads.

```
universe build → residential filter → dispatch to fleet → carrier qualify
   → remediation (re-sweep unresolved) → reconciliation (conservation) → deliverable
```

## Two generations of the runner
- **Legacy** (`deploy/node_run.py` + `carrier_uqual.UqualClient`): thread-per-worker, search-only. Reaches `techType` but **never fires the product/pricing call** — emits techType candidates, not priced leads. Kept for reference/rollback.
- **New async engine** (`runtime/`, the frozen build — this is production): `fleet_scheduler.py` → `fleet_pipeline.py` (search actors → bounded product queue → durable writer) → `product_stage.py` (the product/pricing call, existing-customer exclusion) → `engine/classifier.py` (single classification authority).

## Runtime flow (new engine)
```
fleet_scheduler.py  <addrs.jsonl> <ledger.jsonl>  [--actors N --search-rate R --product-workers M --canary c.json]
  1. gate_hashes()            -> SHA-256 the 5 frozen modules vs production_manifest.json; abort on mismatch
  2. readiness_gate_check()   -> requires an approved end-to-end canary (auth 201 → search 200 → PRODUCT 200 → terminal)
  3. load config/billing_config.json + residential.json (proxy pools), resume from ledger
  4. run: feeder → search actors → product workers → durable append-only writer
```
- **Auth:** `POST /api/v1/auth/session` (201 + bearer). **Search:** `POST /api/v1/address/search` (200). **Product/pricing:** `POST /api/v2/windstream/product-availability` (200 + plans).
- HTTP via `curl_cffi` (`impersonate="chrome124"`). All carrier egress goes through the DataImpulse proxy — enforced fail-closed by `engine/egress_guard.py` (never the machine's own IP).
- `fleet_pipeline.py` / `fleet_actor_v2.py` are independently runnable for single-node testing.

## The five FROZEN / hash-gated modules
`carrier_uqual`, `fleet_actor_v2`, `product_stage`, `billing_mapping`, `fleet_pipeline`
(hashes in `runtime/production_manifest.json`). `fleet_scheduler.gate_hashes()` refuses
startup if any file's hash differs — this is what stops unreviewed changes running in prod.
**Editing one on a branch is expected;** it just means the frozen build won't run your edit
until the change is reviewed, merged, and the manifest is re-signed (see ONBOARDING).

## Carrier evidence invariants (correctness — do not break)
- **Serviceable ⇔ a named, priced fiber-tier bundle populates.** techType=FIBER alone, "call to order", and errorCode 1080 pending-order are **not** leads.
- Product/enrichment failure or "never fetched" → **UNRESOLVED** (retryable). It may never become NONFIBER or NOT_FOUND.
- Enrichment read fine but no priced fiber plan → `UNRESOLVED_NO_PLAN_POPULATED` — a real answer, not a lead.
- **errorCode 1065 (AddressNeedsFix) → UNRESOLVED, retryable — never NOT_FOUND.**
- Only verified **errorCode 1060** (out of territory) → terminal `NOT_FOUND`.
- Unknown responses **fail closed** to UNRESOLVED. HTTP success ≠ classification success (validate the body).
- Raw evidence is immutable; dispositions are rebuilt from it; one versioned classifier per lane.
- **Circuit breaker** (`engine/endpoint_guard.py`): only transport SILENCE trips it, process-wide per endpoint. A 403/429/500 is the endpoint *talking* — rotate the exit, don't stop the pull.

## Pipeline (`pipeline/`, per-market)
`iowa_universe.py` / `iowa_filter.py` (build + residential-filter the universe → kept/held/dropped)
→ `iowa_dispatch.py` (shard to fleet) → `remediate*.py` (re-sweep non-exact / blank-city / recovered)
→ `reconciliation/` + `verify_deliverable.py` (conservation: every address → exactly one bucket, unaccounted = 0)
→ `build_delivery.py` (SERVICEABLE_FIBER only → Sales Rabbit CSV, real city via zippopotam, NEW-only anti-join, rooftop coords).

## External dependencies
- **Kinetic** `buy.gokinetic.com` (auth/search/product). Cloudflare + behavioral-header gated.
- **Proxy:** DataImpulse residential pools (+ iproyal), sticky routes. Quota tracked in `residential.json`.
- **FEMA USA Structures** (ArcGIS) — building footprints for residential + rooftop-pin verification.
- **TIGERweb / zippopotam** — authoritative ZIP→city + address points for blank-city recovery.
- **ipinfo.io / ip-api** — verify proxy exit is US before any Kinetic auth.

## GCP fleet
12+ VMs `fl-*` (`us-central1-a`, debian-12), provisioned from **Cloud Shell**
(`deploy/CLOUD_SHELL_recreate_e2highcpu2.sh`, `deploy/ADD_VMS_cloudshell.sh`),
deployed from the control host via `deploy/deploy_fleet.sh` (SSH key from Secret Manager),
results merged with `deploy/collect.sh` (idempotent, dedup on canonical key).
