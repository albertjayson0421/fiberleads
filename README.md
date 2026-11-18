# FiberLeads

Nationwide monthly lead engine: finds every residential address in an approved
market and determines whether each exact address is serviceable for a qualifying
Kinetic (Windstream) **fiber** plan, then ships NEW-only, rooftop-pinned leads.

> **🧑‍💻 DEVELOPER — start here: [`docs/DEV_TODAY.md`](docs/DEV_TODAY.md).** It's your first-day
> guide (get set up → understand the system → open your first PR) and tells you which of the other
> docs to open, when. Everything in `docs/` is *your* material — the guided training path
> ([`DEV_TRAINING.md`](docs/DEV_TRAINING.md)), the full system + issue history
> ([`DEV_SYSTEM_GUIDE.md`](docs/DEV_SYSTEM_GUIDE.md)), the runbook, architecture, and your tasks.
> Access is least-privilege and audited; some actions are owner-only by design.
>
> **👤 OWNER — your materials are separate, in your `fiberleads-setup/` folder (not here):**
> `OWNER_TODAY.md` (your day, step by step), `OWNER_WHATS_LEFT.md` (full checklist),
> `OWNER_GITHUB_GUIDE.md` (reviewing PRs), `OWNER_OPERATING_GUIDE.md` (running it monthly), plus your
> visual Owner's Guide. Current data status is in [`docs/UNIVERSE_STATUS_CURRENT.md`](docs/UNIVERSE_STATUS_CURRENT.md).

## Repo map
| Path | What it is |
|---|---|
| `runtime/` | The **frozen production build** (async fleet engine). Flat layout is intentional — the startup **hash gate** verifies these module files. Don't restructure it. |
| `runtime/engine/` | Shared library: the single carrier-response `classifier`, `carrier_uqual`, `billing_mapping`, guards (`endpoint_guard`, `egress_guard`, `fetch_guard`), `durable_store`, `failure_handling`. |
| `pipeline/` | Per-market pull scripts: universe build → residential filter → dispatch → remediation → reconciliation → deliverable. |
| `deploy/` | Fleet provisioning (GCP Cloud Shell scripts) + deploy/collect tooling. |
| `docs/` | **All developer docs.** Start: `DEV_TODAY.md` → `DEV_TRAINING.md` (guided path + glossary) → `DEV_SYSTEM_GUIDE.md` (system + issue history + goal) → `RUNNING_A_PULL.md`, then `DEV_DAY1_QUICKSTART.md`, `DEV_ISSUES.md`, `ONBOARDING.md`, `ARCHITECTURE.md`, `CONTROL_PLANE.md`, `STATE_OF_THE_SYSTEM.md`, `WEEKEND_PLAN.md`, `UNIVERSE_STATUS_CURRENT.md`. *(Owner materials live separately in `fiberleads-setup/`.)* |

## Secrets & data are NOT in this repo — by design
- **Secrets** (`residential.json` proxy creds, `config/uqual.txt` Kinetic auth, `fleet_key`) live in **Google Secret Manager** and are injected at runtime. Templates: `runtime/residential.json.example`, `deploy/boxes.txt.example`.
- **Data** (`node_seed.db`, `*.jsonl` universes/ledgers, client CSVs) is never committed — see `.gitignore`.

## Runtime is pinned
Python **3.11.2** + **curl_cffi 0.16.3** (see `runtime/DEPLOY_LOCK.txt`, `requirements.txt`).
Your machine must match this or behavior will differ from production.

## The non-negotiable rules (full list in `docs/ONBOARDING.md`)
- **Serviceable = a priced fiber plan actually populates.** techType=FIBER alone is not a lead; "call to order" / pending order is not a lead.
- A failed / blocked / ambiguous request is **never** "not serviceable" — it stays UNRESOLVED and retryable. Only verified carrier out-of-territory (errorCode 1060) is terminal NOT_FOUND.
- **Residential filter first, serviceability second.** Always.
- Every lead carries an **authoritative rooftop** coordinate; NEW-only vs all prior deliveries; real city names.
- The five **hash-gated** modules are frozen — changing one is a reviewed, owner-signed event (see ONBOARDING).
# fiberleads
