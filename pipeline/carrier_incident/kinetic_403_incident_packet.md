# Kinetic serviceability — carrier incident packet (403 at address/search)

**Prepared 2026-09-10 from RETAINED probe evidence. No new carrier probes were issued to build this. Requesting Kinetic/Windstream correlate the identifiers below with Cloudflare, HUMAN, and origin logs to attribute the exact enforcing layer.**

Secrets (bearer tokens, cookies, credentials, full exit IPs) are excluded by design. Exit IPs available on request in a restricted attachment.

## What succeeds vs fails (three retained probes, same client, one workflow each)

| # | UTC | Exit (country / ASN) | `auth/session` | `address/search` |
|---|---|---|---|---|
| 1 | 2026-09-10T~18:1xZ | US / AS2914 NTT America | **201** (token issued) | **403** |
| 2 | 2026-09-10T~18:2xZ | US / AS40403 Rocks Computer Services | **201** (token issued) | **403** |
| 3 | 2026-09-10T~18:5xZ | US / AS40403 (Iowa address) | **201** (token issued) | **403** |

## Exact response facts (address/search 403)

- `POST https://buy.gokinetic.com/api/v1/address/search`
- HTTP **403**; `Server: cloudflare`; `Content-Type: text/html`; response body = literal `Forbidden` (no JSON, no token).
- `cf-ray` present. Observed values: `a390750508e1420a-EWR`, `a39077378cb38f26-YUL`, `a3909c842c81f867-YUL`.
- `cf-mitigated`: **ABSENT** (header not sent). This is a *missing header*, distinct from a literal `"null"` value. Per Cloudflare docs a Challenge Page always sets `cf-mitigated: challenge`; its absence means this response is **not a Cloudflare Challenge Page**.
- `Retry-After`: **ABSENT** (not sent) — no carrier-stated recovery interval.

## Corresponding auth (same session, immediately prior)

- `POST https://buy.gokinetic.com/api/v1/auth/session?context=residential` → HTTP **201**, `Server: cloudflare`, `Content-Type: application/json`, valid bearer token issued. `cf-ray` present (e.g. `a390773418258f26-YUL`).

## Where the rejection occurs (evidence-supported)

- The proxy `CONNECT` and destination TLS negotiation **succeed** — auth returns a real `201` JSON from the origin on the same session. Therefore the 403 is an **application-layer response returned after destination HTTPS negotiation**, on the `address/search` call — **not** a proxy-CONNECT failure and **not** a transport error.

## What the evidence does and does NOT establish

- **Does establish:** the `403` was served by or through Cloudflare (`server: cloudflare`, `cf-ray` present) and is a block/deny response, **not** a Cloudflare Challenge Page (`cf-mitigated` absent). Auth is unaffected; only search/product are refused. Fresh US residential exits do not change the outcome.
- **Does NOT establish:** which layer made the decision. `server: cloudflare` + a `cf-ray` + `403` are consistent with a Cloudflare WAF managed/custom **block** rule, but are equally consistent with a HUMAN policy decision or an origin decision surfaced as a 403 through Cloudflare. **We are not asserting "Cloudflare WAF rule confirmed."** The `cf-ray` identifies the request path through Cloudflare; it does not name the deciding product.

## Requested correlation (what only Kinetic/Windstream can do)

1. For each `cf-ray` + UTC + path + observed exit identity: which Cloudflare product/rule (if any) produced the 403 — WAF managed/custom rule, rate rule, IP/ASN list, Bot Fight/Super Bot Fight, or a pass-through of an origin/HUMAN decision.
2. Whether HUMAN (PerimeterX) Bot Defender enforced on these requests, and if so under which policy/rule.
3. Whether origin logs show the search request arriving and being denied at origin vs blocked at edge.
4. The sampled-events caveat: a missing Cloudflare Security-Events entry for a `cf-ray` must not be read as "allowed."

## Ask

Authorize the existing qualification client at the enforcing layer(s) (HUMAN `x-px-access-token` trusted access and/or a scoped Cloudflare skip for the approved host/paths), OR provide a carrier-run product-qualification batch. Auth `201` alone will never be treated as restored production; reopening requires an approved end-to-end qualification validation.
