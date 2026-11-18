# Developer Weekend Plan — advance THE PULL

> ⚠️ **Universe status updated 2026-09-12** — see [`UNIVERSE_STATUS_CURRENT.md`](UNIVERSE_STATUS_CURRENT.md).
> The statewide universes are now built/corrected (TX 254/254, NC/KY/IA complete, GA
> addresses complete, SC 33/46 authoritative). Any task below that assumes universes are
> footprint/partial and need a full rebuild is superseded — the remaining data work is GA
> county backfill + the last SC counties. The pull-advancement and clean-clone tasks stand.

**Goal (owner priority):** move toward pulling *every residential address in each target
state to a definitive carrier answer at full deliverable standard — the end state being a full
state resolved unattended in the low single-digit hours (target ~2–4h), delivered NEW-only with
rooftop coordinates*. **Not** autonomy
(deferred). **Not** a live pull (blocked — see below). ~15–24 hours over Fri PM + Sat + Sun.

Read `STATE_OF_THE_SYSTEM.md` first — especially **§0 (the universe-scope rule)** and **§2d
(only NC is truly statewide)**. This plan is downstream of both.

**The one constraint that shapes the whole weekend:** the live carrier pull is parked behind
a Kinetic/Cloudflare **403 behavioral wall** and is **owner-gated** — you **cannot** run a
real production pull, and you must **not** make any carrier or proxy request. That's fine:
the highest-leverage *pull* work is **offline / dev-sandbox** and it directly increases how
many addresses will reach a definitive answer once access is restored:
1. make the pipeline actually run end-to-end from a clean clone,
2. fix the input-quality problem that leaves **~45% of a run stuck at `AddressNeedsFix`**, and
3. get more definitive answers per scarce exit (fewer wasted calls) + honest progress metering.

Do those and you've materially advanced the pull without needing live access.

---

## Day 1 (Fri, 5–8h) — Onboard + get the pipeline running from a clean clone
**Why it's pull work:** you can't pull anything if the pipeline doesn't run; today only the
sweep engine does. Getting a clean clone to run one end-to-end pass on fixtures is the
foundation for every pull improvement.

Follow **`DEV_DAY1_QUICKSTART.md`** exactly. In summary:
1. NDA → accounts → **devcontainer** up (Python **3.11.2** / curl_cffi **0.16.3** — the host
   almost certainly has a newer Python, so the container is mandatory) → read
   `STATE_OF_THE_SYSTEM.md`, `ARCHITECTURE.md`.
2. Get the **7 offline suites green on a fresh clone.** Known clean-clone breakage: secrets
   load at module import (`fleet_pipeline.py:40`, `fleet_actor_v2.py:50` read
   `$FL_CONFIG/uqual.txt`), and only `uqual.txt.example` ships — so `import fleet_pipeline`
   fails `FileNotFoundError`. Day-1 workaround: `cp runtime/config/uqual.txt.example
   runtime/config/uqual.txt`. Real fix (a PR): make secret loading lazy. ⚠️ These are
   **hash-gated frozen modules** — do it on a branch; the owner re-signs the manifest on merge.
3. **Wire up the vendored modules:** `runtime/engine/address_source.py` and
   `residential_filter.py` are now in the repo, but the pipeline scripts still
   `sys.path.insert` the old external dirs (`/Users/masani/FLEET_5K/merge/engine`,
   `/Volumes/T7/.../02_System/engine`). Repoint them at the repo copies.
4. **Run one small pull end-to-end on FIXTURES** (a few hundred addresses, no live carrier):
   universe → residential filter → (recorded/mock responses) → reconcile → delivery. Goal is
   to *see the whole flow work*, not to hit the endpoint.

**Done when:** a fresh clone runs the offline tests green and produces a deliverable from
fixture data end-to-end. **Open it as a PR** (`P0` in `DEV_ISSUES.md`).

## Day 2 (Sat, 5–8h) — The first-ask input-quality fix (biggest pull lever you own)
**Why it's pull work:** errorCode-1065 (`AddressNeedsFix`/`NotFound`/`Suggestions`) was
**~45% of the Inman run** — mostly because addresses are sent with blank/wrong city+ZIP and
TIGER-interpolated synthetic strings on the *first* ask. The remediation ladder
(`remediate2.py`) already recovers many of these *after the fact*; moving that correction to
the **first ask** turns a huge chunk of unresolved into definitive answers **and** cuts the
call multiplier.

1. Study `remediate2.py`'s escalation ladder (ZIP→USPS city, without-city, candidate mining
   on `AddressSuggestions`, street-type/spelling variants) — it's the proven recipe.
2. Apply the cheap, high-yield parts **before the first carrier call** in the universe/
   dispatch path: authoritative ZIP→USPS city fill, canonical street formatting.
3. Prefer **authoritative NG911/E911 address points** over TIGER strings — NC already builds
   this way (`build_nc_statewide.py`); the other states don't yet. Even partial adoption cuts
   the needs-fix rate.
4. **Prove it offline** against captured/fixture responses: measure the needs-fix rate before
   vs. after on the same address set. Keep `remediate2.py` as the backstop.

**Done when:** a measured drop in the first-ask needs-fix rate on a fixture set, in a PR with
before/after numbers.

## Day 3 (Sun, 5–8h) — More answers per scarce exit + fix the progress meter + market-config start
**Why it's pull work:** fresh residential exits are the real ceiling (Truth 2). Fewer wasted
calls per durable = more addresses resolved per exit = more coverage when a real pull runs.
And you cannot manage what you cannot honestly measure.

1. **Cut per-durable call churn.** A sample run spent ~1.75 searches + 0.46 auths *per job*.
   Find and cut needless re-auth and redundant `AddressSuggestions` round-trips in the
   sweep/actor path. Measure calls-per-durable before/after.
2. **Fix the dishonest progress meter.** Progress must count **durable, evidence-backed
   dispositions**, never launched attempts — the project has been burned by a counter that
   reported attempts as settled work. Make the meter read the same conservation the
   reconciliation stage does (kept vs held vs answered vs unresolved), and make it refuse to
   report a "% complete" that isn't backed by rows.
3. **(Stretch) Start the market-config abstraction:** pull the hardcoded bbox / ZIP set /
   paths out of the `iowa_*`/`tx_*` forks into a single `market.json` (state, geography,
   authoritative address source, prior-delivered keyset, output dir). You won't finish
   collapsing all forks, but a working config for one market is what unblocks the
   full-statewide rebuilds of TX/GA/KY/SC later.

**Done when:** a measured reduction in calls-per-durable (PR with numbers), a progress meter
that only reports evidence-backed counts, and a first `market.json` the pipeline reads for at
least the universe/filter stage.

---

## What you will NOT do this weekend (and why that's correct)
- **No live production pull, and no carrier/proxy request at all** — access is 403-walled and
  owner-gated. Don't try to forge behavioral headers (futile + prohibited).
- **No scaling by adding boxes** — measured counterproductive (2 lanes < 1 lane), and moot
  until access is restored. The lever is exit supply + fewer wasted calls.
- **No autonomy build** — explicitly deferred by the owner.
- **No "just characterize the numbers"** — a failed/blocked/never-asked address is UNRESOLVED,
  never "not serviceable"; the progress meter and any status you report count only real,
  evidence-backed dispositions.

## Where the full-statewide universe rebuilds fit
Rebuilding **TX/GA/KY/SC** to true statewide (§0, §2d) from authoritative sources is real,
in-repo work — but each build is a multi-hour data job (millions of NG911 points + the FEMA
screen), not a weekend afternoon, and it doesn't need live carrier access. Land the P0
runnability + input-quality + config work first; the rebuilds are queued as separate issues
in `DEV_ISSUES.md` and become straightforward once the pipeline is config-driven. **NC is
already full statewide and is the template to copy.**

## The honest scoreboard for the weekend
A great weekend = **the pipeline runs clean from a fresh clone**, **the first-ask
input-quality fix measurably shrinks the ~45% needs-fix bucket**, **calls-per-durable drops**,
and **the progress meter stops lying** — concrete, merged PRs that each make the eventual
pull *more complete and more efficient*. That is real progress toward "every address to a
definitive answer," entirely within reach without live access. Whether the pull reaches *all
five states* still depends on the owner clearing carrier access **and** the four
footprint/partial universes being rebuilt to full statewide.
