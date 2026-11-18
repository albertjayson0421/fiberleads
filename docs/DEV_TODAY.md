# Developer — Your First Day

Your single front door. This folder has a lot of reference docs — **ignore all of it except what this
guide sends you to.** Follow these steps in order and you'll be set up, understand the system, and have
your first change open by end of day.

**Before you start:** the NDA + contractor are signed, the owner has invited you to the repo (you have
**Write**), and 2FA (a hardware key) is on for your GitHub and Google accounts.

---

## Step 1 — Get it running (~1 hour)
Follow **[`DEV_DAY1_QUICKSTART.md`](DEV_DAY1_QUICKSTART.md)** exactly:
1. Clone the repo and open it in the **pinned devcontainer** (Python 3.11.2 / curl_cffi 0.16.3 — your
   machine must match production).
2. Run the **7 offline test suites** — all must print `RESULT: N passed, 0 failed`.
3. Run the **simulator**: `python runtime/test_sim_scenarios.py` — the real sweep engine against a
   local mock carrier. No network, no secrets. This is proof the machine works.

**✅ By now:** green suites + a passing simulator from a clean clone.

## Step 2 — Understand the system (~half a day)
Open **[`DEV_TRAINING.md`](DEV_TRAINING.md)** — it's the **guided path**: what to read, in order, with
hands-on exercises and a checkpoint after each, plus a glossary of every term. It walks you through:
- **[`DEV_SYSTEM_GUIDE.md`](DEV_SYSTEM_GUIDE.md)** — the master doc: the whole system, every real issue
  we've hit and how we resolved it, the hard rules that can't break, the dead-ends on the access wall
  (things NOT to retry), and an honest read on the full-state-in-2–4h goal.
- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — the engine internals.
- **[`RUNNING_A_PULL.md`](RUNNING_A_PULL.md)** — how a pull runs, all 8 stages, each with its guard.

Don't skim these — do the DEV_TRAINING exercises. They're how you actually learn it.

**✅ By now:** you can say what makes an address serviceable (a *priced fiber plan populates*), name the
four response buckets, and list what NOT to try against the access wall and why.

## Step 3 — Your first change (the real work)
Read **[`ONBOARDING.md`](ONBOARDING.md)** (the branch → signed-PR → owner-merge workflow) and
**[`CONTROL_PLANE.md`](CONTROL_PLANE.md)** (how you'll run pulls once it's built). Then pick up
**[`DEV_ISSUES.md`](DEV_ISSUES.md) [P0]** — make the repo run end-to-end from a clean clone. It's the
highest-leverage first win and unblocks everything after it.

**✅ Done today:** a **signed PR** (`git commit -S`) that makes a suite pass from a clean clone, with a
description saying what changed and the numbers/tests you ran. The owner reviews and merges it.

---

## Four hard truths — don't fight these, they'll waste your day
1. **Serviceability is behind an authorized-access wall right now.** A live serviceable-lead pull
   returns nothing until the owner secures written Kinetic access — a **policy gate, not a code bug**,
   with **no** scraping/token/workaround path (see `DEV_SYSTEM_GUIDE §3.1a` for everything already
   tried). Universe/address builds need no carrier and *do* run today.
2. **Serviceable = a priced fiber plan populates.** Not `techType=FIBER` alone, not "call to order,"
   not a pending order.
3. **Residential filter FIRST, serviceability SECOND. Always.** Never sweep an unfiltered list.
4. **More boxes/threads against a blocked or slow endpoint makes it worse**, and can get the machine
   firewalled. Route every network retry through the existing guards; never hand-roll a loop.

## How your work reaches production
Branch → commit **signed** → open a **pull request** → the owner reviews the diff + checks, merges it,
and (only if you changed a frozen `runtime/` module) re-signs the manifest. You never push to `main` or
deploy to prod yourself. Keep PRs small and say what you tested. That's the whole loop.

*Everything else in this folder is reference — DEV_TRAINING and DEV_SYSTEM_GUIDE point you to the right
piece when you need it. Start with Step 1.*
