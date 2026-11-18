"""Versioned, evidence-backed customer-status mapping from the current-response billingStatus.

Source/evidence (billing_config.json evidence_ref, sha stored per decision):
  tx_ledger_recovered.db evidence.raw_json primaries 2026-09-03..07 — billingStatus takes exactly
  two observed values: A (existing) and N (new). Live positive control 281 FURY DR INMAN SC = A.
  A=existing is the deployed engine's measured rule; N is the only other observed code.

Rules (STEP 2/3, Masani 2026-09-09):
  A            -> VERIFIED_EXISTING
  N            -> VERIFIED_NEW
  missing/other-> UNKNOWN   (NEVER inferred as new; stays a CUSTOMER_STATUS_HOLD, never exported)
"""
import json, os, hashlib

def _load():
    for c in (os.path.join(os.environ.get("FL_CONFIG") or "", "billing_config.json"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "billing_config.json"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "billing_config.json")):
        if c and os.path.exists(c):
            raw = open(c, "rb").read()
            return json.loads(raw).get("billing_status_mapping", {}), hashlib.sha256(raw).hexdigest(), c
    return {}, "", ""

_MAP, MAPPING_HASH, MAPPING_FILE = _load()
EXISTING = {str(v).strip().upper() for v in _MAP.get("existing_values", [])}
NEW = {str(v).strip().upper() for v in _MAP.get("new_values", [])}
EVIDENCE_REF = _MAP.get("evidence_ref", "")
MAPPING_VERSION = "billing-A_N-1.0.0"
LOADED = bool(EXISTING and NEW and MAPPING_HASH)

VERIFIED_EXISTING = "VERIFIED_EXISTING"
VERIFIED_NEW = "VERIFIED_NEW"
UNKNOWN = "UNKNOWN"

def customer_status(billing_status) -> str:
    """VERIFIED_EXISTING / VERIFIED_NEW / UNKNOWN from a raw current billingStatus.
    UNKNOWN when the mapping is not loaded, or the value is missing/unexpected — never guessed."""
    if not LOADED:
        return UNKNOWN
    bs = str(billing_status or "").strip().upper()
    if bs in EXISTING:
        return VERIFIED_EXISTING
    if bs in NEW:
        return VERIFIED_NEW
    return UNKNOWN

def stamp() -> dict:
    """Provenance stamped on every decision that used this mapping."""
    return {"mapping_version": MAPPING_VERSION, "mapping_hash": MAPPING_HASH[:16], "evidence_ref": EVIDENCE_REF[:80]}
