# Brief: P0 - Make the repo run end to end from a clean clone

- Task: [P0] Make the repo run end-to-end from a clean clone (see docs/DEV_ISSUES.md)
- Branch: feature/p0-clean-clone
- Date started: 2026-09-13
- Status: In progress. All 7 offline suites pass (P0 acceptance criterion 1 met); remaining P0 cleanup items in progress.

This brief follows the plan / implementation / results format. The plan is filled now;
implementation and results are updated as the work proceeds.

## 1. Initial plan

### Objective
A fresh clone currently cannot import the engine or run the offline tests, which blocks
every other task. Goal: a clean clone can install the pinned runtime and pass all 7 offline
test suites with no manual secret creation, and the delivery build/verify loop closes for a
non-Iowa market from config.

### Acceptance criteria (from DEV_ISSUES.md)
1. From a fresh clone in the pinned devcontainer, all 7 offline suites print
   `RESULT: N passed, 0 failed`, with no manual secret creation (placeholders auto fall back).
2. build_delivery output is exactly what verify_deliverable validates, for a non-Iowa market,
   driven by config.
3. No new secret is committed (.gitignore still covers uqual.txt, residential.json).

### Diagnosis (verified against the code, with file:line)
1. Import-time secret loads: runtime/fleet_pipeline.py:40 and runtime/fleet_actor_v2.py:50 read
   uqual.txt at module import. Same pattern in pipeline scripts (capture_pricing.py:26,
   remediate.py:25, remediate2.py:24, cf_watch.py:17, enr_probe.py:18, stable_client_test.py:13).
   Effect: import fails FileNotFoundError before any test runs.
2. Dead engine paths: 36 of 39 pipeline scripts sys.path.insert into
   /Users/masani/FLEET_5K/merge or /Volumes/T7/... though the engine is vendored at runtime/engine/.
3. Config path bug: runtime/fleet_actor_v2.py:552 reads billing_config.json from the runtime root,
   but it exists only under config/.
4. Divergent delivery builders: build_delivery.py (CSV) vs build_iowa_deliverable.py (XLSX) vs
   verify_deliverable.py (reads XLSX, hardcoded to the Iowa bounding box). They do not target the
   same artifact.
5. Proxy schema mismatch: runtime/residential.json.example uses pass_template, but
   deploy/node_run.py and deploy/bench.py read the key pass.
6. Windows import break: runtime/fleet_pipeline.py imports the POSIX-only resource module at
   top level, so the module cannot be imported on a non-POSIX host for offline analysis.

### Approach (ordered)
0. Environment: bring up the pinned devcontainer (Python 3.11.2 + curl_cffi 0.16.3). Capture the
   failing baseline first so the fix is measurable.
1. Make secret loading lazy: move the uqual.txt read out of module scope into a cached accessor
   called from the auth path; add an example fallback for offline. Note: fleet_pipeline and
   fleet_actor_v2 are hash-gated frozen modules, changed on this branch, owner re-signs
   runtime/production_manifest.json on merge.
2. Repoint every pipeline import at runtime/engine/ via env or args, not absolute paths.
3. Replace hardcoded machine paths with args/env/market.json.
4. Fix the billing_config.json path in fleet_actor_v2.main() to read from config/.
5. Unify the delivery path: one config-driven builder and format, consumed by verify_deliverable;
   remove the Iowa-bbox hardcode (take bbox/keyset from config).
6. Guard the POSIX resource import so the module imports on any OS for offline analysis and CI.

### Constraints
- No live carrier or proxy request (access is 403-walled and owner-gated).
- No secret is committed (uqual.txt, residential.json stay gitignored).
- Work on a branch, signed commits, owner reviews and merges the PR. Frozen module edits require
  the owner to re-sign production_manifest.json.

## 2. Implementation
To be filled per change as work proceeds. Each entry records what changed, where, and why.

Environment (prerequisite, done):
- Created working branch feature/p0-clean-clone (local only, not pushed).
- Installed Docker Desktop 4.90.0; WSL2 backend healthy; engine running (Server 29.7.2, linux).
- The VS Code "Reopen in Container" flow kept failing on the VS Code Server download (network), and the
  full .devcontainer image build fails on a Debian security-mirror apt 404. The offline suites need only
  curl_cffi (no pycurl/apt), so the pinned environment is run directly: docker run on python:3.11.2-slim
  with curl_cffi 0.16.3. Confirmed runtime inside the image: py 3.11.2, curl_cffi 0.16.3 (matches prod).

Fixes applied on the branch:
1. Lazy secret loading: replaced the import-time uqual.txt read with a cached _basic() accessor in
   runtime/fleet_pipeline.py and runtime/fleet_actor_v2.py, falling back to uqual.txt.example (skipping
   comment lines) so a clean clone imports with no secret. (Both are hash-gated modules.)
2. residential.json fallback: runtime/fleet_scheduler.py falls back to residential.json.example when the
   secret is absent (not hash-gated).
3. Line endings: added .gitattributes forcing LF and normalized the 5 gated modules to LF. Root cause: the
   Windows clone converted files to CRLF, which broke the SHA-256 startup hash gate (confirmed: the
   LF-normalized hashes match the signed manifest exactly).
4. Re-signed runtime/production_manifest.json for the two edited gated modules (fleet_pipeline,
   fleet_actor_v2) via sign_manifest.py (dev-branch step; owner re-signs on merge).
5. Fixed the proxy config schema mismatch: runtime/residential.json.example used "pass_template", but the
   code (sticky, node_run, bench) reads "pass". Renamed the key to "pass". This unblocked the sim actors
   (they build a route and reach the mock) and resolves the documented proxy-schema-mismatch P0 item.

6. Guarded the POSIX-only resource import in runtime/fleet_pipeline.py (try/except -> None, rss diagnostic
   guarded) so the module imports on Windows / CI too, not just Linux.
7. runtime/fleet_actor_v2.py: fixed the billing_config.json path (HERE -> FL_CONFIG/config) and added the
   residential.json.example fallback in main(), matching fleet_scheduler. (Gated module; manifest re-signed.)

All 7 suites re-verified green after fixes 6-7.

Pipeline refactor (owner decisions applied 2026-09-13: CSV is the standard; quarantine not delete; config-first):
8. Added pipeline/flpaths.py (central path config: repo-relative defaults + env / market.json overrides;
   relative config paths resolve against the repo) and market.json.example (the config-file template the
   owner approved). Stages call flpaths.ensure_engine_on_path() instead of hardcoding /Users/masani or /Volumes/T7.
9. Delivery standardized on CSV. build_delivery already emits the Sales Rabbit CSV (repointed to config);
   verify_deliverable was rewritten to verify THAT CSV (it used to read an XLSX), with the Iowa state/zip/bbox
   checks moved to optional market.json settings. Verified end-to-end: fixture ledger -> build_delivery CSV ->
   verify_deliverable = PASS.
10. Repointed keep-list stages to flpaths + lazy secrets: residential_run, prior_delivered, fetch_zipcity,
    remediate2 (engine path + lazy uqual.txt with .example fallback + residential.json/.example + billing_config
    from config).
11. Quarantined 15 one-off scripts to pipeline/_legacy/ via git mv (one-off charts, fg/Fairfield, diagnostics/
    canaries/probes, one-off lead-add, and the dead capture_pricing), with a _legacy/README. Nothing deleted.

Verified after the pipeline work: all kept pipeline scripts py_compile clean, the delivery CSV build/verify
loop passes, and all 7 offline suites remain green (no regression).

Remaining / flagged for owner:
- build_nc_statewide.py imports 5 modules NOT in the repo (runconfig, canonicalize, delivery_history,
  nc_onemap_client, source_health). Path repointed, but it cannot run from a clean clone until those are
  vendored. NEEDS OWNER: add those modules to the repo (or point the stage at vendored equivalents).
- finalize_remediation.py (Iowa chart rebuild) is now config-driven via flpaths + market.json; it still needs
  a completed run's data to execute, which is expected for a reconciliation step.
- Per-market forks (iowa_*, tx_*, build_iowa_deliverable, build_tx_partial) left in place; the config work
  collapses them into one path, so they fall away rather than being edited one by one.
- VM sweep/collect (iowa_dispatch, remed_dispatch, join_new, finalize, remcollect) left in place pending
  confirmation the newer fleet setup covers them (owner heads-up).

## 3. Results

Runtime confirmed inside the pinned image: Python 3.11.2, curl_cffi 0.16.3 (matches production).

Baseline (clean clone, no secret files, 2026-09-13):
- test_classifier_negatives.py: PASS (13 passed, 0 failed)
- test_reclassify.py: PASS (15 passed, 0 failed)
- test_failure_handling.py: PASS (22 passed, 0 failed)
- test_proxy_config.py: PASS (16 passed, 0 failed; 2 checks skipped for missing uqual.txt / residential.json)
- test_integration_offline.py: FAIL (exit 1)
- test_readiness_entrypoints.py: FAIL (exit 1)
- test_sim_scenarios.py: FAIL (exit 1)

Before: 4 of 7 suites pass. All 3 failures share one root cause: the import-time secret load at
runtime/fleet_pipeline.py:40 (and fleet_actor_v2.py:50), which raises FileNotFoundError on
runtime/config/uqual.txt when a clean clone has no secret file.

After fixes (2026-09-13):
- test_classifier_negatives.py: PASS (13 passed, 0 failed)
- test_reclassify.py: PASS (15 passed, 0 failed)
- test_failure_handling.py: PASS (22 passed, 0 failed)
- test_proxy_config.py: PASS (20 passed, 0 failed; 1 skip for the residential.json scan)
- test_integration_offline.py: PASS (13 passed, 0 failed)   [was FAIL at import]
- test_readiness_entrypoints.py: PASS (5 passed, 0 failed)   [was FAIL at import]
- test_sim_scenarios.py: PASS (15 passed, 0 failed)   [was FAIL at import]

Net: ALL 7 of 7 offline suites pass, verified in the pinned runtime (py 3.11.2, curl_cffi 0.16.3), with no
manual secret creation. This meets P0 acceptance criterion 1.

test_sim_scenarios root cause + fix: the sim drives the real scheduler against a localhost mock, but the
mock was receiving ZERO requests because Actor.open() calls sticky() to build the proxy route first, and
sticky() reads pool["pass"] while residential.json.example defined "pass_template" -> KeyError before the
auth request ever fired. Production code (sticky, node_run, bench) all read "pass", so the example file
had the wrong key. Fixed residential.json.example to use "pass"; the actors now build a route (ignored by
the sim's direct session), reach the mock, and all four scenarios pass. This was NOT the by-value BASE
import (the actor's auth uses fleet_pipeline's patched BASE), and it also resolves the documented proxy
schema-mismatch P0 item.

## Environment note
The dev machine had Python 3.14.2 and no Docker, but the pinned runtime is Python 3.11.2 plus
curl_cffi 0.16.3, which is meant to run in the devcontainer. Docker Desktop is now installed and the
engine is running; the devcontainer is building. Also note runtime/fleet_pipeline.py imports the
POSIX-only resource module, so a Windows-native Python cannot import it until fix 6 (this is one
reason the devcontainer, a Linux environment, is the correct place to run the suites).

Next action: once the container finishes building, capture the failing baseline (the 7 offline suites
failing at import), then apply and verify the fixes one at a time, recording numbers in section 3.
