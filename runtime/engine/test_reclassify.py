"""Offline test for the D1 reclassifier."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reclassify import reclassify, TERMINAL, stronger, CLASSIFIER_VERSION
P = F = 0
def check(n, c):
    global P, F
    if c: P += 1; print("  PASS", n)
    else: F += 1; print("  FAIL", n)

# D1 core: non-definitive -> UNRESOLVED, never a definitive no
check("AddressNotFound -> UNRESOLVED_NOMATCH (not terminal)", reclassify("COMPLETE_REJECTED","AddressNotFound")[0]=="UNRESOLVED_NOMATCH")
check("AddressSuggestions -> UNRESOLVED_AMBIGUOUS", reclassify("COMPLETE_REJECTED","AddressSuggestions")[0]=="UNRESOLVED_AMBIGUOUS")
check("STILL_NOMATCH -> UNRESOLVED_NOMATCH", reclassify("STILL_NOMATCH")[0]=="UNRESOLVED_NOMATCH")
check("AddressFound -> held-needs-evidence (not a definitive no)", reclassify("COMPLETE_REJECTED","AddressFound")[0]=="UNRESOLVED_FOUND_NEEDS_EVIDENCE")
# item-3 correction: in-territory-unserviceable is NOT out-of-territory
check("AddressUnserviceableInTerritory -> UNRESOLVED_IN_TERRITORY_UNSERVICEABLE (NOT terminal)", reclassify("COMPLETE_REJECTED","AddressUnserviceableInTerritory")[0]=="UNRESOLVED_IN_TERRITORY_UNSERVICEABLE")
check("AddressUnserviceableOutOfTerritory -> DEFINITIVE_OUT_OF_TERRITORY (terminal, only 1060-class)", reclassify("COMPLETE_REJECTED","AddressUnserviceableOutOfTerritory")[0]=="DEFINITIVE_OUT_OF_TERRITORY")
# genuine terminals preserved (NOT over-corrected)
check("exact nonfiber stays DEFINITIVE_NONFIBER", reclassify("DEFINITIVE_NONFIBER")[0]=="DEFINITIVE_NONFIBER")
check("NOT_SERVICEABLE(status) -> DEFINITIVE_NONFIBER", reclassify("NOT_SERVICEABLE")[0]=="DEFINITIVE_NONFIBER")
check("SERVICEABLE(status) -> DELIVERABLE_FIBER", reclassify("SERVICEABLE")[0]=="DELIVERABLE_FIBER")
check("EXISTING(status) -> EXCLUDED_EXISTING", reclassify("EXISTING")[0]=="EXCLUDED_EXISTING")
# never invent a definitive no from missing/unknown evidence
check("unknown reject reason -> UNRESOLVED_UNKNOWN_RESPONSE (fail closed, not a no)", reclassify("COMPLETE_REJECTED","Weird")[0]=="UNRESOLVED_UNKNOWN_RESPONSE")
check("unknown class -> UNRESOLVED_UNKNOWN_RESPONSE (never definitive)", reclassify("???")[0]=="UNRESOLVED_UNKNOWN_RESPONSE")
check("no missing/nomatch path yields a DEFINITIVE_* no",
      all(reclassify(c,r)[0] not in ("DEFINITIVE_NONFIBER","DEFINITIVE_OUT_OF_TERRITORY")
          for c,r in [("COMPLETE_REJECTED","AddressNotFound"),("COMPLETE_REJECTED","AddressSuggestions"),
                      ("STILL_NOMATCH",None),("COMPLETE_REJECTED","AddressFound"),("???",None)]))
# priority merge
check("stronger() keeps terminal over unresolved", stronger("UNRESOLVED_NOMATCH","DELIVERABLE_FIBER")=="DELIVERABLE_FIBER")
check("classifier version stamped", isinstance(CLASSIFIER_VERSION, str) and "v2" in CLASSIFIER_VERSION)
print("\nRESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
