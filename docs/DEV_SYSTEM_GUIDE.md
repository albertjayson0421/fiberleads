# The Complete System Guide — read this to understand everything

This is the one doc that ties the system together for a developer: what it does end to end, the
**real** problems we've hit and how each was resolved (with the actual numbers and responses), the
rules that must never break, and an honest analysis of the headline goal — **pull a full state in
~2–4 hours, every residential address to a definitive answer, delivered.** Where a claim is measured
it says MEASURED; where it's a calculation or estimate it says so.

Companion docs: `ARCHITECTURE.md` (engine internals), `STATE_OF_THE_SYSTEM.md` (current state),
`DEV_ISSUES.md` (your task list), `CONTROL_PLANE.md` (how you run pulls in the cloud), `UNIVERSE_STATUS_CURRENT.md`
(per-state address counts).

---

## 1. What the system does, end to end

A nationwide **monthly** lead engine. For each state, one pass:

1. **Universe** — enumerate *every residential address in the entire state* from authoritative
   government sources (state NG911 / county E911 / NAD / TxGIO), never a carrier "footprint."
2. **Residential filter FIRST** — screen every address against FEMA USA Structures `OCC_CLS` at its
   coordinate (free, no carrier call). Drop confident non-residential; keep residential + unclassified.
3. **Serviceability SECOND** — ask Kinetic, per exact address, whether a **priced fiber plan
   populates**. Only that counts as serviceable.
4. **Classify** — one versioned classifier turns each raw carrier response into a proven disposition.
5. **NEW-only** — diff against prior months; keep only brand-new serviceable premises.
6. **Deliver** — each lead ships with an **authoritative rooftop pin** on the correct house, a real
   city, and the fiber confirmation.

It must eventually run **with no agent in the loop** (a scheduled cloud job), and adding a state must
change *only that state's pull method*, nothing else.

---

## 2. The rules that must never break (hard invariants)

These are enforced in code (`standards.py`, `classifier.py`, `endpoint_guard.py`, `egress_guard.py`)
and are the difference between a real deliverable and a silent defect. Breaking one has, historically,
destroyed a deliverable.

- **Serviceable = a priced fiber plan actually populates** — a named bundle (`bundle_detail`) with a
  real `standardPrice`/`promoPrice`. `techType=FIBER` alone is *technology, not a lead*. "Call to
  order," errorCode `1080` pending-order, and no-plan are permanent rule-outs. (Since 2026-09-09 the
  price is confirmed present but not captured/shipped; the plan-populated gate stays.)
- **One versioned classifier.** `classifier.py`, `CLASSIFIER_VERSION` stamped on every result. No
  worker, canary, replay, coordinator, import or export may decide a disposition on its own. (Two
  independent classifiers is how one response became FIBER in one path and UNRESOLVED in another.)
- **A 403 / 429 / Cloudflare challenge is the PATH being refused — never an answer about the
  address.** It can never become `NOT_FOUND` or `NONFIBER`. HTTP 401 is token expiry → remint, not a
  block. HTTP success does not prove classification success — read the whole body.
- **Only verified errorCode `1060` (out-of-territory) becomes terminal `CURRENT_NOT_FOUND`.**
  `1065` AddressNeedsFix, `1700`/AddressNotFound, AddressSuggestions are all "we asked badly" →
  remediation, never a "no." Unknown code / techType fails closed to `UNKNOWN_RESPONSE`.
- **Enrichment failure ≠ no priced plan.** Could-not-read-plans (None / transport / 403 / non-JSON /
  empty) = `ENRICHMENT_UNREAD` (retryable). Read-and-priced-nothing = `NO_PLAN_POPULATED` (a real
  answer, not a lead). Enrichment may never demote confirmed fiber.
- **Completeness = `closes_address()` (an answer whose meaning is proven), never `status in
  TERMINAL`.** Using status-in-terminal once marked 140,584 of 307,522 addresses (45%) "done" with no
  usable answer while the dashboard read "remaining: 0, reconciles: true."
- **Raw carrier evidence is immutable.** A disposition may be rebuilt from it; `raw_json` is never
  edited. Every parser/classifier change must **replay every affected stored response** before prod
  resumes, and bump `CLASSIFIER_VERSION`.
- **Never hammer a non-answering endpoint.** Consecutive *transport silence* is counted process-wide,
  per endpoint (`endpoint_guard.py`); the circuit opens only on sustained silence across all lanes.
  A 429/500/403 is the endpoint *talking* — it never trips the guard. Never raise `TRIP_AFTER` or add
  an unguarded retry loop.
- **Fail-closed egress** (`egress_guard.py`, `REQUIRE_PROXY_EGRESS=1`): all Kinetic traffic goes
  through the configured proxy; a proxy failure ABORTS rather than falling back to direct; raw egress
  IPs are never printed.
- **Residential filter before any carrier call** (`standards.py` R1, exits the pull with code 6 if
  violated). The screen must reconcile `kept + dropped + held == total` or raise `FilterNotApplied`.
- **100% authoritative rooftop coordinates or the market is BLOCKED.** One pin per lead, ≥4 decimals
  on both axes, no shared pins, no best-effort/parcel-centroid. Carrier `geoLat/geoLong` fills a hole
  only — it never overwrites an authoritative point.
- **100% of rows, never a sample**, for anything that ships. Every sweep runs through the enforced
  engine with throttle-recovery, never a hand-rolled loop. Never run Smarty without explicit
  per-run permission and the exact count.
- **Never narrate a carrier "cap/limit/throttle/quota" as an explanation.** If requests fail, dump
  the real response body and diagnose; if a run is slow, state the measured throughput or make it
  faster.

---

## 3. The real issues we've hit — and how each was resolved

This is the troubleshooting history. Format: **the problem → what we tried → what actually came back →
status → what you do about it.** It exists so you don't re-run dead ends or re-introduce a fixed bug.

### 3.1 PRIMARY BLOCKER (OPEN): the behavioral-proof wall on `/api/v1/address/search`
- **Problem:** after the original run completed 209,526 addresses, `buy.gokinetic.com` added a
  PerimeterX/HUMAN behavioral wall. The app bundle now requires `x-meta-info`, `x-resource-id`,
  `x-interaction-quality`, `x-quality-flag`, generated by a real browser sensor (`spx?dxver=4.0.0`)
  + reCAPTCHA. Auth/session is unaffected.
- **Tried:** reverse-engineered the two *deterministic* headers server-side and proved them
  byte-for-byte — `x-resource-id = SHA256_hex(bearer token)`, `x-meta-info = SHA256_hex(device
  fingerprint string)`. Sent valid token + ids + exact body, with and without the behavioral headers,
  from US-residential and other exits. Built a carrier incident packet
  (`pipeline/carrier_incident/kinetic_403_incident_packet.md`).
- **Result (MEASURED, 2026-09-10):** auth/session → **201** (token mints fine); address/search →
  **403** `Forbidden`, `Server: cloudflare`, `cf-ray` present, `cf-mitigated` ABSENT (so *not* a CF
  challenge page), `Retry-After` ABSENT. A native request *with* the behavioral headers → 200; the
  same request *without* them → 403. Fresh exits do not change it. `x-interaction-quality` /
  `x-quality-flag` are genuine proof-of-humanity artifacts that cannot be computed server-side.
- **Status: BLOCKED / open.** The deterministic headers are solved; the two behavioral values are not
  synthesizable without a real browser sensor, and the team's position is **not** to defeat the
  protection. Reopening prod requires a **carrier-supported path** (batch/async API, allowlist,
  snapshot, partner API) or authorization at the enforcing layer. `auth 201` alone is explicitly NOT
  "restored."
- **You:** do **not** spend cycles forging `x-interaction-quality/x-quality-flag`. Pursue the
  carrier-supported source. Keep the deterministic-header code (`_x_meta_info`/`_x_resource_id` in
  `carrier_uqual.py`) — it's needed for the 200 path once access is authorized. Any reopen must pass
  the end-to-end readiness canary, not just a 201.

### 3.1a Dead ends on the access wall — tried, abandoned, **do NOT repeat**
Everything below was attempted against the §3.1 403 wall and does not work. It's recorded so no one
re-burns hours on it. Evidence: `pipeline/kinetic_supported_source/technical_evidence.md`,
`pipeline/carrier_incident/kinetic_403_incident_packet.md`.

| Tried | What happened | Verdict |
|---|---|---|
| **Forge the behavioral headers server-side** | The two *deterministic* headers are solved and kept (`x-resource-id = SHA256(token)`, `x-meta-info = SHA256(device string)`) — they're needed for the 200 path *once access is authorized*. But `x-interaction-quality` (`interactionQuality.toFixed(3)`) and `x-quality-flag` are proof-of-humanity values a real browser's PerimeterX/HUMAN sensor (`spx?dxver=4.0.0`) + reCAPTCHA produce from observed interaction. | **Not synthesizable server-side. Dead end.** Don't try to compute/forge them. |
| **Fresh residential exits / new geographies / ASNs** | Tested Spectrum, T-Mobile, Verizon Business, NTT (AS2914), Rocks (AS40403), incl. an Iowa exit — every one: `auth/session` → **201**, `address/search` → **403**. | **The wall is behavioral, not IP/geo. Dead end.** Rotating exits changes nothing here (this is *distinct* from the per-exit throughput 403 in §3.3, which WAS fixed). |
| **A correctly-shaped stable server-side client** (right Origin/Referer/Fetch-Metadata, one session, no churn) | Authenticates fine (201) but is refused at search (403). | The request-shape fix in §3.2 solved the *old* 403; it does **not** get past this behavioral layer. Dead end for the wall. |
| **Solve the reCAPTCHA / handle the PerimeterX token** | — | **Ruled out by policy — we do not defeat the protection.** Not attempted; do not attempt. |
| **Manual browser spot-check** | A person checking a handful-to-low-hundreds by hand in a normal browser is ordinary human use and works — but does **not** scale to a statewide workload and must never be automated (`manual_fallback_instructions.md`). | A stopgap for a tiny priority list only, not a solution. |

**Established facts (don't re-derive them):** the 403 is an application-layer response *after*
successful TLS + auth (auth returns a real 201 JSON on the same session), served by/through Cloudflare
(`server: cloudflare`, `cf-ray` present) but **not** a Cloudflare Challenge Page (`cf-mitigated`
absent), with **no** `Retry-After`. Which layer decides (Cloudflare WAF vs HUMAN vs origin) is **not**
determinable client-side — only Kinetic can correlate the `cf-ray`s against their logs.

**The only path that is NOT a dead end:** the incident packet + access-request email ask
Kinetic/Windstream to either **(a)** authorize the existing client at the enforcing layer (a HUMAN
`x-px-access-token` trusted-access grant and/or a scoped Cloudflare skip for the approved host/paths),
or **(b)** provide a **carrier-run batch/async qualification** (API, snapshot, or partner feed) sized
for the monthly multi-state workload. This is **owner/business, not engineering.** Auth `201` alone is
never "restored" — reopening requires an approved end-to-end canary (auth 201 → search 200 → product
200 → terminal).

### 3.2 RESOLVED: the *earlier* 403 was a request-shape fingerprint, not an IP block
- **Problem:** `curl_cffi impersonate='chrome124'` sends a real Chrome's **top-level-navigation**
  header profile (`Sec-Fetch-Dest: document`). Every search announced itself as a browser typing the
  API URL into the address bar — which Cloudflare refuses on `/api/v1/address/search`.
- **Result (MEASURED, 2026-09-01):** shipped `Sec-Fetch-Dest: document` → 403 (4/4);
  `Sec-Fetch-Dest: empty` alone → 200 (3/3); full XHR `FETCH_METADATA` block → 200 (4/4). On the
  direct home IP, navigation-shaped 403 and XHR-shaped 200 FIBER **alternated** — proving the home IP
  was never blocked; prior "home IP blocked" 403s were this bug in disguise.
- **Status: RESOLVED.** `FETCH_METADATA = {Sec-Fetch-Dest: empty, Sec-Fetch-Mode: cors,
  Sec-Fetch-Site: same-origin}` is always sent (all three, because a half-set is its own fingerprint).
- **You:** keep `FETCH_METADATA` on every `/api` request. On any new 403, **dump the raw body first**
  to tell this fixable shape apart from the §3.1 behavioral wall before theorizing an IP block.

### 3.3 RESOLVED (free): per-exit request budget + the US-geo lock
- **Problem:** an exit IP answers only **~20 searches** before the carrier edge returns 403 and burns
  that IP for 24h. The client was geo-locked to a US-only proxy subset that runs out of fresh IPs as
  concurrency rises.
- **Result (MEASURED, 2026-09-03):** one exit answered 20 searches (200), then #21 → 403
  `Retry-After: 86400`; the same address on a fresh exit → 200 in the same second. 403% vs concurrency
  on the US subset: 12w=0.1%, 48w=23.6%, 256w=52.4%. Dropping the geo-lock to the **global** pool:
  96w global = **0.0% 403 (0 of 7,100) at 51.5/s** — double the concurrency, dead clean (Kinetic
  checks the *address*, not requester geo).
- **Status: RESOLVED** for the throughput-403 as it existed then (predates §3.1). Global pool
  (`FL_DI_GEO=''`), rotate an exit before ~20 searches, never revisit an IP inside 24h.
- **You:** keep the global pool. Don't confuse this per-IP 403 (`Retry-After: 86400`) with the §3.1
  wall (`Retry-After` ABSENT) — different bodies, different fixes.

### 3.4 OPEN (partially explained): the clean→all-403 regression
- Diffing the last clean run (2026-09-09, s403=0) against the 2026-09-10 all-403 state with
  byte-identical code found one change: sticky/egress grammar went from a form DataImpulse *ignored*
  (fresh exit IP per request) to one that **pins** one exit. Supported (not proven) hypothesis: the
  clean run's 0% 403 was accidentally enabled by per-request rotation staying under the per-exit
  threshold; pinning concentrates load and hits it. Can't be confirmed offline (needs a live canary,
  disabled) and isn't reproduced by the localhost sim (it doesn't model per-IP edge limits).
- **You:** do **not** silently revert to the ignored-parameter form. The candidate fix (explicit
  per-IP budget with rotation) is *specified, not shipped* — validate it with a live rotating-vs-pinned
  A/B before claiming it. The external wall (§3.1) dominates regardless; egress strategy only changes
  how fast the limit is reached.

### 3.5 RESOLVED: "Too Many Attempts" was becoming false negatives
- **Problem:** when rate-limited, Kinetic returns **HTTP 200** with a Laravel body
  `{"message":"Too Many Attempts."}`. The parser only checked `text.startswith('{')`, so it passed
  that up as valid JSON → no errorCode → UNREAD → after max reads → permanent FLAGGED. Every
  rate-limit hit became a permanent flag; a mass-FLAGGED run read as "no fiber."
- **Result (MEASURED, 2026-08-14):** flag rate tracked *total* request rate (84% @420/min, ~20%
  @120/min) and did **not** fall with 40 rotating clean proxy IPs — so it was keyed on the endpoint,
  not per-IP; datacenter proxies were worse (87%).
- **Status: RESOLVED.** `_post` now treats "Too Many Attempts" and Cloudflare "just a moment" as
  `None` → soft retry, never a read. Mass-FLAGGED must never be read as "no fiber."
- **You:** on any throttle-shaped symptom, dump the raw body on request #1. Never let a soft-throttle
  body reach the classifier as a verdict. Always run sweeps through the enforced engine's
  throttle-recovery (`_reconfirm_negatives`), never a hand-rolled loop — a hand-rolled loop
  reintroduced this on Americus (14 "final" leads → 21 after re-reading 2,403 throttled no-matches).

### 3.6 RESOLVED (hardened): the proxy-firewalling incident → the endpoint guard
- **Problem (2026-08-27):** the proxy gateway stopped accepting connections mid-run; 200 workers each
  retried at ~16 failed TCP connects/sec for ~20 min from one IP; IProyal firewalled the machine
  (an abuse filter tripped). Every worker's backoff was reasonable — nothing could see all of them at
  once.
- **Fixed with `endpoint_guard.py`:** a **process-wide** circuit counting consecutive transport
  *silence* per endpoint; opens so nothing leaves the machine; one probe per cooldown (30s→900s);
  abandons resumably after patience. **Proven: 500 attempts on a dead endpoint → 6 sockets opened.**
  A success from any lane resets the streak; a 429/500/403 never trips it. Hardened 2026-09-03 to be
  lane-aware (a single transient lane failure retries on a fresh lane; the hard circuit opens only on
  sustained silence across ≥6 lanes).
- **You:** route every network retry through the guard. Treat `EndpointDown` as "work unfinished and
  resumable," never an empty result — silence is not a finding. (The defect run was about to write a
  header-only CSV that downstream reads as "nobody lives here.")

### 3.7 RESOLVED: the classifier defect cluster (the deliverable-killers)
The 2026-09-01 audit found "we failed to ask," "we asked badly," "the service refused," and "the
answer is no" sharing buckets. Each measured blast radius, all now fixed by one versioned classifier
driven by an exhaustive 4-bucket table (`test_four_buckets.py` fails on any unmapped pair):
- `1065` AddressNeedsFix filed as NOT_FOUND — **31 of 62** answers in a run. → remediation, never NOT_FOUND.
- `1700`/AddressNotFound fell to UNKNOWN, an undrainable lane — **33,638** TX addresses "couldn't
  understand the reply." → needs-fix remediation.
- `metaBundles` is a **dict** keyed `speedAndProductBundleList`, not a list; iterating yielded keys,
  so **35 of 38** fiber rows recorded the literal key as the plan and threw away real bundle names. →
  walk anchored to `BUNDLE_CONTAINER_KEYS`.
- `1080` pending-order (empty `metaBundles`) was **shipping as a lead** (12 Inman addresses). → ruled
  out before NO_PLAN.
- Enrichment failure collapsed into NO_PLAN — 9 rows were 7× transport timeout + 2× never-ran, **0**
  genuine no-plan. → `enrichment_unread()` splits retryable from real.
- **You:** any classifier/parser change → replay every affected stored response, bump the version,
  keep the 4-bucket table exhaustive (an unmapped pair must raise). Never let `bundle_detail()` raise
  (it's caught upstream as a transport failure and would trip the circuit on healthy fiber).

### 3.8 RESOLVED: no-match deletion & the existing-customer leak (submit normalization)
- **AddressSuggestions no-match** = a statement about the *string we submitted*, not the address. The
  engine ignored the `addressCandidates` array and recorded a refusal — deleting every lead behind a
  spelling difference. (`281 Fury Dr, Inman` was knocked serviceable but had been swept, no-matched,
  deleted.) Fix: `matching_candidates()` → `variants()` (`AVE`→`AVENUE`, append missing street type) →
  `_near`. MEASURED: `1201 SW AVE G` → FIBER as `1201 SW AVENUE G`; re-asking a 400-row Inman sample
  recovered 0.8% FIBER (~640 real leads). Unknowns are now **labelled, never deleted**.
- **Existing-customer leak (Chariton):** the `1020` existing-customer flag only fires on an
  exact-match submit; a spelled-out address returns `1003` (fuzzy) and bypasses it — so existing
  customers shipped as new leads. A market showing **~0 existing customers is the red flag**, not a
  clean market. Fix: `normalize_submit()` abbreviates on every submit so Kinetic returns `0000`/`1020`.
  **CAVEAT — Georgia is the opposite:** GA's Kinetic DB stores the *spelled-out* form, so a GA sweep
  showing ~45–48% no-match is this normalization bug, not thin fiber — keep full words for GA.
- **You:** run every no-match through candidates→variants→_near before recording. Treat ~0
  existing-customers as a bypass red flag. Per-region submit normalization; verify the `1020` path fires.

### 3.9 RESOLVED: coordinate placement (leads found, then dropped at the pin)
- Leads were finding fiber and then being dropped at the pin, or pinned to a neighbor's roof, because
  the no-FEMA-footprint exemption was too narrow and the carrier coordinate overwrote authoritative
  E911 points. MEASURED recovery: Iola 38→228, four towns 116→515, 40/40 re-place test; separately,
  the carrier coord had overwritten **5,003 of 5,374 (93%)** of China Grove's NC E911 rooftop points.
- **This ran on every market before 2026-08-24**, including delivered TX East (8,977) and TX West
  (8,370) — both found leads, confirmed fiber, dropped them at the pin.
- **You:** **re-place (not re-sweep)** any pre-2026-08-24 delivered market from disk. Enforce both
  axes ≥4 decimals, no shared pins, `one_roof_one_address`. Authoritative point wins; carrier coord
  fills a hole only.

### 3.10 RESOLVED: statewide residential screening at scale (+ the guard that was blind)
- Serial per-tile paging didn't scale; rebuilt `residential_filter.py` to a tiled FEMA download
  (~2 km tiles) → in-memory ~200 m grid index (point-in-polygon at hundreds of thousands/sec) → atomic
  per-tile gzip cache (killed run resumes; a half tile never reads as whole) → page parallelism. On
  the TX download, 12 workers = **1,605/min at 0% err** vs 6 workers = 653/min.
- The original batch guard only counted "refused lookups" and finished at `unevaluated=0` while
  **532,059 TX addresses** were unresolvable — the failure was invisible to the check meant to catch
  it. Now three guards fail closed: reconciliation `kept+dropped+held==total`, `unevaluated>MAX`,
  `held>MAX`.
- **NOTE:** the vault/engine sources do **not** contain an explicit OOM crash record for this — what's
  evidenced is the tiled download + resume cache + reconciliation guards. If you need literal OOM
  history, pull it from run logs; don't assert it from these docs.
- **You:** keep the three batch guards; never return a pass-through queue.

### 3.11 OPEN (data): SC/NC/KY residential universes vs Iowa
- Only **Iowa** is a *counted* residential universe (941,315 unique premises). SC/NC/KY were geo-only
  scaffolding when these notes were written; the repo's `UNIVERSE_STATUS_CURRENT.md` now reflects the
  2026-09-12 rebuild (TX 254/254 = 10.88M, NC/KY complete, SC 33/46, GA addresses complete). Texas
  "12M" is operator-stated; the counted figure is 10,878,814.
- **You:** never present an operator-stated or geo-scaffold number as a counted denominator. Build a
  state's universe with the authoritative-address-points → FEMA-screen pipeline before quoting its
  monthly volume.

---

## 4. The headline goal — full state in 2–4 hours: an honest analysis

> **Short answer: not achievable as the system exists and is accessed today — it is ~65–140× short on
> the arithmetic, and the current *live* throughput is zero.** It becomes a real question only under a
> different access model (an authorized carrier path) plus a distributed control plane that is
> presently *designed, not built*. This is the truth to build toward, not around.

**The denominator (MEASURED):** worst case TX = **10,878,814** residential addresses.

**The target rate (CALCULATION):**
- 4 hours (14,400 s): 10,878,814 ÷ 14,400 = **756 addresses resolved/sec**
- 2 hours (7,200 s): **1,511/sec**
- At the MEASURED call multiplier (~1.75 searches + 0.46 auth per job), 4h ≈ **~1,300 searches/sec
  sustained**, ~26M carrier calls for TX; 2h is double.

**What one lane actually sustains (MEASURED):**
- Best clean single-lane burst ≈ **0.98 resolved/sec** (a short window, not sustained).
- Adding lanes made it **worse**: 2 lanes = 21/min < 1 lane = 59/min. Concurrency inverts here.
- A **12-box run produced 0 of 609,580** — the real current live rate is **zero** (§3.1 wall + the
  missing global proxy allocator).
- The design's own configured ceiling (auth ≤2/s, ~10 searches per fresh exit) ⇒ a *theoretical*
  ~11 addresses/sec — **never reached live**, because it assumes a clean endpoint.

**The gap:** against the 4h target (756/s), that's ~770× short of the best clean burst, ~66× short of
the theoretical ceiling, and infinitely short of the live rate (0). TX at the best burst extrapolates
to **~128 days**; at the theoretical ceiling **~11 days**.

**Why "more boxes" is not the lever:** the binding constraint is fresh-exit supply and per-exit
tolerance, not CPU/threads — proven by 12-box→0 and 2-lanes<1-lane. 4h against the current per-exit
economics would demand ~1.9M distinct fresh residential exits (2h: ~3.8M) and ~132 auth/sec vs the
2/sec the code deliberately allows.

**The hard blocker:** none of this runs until **authorized Kinetic access** exists (§3.1). The engine
even refuses to start without an approved end-to-end canary that cannot pass while walled.

**So it becomes achievable only if all of these are true:**
1. **Authorized carrier access** that removes both the behavioral wall and the per-exit tolerance —
   realistically a **bulk file the carrier qualifies on their backend**, not ~1,300 realtime
   round-trips/sec against a public endpoint. *Owner-gated, external, not granted.* This is the whole
   ballgame.
2. **Per-address cost near 1 call** — collapse the MEASURED ~45% `1065` needs-fix bucket with
   first-ask input-quality fixes (offline, doable now — `DEV_ISSUES.md` P1).
3. **The distributed control plane actually built** — global coordinator, cross-machine lease queue,
   and the **single global proxy allocator** whose absence caused the 0/609,580 run. Currently
   designed, not built (`CONTROL_PLANE.md`).
4. **Fresh-exit supply proven at scale** *if* still going per-address over proxies — which the "more
   lanes = less throughput" evidence suggests isn't physically reachable against this endpoint without
   an authorized path.

**Bottom line for your planning:** with an authorized/bulk carrier path, "full state in hours" is
plausible — it becomes the carrier's backend problem. Without it, every throughput number sits on top
of a live rate of zero, and the highest-leverage engineering you can do *now* is items 2 and 3 (they
make the pass cheaper and the fleet coordinated) so the system is ready the moment access opens.

---

## 5. Where to start

`DEV_ISSUES.md`, in order: **[P0]** make the repo run end-to-end from a clean clone → **[P1]** package
the pull as a Cloud Run Job (your bounded self-service) → **[P1]** first-ask input-quality (cut the
~45% needs-fix) → the honest-metrics + efficiency items → finish the remaining SC counties + GA county
labels. None of it requires a live carrier call; all of it moves the system toward the goal above so
it's ready when the access does.
