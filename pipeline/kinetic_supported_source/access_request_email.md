Subject: Authorized bulk fiber-serviceability access for a Kinetic sales channel (Outvex)

Hi [Kinetic / Windstream partner-operations contact],

Outvex is a Kinetic residential-fiber sales channel (door-to-door via Sales Rabbit). We check
exact-address fiber serviceability so our reps only knock on households where a Kinetic fiber
plan actually populates. This drives Kinetic fiber sell-through in your footprint — a partner
use, not competitive scraping.

We are writing to move to a SUPPORTED data path. The public consumer availability endpoint is
no longer appropriate for our volume, and we are not attempting to work around its protections.

What changed on the public endpoint (measured 2026-09-09):
- `POST https://buy.gokinetic.com/api/v1/address/search` now requires browser-generated
  behavioral-proof headers — `x-meta-info`, `x-resource-id`, `x-interaction-quality`,
  `x-quality-flag` — produced by your PerimeterX/HUMAN sensor (`spx?dxver=4.0.0&shaid=36232`)
  plus Google reCAPTCHA. A real browser session returns HTTP 200; a correct server-side client
  with a valid token, device-id, session-id and the exact request body but without that
  behavioral proof returns HTTP 403. We have STOPPED automated traffic and will not forge,
  replay, or automate that proof.
- Representative CF-Ray IDs (all UTC 2026-09-09):
  a3872b39490a50e2-IAD, a3872b47cd4361a0-ORD, a3872b572f40d3d3-OMA, a3872b6ced00d211-MIA,
  a3872b7e9a38b602-MIA, a38726e1fbe7b7d7-IAD, a38726fe783bf068-DFW

Operational requirement:
- Target throughput: ~2,000 completed address decisions/second. We will operate at whatever
  rate and concurrency you authorize, but this is the volume our monthly refresh needs.
- Texas workload: approximately 12,000,000 residential service locations statewide, refreshed
  monthly (plus SC, NC, KY on the same cadence).

We understand Kinetic already runs supported serviceability paths and would like to use one:
  0. **Kinetic Nexus (Wholesale)** serviceability API — or access via **Connectbase** or
     **TruContact Universal Order Connect (UOC)**, which we understand Nexus is built on. This is
     our first choice: it is your own supported serviceability-automation channel.
  Also acceptable, in order:
  A. A carrier-operated CSV / NDJSON batch: we submit address lists, you return results.
  B. An asynchronous address-batch API (submit job -> poll -> fetch results).
  C. Account / source-IP allowlisting so an authorized server-side client may query directly at
     a written supported rate.
  D. An authorized full serviceability snapshot for our states plus periodic incremental updates.

If channel enrollment is the right route, we are also glad to engage through the **Windstream
Enterprise ONE Partner Program**.

For each qualified address we need these fields returned:
- exact-match indicator
- technology (e.g., fiber vs. copper)
- populated current fiber plan (plan/product name; we do NOT need pricing)
- call-to-order status
- existing-customer status
- premise identifier (your dfAddressId / location id)
- observation timestamp (UTC)
- definitive error semantics (documented codes: out-of-territory, address-needs-fix, etc.)

We have a 10,000-address representative acceptance sample and our result schema ready to share
on your go, and importers already built for the CSV/NDJSON, async, snapshot, and allowlisted
modes so we can activate the moment you choose one.

Thank you,
[Outvex — Masani]  ·  [phone / email]
