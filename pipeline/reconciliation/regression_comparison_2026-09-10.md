# Successful → failed transition: evidence comparison (item C, 2026-09-10)

Compares the last CLEAN run artifact (release_lastgood_2026-09-09_fg, `fg.log @ 2026-09-09T22:46:45Z`:
auth201 warmed=126, search200 ×290, product200 ×176, **s403=0**) against the current 403 state
(retained probes 2026-09-10: auth 201 → search 403 `Forbidden`).

| Dimension | Clean run (s403=0) | Current (403 at search) | Hypothesis verdict |
|---|---|---|---|
| Loaded transport code (`carrier_uqual`) | `c02e20cf` | `c02e20cf` | **CONTRADICTED** — identical; not a transport regression |
| Native headers (device-id/session-id/x-meta-info/x-resource-id), `_FP_STRING`, search+product bodies, `impersonate=chrome124`, Sec-Fetch/Referer/Origin | present | **byte-identical** (diff empty) | **CONTRADICTED** — request bytes unchanged |
| Host / path / redirects | buy.gokinetic.com, /auth/session,/address/search,/product-availability | same host+paths; 403 is an app-layer reply after TLS (auth 201 same session) | not a routing regression |
| **Sticky/egress grammar** | `__cr.us__sid.` — **DataImpulse IGNORED it → a FRESH exit IP per request** | `__cr.us;sessid.` — **pins ONE exit IP** (verified sticky this session) | **SUPPORTED** — see below |
| Aggregate per-IP traffic | each request from a different IP → stays under the measured ~30/IP threshold | one IP reused for many searches → reaches the ~30/IP threshold → 403 | **SUPPORTED** |
| Cookie/session/token affinity | one bearer token accepted across rotating exit IPs (290 search-200s) | one token, one pinned IP | carrier accepted cross-IP token in the clean run |
| Proxy provider | DataImpulse | DataImpulse | unchanged; not the cause |
| Retries / post-quarantine dispatch | n/a (clean) | fixed in candidate (A2/A4) | n/a to this transition |

## Leading hypothesis (SUPPORTED, not proven)

The clean run's `s403=0` was **enabled by per-request exit rotation** (the `__sid.` grammar was silently
ignored, so every request egressed from a fresh residential IP, never reaching the measured ~30-searches
per-IP threshold). The 2026-09-10 change to a **pinned** `;sessid.` exit concentrates many searches on one
IP, which reaches that per-IP threshold and returns 403. i.e. the sticky "fix" — correct for *session
continuity* — is **counterproductive for throughput against a per-exit-IP-limited edge**, and is the most
plausible client-side contributor to the observed regression.

## Unresolved / not claimed

- **Cannot be confirmed offline.** Confirming requires a live A/B probe (rotating vs pinned egress) against
  the carrier, which is disabled. NOT reproduced in the localhost sim (the sim does not model per-IP edge limits).
- **Not claimed:** that the 403 identifies a specific Cloudflare/HUMAN rule; that prior success proves no
  regression; that a local reproduction would prove live access restored.
- The external block (per-IP / per-egress) remains the dominant factor regardless; egress strategy only
  changes how quickly it is reached.

## Candidate fix path (design, NOT shipped — needs an approved canary to validate)

Make egress strategy explicit and measured: a **per-IP request budget with rotation** — keep the bearer
token, but retire an exit IP before the measured ~30-search threshold and open a fresh sticky session,
distributing load across IPs (what the clean run did accidentally) while preserving durable session/token
handling. Do NOT silently revert to `__sid.` (that hides the egress in an ignored parameter). This change
is unverifiable without a live canary, so it is specified here, not applied to the candidate.
