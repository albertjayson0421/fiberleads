"""Offline tests for proxy config: parameter construction, actor isolation, route
consistency with the production builder, and rejection of malformed configuration.
Prints only PASS/FAIL and sanitized facts — never a credential or full proxy URL."""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
MERGE = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, MERGE)
from proxy_config import build_sticky_url, validate_pool, sanitized_config_hash, COUNTRY_TAG, SESSION_TAG

P = F = 0
def check(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

GOOD = {"user": "baselogin123456789a", "pass": "secretpw12345678", "host": "gw.dataimpulse.com", "port": 823}

print("[parameter construction]")
u = build_sticky_url(GOOD, "abc123def456")
check("exactly one country tag", u.count(COUNTRY_TAG.replace(".", ".")) == 1 and u.count("__cr.us") == 1)
check("exactly one session tag", u.count("sessid.") == 1)
check("uses corrected ;sessid. grammar, not old __sid.", "%3Bsessid." in u or ";sessid" in u)
check("old rotating grammar '__sid.' absent", "__sid." not in u.replace(";sessid.", ""))
check("host and port present", "gw.dataimpulse.com:823" in u)
check("session id present in url", "abc123def456" in u)

print("[actor isolation: distinct session ids -> distinct routes]")
u1 = build_sticky_url(GOOD, "sess0000aaaa")
u2 = build_sticky_url(GOOD, "sess1111bbbb")
check("two actors get two different sticky urls", u1 != u2)
check("but share the same base login (same pool)", u1.split("__cr")[0] == u2.split("__cr")[0])
check("config hash differs per session", sanitized_config_hash(GOOD, "sess0000aaaa") != sanitized_config_hash(GOOD, "sess1111bbbb"))
check("config hash deterministic", sanitized_config_hash(GOOD, "x") == sanitized_config_hash(GOOD, "x"))

print("[rejection of malformed configuration]")
check("missing pass rejected", "missing_pass" in validate_pool({"user": "u", "host": "h", "port": 1}))
check("missing user rejected", "missing_user" in validate_pool({"pass": "p", "host": "h", "port": 1}))
check("base login WITH targeting suffix rejected (duplicate-suffix defect)",
      any(e.startswith("base_login_has_suffix") for e in validate_pool({**GOOD, "user": "login__cr.us;sessid.x"})))
check("base login with ';' rejected", "base_login_has_illegal_char" in validate_pool({**GOOD, "user": "bad;login"}))
raised = False
try: build_sticky_url({"user": "u", "host": "h", "port": 1}, "s")
except ValueError: raised = True
check("build raises on malformed pool", raised)
raised = False
try: build_sticky_url(GOOD, "")
except ValueError: raised = True
check("build raises on empty session id", raised)

print("[route consistency with production builder fleet_actor_v2.sticky]")
try:
    os.environ.setdefault("FL_CONFIG", os.path.join(MERGE, "config"))
    import fleet_actor_v2 as FA
    url, name = FA.sticky([GOOD], 0)
    check("production sticky uses ;sessid. grammar", ";sessid." in url or "%3Bsessid." in url)
    check("production sticky does NOT use old __sid.", "__sid." not in url.replace(";sessid.", ""))
    check("production sticky has exactly one __cr.us", url.count("__cr.us") == 1)
    u_a = FA.sticky([GOOD], 0)[0]; u_b = FA.sticky([GOOD], 0)[0]
    check("production sticky: two calls -> different sessids (actor isolation)", u_a != u_b)
except Exception as e:
    print("  SKIP production-builder consistency (import failed: %s: %s)" % (type(e).__name__, e))

print("[no-duplicate-suffix on REAL residential.json base logins]")
try:
    pools = [p for p in json.load(open(os.path.join(MERGE, "residential.json")))["pools"]
             if str(p.get("name", "")).startswith("dataimpulse")]
    bad = [i for i, p in enumerate(pools) if any(e.startswith("base_login_has_suffix") for e in validate_pool(p))]
    check("no dataimpulse base login carries a targeting suffix (%d pools)" % len(pools), len(bad) == 0)
except Exception as e:
    print("  SKIP residential.json scan (%s)" % e)

print("\nRESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
