"""Validated DataImpulse sticky-route construction (2026-09-10).

Single source of truth for the proxy username grammar, verified against DataImpulse
docs (parameters / country / session-id):
    username = BASE_LOGIN + "__cr.us;sessid." + ACTOR_SESSION_ID
    password = BASE_PASSWORD
Rejects malformed pools and base logins that already carry a targeting suffix
(the duplicate-suffix defect). fleet_actor_v2.sticky() should delegate here.
"""
from urllib.parse import quote

COUNTRY_TAG = "__cr.us"
SESSION_TAG = ";sessid."          # verified: pins the exit; the old "__sid." was ignored


def validate_pool(p) -> list:
    """Return a list of defect strings ([] == valid)."""
    errs = []
    if not isinstance(p, dict):
        return ["not_a_dict"]
    for k in ("user", "pass", "host", "port"):
        if not p.get(k):
            errs.append("missing_" + k)
    u = str(p.get("user", ""))
    # base login must NOT already carry targeting/session suffixes (would duplicate)
    for bad in ("__cr", ";sessid", "__sid"):
        if bad in u:
            errs.append("base_login_has_suffix:" + bad)
    if ";" in u or " " in u:
        errs.append("base_login_has_illegal_char")
    return errs


def build_sticky_url(p, session_id: str) -> str:
    """Build the full authenticated proxy URL for one actor/session. Raises
    ValueError on a malformed pool. Never log the return value (it carries creds)."""
    errs = validate_pool(p)
    if errs:
        raise ValueError("malformed pool: " + ",".join(errs))
    if not session_id or not str(session_id).strip():
        raise ValueError("empty session_id")
    user = str(p["user"]) + COUNTRY_TAG + SESSION_TAG + str(session_id)
    return ("http://" + quote(user, safe="") + ":" + quote(str(p["pass"]), safe="")
            + "@" + str(p["host"]) + ":" + str(p["port"]))


def sanitized_config_hash(p, session_id: str) -> str:
    """Non-reversible fingerprint of the effective config (safe to log)."""
    import hashlib
    basis = "|".join([COUNTRY_TAG, SESSION_TAG,
                      hashlib.sha256(str(p.get("user", "")).encode()).hexdigest()[:12],
                      str(p.get("host")), str(p.get("port")),
                      hashlib.sha256(str(session_id).encode()).hexdigest()[:8]])
    return hashlib.sha256(basis.encode()).hexdigest()[:16]
