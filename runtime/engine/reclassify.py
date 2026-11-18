"""Evidence reclassifier for the D1 replay (2026-09-10).

Maps a STORED ledger disposition (+ its reason/status) to the D1-corrected disposition.
Rule: a search-stage non-definitive signal (NOMATCH / AddressSuggestions / non-exact /
still-nomatch) is UNRESOLVED (retryable), never a terminal reject. Missing evidence,
access errors, malformed/unknown responses, and unresolved matches NEVER become a
definitive no-fiber outcome (fail closed to UNRESOLVED_*).
"""
CLASSIFIER_VERSION = "iowa-reclass-v2-2026-09-10"

TERMINAL = {"DELIVERABLE_FIBER", "EXCLUDED_EXISTING", "EXCLUDED_PENDING_ORDER",
            "DEFINITIVE_NONFIBER", "DEFINITIVE_OUT_OF_TERRITORY"}
# priority: lower wins when a canonical key has several observations across passes
PRIORITY = ["DELIVERABLE_FIBER", "EXCLUDED_EXISTING", "EXCLUDED_PENDING_ORDER",
            "DEFINITIVE_NONFIBER", "DEFINITIVE_OUT_OF_TERRITORY",
            "UNRESOLVED_IN_TERRITORY_UNSERVICEABLE", "UNRESOLVED_FOUND_NEEDS_EVIDENCE",
            "UNRESOLVED_AMBIGUOUS", "UNRESOLVED_NOMATCH", "UNRESOLVED_RETRYABLE",
            "UNRESOLVED_UNKNOWN_RESPONSE"]
_PR = {k: i for i, k in enumerate(PRIORITY)}


def reclassify(old_class, reason=None):
    """Return (new_disposition, change_reason) for one stored observation.
    old_class is the ledger 'class' OR the remediation 'status'."""
    c = str(old_class or "")
    r = str(reason or "")
    # already-terminal positives / exclusions / definitive negatives (unchanged intent)
    if c in ("SERVICEABLE_FIBER",) or c == "SERVICEABLE":
        return "DELIVERABLE_FIBER", "unchanged: proven fiber-with-plan"
    if c == "EXCLUDED_EXISTING" or c == "EXISTING":
        return "EXCLUDED_EXISTING", "unchanged: verified existing customer"
    if c == "EXCLUDED_PENDING_ORDER":
        return "EXCLUDED_PENDING_ORDER", "unchanged: pending order"
    if c == "DEFINITIVE_NONFIBER" or c == "NOT_SERVICEABLE":
        return "DEFINITIVE_NONFIBER", "unchanged: exact non-fiber"
    if c == "DEFINITIVE_OUT_OF_TERRITORY":
        return "DEFINITIVE_OUT_OF_TERRITORY", "unchanged: carrier out-of-territory"
    if c == "STILL_NOMATCH":
        return "UNRESOLVED_NOMATCH", "D1: remediation still could not match -> asked-badly, not a proven no"
    if c == "INCOMPLETE_RETRYABLE":
        return "UNRESOLVED_RETRYABLE", "unchanged: incomplete workflow, retryable"
    if c == "COMPLETE_REJECTED":
        if r == "AddressUnserviceableInTerritory":
            return ("UNRESOLVED_IN_TERRITORY_UNSERVICEABLE",
                    "CORRECTED per classifier contract: in-territory-unserviceable is a carrier reply "
                    "of UNVERIFIED meaning -> UNRESOLVED, NEVER a terminal no. Only errorCode 1060 / "
                    "AddressUnserviceableOutOfTerritory may become DEFINITIVE_OUT_OF_TERRITORY.")
        if r == "AddressUnserviceableOutOfTerritory":
            return "DEFINITIVE_OUT_OF_TERRITORY", "explicit out-of-territory (1060-class) -> terminal"
        if r == "AddressNotFound":
            return "UNRESOLVED_NOMATCH", "D1: AddressNotFound is NOMATCH (asked/not-found), never a proven no"
        if r == "AddressSuggestions":
            return "UNRESOLVED_AMBIGUOUS", "D1: AddressSuggestions is non-exact/ambiguous -> remediation, not terminal"
        if r == "AddressFound":
            return "UNRESOLVED_FOUND_NEEDS_EVIDENCE", "D1: exact match found but disposition not provable from reason alone (raw response not stored) -> held, not a definitive no"
        return "UNRESOLVED_UNKNOWN_RESPONSE", "D1: reject reason %r unrecognized -> fail closed to unresolved" % r
    # anything else: fail closed, never invent a definitive no
    return "UNRESOLVED_UNKNOWN_RESPONSE", "unrecognized old_class %r -> fail closed" % c


def stronger(a, b):
    """Return the higher-priority of two dispositions (terminal proven beats unresolved)."""
    if a is None: return b
    if b is None: return a
    return a if _PR.get(a, 99) <= _PR.get(b, 99) else b
