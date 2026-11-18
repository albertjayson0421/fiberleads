"""Fail-closed egress guard (Masani, 2026-09-09). Kinetic traffic MUST go through an explicitly
configured proxy. Direct egress is prohibited; a proxy failure aborts rather than silently
falling back to the direct connection; NO_PROXY/no_proxy bypass is rejected; raw egress IPs are
never printed — only a hash, country, and network class.

Default: REQUIRE_PROXY_EGRESS=1. Set REQUIRE_PROXY_EGRESS=0 only for offline/no-network tooling
that never touches the carrier.
"""
import os, re, hashlib

class EgressError(RuntimeError):
    """Raised to ABORT rather than ever egress directly. Callers must not swallow-then-direct."""

def _required(env) -> bool:
    return str(env.get("REQUIRE_PROXY_EGRESS", "1")) == "1"

_PROXY_RE = re.compile(r"^https?://[^\s/@]+(?::[^\s/@]*)?@?[^\s/]+:\d+$|^https?://[^\s/]+:\d+$")

def valid_proxy(proxy: str) -> bool:
    if not proxy or not isinstance(proxy, str):
        return False
    return bool(_PROXY_RE.match(proxy.strip()))

def assert_proxy_egress(proxy, env=None):
    """Fail closed. Raises EgressError unless a valid proxy is configured (when required) and no
    NO_PROXY bypass is set. Returns True when egress may proceed through `proxy`."""
    env = os.environ if env is None else env
    if env.get("NO_PROXY") or env.get("no_proxy"):
        raise EgressError("NO_PROXY/no_proxy is set — proxy-bypass rejected (fail closed)")
    if not _required(env):
        return True                      # explicit opt-out for offline tooling only
    if not proxy:
        raise EgressError("REQUIRE_PROXY_EGRESS=1 and no proxy configured — refusing direct egress")
    if not valid_proxy(proxy):
        raise EgressError("configured proxy is invalid — refusing direct egress (fail closed)")
    return True

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")

def redact(text: str) -> str:
    """Remove raw IPv4/IPv6 addresses from any string bound for a log or report."""
    if text is None:
        return text
    s = _IPV4.sub("<ip-redacted>", str(text))
    s = _IPV6.sub("<ip-redacted>", s)
    return s

_DATACENTER = ("hosting", "datacenter", "data center", "cloud", "ovh", "digitalocean",
               "amazon", "aws", "google", "gcp", "azure", "microsoft", "linode", "vultr",
               "hetzner", "leaseweb", "colo", "server")

def egress_class(org: str) -> str:
    o = (org or "").lower()
    return "datacenter" if any(k in o for k in _DATACENTER) else "residential"

def egress_report(info: dict) -> dict:
    """Safe egress descriptor: country, class, hashed egress. NEVER the raw IP."""
    ip = str(info.get("ip") or "")
    return {"country": info.get("country"),
            "network_class": egress_class(info.get("org") or ""),
            "egress_hash": hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else None}
