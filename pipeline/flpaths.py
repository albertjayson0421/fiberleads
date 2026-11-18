"""Central path config for the pipeline stages.

Paths come from here instead of being hardcoded in each script. Defaults are repo-relative so a
clean clone works out of the box; override any of them via environment variables or an optional
market.json at the repo root (or $FL_MARKET). Nothing in here is a secret.

Usage in a stage:
    import flpaths
    flpaths.ensure_engine_on_path()          # replaces the old /Users/masani | /Volumes/T7 sys.path hack
    import classifier, product_stage         # engine modules now importable
    inp = os.path.join(flpaths.DATA, "iowa_all.jsonl")
"""
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_HERE)                       # repo root (parent of pipeline/)
ENGINE = os.path.join(REPO, "runtime", "engine")   # vendored engine modules
CONFIG = os.path.join(REPO, "runtime", "config")   # billing_config.json, uqual.txt(.example)

# Optional overrides: market.json at the repo root (or $FL_MARKET). Absent -> defaults below.
_market = {}
_mpath = os.environ.get("FL_MARKET", os.path.join(REPO, "market.json"))
if os.path.exists(_mpath):
    try:
        _market = json.load(open(_mpath))
    except Exception:
        _market = {}


def _p(key, env, default):
    """Resolution order: market.json -> environment variable -> repo-relative default.
    A relative path value is resolved against the repo root so a committed config stays
    machine-independent (e.g. "./data" works on any clone)."""
    v = _market.get(key) or os.environ.get(env) or default
    if v and not os.path.isabs(v):
        v = os.path.join(REPO, v)
    return v


# Working data (universes / ledgers / deliverables) is gitignored and lives outside the code by
# default. Default to <repo>/data so a clean clone has a valid, writable location.
DATA = _p("data_dir", "FL_DATA", os.path.join(REPO, "data"))
OUT = _p("out_dir", "FL_OUT", os.path.join(DATA, "deliverable"))
LEDGERS = _p("ledgers_dir", "FL_LEDGERS", os.path.join(DATA, "ledgers"))
PRIOR = _p("prior_delivered", "FL_PRIOR", os.path.join(DATA, "prior_delivered"))


def ensure_engine_on_path():
    """Put the vendored engine dir on sys.path and point FL_CONFIG at the config dir.
    Replaces the hardcoded sys.path.insert('/Users/masani/...'|'/Volumes/T7/...') the old
    scripts used, which do not exist on a clean clone."""
    import sys
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    os.environ.setdefault("FL_CONFIG", CONFIG)


def data(*parts):
    """Join under the data dir."""
    return os.path.join(DATA, *parts)


def out(*parts):
    """Join under the output/deliverable dir."""
    return os.path.join(OUT, *parts)
