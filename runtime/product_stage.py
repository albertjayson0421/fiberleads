"""PRODUCT STAGE — turns a UqualClient technology outcome into a serviceability
disposition using the project's existing classifier (inman-classifier/4.2) as
the single classification authority. Adds only the gates the standard requires.

Order (merge correction #1-#4, 2026-09-07):
  1. FIBER + VERIFIED existing customer          -> EXISTING_CUSTOMER_EXCLUDED, product call skipped
  2. FIBER without (success & exactMatch & dfAddressId & uqualProvisioningResult)
                                                 -> PRODUCT_PENDING / nonexact_or_missing_identity
  3. product call on the SAME client/session, bound to this search's context
       fetch marker context_invalidated/missing  -> PRODUCT_UNRESOLVED (runner requalifies)
       ENRICHMENT_UNREAD (fetch failed)          -> PRODUCT_UNRESOLVED (retry)
       PENDING_ORDER(1080) / NO_PLAN / other     -> PRODUCT_PENDING (real answer, not a lead)
       FIBER_PLAN_POPULATED + >=1 PRICED bundle whose NAME is a fiber internet tier
                                                 -> SERVICEABLE_FIBER only if customer status is
                                                    VERIFIED not-existing; otherwise
                                                    EXISTING_STATUS_MISSING_HOLD (evidence kept)
       FIBER_PLAN_POPULATED, only copper tiers   -> PRODUCT_PENDING / priced_but_no_fiber_tier_bundle
  4. every non-FIBER outcome                     -> classifier verbatim
     (AddressUnserviceableInTerritory stays UNRESOLVED even at exactMatch=true;
      only errorCode 1060 may become NOT_FOUND; suggestions/needs-fix stay unresolved).

Customer status is TRI-STATE (True/False/None) and must be VERIFIED by the
operator-supplied, evidence-referenced billing mapping before it can either
exclude (True) or permit a lead (False). Unknown is a hold, never False.
"""
import classifier as CL
import billing_mapping  # versioned, evidence-backed A=existing / N=new customer-status mapping

SERVICEABLE_FIBER = "SERVICEABLE_FIBER"
EXISTING_EXCLUDED = "EXISTING_CUSTOMER_EXCLUDED"
EXISTING_UNVERIFIED_HOLD = "EXISTING_STATUS_MISSING_HOLD"
PRODUCT_PENDING = "PRODUCT_PENDING"
PRODUCT_UNRESOLVED = "PRODUCT_UNRESOLVED"

# Fail-closed fiber-tier NAME rule (verbatim from carrier_kinetic._is_fiber_plan;
# copied so the boxes never import the retired cart client). A component
# (modem/wifi/upgrade/phone/credit...) is never an internet fiber plan.
_NON_INTERNET = ("basic", "upgrade", "wifi", "wi-fi", "modem", "router",
                 "gateway", "equipment", "security", "protect", "camera",
                 "voice", "phone", "unlimited phone", "credit", "discount",
                 "waiver", "install", "activation", "gift", "card", "addon",
                 "add-on", " tv", "stream", "mobile", "warranty")


def is_fiber_tier_name(name):
    n = (name or "").lower()
    if any(t in n for t in _NON_INTERNET):
        return False
    if "internet" not in n and "speed" not in n:
        return False
    return "fiber" in n or "gig" in n


from decimal import Decimal, InvalidOperation


def _money(v):
    """Decimal from the carrier's price shape ({'original','formatted','dollars','cents'}
    or a bare str/number); None when absent/unparseable. Never treats bool as money."""
    if isinstance(v, dict):
        v = v.get("original") if v.get("original") not in (None, "") else v.get("formatted")
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _positive_standard(b):
    """A REAL recurring price: standardPrice > 0.

    Mirrors carrier_kinetic.certify_priced_fiber's `has_standard_recurring`. A $0
    STANDARD price is not a sellable base plan; a $0 PROMO over a >0 standard still
    qualifies (via the standard). Closes the classifier edge where a formatted
    "0.00" passes bundle_has_price (it only rejects "0"/0) — validated case L.
    """
    a = b.get("attributes") if isinstance(b.get("attributes"), dict) else b
    std = _money(a.get("standardPrice"))
    return std is not None and std > 0


def priced_fiber_bundles(enrichment):
    """Names of bundles that are (1) priced by the classifier's rule, (2) not a
    call-to-order placeholder, (3) a fiber INTERNET tier by name, and (4) carry a
    positive standard recurring price. Walks the real metaBundles shape via the
    classifier's own iterator; never raises."""
    out = []
    try:
        for b, _tier in CL._iter_bundles(enrichment):
            n = CL._bundle_name(b)
            if not n or any(c in n.lower() for c in CL.CALL_TO_ORDER):
                continue
            if not CL.bundle_has_price(b) or not is_fiber_tier_name(n) or not _positive_standard(b):
                continue
            out.append(n)
    except Exception:
        pass
    return list(dict.fromkeys(out))


def decide(outcome, det, fetch_plans):
    """(status, sub_status, evidence).

    `outcome`/`det` are exactly what UqualClient.check() returned (det carries the
    tri-state `existing_customer`, `customer_mapping_verified`, `exactMatch`,
    `success`, `dfAddressId`, `uqualProvisioningResult`, `_raw_search`, `_ctx`).
    `fetch_plans(df, uqual, ctx) -> (bundles, txn, raw)` must be the SAME client's
    plans() so the product call rides the session that answered the search.
    """
    det = det or {}
    raw = det.get("_raw_search")
    #: CUSTOMER STATUS from the raw current-response billingStatus via the versioned, evidence-
    #: backed mapping (A=existing, N=new). The frozen transport client hardcodes
    #: customer_mapping_verified=False, so the classifier derives verification here from the mapping
    #: + a present A/N value. UNKNOWN (missing/unexpected) is a HOLD, never inferred as new.
    bs = det.get("billingStatus")
    cust = billing_mapping.customer_status(bs)      # VERIFIED_EXISTING / VERIFIED_NEW / UNKNOWN
    ec = {"VERIFIED_EXISTING": True, "VERIFIED_NEW": False, "UNKNOWN": None}[cust]
    verified = cust in ("VERIFIED_EXISTING", "VERIFIED_NEW") and billing_mapping.LOADED
    ev = {"dfAddressId": det.get("dfAddressId"), "carrier_address": det.get("carrier_address"),
          "exactMatch": det.get("exactMatch"), "billing_status": bs, "customer_status": cust,
          "existing_customer": ec, "customer_mapping_verified": verified,
          "classifier_version": CL.CLASSIFIER_VERSION, **billing_mapping.stamp()}

    if outcome in ("UNREAD", "UNASKED"):
        return "UNREAD", str(det.get("stage") or ""), ev

    if outcome == "FIBER":
        # 1. a VERIFIED existing customer never needs pricing; skip the call.
        if cust == "VERIFIED_EXISTING":
            return EXISTING_EXCLUDED, "verified_existing_customer", ev
        # 2. exact-premise identity + the string the product call must echo.
        if not (det.get("success") is True and det.get("exactMatch") is True
                and det.get("dfAddressId") and det.get("uqualProvisioningResult")):
            ev["missing"] = [k for k in ("success", "exactMatch", "dfAddressId", "uqualProvisioningResult")
                             if not det.get(k)]
            return PRODUCT_PENDING, "nonexact_or_missing_identity", ev
        # 3. the product call, bound to this search's session/token context.
        bundles, txn, products = fetch_plans(det["dfAddressId"], det["uqualProvisioningResult"], det.get("_ctx"))
        marker = products.get("fl_fetch_error") if isinstance(products, dict) else None
        if marker in ("context_invalidated", "missing_context"):
            ev["fetch_error"] = marker
            return PRODUCT_UNRESOLVED, marker + "_requalify", ev
        enrichment = products if isinstance(products, dict) else bundles
        res = CL.classify("FIBER", raw, enrichment=enrichment)
        ev.update({"txn": txn, "product_status": res.get("status"), "product_sub": res.get("sub_status"),
                   "reason": (res.get("reason") or "")[:200], "plan": res.get("plan"),
                   "max_speed": res.get("max_speed"), "fetch_error": marker,
                   "product_http": (products or {}).get("http_status") if isinstance(products, dict) else None})
        if res.get("sub_status") == CL.ENRICHMENT_UNREAD:
            return PRODUCT_UNRESOLVED, str(marker or "enrichment_unread"), ev
        if res.get("status") != CL.FIBER_PLAN_POPULATED:
            # PENDING_ORDER / NO_PLAN / UNKNOWN_RESPONSE: a real answer, not a lead, kept open.
            return PRODUCT_PENDING, str(res.get("sub_status") or res.get("status")), ev
        fib = priced_fiber_bundles(enrichment)
        if not fib:
            return PRODUCT_PENDING, "priced_but_no_fiber_tier_bundle", ev
        ev["fiber_bundles"] = fib[:4]
        # A populated priced fiber plan is a LEAD only with a VERIFIED not-existing (billingStatus=N)
        # customer status from the versioned mapping. UNKNOWN/missing stays a hold, never a lead.
        if cust == "VERIFIED_NEW":
            return SERVICEABLE_FIBER, "priced_fiber_tier_verified_new", ev
        ev["hold_reason"] = "billing_status_unknown_or_missing"
        return EXISTING_UNVERIFIED_HOLD, ev["hold_reason"], ev

    # 4. every non-fiber outcome goes through the same classifier, verbatim.
    if isinstance(raw, dict):
        res = CL.classify(outcome, raw, enrichment=None)
        ev.update({"reason": (res.get("reason") or "")[:200], "validation_result": res.get("validation_result"),
                   "error_code": res.get("error_code")})
        return str(res.get("status")), str(res.get("sub_status") or ""), ev
    return outcome, "", ev
