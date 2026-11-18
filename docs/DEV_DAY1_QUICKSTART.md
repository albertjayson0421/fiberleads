# Dev Day-1 Quickstart

One page, top to bottom. Do it in order. By the end you have a clean clone whose **7 offline
test suites are green** and you've watched one end-to-end pull run against a local mock
carrier. **You will not touch the live carrier — access is 403-walled and owner-gated (see
`STATE_OF_THE_SYSTEM.md` §3, Truth 1). Make no carrier or proxy request.**

Read `STATE_OF_THE_SYSTEM.md` and `ARCHITECTURE.md` before running anything.

---

## 0. Prereqs (before any access) — details in `ONBOARDING.md`
- [ ] NDA + IP-assignment signed.
- [ ] Hardware-key (FIDO2) 2FA on GitHub + Google.
- [ ] Added to the GitHub repo (**Write**, not admin) and the `fiberleads-dev` GCP project.
- [ ] You will **not** get production secrets. Everything below runs on placeholders.

## 1. Clone
```bash
git clone git@github.com:fxleads/fiberleads.git
cd fiberleads
git switch -c day1/<yourname>          # never work on main
```

## 2. Get the pinned runtime (the devcontainer is mandatory)
Production is **Python 3.11.2 + curl_cffi 0.16.3** (`runtime/DEPLOY_LOCK.txt`). A newer
Python/curl_cffi changes TLS/impersonation behavior and your tests won't match prod. Your
host almost certainly has a different Python (this machine has only 3.14), so use the
container:
```bash
# VS Code: "Dev Containers: Reopen in Container"  (.devcontainer/ is pinned to 3.11.2)
# or plain Docker:
docker build -t fiberleads-dev -f .devcontainer/Dockerfile .
docker run --rm -it -v "$PWD":/workspace -w /workspace fiberleads-dev bash
# verify inside the container:
python -c "import curl_cffi,sys; print(sys.version.split()[0], curl_cffi.__version__)"
# expect: 3.11.2 0.16.3
```
If you truly can't use Docker: `python3.11 -m venv .venv && source .venv/bin/activate &&
pip install -r requirements.txt` — but you must have a real Python **3.11.2** first.

## 3. Work around the known clean-clone breakages
These are real, documented defects (`STATE_OF_THE_SYSTEM.md` §2a). Fixing them properly is
the weekend's P0 PR; for Day 1 you just unblock yourself:

**(a) Secrets load at module import.** `fleet_pipeline.py:40` and `fleet_actor_v2.py:50` read
`$FL_CONFIG/uqual.txt` at *import* time; only the `.example` ships, so `import
fleet_pipeline` dies with `FileNotFoundError`. Drop in placeholders (safe — offline tests use
a local mock server and never dial out):
```bash
cp runtime/config/uqual.txt.example   runtime/config/uqual.txt
cp runtime/residential.json.example   runtime/residential.json
```
- [ ] These two files are **gitignored** — never commit them, never put a real value in them.

**(b) Pipeline scripts point at machine-local engine dirs.** 36 of 39 `pipeline/*.py`
hardcode absolute paths, and several `sys.path.insert` `/Users/masani/FLEET_5K/merge/engine`
or `/Volumes/T7/.../02_System/engine` — even though `address_source.py` and
`residential_filter.py` are now vendored in `runtime/engine/`. You do **not** need the
pipeline scripts to run the offline tests (step 4). You *will* need to repoint them for the
fixture pull (step 5) and for the P0 PR — do that on your branch, at
`runtime/engine`.

## 4. Get the 7 offline suites green
Run from the repo root, inside the container. Start with the fully self-contained one, then
the rest:
```bash
# self-contained (classifier only — no secrets, no network):
python runtime/engine/test_classifier_negatives.py
python runtime/engine/test_reclassify.py
python runtime/engine/test_failure_handling.py
python runtime/engine/test_proxy_config.py          # needs residential.json from step 3(a)

# engine-integration + sim (need uqual.txt from step 3(a) + curl_cffi from step 2):
python runtime/test_integration_offline.py
python runtime/test_readiness_entrypoints.py
python runtime/test_sim_scenarios.py                # spins a local mock carrier — end-to-end
```
- [ ] Each prints `RESULT: N passed, 0 failed` and exits 0.
- If `import fleet_pipeline` still fails → you skipped step 3(a).
- If `import curl_cffi` fails → you're not in the 3.11.2 container (step 2).

> Do not "fix" a red test by weakening a correctness assertion — those suites encode the
> invariants in `STATE_OF_THE_SYSTEM.md` §5. If one is genuinely wrong, raise it, don't mute it.

## 5. Watch one end-to-end pull (no live carrier)
`runtime/test_sim_scenarios.py` **is** the committed end-to-end fixture pull: it starts a
local mock of the carrier and drives the real `fleet_scheduler` → search → product → classify
→ durable-writer path against it. Re-run it and read its output — that is the whole flow
working with zero live access.

There is **no committed sample address file** (universes/ledgers are gitignored). To hand-run
the pipeline scripts against your own tiny fixture after step 3(b), make a few rows:
```bash
printf '%s\n' \
  '{"ck":"t1","line1":"123 Main St","city":"Ada","state":"OK","zip":"74820"}' \
  '{"ck":"t2","line1":"9 Elm Ave","city":"Ada","state":"OK","zip":"74820"}' \
  > /tmp/fixture_addrs.jsonl
```
Use it only against the offline/mock path — **never** point it at `buy.gokinetic.com`.

## 6. Done-for-Day-1
- [ ] Clean clone, in the 3.11.2 container.
- [ ] All 7 offline suites print `passed, 0 failed`.
- [ ] `test_sim_scenarios.py` end-to-end run read and understood.
- [ ] You can name the P0 breakages (import-time secret load; hardcoded/`sys.path` engine
      paths; two divergent delivery builders) and where they live.

## What NOT to do on Day 1
- No live pull, no carrier request, no proxy request — access is 403-walled and owner-gated.
- No commits to `main` (protected); no committing `uqual.txt` / `residential.json`.
- No editing a frozen module *on main* — edits belong on a branch; prod runs the frozen build
  until the owner re-signs `runtime/production_manifest.json` (`ONBOARDING.md`, "the hash gate").
