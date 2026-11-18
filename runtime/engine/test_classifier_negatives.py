"""Regression tests for the classifier's DEFINITIVE-NEGATIVE rules (item 3, 2026-09-10).
Each terminal-negative must rest on explicit, exact evidence; ambiguous/incomplete/
in-territory-unserviceable stays UNRESOLVED and never becomes a definitive no."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classifier as CL
P=F=0
def ck(n,c):
    global P,F
    if c: P+=1; print("  PASS",n)
    else: F+=1; print("  FAIL",n)

def cl(primary, enr=None, http=200): return CL.classify("OK", primary, enrichment=enr, http=http)

FIBER_MAXQ="QUAL UP TO 2 GIG RANGE VIA FIBER"
PRICED={"metaBundles":{"speedAndProductBundleList":{"1000":{"bundles":[{"bundleName":"Gig Fiber","standardPrice":"39.99"}],"speed":{"speed":"1000"}}}},"transactionId":"t"}
NOPLAN={"metaBundles":{},"transactionId":"t"}   # read, but no priced bundle

# --- terminal negatives require EXACT + explicit evidence ---
r=cl({"success":True,"techType":"COPPER","exactMatch":True,"maxQual":"copper"})
ck("exact COPPER -> NONFIBER (terminal, supported)", r["status"]==CL.NONFIBER)
r=cl({"success":True,"techType":"COPPER","exactMatch":False,"maxQual":"copper"})
ck("NON-EXACT COPPER -> UNRESOLVED/NEEDS_FIX (not a terminal no)", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.NEEDS_FIX)
r=cl({"success":False,"validationResult":"AddressUnserviceableOutOfTerritory"})
ck("OutOfTerritory -> NOT_FOUND (terminal, only 1060-class)", r["status"]==CL.NOT_FOUND)
r=cl({"success":False,"errorCode":1060})
ck("errorCode 1060 -> NOT_FOUND (terminal)", r["status"]==CL.NOT_FOUND)

# --- in-territory-unserviceable is NOT out-of-territory (the item-3 distinction) ---
r=cl({"success":False,"validationResult":"AddressUnserviceableInTerritory"})
ck("InTerritoryUnserviceable -> UNRESOLVED/IN_TERRITORY_UNSERVICEABLE (never terminal)",
   r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.IN_TERRITORY_UNSERVICEABLE)
ck("  ...and it does NOT close the address", CL.closes_address(r["status"], r["sub_status"]) is False)

# --- 'asked badly' stays unresolved ---
r=cl({"success":False,"validationResult":"AddressNotFound"})
ck("AddressNotFound -> UNRESOLVED/NEEDS_FIX", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.NEEDS_FIX)
r=cl({"success":False,"validationResult":"AddressSuggestions"})
ck("AddressSuggestions -> UNRESOLVED/ADDRESS_SUGGESTIONS", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.ADDRESS_SUGGESTIONS)

# --- HTTP success / empty plan must NOT become a definitive no ---
r=cl({})
ck("HTTP 200 with no success field -> UNRESOLVED/UNKNOWN_RESPONSE (not a no)", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.UNKNOWN_RESPONSE)
r=cl({"success":True,"techType":"FIBER","exactMatch":True,"maxQual":FIBER_MAXQ,"dfAddressId":"d","uqualProvisioningResult":"u"}, enr=None)
ck("fiber exact + enrichment NOT read -> ENRICHMENT_UNREAD (retryable, not no-plan)", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.ENRICHMENT_UNREAD)
r=cl({"success":True,"techType":"FIBER","exactMatch":True,"maxQual":FIBER_MAXQ,"dfAddressId":"d","uqualProvisioningResult":"u"}, enr=NOPLAN)
ck("fiber exact + read-but-empty plans -> NO_PLAN (real answer, not serviceable)", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.NO_PLAN)
r=cl({"success":True,"techType":"SATELLITE","exactMatch":True,"maxQual":"sat"})
ck("unmeasured techType -> UNKNOWN_RESPONSE (never silent nonfiber)", r["status"]==CL.UNRESOLVED and r["sub_status"]==CL.UNKNOWN_RESPONSE)

# --- positive still works ---
r=cl({"success":True,"techType":"FIBER","exactMatch":True,"maxQual":FIBER_MAXQ,"dfAddressId":"d","uqualProvisioningResult":"u"}, enr=PRICED)
ck("fiber exact + priced plan -> FIBER_PLAN_POPULATED", r["status"]==CL.FIBER_PLAN_POPULATED)

print("\nRESULT: %d passed, %d failed" % (P,F))
sys.exit(1 if F else 0)
