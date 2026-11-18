# Kinetic supported-source activation package

Status: `PUBLIC_CONSUMER_AUTOMATION = BLOCKED_BROWSER_BOUND` · `SYSTEM_READY_FOR_SUPPORTED_SOURCE = PASS`

The public consumer checker is browser-behavioral-bound (proof in `technical_evidence.md`).
This package activates the moment Kinetic grants a supported source. Everything below is built
and verified; only Kinetic's chosen mode + its endpoint/credentials are missing.

## Why (one line)
`buy.gokinetic.com/api/v1/address/search` requires browser-generated PerimeterX/HUMAN behavioral
proof headers; a correct server-side client gets 403. We do not forge/replay them.

## Remaining work (concrete, staged, resumable)
- **Texas:** 400,054 addresses remaining of 609,580 residential-eligible in the 201 Kinetic-FTTH
  ZIPs (209,526 already completed with evidence). Queue: `../tx_remaining.jsonl`.
- **Iowa cities:** 15,779 residential-filtered addresses (Centerville 52544 = 3,518). Queue:
  `../cities_sweep_queue.jsonl`.
- **Staged total now:** 415,833 addresses.
- **Monthly statewide TX workload (the access ask):** ~12,000,000 residential service locations.

## Supported-source modes (all built + verified)
| mode | file | activation |
|---|---|---|
| A. carrier CSV/NDJSON batch | `ingest/csv_ndjson_importer.py` | `python3 -m ingest.csv_ndjson_importer <results.csv> <out.ndjson> --submitted <keys.txt>` |
| B. async submit/poll API | `ingest/async_batch_adapter.py` | put endpoints in `supported_endpoints.json`, then `python3 -m ingest.async_batch_adapter <addr.csv> <out.ndjson> --config supported_endpoints.json` |
| C. allowlisted live client | `ingest/live_client_config.py` | fill `live_client_config.json` (`--template` writes one); `python3 -m ingest.live_client_config live_client_config.json` must return AUTHORIZED before any live run |
| D. snapshot + delta | `ingest/snapshot_delta_importer.py` | `... snapshot <snap.ndjson> <store.ndjson>` then `... delta <delta.ndjson> <store.ndjson>` |

Common to all modes:
- `ingest/field_mapping.py` — strict carrier→canonical mapping (edit `DEFAULT_MAP` to match Kinetic's field names).
- `ingest/schema.py` — canonical record + disposition classifier (honors the carrier invariants).
- `ingest/conservation.py` — reconciliation: `python3 -m ingest.conservation <out.ndjson> --universe <keys.txt>` (unaccounted must be 0).
- `ingest/throughput_gate.py` — local capacity gate: `python3 -m ingest.throughput_gate` (must PASS > 2,000/s).

## Verified (2026-09-09)
- Throughput gate: **35,568/s** (target 2,000/s) → PASS.
- End-to-end import→classify→reconcile on 1,000 synthetic carrier rows: 0 unaccounted, identity holds, dispositions correct (serviceable / existing / call-to-order / not-serviceable / needs-fix / no-plan).
- Live-client guard refuses to run without a confirmed allowlist.

## Package contents
- `access_request_email.md` — ready-to-send request (2,000/s, ~12M TX, priority A–E, required fields, 7 CF-Rays).
- `technical_evidence.md` — the browser-bound proof to attach.
- `samples/kinetic_acceptance_sample_10000.csv` (+ `.manifest.json`) — 10,000-address acceptance sample (do NOT submit without authorization).
- `samples/centerville_priority_manual_100.csv` — small manual-fallback priority queue.
- `docs/result_schema.json`, `docs/field_definitions.md` — result schema + field definitions.
- `manual_fallback_instructions.md` — human spot-check for a small priority queue only.
- `state/checker_status.json` — the disabled-automation flags.
- `state/state_preservation_manifest.json` — inventory (counts + hashes) of all preserved state.

## What NOT to do
No forging/replaying/harvesting the behavioral proof; no fingerprint changes; no CAPTCHA/challenge
automation; no identity rotation after a restriction. Activation is a supported source only.
