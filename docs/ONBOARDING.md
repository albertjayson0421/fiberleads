# Developer Onboarding

Start with [`DEV_TODAY.md`](DEV_TODAY.md), then [`DEV_SYSTEM_GUIDE.md`](DEV_SYSTEM_GUIDE.md) and
[`ARCHITECTURE.md`](ARCHITECTURE.md). This system runs a paying client's lead pipeline against a live
carrier — work carefully and never break the correctness invariants below.

## Before you get access
1. **NDA + IP-assignment agreement signed.** Non-negotiable.
2. **Hardware security key (FIDO2) 2FA** enabled on your GitHub and Google accounts.
3. You'll be added to the GitHub repo (**Write**, not admin) and the **`fiberleads-dev`** GCP project.

## Your workspace
You build and test in the **`fiberleads-dev`** project with **dev credentials** (a throwaway proxy
sub-account + the offline fixtures in the repo), and you trigger production runs through the job runner
(`CONTROL_PLANE.md`). Your code reaches production **only through a pull request the owner reviews and
merges** — you never push to `main` or deploy to prod yourself. You propose; the owner ships. Anything
beyond your standing access (an ad-hoc production run, elevated access) is arranged with the owner.

## Environment (must match production)
Python **3.11.2**, **curl_cffi 0.16.3** (`runtime/DEPLOY_LOCK.txt`). Do **not** use a newer
Python/curl_cffi — TLS/impersonation behavior differs and prod will not match your tests.
```
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # curl_cffi==0.16.3 pinned
```
(A devcontainer pinned to this is provided — see `DEV_DAY1_QUICKSTART.md`.)

## How you work (the flow)
```
git switch -c feature/<thing>      # branch — never commit to main
# ... change code ...
python3 runtime/test_integration_offline.py
python3 runtime/test_sim_scenarios.py
git commit -S ...                  # signed commits required
# open a Pull Request  ->  owner reviews  ->  owner merges
```

## The hash gate (why prod won't run your edit yet)
`fleet_scheduler.py` SHA-256s the five frozen modules against `runtime/production_manifest.json` at
startup and **refuses to run** if any differ. So when you edit a frozen module (`carrier_uqual`,
`fleet_actor_v2`, `product_stage`, `billing_mapping`, `fleet_pipeline`):
- On your **dev branch/box** you run with the gate satisfied by a **dev manifest** (regenerate hashes locally).
- Your change reaches prod only after review + merge, when the manifest is re-signed. This is the
  freeze that keeps unreviewed carrier logic out of production.

## Running a pull
See **[`RUNNING_A_PULL.md`](RUNNING_A_PULL.md)** for the accurate stage-by-stage sequence and what runs
today vs. what's gated. (Some older per-market recipes assume live access that isn't available yet —
`RUNNING_A_PULL.md` is the current one.)

## Correctness invariants you must never break
- **Serviceable ⇔ a named, priced fiber-tier bundle populates.** Not techType alone, not "call to order", not a pending order (1080).
- A failed/blocked/ambiguous/never-asked request is **UNRESOLVED**, never "not serviceable."
- **errorCode 1065 → UNRESOLVED (retryable), never NOT_FOUND.** Only verified **1060** → NOT_FOUND. Unknown → fail closed to UNRESOLVED.
- **Residential filter before serviceability. Always.**
- Every shipped lead: authoritative **rooftop** coordinate, **NEW-only** vs all prior deliveries, a **real city** name.
- Never narrate a carrier "cap/limit/throttle" as a cause — dump the real HTTP response and diagnose it.
- Circuit breaker trips only on transport **silence**, process-wide per endpoint. A 403/429/500 means rotate the exit, not stop.
- Raw evidence is immutable; rebuild dispositions from it; one versioned classifier per lane.
- Any parser/classifier change must **replay stored responses** before prod resumes.

## If you're blocked
Open an issue or ping the owner. Don't work around a guard or a gate — they're intentional. Requests
for elevated access go through the owner.
