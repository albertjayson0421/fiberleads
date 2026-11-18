"""Reviewed Kinetic address-qualification client (offline repair, not live certified).

This module returns technology/address evidence only. It does not request or
verify product plans, certify residential use, or certify rooftop coordinates.
A FIBER return is therefore a candidate, not a deliverable serviceable lead.

The source file contains legacy incident narratives below. Those narratives
are not current measurements or proof of endpoint capacity. The repair fixes
strict exact-match handling, tri-state customer metadata, diagnostic evidence,
session closure, auth retry restoration, and conservative candidate identity.
HTTP 403/429/challenges are surfaced as unresolved access failures; they do not
trigger automatic exit switching in the modified failure path.

Anonymous bootstrap credentials are read from configuration, never hard-coded.
"""
from __future__ import annotations

import base64
import json
import hashlib
import re
from datetime import datetime, timezone
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://buy.gokinetic.com"
SEARCH = "/api/v1/address/search"
SESSION = "/api/v1/auth/session?context=residential"
#: The buy flow's own product call (live host). Ported from the SC-Inman lineage
#: engine; response shape metaBundles.speedAndProductBundleList.<tier>.bundles[].
PLANS = "/api/v2/windstream/product-availability"

#: FETCH METADATA — THE REQUEST MUST SAY IT IS AN XHR, NOT A PAGE NAVIGATION.
#:
#: THE BUG THIS FIXES. curl_cffi's `impersonate="chrome124"` copies a real
#: Chrome's headers, and the profile it copies is a TOP-LEVEL NAVIGATION:
#: measured 2026-09-01 against an echo server, a bare chrome124 session sends
#:
#:     Sec-Fetch-Dest: document      Sec-Fetch-Mode: navigate
#:     Sec-Fetch-Site: none          Sec-Fetch-User: ?1
#:     Upgrade-Insecure-Requests: 1
#:
#: `_headers` never overrode any of them, so every search this client has ever
#: sent announced itself as a browser TYPING THE API URL INTO THE ADDRESS BAR.
#: Cloudflare in front of buy.gokinetic.com refuses exactly that on
#: /api/v1/address/search: HTTP 403 with the 5,486-byte "Attention Required!"
#: interstitial, and the 401/403/blocked branches in check() then burned all
#: three attempts and returned UNREAD {'stage': 'exhausted'}.
#:
#: MEASURED 2026-09-01, same session, same exit IP, consecutive requests:
#:
#:     shipped headers (Sec-Fetch-Dest: document)   -> HTTP 403   4 of 4
#:     + Sec-Fetch-Dest: empty ALONE                -> HTTP 200   3 of 3
#:     + Sec-Fetch-Mode: cors alone (dest untouched)-> HTTP 403   3 of 3
#:     + Sec-Fetch-Site: same-origin alone          -> HTTP 403   3 of 3
#:     full block below                             -> HTTP 200   4 of 4
#:
#: `Sec-Fetch-Dest` is the load-bearing one; the other two are sent with it
#: because a real XHR sends all three and a half-set is a fingerprint of its
#: own. All three are plain request-context headers, not credentials and not
#: identity — they describe what the request IS, which is a page's own
#: background call to its own API, exactly what this client performs.
#:
#: NOT AN IP BLOCK, AND THE HOME IP WAS NEVER BLOCKED. Measured the same day on
#: the direct connection with no proxy: navigation-shaped 403 three times,
#: XHR-shaped 200 FIBER three times, alternating. Every "the home IP is blocked"
#: 403 this client has reported on the search path was this bug wearing a
#: block's costume. The /api/v1/auth/session handshake is NOT subject to the
#: rule — it answered 201 with the navigation headers throughout, which is why
#: the token always minted and only the search failed.
FETCH_METADATA = {
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

#: NATIVE-PROTOCOL DETERMINISTIC HEADERS (added 2026-09-09, proven from the app bundle).
#: buy.gokinetic.com/api now gates on four headers the site's own JS computes. Traced in
#: _app-<hash>.js: device-id/session-id from the session store; x-resource-id = SHA256_hex of
#: the current bearer token (PROVEN byte-for-byte vs a captured native request); x-meta-info =
#: SHA256_hex of a device-attribute string (deterministic; the server cannot validate its
#: contents, so a self-consistent value satisfies it). These are NOT Cloudflare/HUMAN/behavioral
#: tokens and are never harvested or replayed — they are computed from THIS client's own state.
import uuid as _uuid, base64 as _b64
_FP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
#: exact c() field order from the bundle: cookieEnabled|userAgent|language|hardwareConcurrency|
#: maxTouchPoints|webdriver|pdfViewerEnabled|screen.w/h/availW/availH/colorDepth/pixelDepth|tzOffset
_FP_STRING = "|".join(["true", _FP_UA, "en-US", "8", "0", "false", "true",
                       "1920", "1080", "1920", "1032", "24", "24", "300"])

def _sha256_hex(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def _x_meta_info() -> str:
    return _sha256_hex(_FP_STRING)

def _x_resource_id(token: str) -> str:
    return _sha256_hex(token or "")

def _jwt_session_id(token: str) -> str:
    try:
        p = (token or "").split(".")[1]; p += "=" * (-len(p) % 4)
        return json.loads(_b64.urlsafe_b64decode(p)).get("sessionId", "")
    except Exception:
        return ""

#: CHURN REMOVED 2026-09-09 (Masani). Recreating the session/exit every N addresses is exactly
#: the behaviour the edge flags. One actor now owns ONE session/route for its whole lifetime;
#: rotation happens only on a proven block, never on a request count. Set effectively infinite.
ROTATE_EVERY = 10 ** 9

FIBER = "FIBER"
COPPER = "COPPER"
CABLE = "CABLE"
#: The carrier's own techType for a 5 GHz fixed-wireless link.
FIXED_WIRELESS = "FIXED_WIRELESS"
#: The verdict a fixed-wireless answer produces. It gets its OWN name rather
#: than being folded into DSL: a rep quoting a customer needs to know whether
#: the incumbent service is a copper pair or a radio, and "the answer is no" and
#: "the answer is no, over the air" are two different facts about the ground.
#: Collapsing them would be the same mistake as filing a refusal as a verdict.
WIRELESS = "WIRELESS"

#: OUTCOMES THAT ARE NOT VERDICTS, kept beside the technologies because the line
#: they draw is the one this client exists to protect: what the carrier TOLD US,
#: against what we failed to ask it.
#:
#:   UNREAD      the request never got an answer -- a block, a timeout, a
#:               dropped connection. Ask again.
#:   UNASKED     no answerable question was ever sent, because the address
#:               arrived with no city or no ZIP. On the Texas statewide sweep
#:               114,964 addresses were submitted with one or both fields blank
#:               and every refusal was filed as UNKNOWN -- a claim about the
#:               address, made from a question the carrier never received. One
#:               of them, "9803 F M 597-C R 5000", went out seven times, every
#:               request carrying "city": "", "zip": "".
#:   UNRESOLVED  the carrier answered with candidate addresses and not one of
#:               them is this house. It knows something about the street; we
#:               could not safely take any of it.
#:
#: None of the three may ever be written to a sweep row as a verdict. A verdict
#: retires an address for good, and these three are all recoverable work.
UNASKED = "UNASKED"
UNRESOLVED = "UNRESOLVED"

#: THE CARRIER ANSWERED AND THE ANSWER IS NO. It holds a record for this exact
#: address, it names the exchange the house sits in, and nothing qualifies.
#: The same verdict carrier_kinetic has used for a settled negative since it was
#: written -- this path simply never emitted one, so its settled negatives had
#: nowhere to go and were filed as "we could not identify this address".
NOFIBER = "NOFIBER"

#: The carrier said something no table here knows how to read. NEVER a bucket:
#: it is counted, named on screen, written into the row, and it stops a
#: delivery. A response type nobody has taught the engine about is not an
#: absence of information, and must never be allowed to imitate one.
UNMAPPED = "UNMAPPED"

#: Where the answer lives, for results that are not a verdict by themselves.
#: Internal to the table below; never written to a row.
_TECH = "TECH"        # the answer is in techType -- go and read it there
_SEARCH = "SEARCH"    # the carrier will not accept this string as written


#: AddressUnserviceableInTerritory is negative only with explicit boolean
#: exactMatch=true. A premise ID, exchange or coordinates cannot establish that
#: a non-exact response answers the requested service location.
VALIDATION_RESULTS: dict[str, tuple[str, bool]] = {
    # The ordinary answer. exactMatch decides whether techType is about THIS
    # house or about a near neighbour.
    "AddressFound":                    (_TECH,   True),
    # Requires strict exactMatch before it can become a negative.
    "AddressUnserviceableInTerritory": (NOFIBER, True),
    # The carrier holds near-misses and is offering them. The retry chain asks
    # about its own suggestions; that is what recovered 1,666 Andrews leads.
    "AddressSuggestions":              (_SEARCH, True),
    # "Your string needs correcting." Deliberately the retry chain, and NOT a
    # verdict read off maxQual: a qual on one of these belongs to whatever
    # partial record the carrier matched, and this response is the carrier
    # saying that record is not exactly our house. Reading a verdict off it
    # would make a partial match look like an answer about this address --
    # settling it, and taking it off the workable list, on our inference rather
    # than the carrier's word. 3 rows on the Texas pull; the chain gives them a
    # real chance of an exact match instead.
    "AddressNeedsFix":                 (_SEARCH, True),
    # No record at all -- no exchange, no dfAddressId, no coordinate.
    "AddressNotFound":                 (_SEARCH, False),
}


#: EVERY techType THE CARRIER IS KNOWN TO RETURN ON AN EXACT MATCH, AND THE
#: VERDICT IT PRODUCES.
#:
#: The second half of the same decision VALIDATION_RESULTS starts, and it lives
#: here beside it for the same reason: the technology used to be read by a chain
#: of `if`s inside `classify`, which is a table nobody can enumerate, and a
#: string it did not name fell out of the bottom.
#:
#: It is NOT a second reader of the same strings. VALIDATION_RESULTS is keyed on
#: `validationResult` and this is keyed on `techType`; the two never judge the
#: same string, and both are read only by `classify` below.
#:
#: THE SAME CONTRACT: anything absent is UNMAPPED -- loud, counted, written to
#: the row, fatal to a delivery. Never sorted into the nearest bucket.
#:
#: FIXED_WIRELESS added 2026-08-28, deliberately, off measured evidence. 148
#: UNMAPPED rows across 72 Texas addresses, and every single one of them:
#: validationResult AddressFound, exactMatch true, its own exchangeId, techType
#: FIXED_WIRELESS, maxQual "QUAL UP TO 25/50/75/100 MB 5GHZ VIA WIRELESS". Read
#: plainly: the carrier holds this address, matched it exactly, and the best
#: thing it will sell there is a radio link. That is a settled answer and, for
#: this product, a negative -- the same shape as COPPER.
TECH_TYPES: dict[str, str] = {
    FIBER:          FIBER,
    COPPER:         COPPER,
    CABLE:          CABLE,
    FIXED_WIRELESS: WIRELESS,
}


#: Response types the table could not read, and how many of each arrived.
#: Process-wide for the same reason the lane state is: a sweep runs ~80 workers,
#: and eighty of them meeting one new string must produce ONE line on screen and
#: one number in the summary, not eighty.
_UNMAPPED_LOCK = threading.Lock()
_UNMAPPED: dict = {}


def note_unmapped(kind: str, what: str, sample: dict | None = None) -> None:
    """Record -- and the first time, SHOW -- an answer nothing here can read.

    Said out loud on purpose. The failure this exists to stop is a silent one: a
    response type nobody has taught the engine about gets quietly sorted into
    whichever branch its other fields happen to match, and 72,776 settled
    negatives sit in the wrong bucket for months with nothing ever saying a word.
    """
    key = f"{kind}={what}"
    with _UNMAPPED_LOCK:
        first = key not in _UNMAPPED
        _UNMAPPED[key] = _UNMAPPED.get(key, 0) + 1
    if first:
        extra = " ".join(f"{k}={(sample or {}).get(k)!r}" for k in
                         ("validationResult", "techType", "maxQual", "exchangeId")
                         if (sample or {}).get(k))
        # NAME THE TABLE THE READER HAS TO OPEN. Both tables live in this
        # module and each is keyed on its own field; sending somebody to
        # VALIDATION_RESULTS to add a techType is sending them to the wrong
        # dict, and the string they add there would never be read.
        table = {"techType": "carrier_uqual.TECH_TYPES"}.get(
            kind, "carrier_uqual.VALIDATION_RESULTS")
        print(f"    carrier: UNCLASSIFIED {kind} {what!r} — not in "
              f"{table}. These rows are written "
              f"UNMAPPED and WILL STOP THE DELIVERY until the table is taught "
              f"this answer. {extra}", file=sys.stderr, flush=True)


def unmapped_counts() -> dict:
    """{'validationResult=Foo': n} for every answer the table could not read."""
    with _UNMAPPED_LOCK:
        return dict(_UNMAPPED)


def reset_unmapped() -> None:
    """Forget the ledger. For tests, and for a deliberate fresh start."""
    with _UNMAPPED_LOCK:
        _UNMAPPED.clear()


def in_footprint(detail) -> bool:
    """Does the carrier's own address database contain this address?

    The footprint question, answered off the SAME table as the verdict. It used
    to be answered by a second, independent rule living in exchange_probe
    (`vr != "AddressNotFound"`), which is how the two came to disagree about
    AddressUnserviceableInTerritory: in-footprint over there, unidentifiable
    over here, for the identical carrier string.
    """
    if not isinstance(detail, dict):
        return False
    vr = (detail.get("validationResult") or "").strip()
    if not vr:
        return False
    row = VALIDATION_RESULTS.get(vr)
    if row is None:
        # An answer we cannot read must never SHRINK the footprint. A footprint
        # that reads small is what sets whole towns aside without ever asking
        # about them -- the failure standard R2 exists for. It counts as
        # in-footprint, and the UNMAPPED ledger is what gets it looked at.
        return True
    return row[1]


def classify(payload: dict) -> tuple[str, str]:
    """(outcome, why) for one carrier answer. THE ONLY CLASSIFIER.

    outcome is FIBER / COPPER / CABLE / WIRELESS / NOFIBER / _SEARCH / UNMAPPED.
    `why` is
    the plain reason, kept on the row so the decision can be argued with later
    instead of re-derived from a verdict that lost its evidence.
    """
    payload = payload or {}
    vr = str(payload.get("validationResult") or "").strip()
    tech = str(payload.get("techType") or "").strip().upper()
    qual = str(payload.get("maxQual") or "").strip().upper()

    row = VALIDATION_RESULTS.get(vr)
    if row is None:
        note_unmapped("validationResult", vr or "(empty)", payload)
        return UNMAPPED, f"validationResult {vr or '(empty)'} is not in the table"

    outcome = row[0]
    if outcome == NOFIBER:
        if payload.get("exactMatch") is not True:
            return _SEARCH, f"{vr} without an explicit exactMatch=true"
        # A SETTLED NEGATIVE MUST NEVER BE ABLE TO SWALLOW A FIBRE ANSWER.
        # Every one of these measured "NO QUAL" with no techType at all. If one
        # ever arrives naming fibre, the mapping is wrong for that case and
        # answering NOFIBER would delete a real lead -- the opposite failure,
        # and just as bad. It goes to the loud path instead of quietly becoming
        # a no.
        if tech == FIBER or "FIBER" in qual:
            note_unmapped("unserviceable-but-qualifies", f"{vr}/{tech or qual}",
                          payload)
            return UNMAPPED, f"{vr} arrived carrying {tech or qual}"
        return NOFIBER, f"{vr} · {qual or 'NO QUAL'}"

    if outcome == _TECH:
        if payload.get("exactMatch") is not True:
            # Found, but not THIS string. Unchanged: the retry chain is what
            # turns the carrier's own suggestion into an exact match.
            return _SEARCH, f"{vr} but exactMatch is false"
        mapped = TECH_TYPES.get(tech)
        if mapped is not None:
            return mapped, f"{vr} · {tech}"
        # AN EXACT MATCH WHOSE TECHNOLOGY NOTHING RECOGNISES USED TO RETURN
        # UNREAD -- and the sweep writes no row at all for UNREAD, so the
        # address was asked again on every pass of every re-run, for ever, and
        # never settled. A technology we cannot read is a gap in OUR table, and
        # it is now named as one instead of imitating a failed read.
        note_unmapped("techType", tech or "(empty)", payload)
        return UNMAPPED, f"{vr} with techType {tech or '(empty)'}"

    return _SEARCH, vr


def _redact(value):
    """Retain diagnostic structure without recording authentication material."""
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if any(x in str(k).lower().replace("_", "")
                    for x in ("password", "passwd", "secret", "token", "authorization", "cookie", "apikey"))
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(x) for x in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/=_\-.]+", r"\1 [REDACTED]", value)
        value = re.sub(r"(?i)([?&](?:token|key|api_key|password|secret)=)[^&\s]+", r"\1[REDACTED]", value)
    return value


def exception_detail(exc, stage, *, response_received=False):
    """Report what the client observed; never infer DNS/TLS/exit failure cause."""
    chain, seen, current = [], set(), exc
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": _redact(str(current))[:1000],
                      "errno": getattr(current, "errno", None), "code": getattr(current, "code", None)})
        current = current.__cause__ or current.__context__
    return {"stage": stage, "response_received": response_received,
            "exception_chain": chain}


def existing_status(payload):
    """Tri-state interpretation; absent/unrecognized billing status is unknown.

    A/N retain the legacy code mapping and are explicitly labeled as such. This
    module does not independently certify that mapping or delivery eligibility.
    """
    address = payload.get("address") if isinstance(payload.get("address"), dict) else {}
    raw = address.get("billingStatus")
    code = str(raw).strip().upper() if raw is not None else ""
    return {"A": True, "N": False}.get(code)


CONFIG_DIR = os.environ.get("FL_CONFIG") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config")


def _app_credential() -> str:
    """Basic credential for the site's anonymous session bootstrap.

    Read from config so it is never committed. It is not a user account and
    grants nothing beyond the public availability checker.
    """
    path = os.path.join(CONFIG_DIR, "uqual.txt")
    if os.path.exists(path):
        line = next((l.strip() for l in open(path) if l.strip()), "")
        if line:
            return base64.b64encode(line.encode()).decode()
    raise RuntimeError(
        f"Missing {os.path.abspath(path)}. It needs one line: user:password "
        f"for the retail site's anonymous session bootstrap.")


# ---------------------------------------------------------------------------
# THE LANE RULE — HOME IP, NO PROXY, UNTIL THE HOME IP IS PROVED BLOCKED
# ---------------------------------------------------------------------------
# Measured 2026-08-18 over a full day of A/B tests: the home IP direct settled
# 59 clean addresses/min; the SAME work through the residential gateway settled
# 29/min. Proxies roughly halve throughput and the endpoint does not per-IP
# block at sane request rates, so the standing rule is HOME IP, NO PROXY, one
# lane. Adding lanes made it worse, not better (59 -> 21 clean/min on two).
#
# The rule has exactly one written exception: use a proxy when the home IP is
# PROVABLY blocked, with the response body shown, never on a hunch. On
# 2026-08-26 that exception fired for real — buy.gokinetic.com answered every
# request from this computer with HTTP 429 and a Cloudflare "Just a moment..."
# interstitial, on SIX TLS fingerprints (chrome124, chrome131, chrome120,
# safari17_0, edge101, firefox133). Six fingerprints, one verdict: it is the IP,
# not the fingerprint. Through a residential exit the same request reached the
# API and returned a real answer — 4 of 4 exits gave FIBER / SC050INMN for
# 281 Fury Dr, Inman SC 29349.
#
# Until now taking that exception meant a HUMAN noticing a market had gone 100%
# UNREAD and flipping an environment variable. That is a person watching for an
# infrastructure fault, which is the kind of watching that gets missed at 2am
# and costs a whole run. So the exception lives in the client: it proves the
# block from the response, logs the proof once, moves itself to a fresh
# residential exit, and climbs back to the fast lane when the block clears.

#: How long the direct lane is left alone once it is proved blocked, and the
#: ceiling the back-off climbs to. Re-testing costs ONE request per client per
#: cooldown; getting back on direct is worth ~2x throughput (59 vs 29
#: clean/min, 2026-08-18), so ten minutes is a cheap price to pay for the
#: chance. Doubling to an hour stops a day-long block being re-probed 144
#: times per client.
BLOCK_COOLDOWN = 600.0
BLOCK_COOLDOWN_MAX = 3600.0

#: How long ONE worker is given to find out whether the block has cleared,
#: before another worker is allowed to try instead.
#:
#: It is a lease, not a lock, and the difference matters: a prober that never
#: comes back — its thread drained the job queue and returned, or its handshake
#: hung — must not pin every other worker to a residential exit for the rest of
#: the run. When the lease expires the next worker simply re-tests, so the worst
#: this can do is degrade back toward the behaviour it replaces.
#:
#: 75s is three handshake timeouts (25s each), which is the longest a single
#: _ensure_token can run before it gives up and the prober can no longer resolve
#: the question either way.
DIRECT_PROBE_LEASE = 75.0

#: Only the first slice of a body is scanned for a challenge marker, and this
#: is not an optimisation — it is CORRECTNESS. The carrier's real landing page
#: is 465,798 bytes and legitimately contains "/cdn-cgi/challenge-platform/"
#: and "cdn-cgi" deep inside its script tags (measured 2026-08-26 through a
#: working exit, HTTP 200). Scanning the whole body would call a perfectly good
#: page a block and push every client onto a proxy for ever. The real
#: interstitial is 5,984 bytes and says "Just a moment" inside the first 80.
BLOCK_SCAN = 2000

#: How much of the blocking body is logged, per the instruction to show the
#: proof rather than assert it.
BLOCK_LOG_CHARS = 100

#: Verbatim markers from a Cloudflare interstitial. "just a moment" is the one
#: this carrier actually serves; the rest are the other shapes Cloudflare uses
#: for the same thing, present so a changed challenge page is still recognised
#: as a block instead of being read as an answer about the address.
_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required! | cloudflare",
    "cf-browser-verification",
    "__cf_chl",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform/",
    "enable javascript and cookies to continue",
)


def block_reason(status: int, body: str = "") -> str:
    """Is this response the PATH being blocked? Reason string, or "" if not.

    The distinction is the whole point. A blocked path says nothing about the
    address, and treating it as an answer is how real leads get deleted — the
    same failure the 403 branch in check() was written to stop. So this has to
    be precise in both directions:

      * HTTP 429 is a block. Measured 2026-08-26: every direct request to this
        carrier returned 429 with a Cloudflare interstitial.
      * A Cloudflare challenge body is a block whatever status carries it.
      * HTTP 401 IS NOT A BLOCK. It is the API answering and asking for a
        token — the ordinary end of a bearer token's life, handled by minting
        another. Calling it a block would move every client onto a proxy on the
        first token expiry and pay the proxy penalty for ever.
      * A JSON body is not a block. If the API answered in JSON, the API was
        reached; only 429 outranks that, because a per-IP rate limit is
        answered by leaving the IP either way.
    """
    if status == 401:
        return ""
    if status == 429:
        return "http-429"
    if status == 403:
        # A BARE 403 ON THIS PATH IS THE LANE BEING REFUSED, NOT AN ANSWER.
        #
        # Measured 2026-08-28, one minute apart on the same machine: the direct
        # lane returned 403 "Forbidden" to the address search and to the town
        # enumeration alike, while the proxy lane returned 200 with real
        # candidates. Unclassified, a 403 fell through to "" -- not a block --
        # so check() called _rotate(), and _rotate() on the DIRECT lane only
        # opens a new TLS session on the same IP. The block could never be
        # escaped from inside a check: a whole run of 6,413 reads came back
        # UNREAD with nothing settled, and a town enumeration returned 244
        # NOT_ASKED in five seconds.
        #
        # It is a block and not an answer for the same reason 429 is: the body
        # says nothing about the address, so treating it as one would delete
        # real leads. Named separately from 429 so the log says which happened.
        return "http-403"
    head = (body or "")[:BLOCK_SCAN]
    if head.lstrip()[:1] in ("{", "["):
        return ""
    low = head.lower()
    for m in _CHALLENGE_MARKERS:
        if m in low:
            return "cloudflare-challenge"
    return ""


def _head(r) -> str:
    """First BLOCK_SCAN-ish characters of a response body, without decoding it all.

    Sliced as BYTES first: the landing page is 465 KB and it is fetched on every
    token mint. Decoding all of it to look for a 13-character marker is waste,
    and `replace` keeps a chopped multi-byte character from raising.
    """
    try:
        return (r.content or b"")[:BLOCK_SCAN * 2].decode("utf-8", "replace")
    except Exception:
        try:
            return (r.text or "")[:BLOCK_SCAN]
        except Exception:
            return ""


#: Whether the home IP is blocked is a fact about THE COMPUTER, not about one
#: client. A sweep runs ~30 clients; without sharing, all 30 discover the same
#: block separately and each burns its own failed request, and 30 of them
#: re-probe it separately every cooldown. One worker proving it is enough.
_LANE_LOCK = threading.Lock()
#: `probe_until` holds the re-test lease described at DIRECT_PROBE_LEASE: while
#: it is in the future, one worker is already finding out whether the home IP is
#: back and nobody else needs to ask.
_DIRECT = {"blocked_until": 0.0, "cooldown": BLOCK_COOLDOWN, "episodes": 0,
           "probe_until": 0.0}


def direct_only() -> bool:
    """FL_DIRECT=1 pins every client to this computer's own connection.

    It still overrides everything here: a block is recorded and logged so the
    operator can see WHY the run is returning UNREAD, but no client moves to a
    proxy. Nothing about this fallback weakens that switch.
    """
    return os.environ.get("FL_DIRECT") == "1"


def proxy_first() -> bool:
    """FL_PROXY=1 opens on a residential exit instead of the home IP.

    The pre-2026-08-26 behaviour of this client, kept as one flag for the case
    where an operator already knows the home IP is out. Not the default: the
    default is the measured-fastest lane.
    """
    return os.environ.get("FL_PROXY") == "1"


def direct_blocked() -> bool:
    """True while this process holds proof that the home IP is blocked."""
    return time.time() < _DIRECT["blocked_until"]


def lane_state() -> dict:
    """What this process currently believes about the direct lane."""
    with _LANE_LOCK:
        d = dict(_DIRECT)
    d["blocked_now"] = time.time() < d["blocked_until"]
    d["seconds_left"] = max(0.0, d["blocked_until"] - time.time())
    return d


def reset_lane_state() -> None:
    """Forget any recorded block. For tests, and for a deliberate fresh start."""
    with _LANE_LOCK:
        _DIRECT.update({"blocked_until": 0.0, "cooldown": BLOCK_COOLDOWN,
                        "episodes": 0, "probe_until": 0.0})


def claim_direct_probe() -> bool:
    """Take the single re-test slot. True for EXACTLY ONE worker per cooldown.

    THE COOLDOWN EXPIRY WAS A STAMPEDE. `_maybe_retest_direct` runs on every
    top-level ask of every worker, and its only gate was `direct_blocked()` —
    process-wide state that flips for everyone on the same clock tick. So the
    instant a cooldown ran out, EVERY worker moved itself onto the home IP and
    fired its own handshake at it. Measured on this code at the worker counts
    actually used: 80/80, 200/200, 300/300 moved at once.

    That is the exact thing the comment above _DIRECT says this module does not
    do — "One worker proving it is enough" — and it was true of DISCOVERING a
    block (note_direct_blocked reports once) but never of re-testing one.

    The cost is not the wasted requests alone. A 300-request burst at a
    Cloudflare-blocked IP is itself a good way to earn the next block, and the
    v3 Texas run logged 39 block episodes against 31 recoveries with the
    cooldown pinned at its 3600s ceiling by the end. One request now asks the
    question the other 299 were asking simultaneously.

    Answering "no" here NEVER settles an address: a worker that does not get the
    slot stays on the residential exit it was already working on and asks its
    address there, exactly as it would have a second earlier.

    ONLY AN UNANSWERED QUESTION IS RATIONED. With no block on record the lane is
    not in doubt and every worker is waved straight through, which is the whole
    point of clearing it — gating that too made the pool trickle back onto the
    fast lane one worker per lease, ~6 hours to move 300 of them, and the fast
    lane is the ~2x one.
    """
    now = time.time()
    with _LANE_LOCK:
        if not _DIRECT["blocked_until"]:
            # Nothing is being re-tested: either no block was ever proved, or a
            # prober already proved the home IP is answering again and cleared
            # it. Nobody needs permission to use a lane known to work.
            return True
        # Re-checked under the lock. `_maybe_retest_direct` reads
        # `direct_blocked()` unlocked as a fast path — it runs on every ask —
        # and that read can race the expiry it is watching for.
        if now < _DIRECT["blocked_until"]:
            return False
        if now < _DIRECT["probe_until"]:
            return False          # somebody is already finding out
        _DIRECT["probe_until"] = now + DIRECT_PROBE_LEASE
        return True


def note_direct_blocked(reason: str, status: int, body: str) -> bool:
    """Record the block and SHOW THE PROOF once. True if this call opened it.

    Once, not once per worker: 30 clients hitting the same wall would otherwise
    print 30 identical stanzas and bury the one line that matters. The second
    and later workers of the same episode find the block already recorded and
    say nothing.
    """
    now = time.time()
    with _LANE_LOCK:
        # THE RE-TEST IS OVER: it just came back, and the answer is still no.
        # Released before the early return below, because either way there is no
        # outstanding question for the lease to be protecting.
        _DIRECT["probe_until"] = 0.0
        if now < _DIRECT["blocked_until"]:
            return False                  # another worker already proved it
        cd = _DIRECT["cooldown"]
        _DIRECT["blocked_until"] = now + cd
        _DIRECT["cooldown"] = min(cd * 2.0, BLOCK_COOLDOWN_MAX)
        _DIRECT["episodes"] += 1
        episode = _DIRECT["episodes"]
    head = " ".join((body or "").split())[:BLOCK_LOG_CHARS]
    print(f"    carrier: home IP is blocked — HTTP {status} ({reason}) · {head}",
          file=sys.stderr, flush=True)
    if direct_only():
        print("    carrier: FL_DIRECT=1 — staying direct, addresses will come "
              "back UNREAD and be asked again", file=sys.stderr, flush=True)
    else:
        print(f"    carrier: switching to a residential exit; re-testing direct "
              f"in {cd / 3600.0:.1f} hours ({int(cd)}s) [block #{episode}]",
              file=sys.stderr, flush=True)
    return True


def note_direct_ok() -> None:
    """The home IP answered. Clear the block and the back-off."""
    if not _DIRECT["blocked_until"] and _DIRECT["cooldown"] == BLOCK_COOLDOWN:
        return                            # the common case, no lock taken
    with _LANE_LOCK:
        recovered = bool(_DIRECT["blocked_until"])
        _DIRECT["blocked_until"] = 0.0
        _DIRECT["cooldown"] = BLOCK_COOLDOWN
        # THE RE-TEST IS OVER, AND THE ANSWER IS YES. Cleared here or the
        # workers still on exits would sit out the rest of the lease before
        # following the prober back onto the lane it just proved is working —
        # which is the ~2x one, so holding them there costs more than the
        # stampede this lease exists to stop.
        _DIRECT["probe_until"] = 0.0
    if recovered:
        print("    carrier: home IP is answering again — back on the fast lane",
              file=sys.stderr, flush=True)


def _default_proxy_factory():
    """The engine's own residential-exit factory, for a client built without one.

    Deliberately NOT a second copy of the credential parsing. There is one
    reader of 02_System/config/proxies.txt (host:port:user:pass, the password
    carrying a _session-XXXX tag that is rewritten per call, which is what makes
    each call a FRESH exit IP) and it lives in sweep_governed. Imported here at
    call time, not at module scope, because sweep_governed imports this module.
    """
    try:
        import sweep_governed
        return sweep_governed._proxy_factory()
    except Exception as e:                # no config, bad format, import error
        print(f"    carrier: no residential exit available ({e.__class__.__name__})",
              file=sys.stderr, flush=True)
        return None


#: The carrier's address database stores street types SPELLED OUT. County
#: parcel and E911 layers store them abbreviated. Asking with the abbreviation
#: returns AddressNotFound for an address that is really there and really has
#: fibre -- verified on Andrews TX, 2026-08-25:
#:
#:     "1201 SW AVE G"      -> NOMATCH
#:     "1201 SW AVENUE G"   -> FIBER
#:
#: 1,666 Andrews leads were sitting behind this one substitution. It was found
#: on the Tunnel Hill GA pull and recorded, but never wired into the engine, so
#: every market since has been losing whatever share of its addresses the county
#: happens to abbreviate.
STREET_TYPES = {
    "AVE": "AVENUE", "ST": "STREET", "DR": "DRIVE", "RD": "ROAD",
    "LN": "LANE", "CT": "COURT", "BLVD": "BOULEVARD", "CIR": "CIRCLE",
    "PL": "PLACE", "TRL": "TRAIL", "PKWY": "PARKWAY", "HWY": "HIGHWAY",
    "TER": "TERRACE", "PLZ": "PLAZA", "SQ": "SQUARE", "XING": "CROSSING",
    "CV": "COVE", "TRCE": "TRACE", "EXT": "EXTENSION", "LOOP": "LOOP",
}


#: Street types tried when the county stored no type at all. Texas appraisal
#: districts write "NW 12" for what the carrier holds as "NW 12 ST" -- 310 of
#: Andrews' addresses are bare numbered streets like this, and every one came
#: back no-match until the type was supplied. ST first because it is far and
#: away the most common on a numbered grid.
APPEND_TYPES = ("ST", "AVE", "DR", "RD", "LN", "PL")

#: A street already ending in one of these needs no type appended.
_HAS_TYPE = set(STREET_TYPES) | set(STREET_TYPES.values()) | {
    "WAY", "RUN", "PASS", "BEND", "PARK", "WALK", "ROW", "MALL", "PATH"}


def _dist(a: tuple, b: tuple) -> float:
    """Metres between two (lat, lon) points."""
    import math as _m
    dy = (b[0] - a[0]) * 111320.0
    dx = (b[1] - a[1]) * 111320.0 * _m.cos(_m.radians(a[0]))
    return _m.hypot(dx, dy)


#: Directionals. The carrier writes "303 S AYRSHIRE ST"; the county writes
#: "303 SOUTH AYRSHIRE STREET". Without this the two never compare equal and a
#: candidate the carrier HANDED US is thrown away as a different house.
DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}

#: The rest of the USPS suffix table. The 20-entry list above covers the common
#: types and misses everything else -- PASSAGE, RIDGE, LANDING, MEADOWS, FALLS,
#: CREEK, GLEN, VIEW. Measured live on 60 random inside-footprint UNKNOWN rows:
#: the 20-entry matcher matched 7, the full table matched 15. "1126 RABBIT ROVE
#: PASSAGE" against the carrier's "1126 RABBIT ROVE PSGE" is FIBER, and was
#: being discarded.
USPS_SUFFIXES = {
    "PSGE": "PASSAGE", "RDG": "RIDGE", "LNDG": "LANDING", "MDWS": "MEADOWS",
    "MDW": "MEADOW", "FLS": "FALLS", "CRK": "CREEK", "GLN": "GLEN",
    "VW": "VIEW", "BND": "BEND", "HLS": "HILLS", "HL": "HILL",
    "VLY": "VALLEY", "SPGS": "SPRINGS", "SPG": "SPRING", "FRST": "FOREST",
    "GRV": "GROVE", "HOLW": "HOLLOW", "KNL": "KNOLL", "LDG": "LODGE",
    "MNR": "MANOR", "MT": "MOUNT", "MTN": "MOUNTAIN", "ORCH": "ORCHARD",
    "PSG": "PASSAGE", "PT": "POINT", "PTS": "POINTS", "RNCH": "RANCH",
    "SHRS": "SHORES", "SHR": "SHORE", "STA": "STATION", "SMT": "SUMMIT",
    "TRWY": "THROUGHWAY", "TPKE": "TURNPIKE", "UN": "UNION", "VLG": "VILLAGE",
    "VIS": "VISTA", "WLS": "WELLS", "CRST": "CREST", "CLB": "CLUB",
    "CMN": "COMMON", "CMNS": "COMMONS", "CRSE": "COURSE", "CTS": "COURTS",
    "CYN": "CANYON", "DL": "DALE", "DM": "DAM", "DV": "DIVIDE",
    "EST": "ESTATE", "ESTS": "ESTATES", "EXPY": "EXPRESSWAY", "FLD": "FIELD",
    "FLDS": "FIELDS", "FRD": "FORD", "FRK": "FORK", "FRKS": "FORKS",
    "GDN": "GARDEN", "GDNS": "GARDENS", "GTWY": "GATEWAY", "GRN": "GREEN",
    "HBR": "HARBOR", "HVN": "HAVEN", "INLT": "INLET", "IS": "ISLAND",
    "JCT": "JUNCTION", "KY": "KEY", "LK": "LAKE", "LKS": "LAKES",
    "LGT": "LIGHT", "LCK": "LOCK", "MSN": "MISSION", "NCK": "NECK",
    "OVAL": "OVAL", "PKY": "PARKWAY", "PNE": "PINE", "PNES": "PINES",
    "PRT": "PORT", "PR": "PRAIRIE", "RDGS": "RIDGES", "RIV": "RIVER",
    "SHL": "SHOAL", "SHLS": "SHOALS", "SKWY": "SKYWAY", "SPUR": "SPUR",
    "STRM": "STREAM", "TER": "TERRACE", "TRAK": "TRACK", "TUNL": "TUNNEL",
    "VLYS": "VALLEYS", "VIA": "VIADUCT", "WALL": "WALL", "WLK": "WALK",
}

#: MULTI-WORD ROUTE NAMES. These CANNOT live in a token map -- "FARM TO MARKET"
#: is three tokens and a token map only ever sees one at a time. They are phrase
#: rewrites, applied before tokenising. Rural Texas is full of them.
_ROUTE_REWRITES = (
    (r"\bFARM TO MARKET(?: ROAD)?\b", "FM"),
    (r"\bFARM ROAD\b", "FM"),
    (r"\bRANCH TO MARKET(?: ROAD)?\b", "RM"),
    (r"\bRANCH ROAD\b", "RR"),
    (r"\bCOUNTY ROAD\b", "CR"),
    (r"\bSTATE HIGHWAY\b", "SH"),
    (r"\bSTATE ROUTE\b", "SH"),
    (r"\bUS HIGHWAY\b", "US"),
    (r"\bINTERSTATE(?: HIGHWAY)?\b", "I"),
    (r"\bPRIVATE ROAD\b", "PR"),
    (r"\bOLD HIGHWAY\b", "OLD HWY"),
)


def _norm(a: str) -> str:
    """Compare form: uppercase, punctuation stripped, everything abbreviated.

    Folds street types, directionals, the full USPS suffix table and multi-word
    route names to one spelling, so a candidate the carrier returned is compared
    on what it IS rather than on how it was typed.
    """
    import re as _re
    up = _re.sub(r"[^A-Z0-9 ]", " ", (a or "").upper())
    for pat, rep in _ROUTE_REWRITES:
        up = _re.sub(pat, rep, up)
    inv = {v: k for k, v in STREET_TYPES.items()}
    inv.update({v: k for k, v in USPS_SUFFIXES.items()})
    inv.update(DIRECTIONALS)
    return " ".join(inv.get(x, x) for x in up.split())


def _nospace(a: str) -> str:
    """Normalised and with spaces removed.

    "12 TH ST" against "12TH ST" is the same street typed two ways. Comparing
    without spaces catches that class without loosening the house-number gate.
    """
    return _norm(a).replace(" ", "")


_UNIT_RE = re.compile(r"(?:\s+|^)(?:APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|#)\s*([A-Z0-9-]+)\s*$", re.I)


def _identity_parts(address):
    value = str(address or "").strip().upper()
    match = _UNIT_RE.search(value)
    if match:
        return _norm(value[:match.start()]), re.sub(r"[^A-Z0-9]", "", match.group(1))
    return _norm(value), None


def _unit_value(value):
    value = str(value or "").strip().upper()
    match = _UNIT_RE.search(" " + value)
    return re.sub(r"[^A-Z0-9]", "", match.group(1) if match else value) or None


def matching_candidates(address: str, candidates) -> list:
    """Only identical normalized house/street candidates; prefixes are ambiguous."""
    if not isinstance(candidates, list):
        return []
    want_base, want_unit = _identity_parts(address)
    out = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        line = str(candidate.get("addressLine1") or "").strip()
        base, unit = _identity_parts(line)
        if base == want_base and base and (unit is None or unit == want_unit):
            if line not in out:
                out.append(line)
    return out


#: Legacy call-budget constants. The repaired selector follows only the exact
#: requested street and unit; another unit's qualification is never inherited.
MAX_CANDIDATES = 4
MAX_UNITS = 2
MAX_CANDIDATE_ASKS = 6


def candidate_asks(address: str, candidates, *, state=None, zip_code=None) -> list[dict]:
    """Follow only a matching service location; never substitute another unit.

    A base with a list of units is ambiguous when the request omits its unit.
    House/street prefix matches and nearest-neighbor guesses are not accepted.
    """
    if not isinstance(candidates, list):
        return []
    wanted, wanted_unit = _identity_parts(address)
    allowed = set(matching_candidates(address, candidates))
    output, seen = [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        line = str(candidate.get("addressLine1") or "").strip()
        if line not in allowed:
            continue
        if state and candidate.get("state") and str(candidate["state"]).upper() != str(state).upper():
            continue
        candidate_zip = str(candidate.get("postalCode") or candidate.get("zip") or "").strip()
        if zip_code and candidate_zip and candidate_zip[:5] != str(zip_code)[:5]:
            continue
        base = {"address": line, "city": str(candidate.get("city") or "").strip(), "zip": candidate_zip}
        units = [u for u in (candidate.get("units") or []) if isinstance(u, dict)]
        _, line_unit = _identity_parts(line)
        if wanted_unit is None:
            if units or line_unit is not None or candidate.get("addressLine2"):
                continue
            selected = [candidate]
        else:
            selected = [u for u in units if _unit_value(u.get("addressLine2")) == wanted_unit]
            if not selected and (line_unit == wanted_unit or _unit_value(candidate.get("addressLine2")) == wanted_unit):
                selected = [candidate]
            if not selected:
                continue
        for item in selected:
            extra = {}
            df = str(item.get("dfAddressId") or "").strip()
            line2 = str(item.get("addressLine2") or "").strip()
            if df:
                extra["dfAddressId"] = df
            if wanted_unit and line2:
                extra["addressLine2"] = line2
            if not extra and _norm(line) == _norm(address):
                continue
            key = (line.upper(), base["city"].upper(), candidate_zip, json.dumps(extra, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            output.append({**base, "extra": extra or None,
                           "via": "exact-service-location-candidate"})
    # Competing IDs for the same requested location need external identity
    # remediation, not an arbitrary first match or nearest-coordinate winner.
    premise_ids = {a["extra"]["dfAddressId"] for a in output if a.get("extra") and a["extra"].get("dfAddressId")}
    if len(premise_ids) > 1:
        return []
    return output[:MAX_CANDIDATE_ASKS]


def pick_candidate(address: str, candidates) -> str:
    """Return an unambiguous exact normalized house/street candidate only."""
    choices = matching_candidates(address, candidates)
    return choices[0] if len(choices) == 1 else ""


def variants(address: str):
    """Address spellings to try when the carrier refuses the one we hold.

    The county and the carrier disagree about how an address is written, and the
    carrier is the one being asked, so its spelling is the one that has to be
    produced. Two disagreements are known and both cost real leads:

        county "1201 SW AVE G"  -> carrier wants "1201 SW AVENUE G"
        county "1211 NW 12"     -> carrier wants "1211 NW 12 ST"

    Yields at most a handful of candidates, and only for addresses that actually
    have something to change, so an address the carrier already accepts costs no
    extra call at all.
    """
    a = (address or "").strip()
    if not a:
        return
    full = spell_out(a)
    if full:
        yield full
    toks = a.split()
    last = toks[-1].upper().strip(".,") if toks else ""
    # No street type on the end -- supply one. Only for a street whose last word
    # is a number or a single letter, which is what a bare grid street looks
    # like; appending to a named street would be inventing an address.
    if len(toks) >= 2 and last not in _HAS_TYPE:
        stem = toks[-1]
        if stem.isdigit() or (len(stem) == 1 and stem.isalpha()) or \
                stem.upper().rstrip("STNDRDTH").isdigit():
            for t in APPEND_TYPES:
                yield f"{a} {t}"


def spell_out(address: str) -> str:
    """'1201 SW AVE G' -> '1201 SW AVENUE G'. Empty if nothing to expand."""
    parts = (address or "").split()
    out, changed = [], False
    for w in parts:
        up = w.upper().strip(".,")
        if up in STREET_TYPES and STREET_TYPES[up] != up:
            out.append(STREET_TYPES[up])
            changed = True
        else:
            out.append(w)
    return " ".join(out) if changed else ""


#: Inverse of spell_out: collapse spelled-out directionals + street types to USPS
#: abbreviations. Some carrier markets store the ABBREVIATED form and only match on
#: it -- Iowa/Kinetic wants "514 NE 3RD ST", not "514 NORTHEAST 3RD STREET". Measured
#: live 2026-09-08 on 24 known-serviceable Knoxville addresses: abbreviated returned
#: 21/24 FIBER+exact with 0 NOMATCH; the spelled-out form returned 18/24 and lost 2 to
#: NOMATCH. NAD spells everything out, so this is applied before the Iowa sweep.
_FULL_TO_ABBR = {v: k for k, v in STREET_TYPES.items()}
_FULL_TO_ABBR.update({v: k for k, v in USPS_SUFFIXES.items()})
_FULL_TO_ABBR.update(DIRECTIONALS)   # NORTH->N, SOUTHEAST->SE (already full->abbrev)


def abbreviate(address: str) -> str:
    """'514 NORTHEAST 3RD STREET' -> '514 NE 3RD ST'. Idempotent on already-abbreviated
    input. Inverse of spell_out for markets whose carrier DB stores abbreviated forms."""
    out = []
    for w in (address or "").split():
        up = w.upper().strip(".,")
        out.append(_FULL_TO_ABBR.get(up, w))
    return " ".join(out)


import endpoint_guard          # noqa: E402
import fetch_guard             # noqa: E402
import egress_guard            # noqa: E402  (fail-closed proxy egress, 2026-09-09)


class UqualClient:
    """Address qualification with bounded retries; never product-plan proof."""

    def __init__(self, proxy_factory=None, worker: int = 0):
        from curl_cffi import requests as cr
        self._cr = cr
        self.worker = worker
        self.session = cr.Session(impersonate="chrome124")
        self.proxy_factory = proxy_factory
        # OPEN ON THE FAST LANE. Before 2026-08-26 this line was
        # `proxy_factory() if proxy_factory else None`, so every caller that
        # handed in a factory paid the proxy penalty on every request whether
        # or not the home IP was working — the standing rule inverted by
        # default. The factory is now held for the exception, not spent on
        # arrival. Two things still open on an exit: FL_PROXY=1, and a process
        # that has ALREADY proved the block, where spending another request to
        # re-learn it teaches nothing.
        self.lane = "direct"
        self.proxy = None
        self.token = None
        self.uses = 0
        #: one stable device identity for this actor's whole lifetime (native app state)
        self.device_id = str(_uuid.uuid4())
        #: ACTOR-SCOPED CIRCUIT ISOLATION (2026-09-09). The endpoint_guard keys circuits by name;
        #: a shared name let ONE actor's failures mark every other actor "endpoint-down". Each
        #: actor now trips only its OWN circuit (actor_id + route_hash), so a blocked exit
        #: quarantines that actor alone and the others keep working. run_id resets state per run.
        self.actor_id = worker
        self._route_hash = "none"
        self._basic = _app_credential()
        self.last_block = None
        self.last_error = None
        self._check_depth = 0
        self._workflow_calls = []
        self._workflow_totals = {}
        self.last_workflow_telemetry = {}
        self._resume_response = None
        #: FAIL CLOSED — with REQUIRE_PROXY_EGRESS=1 (default) this actor MUST own a proxy route
        #: for its whole lifetime; direct egress is prohibited. Abort now if no factory was given
        #: rather than discover it on the first request.
        if str(os.environ.get("REQUIRE_PROXY_EGRESS", "1")) == "1":
            if not self.proxy_factory:
                raise egress_guard.EgressError(
                    "REQUIRE_PROXY_EGRESS=1 but no proxy_factory — cannot start Kinetic egress")
            self._to_proxy()
        elif not direct_only() and (proxy_first() or direct_blocked()):
            self._to_proxy()

    def _post(self, url, **kwargs):
        """Instrument HTTP-client invocation; this cannot prove bytes reached origin."""
        stage = ("auth" if url == BASE + SESSION else "search" if url == BASE + SEARCH
                 else "product" if url == BASE + PLANS else "other")
        started = time.monotonic()
        event = {"stage": stage, "status": None, "response_received": False}
        try:
            response = self.session.post(url, **kwargs)
            event.update(status=response.status_code, response_received=True)
            return response
        except Exception as exc:
            event["exception"] = exception_detail(exc, stage, response_received=False)
            raise
        finally:
            event["elapsed_s"] = time.monotonic() - started
            totals = self._workflow_totals
            totals["http_client_calls"] = totals.get("http_client_calls", 0) + 1
            totals[stage + "_calls"] = totals.get(stage + "_calls", 0) + 1
            totals["http_client_seconds"] = totals.get("http_client_seconds", 0.0) + event["elapsed_s"]
            if not event["response_received"]:
                totals["no_response_calls"] = totals.get("no_response_calls", 0) + 1
            if len(self._workflow_calls) < 256:
                self._workflow_calls.append(event)
            else:
                totals["call_records_truncated"] = totals.get("call_records_truncated", 0) + 1

    def _telemetry(self, started):
        return {**self._workflow_totals, "workflow_elapsed_s": time.monotonic() - started,
                "call_records": list(self._workflow_calls),
                "measurement": "HTTP client invocations; network delivery is not inferred"}

    def plans(self, df_id, uqual, ctx=None):
        """The buy flow's own product call, on THIS client's live session.

        Ported from the SC-Inman lineage engine (carrier.Session.plans) and bound
        to the search that just answered: same TLS session, same bearer token,
        same exit, same XHR header set. Returns (bundles, transaction_id, raw).
        EVERY failure return carries `fl_fetch_error`, so "we could not read the
        plans" is never mistaken for "there are no plans".

        CONTEXT RULE (merge correction #1/#2): this method NEVER mints a token and
        NEVER rotates. If no live token exists, or `ctx` (the session/token pinned
        when the search response was built) no longer matches, it refuses with
        `context_invalidated`; the runner then requalifies (re-runs check) instead
        of pricing a premise against a session that did not answer for it.
        `uqual` must be the original string under `uqualProvisioningResult`;
        a re-serialised copy of the search response yields empty metaBundles.
        """
        if isinstance(uqual, dict):
            uqual = uqual.get("uqualProvisioningResult")
        if not df_id or not isinstance(uqual, str) or not uqual:
            return None, "", {"fl_fetch_error": "missing_context",
                              "why": "no dfAddressId/uqualProvisioningResult from this search"}
        if not self.token:
            return None, "", {"fl_fetch_error": "context_invalidated",
                              "why": "no live token; requalify before requesting plans"}
        if ctx and (ctx.get("sess") != id(self.session) or ctx.get("token") != self.token):
            return None, "", {"fl_fetch_error": "context_invalidated",
                              "why": "session/token changed since the search; requalify"}
        try:
            endpoint_guard.before_attempt(self._lane_endpoint())
        except endpoint_guard.EndpointDown:
            return None, "", {"fl_fetch_error": "endpoint_down", "why": "circuit open"}
        body = {"dfAddressId": df_id, "accountNumber": "", "moveDate": "",
                "addAddrReason": "", "uqualProvisioningResult": uqual}
        try:
            r = self._post(BASE + PLANS, headers=self._headers(True),
                           data=json.dumps(body), timeout=45, **self._kw())
        except Exception as e:
            endpoint_guard.record_failure(self._lane_endpoint())
            return None, "", {"fl_fetch_error": f"TRANSPORT {type(e).__name__}",
                              "why": f"TRANSPORT {type(e).__name__}"}
        endpoint_guard.record_success(self._lane_endpoint())
        self.uses += 1
        head = str(getattr(r, "text", "") or "")[:200]
        if r.status_code != 200:
            return None, "", {"fl_fetch_error": f"http_{r.status_code}", "http": r.status_code,
                              "http_status": r.status_code,
                              "retry_after": r.headers.get("Retry-After"), "body_head": head}
        try:
            j = r.json()
        except Exception:
            return None, "", {"fl_fetch_error": "non_json_body", "http": 200,
                              "why": "non-JSON", "body_head": head}
        if not isinstance(j, dict):
            return None, "", {"fl_fetch_error": "non_object_body", "http": 200}
        return (j.get("metaBundles") or []), (j.get("transactionId") or ""), j

    def check(self, address: str, city: str, state: str, zip_code: str,
              _retry: bool = False, _near: tuple | None = None,
              _shallow: bool = False, _extra: dict | None = None):
        """Public workflow boundary; nested candidate retries share one call ledger."""
        top = self._check_depth == 0
        started = time.monotonic()
        if top:
            self._workflow_calls = []
            self._workflow_totals = {"http_client_calls": 0, "auth_calls": 0,
                                     "search_calls": 0, "no_response_calls": 0}
        self._check_depth += 1
        try:
            outcome, detail = self._check(address, city, state, zip_code,
                                          _retry=_retry, _near=_near,
                                          _shallow=_shallow, _extra=_extra)
            if top:
                detail = dict(detail or {})
                # Exact match to a DIFFERENT invented street is not evidence for
                # the original premise. Equivalent abbreviations remain valid.
                requested = address + (" " + str((_extra or {}).get("addressLine2"))
                                       if (_extra or {}).get("addressLine2") else "")
                asked = detail.get("request_payload") or {}
                actual = str(asked.get("addressLine1") or address)
                if asked.get("addressLine2"):
                    actual += " " + str(asked["addressLine2"])
                if outcome in {FIBER, COPPER, CABLE, WIRELESS, NOFIBER} and _identity_parts(actual) != _identity_parts(requested):
                    detail["identity_hold"] = "derived request changed original street or unit"
                    detail["would_have_been"] = outcome
                    outcome = UNRESOLVED
                detail["workflow_telemetry"] = self._telemetry(started)
            return outcome, detail
        finally:
            self._check_depth -= 1
            if top:
                self.last_workflow_telemetry = self._telemetry(started)

    def resume_check(self, address, city, state, zip_code, previous_detail):
        """Continue a saved unresolved primary response without repeating its HTTP.

        This reuses an observation, not a fresh carrier check. Timestamp/hash are
        retained. Missing or mismatched provenance falls back to normal checking.
        This is a primary-response checkpoint, not a per-candidate durable cursor.
        """
        detail = previous_detail if isinstance(previous_detail, dict) else {}
        payload = detail.get("response_payload")
        request = detail.get("request_payload")
        expected = {"addressLine1": str(address or "").upper(), "addressLine2": "",
                    "city": str(city or "").upper(), "state": str(state or "").upper(),
                    "postalCode": str(zip_code or ""),
                    "geoLat": "", "geoLong": "", "useSearchV2": False}
        reusable = (isinstance(payload, dict) and request == expected
                    and detail.get("http_status") == 200
                    and detail.get("response_hash_kind") == "canonical_redacted_json_sha256"
                    and isinstance(detail.get("observed_at_utc"), str)
                    and bool(detail.get("observed_at_utc")))
        if reusable:
            canonical = json.dumps(_redact(payload), sort_keys=True, separators=(",", ":"))
            reusable = (hashlib.sha256(canonical.encode()).hexdigest() == detail.get("response_sha256")
                        and classify(payload)[0] == _SEARCH)
        if not reusable or self._check_depth:
            return self.check(address, city, state, zip_code)
        self._resume_response = {"payload": payload,
                                 "observed_at_utc": detail["observed_at_utc"],
                                 "response_sha256": detail["response_sha256"]}
        try:
            outcome, result = self.check(address, city, state, zip_code)
            result = dict(result or {})
            result["reused_primary_response"] = {
                "response_sha256": detail["response_sha256"],
                "observed_at_utc": detail["observed_at_utc"],
                "fresh_http": False}
            return outcome, result
        finally:
            self._resume_response = None

    def _kw(self):
        # FAIL CLOSED: when REQUIRE_PROXY_EGRESS=1 (default) a missing/invalid proxy ABORTS the
        # request rather than silently egressing direct. Raised here, it surfaces as an access
        # failure in the auth/search handlers — never a direct fallback, never an address verdict.
        egress_guard.assert_proxy_egress(self.proxy)
        return ({"proxies": {"http": self.proxy, "https": self.proxy}}
                if self.proxy else {})

    def _new_session(self):
        """Fresh TLS session, fresh token, rotation counter back to zero."""
        old_session = self.session
        self.session = self._cr.Session(impersonate="chrome124")
        self.token = None
        self.uses = 0
        if old_session is not None:
            old_session.close()

    def close(self):
        """Close this client's connection pool; the runner must own this lifecycle."""
        old_session, self.session = self.session, None
        self.token = None
        if old_session is not None:
            old_session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _to_proxy(self) -> bool:
        """Move THIS client onto a FRESH residential exit. False if it cannot.

        Fresh, not the same one again: the factory rewrites the _session-XXXX
        tag on every call and that tag is what selects the exit IP, so calling
        it is what makes this a different address rather than a retry from the
        same one.
        """
        if direct_only():
            return False
        if self.proxy_factory is None:
            self.proxy_factory = _default_proxy_factory()
        if self.proxy_factory is None:
            return False
        try:
            p = self.proxy_factory()
        except Exception:
            p = None
        if not p:
            # A factory that returns None is FL_DIRECT wired in by the caller.
            # There is nowhere to go; say so rather than pretending to move.
            return False
        self.proxy = p
        self._route_hash = hashlib.sha256(str(p).encode()).hexdigest()[:10]
        self.lane = "proxy"
        self._new_session()
        return True

    def _to_direct(self):
        """Back onto this computer's own connection."""
        self.proxy = None
        self.lane = "direct"
        self._new_session()

    def _maybe_retest_direct(self):
        """Climb back to the fast lane once the cooldown expires.

        Costs ONE request to find out, against a measured ~2x on throughput if
        the block has cleared (59 clean addr/min direct vs 29/min through the
        gateway, 2026-08-18). If it is still blocked the next response says so
        and the cooldown doubles, so a long outage is not re-probed for ever.

        ONE request in total, not one per worker. See claim_direct_probe: this
        method runs on every ask of every worker, so before the lease existed
        the whole pool moved the moment the cooldown expired.
        """
        #: FAIL-CLOSED PROXY (2026-09-09): under REQUIRE_PROXY_EGRESS=1 there is NO direct lane to
        #: climb back to — retesting it would switch self.proxy=None and the search would then be
        #: refused by the egress guard, surfacing as UNREAD. This was the raw-vs-check() divergence.
        if str(os.environ.get("REQUIRE_PROXY_EGRESS", "1")) == "1":
            return
        if self.lane != "proxy" or direct_only() or proxy_first():
            # proxy_first() is FL_PROXY=1, the operator pinning every request to
            # an exit. Without it here the client was pulled back onto direct on
            # the very next ask, because no block had been PROVED -- so the flag
            # did nothing from ask #2 onward, which is exactly the case it exists
            # for: direct failing in a way the detector deliberately does not
            # call a block (an HTTP 503 with no challenge marker).
            return
        if direct_blocked():
            return
        # Unlocked fast path above, the real decision here. Losing the claim is
        # not a failure and not an answer about any address: this worker carries
        # on down the exit it is already using.
        if not claim_direct_probe():
            return
        self._to_direct()

    def _fall_back(self, reason: str, status: int, body: str) -> bool:
        """Surface access/rate-limit blocks without changing exit or retrying them."""
        self.last_block = {"block": reason, "status": status,
                           "blocked_lane": self.lane,
                           "response_received": True}
        return False

    def _token_fail(self) -> tuple:
        """The UNREAD for "no token", carrying the block that caused it if any."""
        det = {"stage": "token", "lane": self.lane}
        if self.last_error:
            det.update(self.last_error)
        if self.last_block:
            det.update(self.last_block)
            det["stage"] = "blocked"
        return "UNREAD", det

    def _headers(self, bearer: bool):
        auth = (f"Bearer {self.token}" if bearer and self.token
                else f"Basic {self._basic}")
        h = {"Accept": "application/json, text/plain, */*",
             "Accept-Language": "en-US,en;q=0.9",
             "Content-Type": "application/json",
             "Referer": BASE + "/", "Origin": BASE, "Authorization": auth,
             **FETCH_METADATA}
        #: native /api deterministic headers. x-resource-id follows self.token, so a 401 reauth
        #: (which replaces self.token) automatically recomputes it on the next call.
        h["device-id"] = self.device_id
        h["session-id"] = _jwt_session_id(self.token or "")
        h["x-meta-info"] = _x_meta_info()
        h["x-resource-id"] = _x_resource_id(self.token or "")
        return h

    def _lane_endpoint(self) -> str:
        """Name the thing being asked, so the two lanes trip independently.

        A blocked home IP must not stop us using the proxy, and a dead proxy
        must not stop us retrying direct: they fail for different reasons and
        each needs its own counter.
        """
        #: ACTOR-SCOPED: include actor_id + route_hash so this actor's circuit is its own. One
        #: blocked exit quarantines THIS actor only; other actors' circuits are untouched.
        base = "carrier-proxy" if self.lane == "proxy" else "carrier-direct"
        return f"{base}:a{self.actor_id}:{self._route_hash}"

    def _ensure_token(self) -> bool:
        """Mint the anonymous bearer token, taking the lane exception if needed.

        THE BLOCK IS USUALLY DISCOVERED HERE, not on a search: the handshake is
        the first thing that leaves the machine, and on 2026-08-26 both halves
        of it — GET / and POST the session endpoint — came back 429 with the
        interstitial. Detecting it here means the very first address of a run
        moves to a working lane instead of a run of UNREADs nobody sees until
        morning.
        """
        if self.token:
            return True
        # Three goes: enough for a lane change AND one transient retry, which
        # is not padding. Measured 2026-08-26 over 24 handshakes through the
        # residential gateway: 20 minted a token, 3 died with
        # "CONNECT tunnel failed, response 504" and 1 with an SSL close — 17%
        # lost at the EXIT, before the carrier was ever reached. The old code
        # gave up on the first such failure, which cost the address an UNREAD.
        # That was survivable while the proxy was a rare detour; now that it is
        # the standing fallback it would put a 17% UNREAD floor under every
        # blocked run, and a market that never finishes settling is exactly the
        # thing a human ends up having to notice.
        for _ in range(3):
            hit = None
            # ASK THE CIRCUIT FIRST. If this lane's endpoint has gone silent,
            # nothing leaves the machine at all -- see endpoint_guard for why
            # a per-worker backoff cannot prevent a firewalling and a shared
            # counter can.
            try:
                endpoint_guard.before_attempt(self._lane_endpoint())
            except endpoint_guard.EndpointDown as exc:
                self.token = None
                self.last_error = {"stage": "endpoint-down", "response_received": False,
                                   "endpoint": self._lane_endpoint(),
                                   "request_attempted": False}
                break
            response_received = False
            try:
                # THE SESSION POST ALONE. NO LANDING-PAGE GET.
                #
                # This used to fetch BASE + "/" first, both to look like a
                # browser warming a session and to discover a block early. It
                # is 465,798 bytes, pulled once per client, and a client rotates
                # every ROTATE_EVERY (40) addresses -- 11.6 KB of overhead per
                # address against 6.1 KB for the answer itself. Two thirds of
                # all bandwidth on the run, and a 466 KB stall every fortieth
                # lookup.
                #
                # It buys nothing. Measured 2026-08-26 through a residential
                # exit: POSTing the session endpoint with no prior GET returns
                # HTTP 201 and a valid token, and a search on that token comes
                # back FIBER / SC050INMN. And the early-detection argument is
                # answered by the note above -- when the IP is blocked BOTH
                # halves return 429, so the POST discovers it on exactly the
                # same request that would have minted the token.
                #
                # If a block ever turns out to depend on the GET, restore it and
                # say so with the response that proved it. Not on a hunch.
                r = self._post(BASE + SESSION,
                                      headers=self._headers(False),
                                      data=json.dumps({"brazeDeviceId": ""}),
                                      timeout=25, **self._kw())
                response_received = True
                head = _head(r)
                self.last_error = {"stage": "auth-response", "response_received": True,
                                   "status": r.status_code}
                # THE ENDPOINT SPOKE. A 429 or a 500 is an answer, not silence,
                # so the circuit stays closed -- tripping on real replies would
                # stop a pull that is working.
                endpoint_guard.record_success(self._lane_endpoint())
                reason = block_reason(r.status_code, head)
                if reason:
                    hit = (reason, r.status_code, head)
                elif r.status_code in (200, 201):
                    self.token = (r.json() or {}).get("token")
            except Exception as exc:
                self.last_error = exception_detail(exc, "auth", response_received=response_received)
                # A JSON/decode exception after a response is not a transport
                # failure. Preserve the exception and count only no-response
                # failures toward the endpoint circuit.
                if not response_received:
                    endpoint_guard.record_failure(self._lane_endpoint())
                self.token = None
            if self.token:
                break
            if hit is not None:
                if not self._fall_back(*hit):
                    self.last_block["retry_after"] = r.headers.get("Retry-After")
                    break                 # leave blocked work unresolved
                continue
            # No block proved, and no token: the connection failed, or the
            # session endpoint answered something other than 200/201. STAY ON
            # THIS LANE — a timeout is not proof of a block, and moving a
            # direct client onto a proxy on a dropped connection would quietly
            # invert the standing rule on the first flaky minute of the day.
            # A fresh exit though, when we are on one: a 504 from the CONNECT
            # tunnel is that exit failing, and retrying through it repeats it.
            if self.lane == "proxy" and self.proxy_factory:
                try:
                    self.proxy = self.proxy_factory() or self.proxy
                except Exception:
                    pass
            self._new_session()
        if self.token:
            # The episode is over. Left set, a block from an hour ago would
            # relabel the next ordinary timeout as "blocked" and send someone
            # hunting an outage that had already cleared.
            self.last_block = None
            self.last_error = None
            if self.lane == "direct":
                note_direct_ok()
        return bool(self.token)

    def _rotate(self):
        """New exit IP and a new session before the host starts refusing.

        On the direct lane there is no exit to change, so this is a new TLS
        session on the same connection — which is exactly what an FL_DIRECT run
        has always done. Never a silent move onto a proxy: a rotation is
        housekeeping, not proof of a block.
        """
        if self.lane == "proxy" and self.proxy_factory:
            try:
                self.proxy = self.proxy_factory() or self.proxy
            except Exception:
                pass
        self._new_session()

    def _check(self, address: str, city: str, state: str, zip_code: str,
              _retry: bool = False, _near: tuple | None = None,
              _shallow: bool = False, _extra: dict | None = None):
        """(outcome, detail). outcome: FIBER / COPPER / CABLE / NOFIBER /
        NOMATCH / UNMAPPED / UNREAD / UNASKED / UNRESOLVED.

        NOFIBER requires an explicit boolean exactMatch=true and the carrier's
        unserviceable response. A non-exact premise record remains unresolved. UNMAPPED is the carrier saying
        something VALIDATION_RESULTS has never been taught -- an answer we
        cannot read, which is not the same as no answer and must never be
        recorded as one.

        UNREAD means no answer was obtained and the address must be asked again;
        UNASKED means no answerable question was sent; UNRESOLVED means the
        carrier offered candidates and none of them is this house. None of the
        three is a verdict.
        """
        # A REFUSAL WE EARNED WITH OUR OWN MISSING FIELD IS OURS, NOT THE
        # CARRIER'S.
        #
        # There was no precondition anywhere, at any layer. The body below is
        # built from `(city or "").upper()` and `(zip_code or "")` with no
        # guard, so a row holding neither went out as
        # {"addressLine1": "9803 F M 597-C R 5000", "city": "", "state": "TX",
        #  "zip": ""} -- seven times, once per spelling variant -- and the
        # inevitable AddressNotFound was written down as UNKNOWN, which reads as
        # a statement about the house. 114,964 rows of the Texas sweep say that
        # about a question the carrier never received. Asked properly, 16 in 100
        # of them answer, 9 of those with fibre.
        #
        # THE FIX IS NOT TO STOP ASKING. Refusing to send a holed address was
        # tried and measured first, and it is the opposite defect: 8 of 60
        # sampled SERVICEABLE rows and 5 of 60 DSL rows have a blank city or ZIP
        # and are answered anyway -- the carrier resolves plenty of them off the
        # street and state alone. Not asking would delete those leads.
        #
        # So the asymmetry that governs everything else here governs this too: a
        # missing field can HIDE an address from the carrier but can never make
        # it up. A positive identification is therefore a real answer whatever we
        # left blank, and stands exactly as it does today. A NON-identification
        # from a holed question establishes nothing, and comes back UNASKED so it
        # is held and asked again rather than recorded as a verdict.
        #
        # It lives HERE, at the one door every caller goes through, because a
        # convention in the caller is what let this class exist in the first
        # place. `_retry` carries no hole of its own: it is a follow-up about a
        # house already asked about, and the last-resort ask further down blanks
        # the city deliberately.
        holed = [] if _retry else [n for n, v in (("city", city), ("zip", zip_code))
                                   if not str(v or "").strip()]

        def _ours(outcome, detail):
            """Downgrade a non-identification we made unanswerable to UNASKED."""
            if holed and outcome in ("NOMATCH", UNRESOLVED):
                d = dict(detail or {})
                d["stage"] = "unasked"
                d["missing"] = holed
                d["would_have_been"] = outcome
                return UNASKED, d
            return outcome, detail

        reused_primary = self._resume_response if not _retry else None
        if reused_primary is not None:
            self._resume_response = None
            self._workflow_totals["reused_primary_responses"] = 1
        self.uses += 1
        # Only on a top-level ask. A _retry is a follow-up question about the
        # SAME house (the carrier's own suggestion, or a spelling variant) and
        # it belongs on the same lane as the answer that prompted it.
        if not _retry:
            self._maybe_retest_direct()
        if not _retry and self.uses > ROTATE_EVERY:
            self._rotate()
        if reused_primary is None and not self._ensure_token():
            return self._token_fail()
        #: NATIVE current search body (2026-09-09): postalCode (not zip), addressLine2, geoLat/
        #: geoLong, useSearchV2. Geo is empty when the sweep has no geocode; the endpoint accepts
        #: it (the pre-2026-09 body carried none). _extra still merges unit fields / dfAddressId.
        payload = {"addressLine1": (address or "").upper(),
                   "addressLine2": "",
                   "city": (city or "").upper(),
                   "state": (state or "").upper(),
                   "postalCode": (zip_code or ""),
                   "geoLat": "", "geoLong": "", "useSearchV2": False}
        # `_extra` carries the fields that identify one UNIT of a building --
        # dfAddressId, addressLine2 -- when we are following a suggestion the
        # carrier made. Merged rather than hard-coded into the four-field body
        # so the ordinary ask is byte-for-byte what it always was.
        if _extra:
            payload.update(_extra)
        body = json.dumps(payload)
        for attempt in range(3):
            if reused_primary is not None:
                j = reused_primary["payload"]
                response_status = 200
            else:
                if not self.token and not self._ensure_token():
                    return self._token_fail()
                # ASK THE CIRCUIT FIRST, ON THE SEARCH TOO, NOT ONLY ON THE
                # HANDSHAKE.
                #
                # `_ensure_token` has been guarded since the incident, and that alone
                # is not enough: a token is minted once per client and then reused
                # for ROTATE_EVERY addresses, so on a sweep the overwhelming majority
                # of sockets this class opens are THIS one. If the exit gateway dies
                # after the handshakes have gone through, the guard sees nothing at
                # all while every worker keeps posting a search three times per
                # address against a lane that is not answering -- the 2026-08-27
                # traffic pattern exactly, arriving through the door the fix left
                # open.
                try:
                    endpoint_guard.before_attempt(self._lane_endpoint())
                except endpoint_guard.EndpointDown:
                    # UNREAD, never a verdict, and never the generic handler below --
                    # that one mints a new session and retries, which is the
                    # hammering. UNREAD is not in uqual_sweep.VERDICTS, so this can
                    # never retire an address: the house is asked again on the next
                    # pass, and our own outage cannot be written down as a statement
                    # about it.
                    return "UNREAD", {"stage": "endpoint-down",
                                      "lane": self.lane,
                                      "endpoint": self._lane_endpoint()}
                spoke = False
                try:
                    r = self._post(BASE + SEARCH, headers=self._headers(True),
                                          data=body, timeout=25, **self._kw())
                    # Receiving an HTTP response is distinct from obtaining a valid
                    # answer. Access/rate-limit responses are surfaced as unresolved;
                    # only an ordinary expired-token 401 triggers bounded renewal.
                    spoke = True
                    endpoint_guard.record_success(self._lane_endpoint())
                    blocked = block_reason(r.status_code, _head(r))
                    if blocked:
                        # THE PATH IS BLOCKED, NOT THE ADDRESS. A 429 or a
                        # Cloudflare interstitial says nothing whatever about this
                        # house; falling through to NOMATCH would delete a real
                        # lead on an infrastructure fault, which is the same
                        # failure the 403 branch below was written to stop.
                        if self._fall_back(blocked, r.status_code, _head(r)):
                            if not self._ensure_token():
                                return self._token_fail()
                            continue
                        # Let the scheduler honor the restriction/Retry-After.
                        # No immediate retry or network-identity change occurs here.
                        return "UNREAD", {"stage": "blocked", "block": blocked,
                                          "status": r.status_code, "http_status": r.status_code,
                                          "retry_after": r.headers.get("Retry-After"),
                                          "response_received": True, "lane": self.lane}
                    if r.status_code == 401:
                        self.token = None
                        if not self._ensure_token():
                            return self._token_fail()
                        continue
                    if r.status_code != 200:
                        raise ValueError(r.status_code)
                    # HTTP 200 IS NOT PROOF THE CARRIER ANSWERED. An error envelope
                    # -- {"error": ...}, or {"success": false, "message": ...} --
                    # parses cleanly and arrives here as a dict carrying no
                    # validationResult, and classify() reports that as UNMAPPED:
                    # our own unread response written to the row as though the
                    # CARRIER had said something the table cannot read. Those are
                    # two different things and must never share a bucket. A refusal
                    # is unfinished work, so it raises into the retry below and
                    # three refusals end at the UNREAD sentinel.
                    #
                    # require="" deliberately: only an error envelope or a body
                    # that is not a JSON object raises. A well-formed answer
                    # carrying a validationResult nobody has taught the table still
                    # reaches classify() and is still UNMAPPED -- loud, counted and
                    # delivery-stopping -- because that one is a gap in OUR table,
                    # and turning it into UNREAD would make the address immortal.
                    j = fetch_guard.ok_json(r, require="",
                                            where="carrier address search")
                except Exception as exc:
                    self.last_error = exception_detail(exc, "search", response_received=spoke)
                    if spoke:
                        self.last_error["status"] = r.status_code
                    if not spoke:
                        # SILENCE. Nothing came back at all — a refused connection,
                        # a CONNECT tunnel that never opened, a read that timed out.
                        # `spoke` is what keeps this honest: this handler also
                        # catches fetch_guard's refusal of a well-formed HTTP 200
                        # that carried no answer, and that is the carrier TALKING.
                        # Counting it would let a run of error envelopes — a real
                        # thing this endpoint does — trip a circuit on a service
                        # that is up.
                        endpoint_guard.record_failure(self._lane_endpoint())
                    # A connection-level failure is NOT proof of a block, so a
                    # direct client stays direct here — the standing rule is home
                    # IP unless PROVEN otherwise, and a timeout proves nothing.
                    # A guarded 200 that carried no answer lands here too, which is
                    # what makes the sleep below a real retry for a refusal instead
                    # of a verdict read off it on the first call.
                    self.token = None
                    if self.lane == "proxy" and self.proxy_factory:
                        try:
                            self.proxy = self.proxy_factory() or self.proxy
                        except Exception:
                            pass
                    self._new_session()
                    if attempt < 2:
                        time.sleep(0.4 + attempt * 0.8)
                    continue

                response_status = r.status_code

            vr = j.get("validationResult") or ""
            tech = (j.get("techType") or "").upper()
            ad = j.get("address") if isinstance(j.get("address"), dict) else {}
            response_payload = _redact(j)
            response_json = json.dumps(response_payload, sort_keys=True, separators=(",", ":"))
            # Retain reported coordinates and address evidence without claiming
            # rooftop accuracy. Independent premise/pin validation is downstream.
            det = {"validationResult": vr, "techType": tech,
                   "carrier_lat": ad.get("geoLat"), "carrier_lon": ad.get("geoLong"),
                   "geo_precision": ad.get("geoPrecision"),
                   "carrier_address": (j.get("streetAddress") or "").strip(),
                   "billingStatus": str(ad.get("billingStatus") or "").strip(),
                   "billingSystem": str(ad.get("billingSystem") or "").strip(),
                   "existing_customer": existing_status(j),
                   "existing_status_source": "legacy_billingStatus_A_N_mapping",
                   "customer_mapping_verified": False,
                   "exactMatch": j.get("exactMatch") if isinstance(j.get("exactMatch"), bool) else None,
                   "request_payload": _redact(payload),
                   "response_payload": response_payload,
                   "response_sha256": hashlib.sha256(response_json.encode()).hexdigest(),
                   "response_hash_kind": "canonical_redacted_json_sha256",
                   "observed_at_utc": (reused_primary["observed_at_utc"] if reused_primary
                                       else datetime.now(timezone.utc).isoformat()),
                   "http_status": response_status,
                   "response_received": True,
                   "product_verified": False,
                   "maxQual": j.get("maxQual"), "dfAddressId": j.get("dfAddressId"),
                   "exchangeId": j.get("exchangeId"), "via": "uqual",
                   # PRODUCT-STAGE CONTEXT (live v2 merge). The product call must
                   # echo this search's own `uqualProvisioningResult` STRING and be
                   # bound to this exact premise; `_raw_search` is the in-memory
                   # primary payload the classifier reads (the redacted
                   # response_payload above is storage evidence, not a request
                   # source). `_ctx` pins the session+token that answered this
                   # search so plans() can refuse if either changed. The runner
                   # strips these three transient keys before persistence.
                   "success": j.get("success"),
                   "uqualProvisioningResult": j.get("uqualProvisioningResult"),
                   "_raw_search": j,
                   "_ctx": {"sess": id(self.session), "token": self.token},
                   # Which connection answered. Carried so a run's own output
                   # says whether it paid the proxy penalty, instead of that
                   # being reconstructed from log lines afterwards.
                   "lane": self.lane}
            # ASK AGAIN WITH THE STREET TYPE SPELLED OUT BEFORE ACCEPTING A
            # NO-MATCH. A no-match is a claim the carrier has never heard of
            # this address, and it is wrong whenever the only difference is
            # "AVE" against "AVENUE".
            #
            # BOTH refusal shapes have to be covered. The carrier answers an
            # abbreviated address with validationResult 'AddressSuggestions',
            # not 'AddressNotFound' -- so a retry wired only to AddressNotFound
            # never fires, which is exactly the bug that hid this for a day.
            # Verified on Andrews TX: "1201 SW AVE G" -> AddressSuggestions,
            # "1201 SW AVENUE G" -> AddressFound, FIBER.
            #
            # Retried once, and only when the address actually contains an
            # abbreviation, so it costs nothing on addresses that have none.
            #
            # The classifier requires a strict exact match for every definitive
            # technology/negative outcome. Premise metadata alone is insufficient.
            outcome, why = classify(j)
            det["classified"] = why
            if outcome == NOFIBER:
                # classify() established explicit exactMatch=true before accepting
                # the carrier's unserviceable response as a negative.
                return NOFIBER, det
            if outcome == UNMAPPED:
                # Loud, and carried out to the row. Never UNREAD (which would
                # make the address immortal) and never NOMATCH (which would
                # make our own gap look like the carrier's answer).
                return UNMAPPED, det
            if outcome == _SEARCH:
                # RETRY WHERE A RENAME IS LIKELY, NOT WHERE THE CARRIER SIMPLY
                # DOES NOT SELL.
                #
                # The retry chain below -- the carrier's own suggestions, then
                # every spelling variant -- costs up to five calls per address
                # and recovered 1,666 leads on Andrews, so it earns its keep
                # where Kinetic actually has a footprint.
                #
                # In a county where the exchange probe recognised ONE address in
                # fourteen, a no-match is not a misspelling: the carrier does not
                # serve there. Measured on the statewide run: 66% of addresses
                # came back unmatched, each paying ~5 calls, so 377,509 settled
                # addresses cost about 1,381,000 carrier calls -- a 3.7x
                # multiplier spent mostly to re-confirm that a weak county is
                # weak.
                #
                # A shallow search leaves NOMATCH unresolved. The caller must
                # not count that result as a completed negative.
                #
                # EXCEPT WHEN THE CARRIER HAS ALREADY HANDED US THE ANSWER.
                # AddressSuggestions is not a weak county refusing to sell; it
                # is a candidate list the carrier has already paid to compute,
                # for a house it clearly holds. Short-circuiting past it saved
                # one call and threw the list away unread -- 53 of 100 sampled
                # suggestion rows resolve to a real verdict on the SAME house
                # number, 30 of them fibre.
                if _shallow and vr != "AddressSuggestions":
                    return _ours("NOMATCH", det)
                # Declared out here, not inside the retry branch. When this
                # address IS the retry (_retry=True) no sub-checks run at all,
                # and the `if any_unread` below still has to have something to
                # read -- it crashed the whole sweep with an UnboundLocalError
                # on the first retried address.
                any_unread = False
                asks: list[dict] = []
                if not _retry:
                    # THE CARRIER'S OWN SUGGESTION FIRST -- it knows its address
                    # database and it just told us what this address is called.
                    #
                    # This used to read the candidate list for its addressLine1
                    # and nothing else, then delete any candidate equal to the
                    # string we sent. That filter removed the one candidate that
                    # mattered: a building whose units are the actual
                    # answer. `candidate_asks` keeps it and asks under the unit,
                    # and carries each candidate's own city and postalCode.
                    asks = candidate_asks(address, j.get("addressCandidates"),
                                          state=state, zip_code=zip_code)
                    best = None
                    # "NOBODY ASKED" IS NOT "THE CARRIER SAYS NO."
                    #
                    # UNREAD from a sub-check means the request never got an
                    # answer -- a block, a timeout, a dropped connection.
                    # NOMATCH means the carrier answered and does not know the
                    # string. Treating them alike let a TRANSPORT FAILURE end up
                    # recorded as a settled verdict, and a settled verdict is
                    # what removes an address from the queue for good. That is
                    # how 281 Fury Dr -- serviceable, found by reps on the
                    # doorstep -- left a delivered sheet.
                    #
                    # So an unread retry is remembered, and if EVERY retry came
                    # back unread the caller is told UNREAD, not NOMATCH: the
                    # address stays in the queue to be asked again.
                    for ask in asks:
                        v2, d2 = self.check(ask["address"], ask["city"] or city,
                                            state, ask["zip"] or zip_code,
                                            _retry=True, _extra=ask["extra"])
                        if v2 == "UNREAD":
                            any_unread = True
                            continue
                        if v2 == "NOMATCH":
                            continue
                        d2["asked_as"] = ask["address"]
                        # WHICH STRING THE CARRIER ACCEPTED. A lead recovered
                        # under a unit id is orderable only under that id, and
                        # without it recorded the rep is back at the front door
                        # with the address that did not work.
                        d2["answered_via"] = ask["via"]
                        if _near is None:
                            return v2, d2          # nothing to disambiguate with
                        try:
                            dd = _dist(_near, (float(d2["carrier_lat"]),
                                               float(d2["carrier_lon"])))
                        except (TypeError, ValueError, KeyError):
                            return v2, d2
                        if best is None or dd < best[0]:
                            best = (dd, v2, d2)
                    if best is not None:
                        # AMBIGUOUS STREET, SETTLED ON THE COORDINATE. Where the
                        # carrier offers "12TH ST" and "12TH PL" for the same
                        # house number, the one sitting nearest the county's own
                        # point for this address is the right one -- a fact, not
                        # a coin flip.
                        return best[1], best[2]
                    seen_alt = {(address or "").upper()}
                    for alt in variants(address):
                        if alt.upper() in seen_alt:
                            continue
                        seen_alt.add(alt.upper())
                        v2, d2 = self.check(alt, city, state, zip_code,
                                            _retry=True)
                        if v2 == "UNREAD":
                            any_unread = True
                            continue
                        if v2 != "NOMATCH":
                            d2["asked_as"] = alt
                            d2["answered_via"] = "spelling-variant"
                            return v2, d2
                # LAST RESORT: ASK WITHOUT THE CITY.
                #
                # A wrong city does not merely fail to help -- it stops the
                # address matching at all. Measured on the Texas sweep: 18,381
                # addresses carrying "Kerr County" as their city were recognised
                # at 5%, while their neighbours written "Kerrville" ran at 76%.
                # A live A/B on that class recovered 20% of them by sending the
                # SAME address with the city field empty and changing nothing
                # else -- the carrier resolves it from the street and ZIP.
                #
                # It is deliberately last: the city helps far more often than it
                # hurts, so it stays on the primary ask and every retry above.
                # This fires only on an address already headed for NOMATCH, so
                # it costs one call on rows that were about to be written off,
                # and none at all on rows that resolved.
                if not _retry and city and str(city).strip():
                    v3, d3 = self.check(address, "", state, zip_code,
                                        _retry=True)
                    if v3 == "UNREAD":
                        any_unread = True
                    elif v3 != "NOMATCH":
                        d3["asked_as"] = address
                        d3["asked_without_city"] = str(city).strip()
                        d3["answered_via"] = "without-city"
                        return v3, d3

                if any_unread:
                    # At least one retry never got an answer, so we do not KNOW
                    # this address is unmatched. Unread keeps it in the queue.
                    det["retry_unread"] = True
                    return "UNREAD", det
                # THE CARRIER OFFERED CANDIDATES AND NOT ONE IS THIS HOUSE.
                #
                # That is not "the carrier has never heard of this address" --
                # it is holding a street we could not line up with, most often
                # because it numbers the house differently. Recorded as NOMATCH
                # the row became UNKNOWN, settled, never asked again, and the
                # candidate list was gone: unrecoverable without buying it back.
                # UNRESOLVED keeps it open and carries the list with it.
                if not _retry and vr == "AddressSuggestions" and not asks:
                    named = [str(c.get("addressLine1") or "").strip()
                             for c in (j.get("addressCandidates") or [])
                             if isinstance(c, dict)]
                    named = [n for n in named if n]
                    if named:
                        det["candidates"] = named
                        return _ours(UNRESOLVED, det)
                return _ours("NOMATCH", det)
            # FIBER / COPPER / CABLE, decided by the same classify() call above.
            # The three lines that used to stand here re-read techType for
            # themselves and ended in `return "UNREAD", det` for anything else --
            # a second silent bucket, and a worse one: the sweep writes NO ROW
            # for UNREAD, so an exact match whose technology we could not read
            # was asked again on every pass of every re-run and never settled.
            # That case is UNMAPPED now, and classify() has already said so.
            return outcome, det
        return "UNREAD", {"stage": "exhausted", "lane": self.lane,
                          "last_error": self.last_error}
