#!/usr/bin/env python3
"""THE carrier response classifier. One copy. Every path imports this.

No worker, canary, replay tool, coordinator, import or export may decide a
disposition on its own. Two independent classifiers is how the same response
became FIBER in one path and UNRESOLVED in another on 2026-09-01.

INVARIANTS (see /Users/masani/CLAUDE.md "Carrier evidence invariants"):

  * exactMatch=true + techType=FIBER establishes the TECHNOLOGY. Whether it is
    a LEAD is decided by the priced-plan rule: a named bundle carrying a real
    standardPrice/promoPrice. "Call to order" is not a lead.
  * Enrichment may never demote confirmed fiber to NONFIBER or NOT_FOUND. It
    MAY leave it unresolved, and WHICH unresolved is the point:
      - the product call failed, was refused, or never ran
        -> CURRENT_UNRESOLVED_ENRICHMENT_UNREAD  (we failed to ask; retryable)
      - the product response was read and priced nothing
        -> CURRENT_UNRESOLVED_NO_PLAN_POPULATED  (a real answer; not a lead)
    Collapsing those two is what 4.0 fixes. MEASURED: all 9 rows in the Inman
    NO_PLAN bucket were the first kind wearing the second kind's name.
  * The primary qualification payload is preserved separately from the optional
    enrichment payload.
  * `status in TERMINAL` is NOT the completeness test. closes_address() is.
    A disposition closes an address only when the carrier answered AND the
    meaning of that answer is proven.
  * AddressNeedsFix / errorCode 1065 -> CURRENT_UNRESOLVED_ADDRESS_NEEDS_FIX,
    which stays in the retry/remediation backlog and can never become
    NOT_FOUND.
  * AddressNotFound / errorCode 1700 -> the carrier could not match the address
    STRING we submitted at all (most were TIGER-interpolated synthetic strings).
    Same "we asked badly" family as 1065 and the SAME remediation action
    (re-ask with an authoritative address string), so it takes the SAME
    sub_status CURRENT_UNRESOLVED_ADDRESS_NEEDS_FIX -- which is the only one the
    ADDRESS_REMEDIATION drain (needsfix_runner) actually processes. RETRYABLE;
    can NEVER become NOT_FOUND. The exact code 1700 stays in the reason and in
    the immutable raw_json, so it is always separable from 1065. (Added 4.1
    2026-09-03: before it, 1700 fell through to UNKNOWN_RESPONSE -- the
    fail-closed default for a code the classifier had no rule for -- which is an
    undrainable lane, so 33,638 East/West/fresh Texas addresses read as
    "we could not understand the reply" when the truth is "we asked badly".)
  * Only an explicit out-of-territory/unserviceable reply (verified errorCode
    1060) may become CURRENT_NOT_FOUND.
  * Unknown error codes fail closed to CURRENT_UNRESOLVED_UNKNOWN_RESPONSE.
  * HTTP success does not prove classification success: the whole body is read.
  * A derived disposition may be rebuilt from immutable raw evidence; the raw
    evidence itself is never edited.
"""
from __future__ import annotations

CLASSIFIER_VERSION = "inman-classifier/4.2 2026-09-04"

FIBER_PLAN_POPULATED = "CURRENT_FIBER_PLAN_POPULATING"   # storage name, kept stable
NONFIBER = "CURRENT_NONFIBER"
NOT_FOUND = "CURRENT_NOT_FOUND"
UNRESOLVED = "CURRENT_UNRESOLVED"
UNREAD = "CURRENT_UNREAD"
FAILED_RETRYABLE = "CURRENT_FAILED_RETRYABLE"

#: Sub-reasons that must survive into the backlog, not be flattened.
NEEDS_FIX = "CURRENT_UNRESOLVED_ADDRESS_NEEDS_FIX"
UNKNOWN_RESPONSE = "CURRENT_UNRESOLVED_UNKNOWN_RESPONSE"
#: Qualified as fiber, but NO purchasable plan populated. Not serviceable.
NO_PLAN = "CURRENT_UNRESOLVED_NO_PLAN_POPULATED"
#: The carrier says someone has ALREADY ORDERED service at this address. It is
#: not a prospect and never becomes one by waiting. Distinct from "no plan":
#: this is a definite answer, not a gap.
PENDING_ORDER = "CURRENT_NOT_A_LEAD_PENDING_ORDER"

#: WE COULD NOT READ THE PLANS. Added 4.0 after the audit of 2026-09-01.
#:
#: MEASURED on the Inman run.db, every row in the NO_PLAN bucket, n=9:
#:   7 x enrichment == {"why": "TRANSPORT Timeout"}   (the product call timed out)
#:   2 x enrichment absent                           (the product call never ran)
#:   0 x a product response that was actually read and carried no priced plan.
#: All nine were exactMatch=true techType=FIBER addresses. Every one of them was
#: OUR failure to fetch the plans, and all nine were filed as
#: CURRENT_UNRESOLVED_NO_PLAN_POPULATED -- "the answer is no priced plan".
#: `carrier.plans()` returns {"why": ...} / {"http": N} on a failure, and
#: `carrier.check` passed that straight in as if it were a product response, so
#: "we failed to ask", "the service refused" and "the answer is no" all landed
#: in one bucket. That is the collapse CLAUDE.md's REAL-DATA RULE forbids.
ENRICHMENT_UNREAD = "CURRENT_UNRESOLVED_ENRICHMENT_UNREAD"

#: The carrier could not match OUR address and offered corrections instead.
#: MEASURED n=216 on the Inman run: these were landing in UNKNOWN_RESPONSE
#: alongside 5,356 genuine carrier answers. Same class as errorCode 1065 --
#: we asked badly -- so it belongs in the remediation backlog, not in a bucket
#: named "we could not understand the reply".
ADDRESS_SUGGESTIONS = "CURRENT_UNRESOLVED_ADDRESS_SUGGESTIONS"

#: The carrier replied "in territory, unserviceable". MEASURED n=5,356.
#: It is a REPLY, not an unreadable one -- but its meaning has not been verified
#: against the live buy flow, so it may never become a terminal CURRENT_NOT_FOUND
#: (only errorCode 1060 may) and it may never close an address. It gets its own
#: name so it stops sharing UNKNOWN_RESPONSE with unparsed shapes.
IN_TERRITORY_UNSERVICEABLE = "CURRENT_UNRESOLVED_IN_TERRITORY_UNSERVICEABLE"

#: MEASURED 2026-09-01. The product-availability response returns
#:   errorInfo: {"errorCode": "1080",
#:               "errorMessage": "There is a pending order for this address."}
#: with metaBundles {} — so it presents exactly like a missing plan and was
#: silently shipping as a lead. Masani confirmed from the live buy flow that all
#: 12 such addresses show "There is a pending order for this address."
PENDING_ORDER_CODES = {"1080", 1080}
PENDING_ORDER_TEXT = ("pending order",)

#: A bundle only counts when it carries a real price. "Call to order" is
#: NOT serviceable - a standing rule I broke on 2026-09-01 by treating the
#: product call as optional enrichment.
PRICE_KEYS = ("standardPrice", "promoPrice", "prices", "price", "monthlyRate")
CALL_TO_ORDER = ("call to order", "call for", "contact us", "call us")

#: Kept for readers that mean "this row is a stored disposition, not an
#: in-flight retry". It is NOT the completeness test -- see closes_address().
#: It used to be both, and that is how 140,584 addresses with no usable answer
#: got completed=1 (see the CLOSES / STAYS OPEN table below).
TERMINAL = (FIBER_PLAN_POPULATED, NONFIBER, NOT_FOUND, UNRESOLVED)

FIBER_TECHS = {"FIBER", "FTTH", "FTTP"}

#: MEASURED on the Inman run.db, every non-fiber technology the carrier has
#: actually returned: COPPER, n=2,022. Nothing else has ever been observed.
#: An unmeasured techType is NOT quietly turned into a terminal "no": it fails
#: closed to UNKNOWN_RESPONSE with the string in the reason, so a new carrier
#: technology shows up as a loud unresolved bucket instead of as 300,000
#: silent not-serviceable answers.
NONFIBER_TECHS = {"COPPER"}

# ---------------------------------------------------------------------------
# THE FOUR THINGS. CLAUDE.md, the REAL-DATA RULE:
#
#   "we failed to ask", "we asked badly", "the service refused" and "the answer
#    is no" are FOUR DIFFERENT THINGS and must never share a bucket, a verdict
#    or a sentence."
#
# Two of them are not in the original four and are named anyway, because
# forcing them into one of the four would be the same collapse one level down:
#   * ANSWER_NOT_UNDERSTOOD -- the carrier replied and we cannot prove what the
#     reply means. That is neither a refusal nor an answer we may act on.
#   * (a refusal and a transport failure stay separate: a 403 is the endpoint
#     TALKING, silence is not.)
#
# EXHAUSTIVE: every (status, sub_status) pair classify() can emit appears
# exactly once below. test_four_buckets.py drives every branch of classify()
# and fails if any emitted pair is missing from this table, so an unmapped
# shape can never be silently defaulted.
ASKED_AND_ANSWERED = "THE_CARRIER_ANSWERED"
ASKED_BADLY = "WE_ASKED_BADLY"
FAILED_TO_ASK = "WE_FAILED_TO_ASK"
SERVICE_REFUSED = "THE_SERVICE_REFUSED"
ANSWER_NOT_UNDERSTOOD = "THE_CARRIER_REPLIED_MEANING_UNPROVEN"
#: A parsed carrier reply whose BUSINESS MEANING is not authorized/proven is
#: not the same work as an unknown payload shape.  The latter can advance by a
#: local classifier replay; the former requires authoritative provider evidence
#: and must remain owed in its own fail-closed queue.  Keeping these distinct is
#: what prevents a replay fixed point from masquerading as executable work.
ANSWER_MEANING_UNPROVEN = "THE_CARRIER_REPLY_MEANING_NEEDS_AUTHORIZED_EVIDENCE"

#: (status, sub_status) -> which of the things it is, and whether it CLOSES the
#: address. "Closes" means: our obligation to this address is discharged and it
#: may leave the backlog. Only an answer whose meaning is proven closes one.
FOUR_BUCKETS = {
    (FIBER_PLAN_POPULATED, ""):            (ASKED_AND_ANSWERED, True),
    (NONFIBER, ""):                        (ASKED_AND_ANSWERED, True),
    (NOT_FOUND, "OUT_OF_TERRITORY"):       (ASKED_AND_ANSWERED, True),
    # A pending order is a definite carrier answer: someone already ordered.
    (UNRESOLVED, PENDING_ORDER):           (ASKED_AND_ANSWERED, True),
    # Enrichment was READ and carried no priced plan. CLAUDE.md: "This is a
    # real answer, and it is not serviceable."
    (UNRESOLVED, NO_PLAN):                 (ASKED_AND_ANSWERED, True),
    # The carrier replied, but we cannot prove the meaning -> stays open.
    (UNRESOLVED, IN_TERRITORY_UNSERVICEABLE): (ANSWER_MEANING_UNPROVEN, False),
    (UNRESOLVED, UNKNOWN_RESPONSE):        (ANSWER_NOT_UNDERSTOOD, False),
    # The carrier could not read OUR address -> remediation backlog.
    (UNRESOLVED, NEEDS_FIX):               (ASKED_BADLY, False),
    (UNRESOLVED, ADDRESS_SUGGESTIONS):     (ASKED_BADLY, False),
    # We never got the question through, or never asked it.
    (UNRESOLVED, ENRICHMENT_UNREAD):       (FAILED_TO_ASK, False),
    (UNREAD, ""):                          (FAILED_TO_ASK, False),
    (FAILED_RETRYABLE, ""):                (SERVICE_REFUSED, False),
}

#: `UNREAD`/`FAILED_RETRYABLE` carry an http_NNN sub_status; they are keyed on
#: "" above and looked up with the sub_status dropped.
_HTTP_SUB_STATUS_STATUSES = (UNREAD, FAILED_RETRYABLE)


def four_buckets(status, sub_status=""):
    """(which_of_the_things, closes_address) for a disposition. Never guesses.

    An unmapped pair raises. Silently defaulting an unrecognised disposition to
    "answered" is precisely the failure this table exists to make impossible,
    and a KeyError at the till is cheaper than a lie in the deliverable.
    """
    sub = sub_status or ""
    if status in _HTTP_SUB_STATUS_STATUSES:
        sub = ""
    return FOUR_BUCKETS[(status, sub)]


def closes_address(status, sub_status=""):
    """Does this disposition discharge our obligation to the address?

    THIS, NOT `status in TERMINAL`, is the completeness test.

    MEASURED on the Inman run.db 2026-09-02, with `status in TERMINAL` deciding
    `completed`: 307,522 of 307,522 addresses carried completed=1 and the
    backlog read 0, while 140,584 of them (45%) had no usable answer --
    135,012 CURRENT_UNRESOLVED_ADDRESS_NEEDS_FIX ("we asked badly"),
    5,356 in-territory-unserviceable replies of unproven meaning,
    216 AddressSuggestions ("we asked badly"),
    9 enrichment failures. The command centre read that as
    "settled: 307522, remaining: 0, reconciles: true".

    "We are done with this address" and "we never got a usable answer" were the
    same flag. They are not the same thing.
    """
    try:
        return four_buckets(status, sub_status)[1]
    except KeyError:
        return False          # fail closed: an unknown disposition never closes

#: The same rule as SQL, for the queue and reporting queries. Kept next to
#: closes_address so the two can never drift; test_four_buckets.py proves they
#: agree on every pair in FOUR_BUCKETS by running both.
CLOSED_SQL = (
    "(status IN ('CURRENT_FIBER_PLAN_POPULATING','CURRENT_NONFIBER',"
    "'CURRENT_NOT_FOUND') OR (status='CURRENT_UNRESOLVED' AND "
    "COALESCE(sub_status,'') IN ('CURRENT_NOT_A_LEAD_PENDING_ORDER',"
    "'CURRENT_UNRESOLVED_NO_PLAN_POPULATED')))")

#: The only code permitted to produce a terminal NOT_FOUND.
OUT_OF_TERRITORY_CODES = {1060}
#: The carrier could not read OUR address. Remediation backlog, never no.
NEEDS_FIX_CODES = {1065}
#: The carrier could not MATCH the address string we submitted at all
#: (validationResult AddressNotFound). Most are TIGER-interpolated synthetic
#: strings. Same "asked badly" remediation family as 1065, and it deliberately
#: takes the SAME sub_status (CURRENT_UNRESOLVED_ADDRESS_NEEDS_FIX) so the
#: existing ADDRESS_REMEDIATION drain re-asks it with an authoritative address.
#: NEVER a terminal no -- only 1060 may become NOT_FOUND.
ADDRESS_NOT_FOUND_CODES = {1700}

STOP_HTTP = (401, 403, 407, 429)

#: metaBundles is a DICT keyed by bundle-list name -- NOT a list.
#:
#: Measured 2026-09-01 against every stored product-availability response in
#: run.db (n=35): metaBundles was a dict 35/35 times, its only key
#: "speedAndProductBundleList", whose value is itself a dict keyed by speed tier
#: ("100"/"300"/"1000"/"2000"). Each tier holds
#: {"bundles": [...], "addons": {...}, "speed": {"speed": "300", "context": ...}}
#: and the real plan names are bundles[].bundleName -- for example
#: "Kinetic Internet (Range 233 to 375Mb)", carrying a bundleId.
#:
#: Iterating a dict yields its KEYS, so the previous parser fell into its branch
#: for bare strings and recorded the LIST NAME as the plan. 35 of the 38 stored
#: fiber rows carry the literal
#: "QUAL UP TO 2 GIG RANGE VIA FIBER | speedAndProductBundleList": the genuine
#: bundle names were fetched over the network and then thrown away. No bare
#: string was ever observed in this position (322/322 bundles were dicts), so a
#: bare string is no longer mistaken for a plan name.
#:
#: BOTH shapes are still walked. A shape change must never RAISE inside
#: classification: an exception here is caught upstream as a transport failure,
#: which would advance the circuit breaker on perfectly healthy fiber addresses.
BUNDLE_NAME_KEYS = ("bundleName", "apiName", "name", "displayName",
                    "offerName", "title")
BUNDLE_SPEED_KEYS = ("downloadSpeed", "speed", "maxSpeed", "displaySpeed")

#: The ONLY keys the walk will descend through, besides numeric speed tiers.
#:
#: This is not tidiness, it is the same defect one level down. `backfill.py`
#: replays stored evidence and hands the WHOLE product-availability response
#: here, not just metaBundles -- and that response carries its own
#: attributes=[{"name": "Tier", "seq": "8", "value": "Tier 2"}]. A generic walk
#: finds that "name" and files "Tier" as a plan. Anchoring the descent means a
#: sibling field can never be mistaken for a product.
BUNDLE_CONTAINER_KEYS = frozenset((
    "metaBundles", "speedAndProductBundleList", "bundleList",
    "productBundleList", "speedAndProductBundles",
))

#: Longest plan string written to the deliverable, and the speed field's cap.
PLAN_CAP = 300
SPEED_CAP = 120


def _num(x):
    try:
        return int(x)
    except Exception:
        return None


def _tier_sort(k):
    """Deterministic key order: numeric speed tiers ascending, then names.

    A derived disposition must be rebuildable from immutable raw evidence, so
    the plan string may never depend on dict insertion order.
    """
    s = str(k)
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def _attrs(b):
    a = b.get("attributes")
    return a if isinstance(a, dict) else b


def _bundle_name(b):
    a = _attrs(b)
    for k in BUNDLE_NAME_KEYS:
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _bundle_speed(b, tier=""):
    a = _attrs(b)
    for k in BUNDLE_SPEED_KEYS:
        v = a.get(k)
        if isinstance(v, dict):          # {"speed": "300", "context": ...}
            v = v.get("speed")
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return tier


def _iter_bundles(node, tier="", depth=0):
    """Yield (bundle_dict, speed_tier) from EITHER shape of metaBundles."""
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and _bundle_name(item):
                yield item, tier
            else:
                yield from _iter_bundles(item, tier, depth + 1)
        return
    if not isinstance(node, dict):
        return
    # A speed-tier node: {"bundles": [...], "addons": {...}, "speed": {...}}.
    if isinstance(node.get("bundles"), list):
        sp = node.get("speed")
        if isinstance(sp, dict) and sp.get("speed"):
            tier = str(sp["speed"])
        elif isinstance(sp, (str, int)) and str(sp).strip():
            tier = str(sp).strip()
        for b in node["bundles"]:
            if isinstance(b, dict):
                yield b, tier
        return
    # A bare bundle handed in directly.
    if _bundle_name(node):
        yield node, tier
        return
    # Otherwise a container keyed by speed tier and/or bundle-list name. ONLY
    # those keys are descended -- see BUNDLE_CONTAINER_KEYS for why a generic
    # walk is unsafe here.
    for k in sorted(node, key=_tier_sort):
        ks = str(k)
        if ks.isdigit():
            yield from _iter_bundles(node[k], ks, depth + 1)
        elif ks in BUNDLE_CONTAINER_KEYS:
            yield from _iter_bundles(node[k], tier, depth + 1)


def bundle_has_price(b):
    """A plan with no price is not a plan you can sell."""
    a = _attrs(b)
    for k in PRICE_KEYS:
        v = a.get(k)
        if isinstance(v, dict):
            v = v.get("formatted") or v.get("original") or v.get("dollars")
        if isinstance(v, list) and v:
            v = (v[0] or {}).get("formatted") if isinstance(v[0], dict) else v[0]
        if v not in (None, "", "0", 0) and str(v).strip():
            return True
    return False


#: Keys that appear ONLY in a `carrier.plans()` failure marker, never in a
#: product-availability response. MEASURED against the stored evidence:
#: plans() returns {"why": "TRANSPORT ..."} on an exception, {"http": N} on a
#: non-200, and {"http": 200, "why": "non-JSON"} on a body it could not parse.
#: A real product response always carries product keys (metaBundles, errorInfo,
#: transactionId, ...) and never consists solely of these.
_FETCH_MARKER_KEYS = frozenset(("why", "http", "retry_after", "fl_fetch_error"))


def enrichment_unread(enrichment):
    """(unread, reason). "We could not read the plans" is NOT "there are no plans".

    This is the whole point of the 4.0 change. Before it, every one of these
    landed in CURRENT_UNRESOLVED_NO_PLAN_POPULATED next to genuine "the carrier
    priced nothing here" answers:

      * enrichment is None            -- the product call was never made
      * {"why": "TRANSPORT Timeout"}  -- it timed out          (MEASURED, n=7)
      * {"http": 403}                 -- the endpoint refused
      * {"http": 200, "why": "non-JSON"} -- we could not parse the body
      * {}                            -- an empty body. On 2026-09-01 an empty
        bundle call demoted every fiber address in a run; the sweep would have
        reported ZERO leads while appearing to work.

    Returns unread=False only for a payload that was actually READ, whatever it
    then contained -- including a genuinely empty product response, which is a
    real answer and stays NO_PLAN.
    """
    if enrichment is None:
        return True, "product_call_not_made"
    if isinstance(enrichment, dict):
        marker = enrichment.get("fl_fetch_error")
        if marker:
            return True, f"product_call_failed:{marker}"
        keys = set(enrichment)
        if not keys:
            return True, "product_call_returned_empty_body"
        if keys <= _FETCH_MARKER_KEYS:
            why = enrichment.get("why") or f"http_{enrichment.get('http')}"
            return True, f"product_call_failed:{why}"
        http = _num(enrichment.get("http"))
        if http is not None and http != 200:
            return True, f"product_call_failed:http_{http}"
        return False, ""
    if isinstance(enrichment, list):
        # `carrier.check` used to collapse a failed fetch to [] via
        # `bundles or []`, which is indistinguishable from a real empty read.
        # carrier.py no longer does that -- it hands over the failure marker --
        # so a bare empty list can only be a failure whose marker was lost.
        if not enrichment:
            return True, "product_call_returned_bare_empty_list"
        return False, ""
    return True, f"product_call_returned_{type(enrichment).__name__}"


def bundle_detail(enrichment):
    """(names, speeds) from the product-availability payload. NEVER raises.

    An unrecognised shape yields nothing, which leaves plan detail unavailable
    and therefore CANNOT demote a confirmed fiber qualification.
    """
    names, speeds = [], []
    try:
        for b, tier in _iter_bundles(enrichment):
            n = _bundle_name(b)
            # A named bundle with no price, or a "call to order" placeholder, is
            # NOT a populated plan and must not make an address serviceable.
            if n and any(c in n.lower() for c in CALL_TO_ORDER):
                continue
            if n and not bundle_has_price(b):
                continue
            if n:
                names.append(n)
            s = _bundle_speed(b, tier)
            if s:
                speeds.append(s)
    except Exception:
        pass
    return list(dict.fromkeys(names)), list(dict.fromkeys(speeds))


def _plan_string(head, names, cap=PLAN_CAP):
    """maxQual plus as many WHOLE bundle names as fit.

    Never emits a truncated name: half a bundle name still reads like a real
    plan, and this string is shipped.
    """
    out = head[:cap]
    for n in names:
        cand = out + " | " + n
        if len(cand) > cap:
            break
        out = cand
    return out


def classify(verdict, primary, enrichment=None, http=200):
    """One response -> one disposition. `primary` is the address/search payload,
    `enrichment` the optional product-availability payload.

    Returns a dict; `sub_status` carries the fail-closed detail and
    `classifier_version` travels with every result.
    """
    out = {"status": None, "sub_status": "", "reason": "", "technology": "",
           "plan": "", "max_speed": "", "http": http,
           "classifier_version": CLASSIFIER_VERSION,
           "exact_match": None, "error_code": None, "validation_result": None}

    if verdict in ("UNREAD", "TRANSPORT") or http != 200:
        code = _num((primary or {}).get("http")) or http
        out["http"] = code
        out["status"] = FAILED_RETRYABLE if code in STOP_HTTP else UNREAD
        out["sub_status"] = f"http_{code}" if code else "unrecognised_payload"
        out["reason"] = out["sub_status"]
        return out

    j = primary if isinstance(primary, dict) else {}
    out["error_code"] = _num(j.get("errorCode"))
    out["validation_result"] = j.get("validationResult")
    out["exact_match"] = j.get("exactMatch")

    # HTTP 200 is not a classification. The body decides.
    if "success" not in j:
        out["status"] = UNRESOLVED
        out["sub_status"] = UNKNOWN_RESPONSE
        out["reason"] = "http_200_without_success_field"
        return out

    if j.get("success") is True:
        tech = (j.get("techType") or "").upper()
        maxq = (j.get("maxQual") or "")
        out["technology"] = tech
        if tech in FIBER_TECHS:
            if not maxq or "FIBER" not in maxq.upper():
                out["status"] = UNRESOLVED
                out["sub_status"] = UNKNOWN_RESPONSE
                out["reason"] = f"techType {tech} with maxQual {maxq!r}"
                return out
            # CONFIRMED FIBER TECHNOLOGY. Whether it is a LEAD is decided by
            # the priced-plan rule below.
            #
            # ORDER MATTERS, AND THIS IS THE 4.0 FIX.
            # 1. Could we READ the plans at all? If not, we have no basis for
            #    any statement about plans -- not "no plan", not "pending
            #    order". "We failed to ask" leaves here, on its own, retryable.
            # 2. Only then may the read payload be interpreted.
            _unread, _why = enrichment_unread(enrichment)
            if _unread:
                out["status"] = UNRESOLVED
                out["sub_status"] = ENRICHMENT_UNREAD
                out["technology"] = tech
                out["reason"] = (f"fiber qualification {maxq!r}; the plans were "
                                 f"NOT read ({_why}) — this is our failure to "
                                 f"fetch, not a carrier answer; retry")
                return out
            # PERMANENT RULE: serviceable ONLY when a fiber plan actually
            # populates with available plans AND pricing. A maxQual string is a
            # qualification, not a plan; "call to order" is not serviceable.
            # PENDING ORDER is checked FIRST among the READ outcomes: it is a
            # definite carrier answer, not an absence of one, and must never be
            # filed as "no plan yet".
            _ei = (enrichment or {}).get("errorInfo") if isinstance(enrichment, dict) else None
            _ecode = str((_ei or {}).get("errorCode", "")).strip()
            _emsg = str((_ei or {}).get("errorMessage", "")).lower()
            if _ecode in PENDING_ORDER_CODES or any(t in _emsg for t in PENDING_ORDER_TEXT):
                out["status"] = UNRESOLVED
                out["sub_status"] = PENDING_ORDER
                out["technology"] = tech
                out["reason"] = (f"carrier reports a PENDING ORDER at this address "
                                 f"(errorCode {_ecode or 'n/a'}); not a prospect")
                return out
            _names, _speeds = bundle_detail(enrichment)
            if not _names:
                # The product response WAS read and carries no priced fiber
                # plan. A real answer, and not serviceable. Reaching here is now
                # proof the call succeeded -- enrichment_unread() has already
                # taken every could-not-read case out of this branch.
                out["status"] = UNRESOLVED
                out["sub_status"] = NO_PLAN
                out["reason"] = (f"fiber qualification {maxq!r}; product response "
                                 f"read and carries no priced plan")
                out["technology"] = tech
                return out
            out["status"] = FIBER_PLAN_POPULATED
            out["plan"] = maxq
            out["reason"] = "exact_address_fiber_qualification"
            bb = j.get("broadbandService") or {}
            primary_speed = str(bb.get("finalExpectedSpeedDown")
                                or bb.get("finalQualSpeed") or "")
            # Walks the REAL structure. Cannot raise, so a shape change can
            # never be mistaken for a transport failure by the caller.
            names, speeds = bundle_detail(enrichment)
            if names:
                out["plan"] = _plan_string(maxq, names)
                out["reason"] += f"; {len(names)}_bundles"
            elif enrichment is None:
                out["reason"] += "; enrichment not attempted"
            else:
                out["reason"] += "; enrichment returned no bundles (plan detail unavailable)"
            # Speed precedence: the primary payload's own figure FIRST -- it is
            # what every already-stored row carries, in kbps -- then the
            # enrichment's Mb tiers, then the qualification string. Enrichment
            # never OVERWRITES a primary figure: mixing kbps and Mb tiers inside
            # one shipped column is how a deliverable grows two unit conventions
            # halfway through a run.
            out["max_speed"] = (primary_speed
                                or (" | ".join(speeds)[:SPEED_CAP] if speeds else "")
                                or maxq)
            return out
        if tech in NONFIBER_TECHS:
            # A DEFINITIVE non-fiber "no" requires an EXACT premises match. A non-exact
            # match is "we asked badly" about SOME address, not "the answer is no" about
            # THIS premises -> keep it unresolved (remediation), never a terminal negative.
            # (2026-09-10 hardening; aligns nonfiber with the exact-match rule fiber already
            # requires to be a lead. Exact nonfiber is preserved.)
            if j.get("exactMatch") is not True:
                out["status"] = UNRESOLVED
                out["sub_status"] = NEEDS_FIX
                out["reason"] = (f"tech_{tech.lower()}_but_not_exact_match "
                                 f"(exactMatch={j.get('exactMatch')!r}) — re-ask with authoritative address")
                return out
            out["status"] = NONFIBER
            out["plan"] = maxq
            out["reason"] = f"tech_{tech.lower()}_exact"
            return out
        if tech:
            # A techType we have never measured. It used to fall into NONFIBER,
            # which is a TERMINAL "the answer is no" produced from a string
            # nobody has verified. Fail closed and say so loudly instead.
            out["status"] = UNRESOLVED
            out["sub_status"] = UNKNOWN_RESPONSE
            out["reason"] = (f"unmeasured techType {tech!r} — not classified as "
                             f"nonfiber without evidence")
            return out
        out["status"] = UNRESOLVED
        out["sub_status"] = UNKNOWN_RESPONSE
        out["reason"] = "success_true_without_techtype"
        return out

    # success is false. WHY decides, and only one reason is terminal.
    code = out["error_code"]
    vr = out["validation_result"] or ""
    if code in NEEDS_FIX_CODES or vr == "AddressNeedsFix":
        out["status"] = UNRESOLVED
        out["sub_status"] = NEEDS_FIX
        out["reason"] = f"carrier_could_not_match_our_address:{vr or code}"
        return out
    if code in ADDRESS_NOT_FOUND_CODES or vr == "AddressNotFound":
        # errorCode 1700 / AddressNotFound: the carrier could not MATCH the
        # address STRING we submitted (most are TIGER-interpolated synthetic
        # strings invented along a street centreline). This is "we asked badly",
        # the SAME family as 1065 AddressNeedsFix -- NOT "the answer is no". It
        # therefore takes the SAME remediation sub_status and the SAME drain
        # (needsfix_runner substitutes an authoritative address string and
        # re-asks); a distinct sub_status would land these in an ADDRESS_REMEDIATION
        # lane with no drain the engine runs. RETRYABLE, and it may NEVER become
        # CURRENT_NOT_FOUND -- only a verified out-of-territory reply (1060) may.
        # The exact code stays in the reason (and forever in the immutable
        # raw_json), so 1700 remains distinguishable from 1065.
        out["status"] = UNRESOLVED
        out["sub_status"] = NEEDS_FIX
        out["reason"] = ("carrier_could_not_match_our_address:AddressNotFound "
                         "(errorCode 1700; address string not found -- re-ask "
                         "with an authoritative address)")
        return out
    if vr == "AddressSuggestions":
        # The carrier could not match OUR address and offered alternatives.
        # Same class as 1065: we asked badly. MEASURED n=216 on the Inman run,
        # every one of them sitting in UNKNOWN_RESPONSE next to 5,356 genuine
        # carrier replies. It belongs in the remediation backlog.
        out["status"] = UNRESOLVED
        out["sub_status"] = ADDRESS_SUGGESTIONS
        out["reason"] = ("carrier_could_not_match_our_address:AddressSuggestions "
                         "(alternatives offered; re-ask with a corrected address)")
        return out
    if vr == "AddressUnserviceableInTerritory":
        # A REPLY, not an unreadable one -- but only errorCode 1060 /
        # AddressUnserviceableOutOfTerritory may become a terminal NOT_FOUND,
        # and the live meaning of this string has not been verified. Named, kept
        # unresolved, kept OPEN. MEASURED n=5,356 on the Inman run.
        out["status"] = UNRESOLVED
        out["sub_status"] = IN_TERRITORY_UNSERVICEABLE
        out["reason"] = ("carrier_reply_in_territory_unserviceable:"
                         "AddressUnserviceableInTerritory — meaning not verified "
                         "against the live buy flow; never a terminal no")
        return out
    if code in OUT_OF_TERRITORY_CODES or vr == "AddressUnserviceableOutOfTerritory":
        out["status"] = NOT_FOUND
        out["sub_status"] = "OUT_OF_TERRITORY"
        out["reason"] = f"carrier_out_of_territory:{vr or code}"
        return out
    # Fail closed. An unknown code is never a terminal no.
    out["status"] = UNRESOLVED
    out["sub_status"] = UNKNOWN_RESPONSE
    out["reason"] = f"unknown_carrier_response code={code} validationResult={vr!r}"
    return out
