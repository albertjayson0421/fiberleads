# FiberLeads

Nationwide monthly lead engine: finds residential addresses in an
approved market, determines whether each exact address is serviceable
for a qualifying Kinetic (Windstream) **fiber** plan, and delivers
NEW-only, rooftop-pinned leads.

> **Developer starting point:**
> [`docs/DEV_TODAY.md`](docs/DEV_TODAY.md). Use the developer guide for
> setup, system orientation, and the first contribution. It links to the
> guided training path, system guide, runbook, architecture, and issue
> history.
>
> **Owner materials:** Owner operating and review materials are
> maintained separately in the `fiberleads-setup/` folder. Current data
> status is documented in
> [`docs/UNIVERSE_STATUS_CURRENT.md`](docs/UNIVERSE_STATUS_CURRENT.md).

## Project structure

  -----------------------------------------------------------------------
  Path                                Purpose
  ----------------------------------- -----------------------------------
  `runtime/`                          Frozen production build and
                                      asynchronous fleet engine. Its flat
                                      layout is intentional because the
                                      startup hash gate verifies
                                      protected module files.

  `runtime/engine/`                   Shared components, including
                                      carrier-response classification,
                                      qualification and billing mappings,
                                      endpoint/egress/fetch guards,
                                      durable storage, and failure
                                      handling.

  `pipeline/`                         Per-market workflow: universe
                                      build, residential filtering,
                                      dispatch, remediation,
                                      reconciliation, and deliverable
                                      generation.

  `deploy/`                           Fleet provisioning scripts and
                                      deployment/collection tooling.

  `docs/`                             Developer documentation,
                                      onboarding, runbooks, architecture,
                                      control-plane details, and current
                                      system status.
  -----------------------------------------------------------------------

For the complete documentation path, start with `docs/DEV_TODAY.md`,
then follow the links to `DEV_TRAINING.md`, `DEV_SYSTEM_GUIDE.md`,
`RUNNING_A_PULL.md`, and the relevant operational and architecture
documents.

## Secrets and data

Secrets and operational data are intentionally kept out of the
repository.

-   **Secrets** such as proxy credentials, Kinetic authentication, and
    fleet keys are stored in Google Secret Manager and injected at
    runtime. Use the checked-in example templates for configuration
    shape; never commit real credentials.
-   **Operational data** such as seed databases, JSONL universes and
    ledgers, and client CSV files must remain outside version control.
    See `.gitignore`.
-   Never copy credentials or customer data into test fixtures, logs,
    documentation, or commits.

## Runtime requirements

The production runtime is pinned to:

-   Python **3.11.2**
-   `curl_cffi` **0.16.3**

See `runtime/DEPLOY_LOCK.txt` and `requirements.txt` for the project's
dependency specifications. Use the pinned environment when validating
behavior intended to match production.

## Core operating rules

The full rules are documented in
[`docs/ONBOARDING.md`](docs/ONBOARDING.md).

-   A lead is serviceable only when a qualifying, priced fiber plan is
    actually returned. `techType=FIBER` by itself is not sufficient;
    "call to order" and pending-order responses are not leads.
-   Failed, blocked, or ambiguous requests must remain **UNRESOLVED**
    and retryable. They must not be treated as confirmed non-serviceable
    results. Only verified carrier out-of-territory response
    `errorCode 1060` is terminal `NOT_FOUND`.
-   Apply residential filtering before serviceability checks.
-   Each delivered lead must carry an authoritative rooftop coordinate
    and follow the NEW-only delivery rule against prior deliveries.
-   Preserve accurate city names and the existing accounting and
    reconciliation requirements.
-   The five hash-gated production modules are protected. Changes
    require project-owner review and explicit approval; do not bypass or
    weaken the startup hash gate.

## Engineering work update --- November 21, 2026

The following offline validation and test-maintenance work was
completed:

  Offline regression suite                                       Result
  ------------------------------------------- -------------------------
  `runtime/test_integration_offline.py`                       13 passed
  `runtime/test_actor_v2_accounting.py`                       13 passed
  `runtime/test_http_guard_behavior.py`                       11 passed
  `runtime/engine/test_endpoint_guard.py`                     15 passed
  `runtime/engine/test_failure_handling.py`                   22 passed
  **Total**                                     **74 passed, 0 failed**

Python compilation was also checked for six related test files:

``` cmd
.venv\Scripts\python.exe -m py_compile runtime\test_integration_offline.py runtime\test_actor_v2_accounting.py runtime\test_http_guard.py runtime\test_http_guard_behavior.py runtime\engine\test_endpoint_guard.py runtime\engine\test_failure_handling.py
```

Test-only maintenance included clearer assertion wording and cleanup of
named endpoint-guard test state. The offline suites exercise the listed
test paths; they do not, by themselves, establish that every production
execution path uses each guard or failure-handling component.

## Proposed review items for the project owner

The following are **recommendations for review**, not approved
production changes:

1.  **Accounting completeness:** consider stricter validation for jobs
    that remain unaccounted for.
2.  **Duplicate and overlapping states:** consider detecting duplicate
    or mutually inconsistent accounting states.
3.  **Reconciliation enforcement:** consider making reconciliation
    mismatches fail closed rather than relying only on reported metrics.
4.  **Cold-reopen validation:** consider validating exact ledger key
    sets and record types when reopening persisted accounting data.

These items should be investigated and implemented only after the
project owner reviews and explicitly approves the proposed production
changes.

## Production change and approval boundary

-   Offline regression tests may be run in an isolated, non-production
    environment.
-   Do not use real carrier, proxy, or production services for offline
    tests.
-   Do not modify, sign, or regenerate
    `runtime/production_manifest.json` as part of unapproved work.
-   Do not bypass, weaken, or work around the startup hash gate.
-   Any change to a hash-gated production module requires explicit
    project-owner review and approval before the manifest is regenerated
    or anything is deployed.
-   Test results should be reported separately from production
    validation or deployment status.

**Status of the November 20, 2026 work:** the five listed offline
regression suites passed (74/74), and the six-file Python compilation
check completed without errors. No production deployment was performed
and the production manifest was not regenerated. The proposed accounting
and ledger improvements remain pending project-owner review.
