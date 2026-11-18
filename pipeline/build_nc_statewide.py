#!/usr/bin/env python3
"""NC statewide sweepable run.db. NO NETWORK CARRIER.

REPLICATES the proven TX statewide build (TX-statewide-2026-09-03/
build_statewide_scope.py) for North Carolina in every mechanical respect: the
verbatim TX-etx schema, baseline+assignments+residential_screen seeded, all
pipeline tables empty, assignments completed=0/attempts=0 (NEVER_ASKED ->
reaskable), residential_screen=SCREENED_RESIDENTIAL. Makes ZERO carrier/proxy
requests to buy.gokinetic.com. It DOES download public authoritative data:
  * NC OneMap AddressNC (the NC 911 board's statewide NG911 address points) --
    authoritative ROOFTOP coordinate per address.
  * FEMA USA Structures OCC_CLS -- the mandated residential screen.
Neither is the carrier.

SCOPE = all 100 North Carolina counties from the official state denominator.
The historical 27-county Windstream footprint remains available only through
``FL_NC_SCOPE=footprint`` for reproducibility. ``county_name`` is carried on
every row so a later authorized provider can narrow territory without rebuilding
or losing the statewide universe.

RESIDENTIAL FILTER FIRST. Every acquired address is FEMA-screened BEFORE any row
is seeded. residential_filter._judge returns keep / hold / drop:
  keep  = confident OCC_CLS=Residential (corroborated, within 10 m, adequate size)
  hold  = UNCERTAIN: no FEMA footprint at the point / nearest home >10 m /
          OCC_CLS=Unclassified / area unknown / too small / buildings disagree
  drop  = confident non-residential: Commercial/Industrial/Education/Government/
          Assembly/Agriculture/Utility (a DROP_CLASS) or an outbuilding.

SEED = keep + hold  (everything NOT confidently dropped). This DIVERGES from the
TX reference, which seeded keep only. The divergence is MANDATORY here, not a
preference:
  * Objective-Preservation rule F (CLAUDE.md): "A missing footprint, a coordinate
    mismatch, or a distance over 30 m cannot by itself establish that an address
    is non-residential." The hold bucket IS exactly those cases.
  * FAIL-OPEN territory rule: "An uncertain address stays eligible. Exclude only
    with documented evidence placing it outside the footprint."
  * Task req(2): "drop confident non-residential, keep Residential+Unclassified."
  * Never cut leads. Windstream/Kinetic is a RURAL ILEC; in its rural counties
    the majority of NG911 addresses have no FEMA footprint (measured on CLAY:
    keep 18.7% / hold 80.7% / drop 0.4%), so keep-only would set aside most
    genuine rural prospects -- using the building gate to prove non-residential,
    which rule F forbids.
Only DROP is excluded from the sweep, because a DROP carries documented
non-residential OCC_CLS evidence -- the one thing rule F requires to exclude.
DROP and HOLD are BOTH preserved in universe.jsonl; nothing is deleted.

scope_reason on every seeded row is NET_NEW_STATEWIDE_KEEP (confident residential)
or NET_NEW_STATEWIDE_HOLD_FAILOPEN (uncertain, kept fail-open). A strict
keep-only sweep is therefore `WHERE scope_reason='NET_NEW_STATEWIDE_KEEP'` -- a
filter, never a rebuild. residential_screen.status is uniformly
SCREENED_RESIDENTIAL (the screen's PASS = not-confidently-non-residential, per
req(2)); the raw FEMA verdict (keep/hold) and reason are preserved verbatim in
residential_screen.reason / .evidence and baseline.residential_evidence, so no
bucket is collapsed.

NET-NEW. eligible(keep+hold, unique canonical) minus delivered_NC
(delivery_history, ADDRESS|ZIP tolerant). No NC run.db exists, so there is NO
settled-terminal set to subtract (unlike TX, which subtracted etx/wtx CLOSED).

Schema is the verbatim .schema of TX-etx-2026-09-01/run.db (work/etx_schema.sql,
sqlite_sequence line removed -- it is auto-created by AUTOINCREMENT and cannot be
CREATEd). Only baseline / assignments / residential_screen are seeded; every
pipeline table is created empty.
"""
from __future__ import annotations

import json
import os
import sys
import time

import flpaths
flpaths.ensure_engine_on_path()   # was: three /Volumes/T7 engine dirs
# NOTE (clean-clone blocker, flagged for owner): this stage also imports runconfig, canonicalize,
# delivery_history, nc_onemap_client, source_health, which are NOT vendored in runtime/engine. They
# must be added to the repo before build_nc_statewide can run from a clean clone.

# rural NC counties are majority no-footprint/Unclassified -> high FEMA "held"
# share; we SEED held (fail-open), so the residential_filter refusal guard that
# trips on a high held share is intentionally relaxed. This is the operator
# decision residential_filter asks to be made out loud.
os.environ.setdefault("FL_ACCEPT_HELD_SHARE", "0.999")
os.environ.setdefault("FL_FOOTPRINT_CACHE", "1")

import runconfig as RC          # noqa: E402
RC.add_paths("carrier")
import canonicalize as C        # noqa: E402
import delivery_history as DH   # noqa: E402
import residential_filter as RF  # noqa: E402
import sqlite3                   # noqa: E402
from nc_onemap_client import (OneMapClient, SourceFailure,
                              SourceUnavailable, retry_delay)  # noqa: E402
from source_health import SourceHealthReporter  # noqa: E402

ROOT = os.environ.get("FL_NC_ROOT", "/Volumes/T7/Masani Files/runs/NC-statewide-2026-09-03/")
DB = os.environ.get("FL_NC_DB", ROOT + "run.db")
RUN_ID = "nc-statewide-2026-09-03"
STATE = "NC"
SCHEMA_SQL = "/Volumes/T7/Masani Files/runs/NC-statewide-2026-09-03/work/etx_schema.sql"
UNIVERSE_OUT = ROOT + "universe.jsonl"
NC_ADDR = ("https://services.nconemap.gov/secure/rest/services/AddressNC/"
           "NC1Map_Addresses/MapServer/0/query")
FIELDS = "st_address,post_code,inc_muni,uninc_comm,msagcomm,county,unit,building_type,placement"
PAGE = 2000
SOURCE_HEALTH = ROOT + "work/nc_onemap_resolver_health.json"
SOURCE_HEALTH_DB = os.environ.get(
    "FL_SOURCE_HEALTH_DB", DURABLE_ENGINE + "/source_health.sqlite")
SOURCE_ID = "nc_onemap"
TASK_ID = os.environ.get("FL_TASK_ID", "nc-statewide100-v3")

#: The historical carrier-footprint subset is retained for reproducibility, but
#: the delivery requirement is statewide. The authoritative denominator is the
#: State of North Carolina's 100-county list. AddressNC also currently exposes a
#: stray ``SEVIER`` value; it is not an NC county and is deliberately excluded.
FIPS2NAME = {
    "37007": "ANSON", "37025": "CABARRUS", "37037": "CHATHAM", "37043": "CLAY",
    "37051": "CUMBERLAND", "37057": "DAVIDSON", "37067": "FORSYTH",
    "37081": "GUILFORD", "37085": "HARNETT", "37093": "HOKE", "37097": "IREDELL",
    "37105": "LEE", "37119": "MECKLENBURG", "37123": "MONTGOMERY",
    "37125": "MOORE", "37149": "POLK", "37151": "RANDOLPH", "37153": "RICHMOND",
    "37155": "ROBESON", "37159": "ROWAN", "37161": "RUTHERFORD", "37165": "SCOTLAND",
    "37167": "STANLY", "37169": "STOKES", "37171": "SURRY", "37179": "UNION",
    "37183": "WAKE"}
FOOTPRINT_COUNTIES = tuple(sorted(FIPS2NAME.values()))
STATEWIDE_COUNTIES = tuple(sorted({
    "ALAMANCE", "ALEXANDER", "ALLEGHANY", "ANSON", "ASHE", "AVERY",
    "BEAUFORT", "BERTIE", "BLADEN", "BRUNSWICK", "BUNCOMBE", "BURKE",
    "CABARRUS", "CALDWELL", "CAMDEN", "CARTERET", "CASWELL", "CATAWBA",
    "CHATHAM", "CHEROKEE", "CHOWAN", "CLAY", "CLEVELAND", "COLUMBUS",
    "CRAVEN", "CUMBERLAND", "CURRITUCK", "DARE", "DAVIDSON", "DAVIE",
    "DUPLIN", "DURHAM", "EDGECOMBE", "FORSYTH", "FRANKLIN", "GASTON",
    "GATES", "GRAHAM", "GRANVILLE", "GREENE", "GUILFORD", "HALIFAX",
    "HARNETT", "HAYWOOD", "HENDERSON", "HERTFORD", "HOKE", "HYDE",
    "IREDELL", "JACKSON", "JOHNSTON", "JONES", "LEE", "LENOIR",
    "LINCOLN", "MACON", "MADISON", "MARTIN", "MCDOWELL", "MECKLENBURG",
    "MITCHELL", "MONTGOMERY", "MOORE", "NASH", "NEW HANOVER",
    "NORTHAMPTON", "ONSLOW", "ORANGE", "PAMLICO", "PASQUOTANK", "PENDER",
    "PERQUIMANS", "PERSON", "PITT", "POLK", "RANDOLPH", "RICHMOND",
    "ROBESON", "ROCKINGHAM", "ROWAN", "RUTHERFORD", "SAMPSON", "SCOTLAND",
    "STANLY", "STOKES", "SURRY", "SWAIN", "TRANSYLVANIA", "TYRRELL",
    "UNION", "VANCE", "WAKE", "WARREN", "WASHINGTON", "WATAUGA", "WAYNE",
    "WILKES", "WILSON", "YADKIN", "YANCEY",
}))
assert len(STATEWIDE_COUNTIES) == 100
_scope = os.environ.get("FL_NC_SCOPE", "statewide").strip().lower()
COUNTIES = list(FOOTPRINT_COUNTIES if _scope == "footprint" else STATEWIDE_COUNTIES)
_ov = os.environ.get("FL_NC_COUNTIES", "").strip()
if _ov:
    COUNTIES = [c.strip().upper() for c in _ov.split(",") if c.strip()]


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fsync_parent(path):
    """Persist a completed rename across a host crash/power loss."""
    fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_atomic(path, value):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(value, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_parent(path)


def write_text_atomic(path, value):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(value)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_parent(path)


def durable_accounting(conn):
    """Return reconciliation counts derived only from committed rows.

    Process-local counters reset on resume and therefore cannot be compared to a
    database accumulated over earlier invocations. These queries express the
    same identities against durable state, which is the accounting authority.
    """
    baseline = conn.execute("SELECT COUNT(*) FROM baseline").fetchone()[0]
    persisted_net = conn.execute(
        "SELECT COUNT(*) FROM baseline WHERE scope_reason IN "
        "('NET_NEW_STATEWIDE_KEEP','NET_NEW_STATEWIDE_HOLD_FAILOPEN')"
    ).fetchone()[0]
    excluded = conn.execute("SELECT COUNT(*) FROM scope_excluded").fetchone()[0]
    delivered = conn.execute(
        "SELECT COUNT(*) FROM scope_excluded "
        "WHERE reason='EXCLUDED_ALREADY_DELIVERED_NC'"
    ).fetchone()[0]
    return {
        "baseline": baseline,
        "net_new": persisted_net,
        "scope_excluded": excluded,
        "delivered": delivered,
    }


def flush_seed_batches(conn, net_batch, excl_batch,
                       bl_sql, rs_sql, as_sql, ex_sql):
    """Drain seed buffers while preserving an enclosing county transaction."""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        if net_batch:
            conn.executemany(bl_sql, [r[0] for r in net_batch])
            conn.executemany(rs_sql, [r[1] for r in net_batch])
            conn.executemany(as_sql, [r[2] for r in net_batch])
            net_batch.clear()
        if excl_batch:
            conn.executemany(ex_sql, excl_batch)
            excl_batch.clear()
        if owns_transaction:
            conn.execute("COMMIT")
    except Exception:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _clean_city(v):
    v = str(v or "").strip()
    if not v or v.upper() in ("UNINCORPORATED", "NULL", "NONE"):
        return ""
    return v


def _get_with_retry(client, params, *, page, label, say, attempts=3):
    """Run one logical request; an exhausted failure remains unfinished."""
    last = None
    for attempt in range(attempts):
        try:
            return client.get_json(params, page=page)
        except SourceUnavailable as exc:
            last = exc
            # DNS already exhausted system + two independent DoH resolvers in
            # one call. Blocks, rate limits, auth/application errors and bad
            # payloads likewise need the persisted circuit scheduler, not an
            # immediate mini-storm. Only transient response/connect/reset/5xx
            # failures get a small bounded in-process retry.
            immediate = exc.failure.classification in {
                "SOURCE_RESPONSE_TIMEOUT", "TCP_CONNECT_TIMEOUT",
                "TCP_CONNECT_FAILURE", "TCP_RESET", "SOURCE_HTTP_5XX"}
            if immediate and attempt + 1 < attempts:
                delay = retry_delay(attempt)
                say(f"    {label} retry {attempt + 1}/{attempts - 1}: "
                    f"{exc.failure.classification}; sleep {delay:.2f}s")
                time.sleep(delay)
            else:
                break
    raise last


def county_count(client, name, say):
    body = _get_with_retry(
        client,
        {"where": "county='%s'" % name,
         "returnCountOnly": "true", "f": "json"},
        page=False, label=f"{name} count", say=say)
    if "count" not in body:
        raise SourceUnavailable(SourceFailure(
            "SOURCE_MALFORMED_RESPONSE",
            f"AddressNC count response missing count for {name}"))
    try:
        return int(body["count"])
    except (TypeError, ValueError) as exc:
        raise SourceUnavailable(SourceFailure(
            "SOURCE_MALFORMED_RESPONSE",
            f"AddressNC count is not an integer for {name}: {body.get('count')!r}")) from exc


def fetch_county(client, name, say):
    """All address points in a county, offset-paged, deduped to addr|zip.
    Returns (rows, physical_fetched, expected_count, reqs). Truncation guard:
    caller asserts physical_fetched == expected_count."""
    expected = county_count(client, name, say)
    out = {}
    physical = 0
    offset = 0
    reqs = 0
    while True:
        d = _get_with_retry(client, {
            "where": "county='%s'" % name, "outFields": FIELDS,
            "returnGeometry": "true", "outSR": "4326",
            "orderByFields": "objectid", "resultOffset": offset,
            "resultRecordCount": PAGE, "f": "json"},
            page=True, label=f"{name} offset {offset}", say=say)
        reqs += 1
        feats = d.get("features", [])
        physical += len(feats)
        for f in feats:
            a = f.get("attributes", {})
            g = f.get("geometry") or {}
            addr = str(a.get("st_address") or "").strip()
            if not addr or g.get("x") is None or g.get("y") is None:
                continue
            unit = str(a.get("unit") or "").strip()
            if unit:
                addr = f"{addr} {unit}"
            town = (_clean_city(a.get("inc_muni")) or _clean_city(a.get("msagcomm"))
                    or _clean_city(a.get("uninc_comm")))
            zp = str(a.get("post_code") or "").strip()[:5]
            out[addr.upper() + "|" + zp] = {
                "address": addr.title(), "city": town.title(), "zip": zp,
                "lat": round(float(g["y"]), 7), "lon": round(float(g["x"]), 7),
                "county": name}
        if not feats or not d.get("exceededTransferLimit"):
            break
        offset += len(feats)
    return list(out.values()), physical, expected, reqs


def main():
    t0 = time.time()
    os.makedirs(ROOT + "work", exist_ok=True)
    os.makedirs(ROOT + "logs", exist_ok=True)
    log_f = open(ROOT + "logs/build.log", "w")

    def say(m=""):
        print(m, flush=True)
        log_f.write(m + "\n")
        log_f.flush()

    say(f"NC STATEWIDE SWEEP QUEUE  run_id={RUN_ID}")
    say(f"  db        {DB}")
    say(f"  source    NC OneMap AddressNC (NG911 rooftop)  {NC_ADDR}")
    say(f"  scope     {len(COUNTIES)} counties ({_scope})")
    say(f"  seed      keep + hold (fail-open); drop excluded (documented non-res)")
    say(f"  canon     {C.CANONICALIZER_VERSION}   NO CARRIER, NO PROXY")

    # ------------------------------------------------------- delivered NC keys
    say("\nSECTION 1  EXCLUSION SET (delivery history, read-only)")
    hist_path = DH.HISTORY_PATH
    nc_delivered = set()
    n_recs = 0
    with open(hist_path) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            rec = json.loads(ln)
            n_recs += 1
            if "NC" in (rec.get("states") or []):
                nc_delivered.update(rec.get("keys") or [])
    nc_delivered_norm = {DH._normalised(k) for k in nc_delivered}
    say(f"  delivery history {n_recs:,} records; NC delivered distinct keys "
        f"{len(nc_delivered):,} (+{len(nc_delivered_norm):,} normalised)")

    # ---------------------------------------------------------------- open db
    # RESUME BY DEFAULT. A timeout restart must NOT wipe seeded counties, or the
    # build oscillates forever (the 12->8 county regression). Only an explicit
    # FL_FRESH=1 starts clean. Schema is applied only when the db is new; already
    # seeded counties are skipped in the loop below.
    if os.environ.get("FL_FRESH") == "1":
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(DB + ext):
                os.remove(DB + ext)
    conn = sqlite3.connect(DB, timeout=120, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _have_bl = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='baseline'").fetchone()
    if not _have_bl:
        with open(SCHEMA_SQL) as fh:
            conn.executescript(fh.read())
        say(f"  applied schema from {SCHEMA_SQL}")
    else:
        say("  RESUMING existing db (schema present) — keeping seeded counties")
    try:
        _seeded_counties = {r[0] for r in conn.execute("SELECT DISTINCT county FROM baseline")}
    except Exception:
        _seeded_counties = set()
    if _seeded_counties:
        say(f"  resume: {len(_seeded_counties)} counties already seeded, will skip them")

    BL_COLS = ["canonical_key", "raw_address", "normalized_address", "city",
               "state", "zip5", "zip_missing", "number", "predir", "street",
               "suffix", "postdir", "unit_type", "unit_number", "lat", "lon",
               "pin_source", "struct_type", "placement", "source_file",
               "source_row_id", "source_sha256", "physical_rows",
               "prev_disposition", "previously_queried", "previously_delivered",
               "previously_held", "previous_class", "residential_evidence",
               "county", "land_use", "canonicalizer_version", "county_name",
               "scope_reason", "rooftop_state"]
    BL_SQL = (f"INSERT INTO baseline({','.join(BL_COLS)}) "
              f"VALUES({','.join('?' * len(BL_COLS))})")
    RS_SQL = ("INSERT INTO residential_screen"
              "(canonical_key,status,reason,evidence,screener_version,screened_utc)"
              " VALUES(?,?,?,?,?,?)")
    AS_SQL = ("INSERT INTO assignments"
              "(canonical_key,shard,attempts,completed,prio) VALUES(?,?,0,0,0)")
    EX_SQL = ("INSERT OR IGNORE INTO scope_excluded"
              "(canonical_key,raw_address,city,zip5,source_file,"
              "previously_queried,residential_evidence,has_coordinate,reason,"
              "evidence) VALUES(?,?,?,?,?,?,?,?,?,?)")
    now = utc()
    SCREENER = "universe-keep+hold/nc_onemap+fema_occ_cls"
    PIN_SRC = "nc_onemap_ng911_address_point"

    # --------------------------------------------------- per-county pipeline
    say("\nSECTION 2  ACQUIRE -> FEMA SCREEN -> DEDUP -> SEED(keep+hold)  (per county)")
    CACHE_DIR = ROOT + "work/screen_cache"
    os.makedirs(CACHE_DIR, exist_ok=True)
    remaining_source_counties = sum(
        not os.path.exists(os.path.join(CACHE_DIR, county + ".json.done"))
        for county in COUNTIES)
    source_reporter = SourceHealthReporter(
        SOURCE_HEALTH_DB, SOURCE_ID, task_id=TASK_ID,
        failure_threshold=3, cooldown_seconds=60)
    source_reporter.store.set_remaining(
        SOURCE_ID, remaining_source_counties, unit="counties",
        detail={"configured": len(COUNTIES),
                "completed_source_caches": len(COUNTIES) - remaining_source_counties})
    sess = OneMapClient(NC_ADDR, health_path=SOURCE_HEALTH)
    universe_tmp = UNIVERSE_OUT + ".building"
    uni = open(universe_tmp, "w")

    def write_universe(name, kept, dropped, held):
        """Recreate the complete universe from durable county caches.

        Resume-skipped counties must be emitted too. The previous implementation
        truncated universe.jsonl and then skipped them, leaving a zero/partial
        artifact even while run.db was intact.
        """
        for r in dropped:
            uni.write(json.dumps({"address": r["address"], "city": r["city"],
                      "state": STATE, "zip": r["zip"], "lat": r["lat"],
                      "lon": r["lon"], "county": name, "residential": False,
                      "res_verdict": "drop", "reason": r.get("reason", "")},
                      separators=(",", ":")) + "\n")
        for r in held:
            uni.write(json.dumps({"address": r["address"], "city": r["city"],
                      "state": STATE, "zip": r["zip"], "lat": r["lat"],
                      "lon": r["lon"], "county": name, "residential": False,
                      "res_verdict": "hold", "reason": r.get("reason", "")},
                      separators=(",", ":")) + "\n")
        for r in kept:
            uni.write(json.dumps({"address": r["address"], "city": r["city"],
                      "state": STATE, "zip": r["zip"], "lat": r["lat"],
                      "lon": r["lon"], "county": name, "residential": True,
                      "res_verdict": "keep",
                      "residential_evidence": r.get(RF.EVIDENCE_FIELD, "")},
                      separators=(",", ":")) + "\n")
    # Resume must deduplicate against rows already committed by earlier county
    # passes as well as rows encountered in this process.  Starting empty made
    # the first cross-county duplicate abort with baseline's UNIQUE constraint.
    seen = {r[0] for r in conn.execute("SELECT canonical_key FROM baseline")}
    per_county = {}
    tot_acq = tot_keep = tot_hold = tot_drop = 0
    n_net = n_delivered = n_review = n_dup = 0
    n_net_keep = n_net_hold = 0
    net_batch = []
    excl_batch = []

    def flush():
        # If the caller already owns a county transaction, retain it. This lets
        # 20k-row memory batches stay bounded without making a partially seeded
        # county look complete after a crash. Outside county seeding, flush()
        # remains an atomic standalone operation.
        flush_seed_batches(conn, net_batch, excl_batch,
                           BL_SQL, RS_SQL, AS_SQL, EX_SQL)

    # counters shared with the per-row seeder (mutable holder to avoid nonlocal
    # churn across the per-county closure)
    cc_ct = {}

    def seed_eligible(r, fam, evid):
        """Canonicalize, dedup, net-new-classify and seed ONE eligible address.
        fam in {'KEEP','HOLD'}; evid is the FEMA reason/evidence string."""
        addr, zc = r["address"], r["zip"]
        parsed = C.parse(addr, zc, STATE)
        if parsed["status"] != "OK":
            cc_ct["rev"] += 1
            conn.execute("INSERT OR IGNORE INTO canonicalization_review "
                         "VALUES(?,?,?,?,?,?,?)",
                         ("nc_onemap:" + name, -1, addr, zc, r["city"],
                          parsed.get("reason", ""), ""))
            return
        k = C.key_v2(parsed)
        if k in seen:
            cc_ct["dup"] += 1
            conn.execute("UPDATE baseline SET physical_rows=physical_rows+1 "
                         "WHERE canonical_key=?", (k,))
            return
        seen.add(k)
        az = DH.key(addr, zc)
        if az in nc_delivered or DH._normalised(az) in nc_delivered_norm:
            cc_ct["deliv"] += 1
            excl_batch.append((
                k, addr, r["city"], parsed["zip5"], "nc_onemap:" + name, 0,
                "RESIDENTIAL_FILTER_PASSED;" + fam, 1,
                "EXCLUDED_ALREADY_DELIVERED_NC",
                "delivery_history ADDRESS|ZIP match"))
        else:
            cc_ct["net"] += 1
            cc_ct["net_keep" if fam == "KEEP" else "net_hold"] += 1
            scope = ("NET_NEW_STATEWIDE_KEEP" if fam == "KEEP"
                     else "NET_NEW_STATEWIDE_HOLD_FAILOPEN")
            bl = (
                k, addr, C.normalized_string(parsed), r["city"], STATE,
                parsed["zip5"], int(parsed["zip_missing"]), parsed["number"],
                parsed["predir"], parsed["street"], parsed["suffix"],
                parsed["postdir"], parsed["unit_type"], parsed["unit_number"],
                r["lat"], r["lon"], PIN_SRC, "", "", "nc_onemap:" + name, -1, "", 1,
                "PREV_UNQUERIED", 0, 0, 0, "",
                "RESIDENTIAL_FILTER_PASSED;" + fam + ";" + str(evid)[:110],
                name, "", C.CANONICALIZER_VERSION, name, scope, "")
            rs = (k, "SCREENED_RESIDENTIAL", "res_verdict=" + fam.lower(),
                  "prefiltered:nc_onemap+fema_occ_cls:" + fam + ":" + str(evid)[:100],
                  SCREENER, now)
            asg = (k, int(k[:8], 16) % 64)
            net_batch.append((bl, rs, asg))
            if len(net_batch) >= 20000:
                flush()
        if len(excl_batch) >= 20000:
            flush()

    for ci, name in enumerate(COUNTIES, 1):
        ct0 = time.time()
        cache_f = os.path.join(CACHE_DIR, name + ".json")
        done_f = cache_f + ".done"
        if name in _seeded_counties:
            if not os.path.exists(done_f):
                raise SystemExit(f"REFUSED: seeded county {name} has no completed cache")
            cd = json.load(open(cache_f))
            kept, dropped, held = cd["kept"], cd["dropped"], cd["held"]
            physical, expected = cd["physical"], cd["expected"]
            uniq_addr, reqs = cd["uniq_addr"], cd["reqs"]
            if (physical != expected or
                    len(kept) + len(dropped) + len(held) != uniq_addr):
                raise SystemExit(f"REFUSED: seeded county {name} cache did not reconcile")
            write_universe(name, kept, dropped, held)
            tot_acq += uniq_addr
            tot_keep += len(kept)
            tot_hold += len(held)
            tot_drop += len(dropped)
            say(f"  [{ci:3d}/{len(COUNTIES)}] {name:<12s} already seeded — "
                f"resume-skip DB; rebuilt {uniq_addr:,} universe rows from cache")
            continue
        if os.path.exists(done_f):
            # RESUME: reuse this county's already-fetched + FEMA-screened result
            # from the T7 SSD. No NG911 request, no FEMA request -- a restart
            # never re-hammers an endpoint and never loses completed network work.
            cd = json.load(open(cache_f))
            kept, dropped, held = cd["kept"], cd["dropped"], cd["held"]
            physical, expected = cd["physical"], cd["expected"]
            uniq_addr, reqs = cd["uniq_addr"], cd["reqs"]
            if len(kept) + len(dropped) + len(held) != uniq_addr:
                raise SystemExit(f"REFUSED: {name} cache did not reconcile")
            say(f"  [{ci:3d}/{len(COUNTIES)}] {name:<12s} CACHED  keep {len(kept):,} "
                f"hold {len(held):,} drop {len(dropped):,}  (no network)")
        else:
            source_started = time.perf_counter()
            source_reporter.begin(
                name, remaining=remaining_source_counties,
                remaining_unit="counties",
                remaining_detail={"next_county": name,
                                  "completed_source_caches":
                                      len(COUNTIES) - remaining_source_counties})
            try:
                rows, physical, expected, reqs = fetch_county(sess, name, say)
                if physical != expected:
                    raise SourceUnavailable(SourceFailure(
                        "SOURCE_TRUNCATED",
                        f"{name}: fetched {physical} physical rows; expected {expected}"))
                source_reporter.record_success(
                    latency_ms=(time.perf_counter() - source_started) * 1000,
                    completed_units=0)
            except SourceUnavailable as exc:
                source_reporter.record_failure(
                    exc.failure.classification, exc.failure.detail,
                    retryable=exc.failure.retryable,
                    latency_ms=(time.perf_counter() - source_started) * 1000,
                    retry_delay_seconds=60)
                raise
            except Exception as exc:
                source_reporter.record_failure(
                    "SOURCE_CLIENT_FAILURE", f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - source_started) * 1000,
                    retry_delay_seconds=60)
                raise
            uniq_addr = len(rows)
            screen_in = [{"canonical_key": r["address"].upper() + "|" + r["zip"],
                          "address": r["address"], "city": r["city"], "zip": r["zip"],
                          "lat": r["lat"], "lon": r["lon"], "county": r["county"],
                          "struct_type": ""} for r in rows]
            kept, dropped, held, counts = RF.filter_residential(
                screen_in, workers=24, progress_log=lambda m: None)
            if len(screen_in) != len(kept) + len(dropped) + len(held):
                raise SystemExit(f"REFUSED: {name} FEMA screen did not reconcile")
            write_json_atomic(cache_f, {
                "kept": kept, "dropped": dropped, "held": held,
                "physical": physical, "expected": expected,
                "uniq_addr": uniq_addr, "reqs": reqs,
            })
            write_text_atomic(done_f, now)
            remaining_source_counties -= 1
            source_reporter.store.advance_remaining(SOURCE_ID, 1)

        cc_ct.clear()
        for kkey in ("net", "deliv", "rev", "dup", "net_keep", "net_hold"):
            cc_ct[kkey] = 0
        c_keep = len(kept)
        c_hold = len(held)
        c_drop = len(dropped)

        write_universe(name, kept, dropped, held)

        # The whole county's DB transition is atomic. flush() may drain memory
        # batches within this transaction, but it cannot commit them early. A
        # crash therefore leaves either the prior DB or the complete county,
        # never a partial county that resume mistakes for a seeded one.
        conn.execute("BEGIN")
        try:
            # HOLD: uncertain -> seeded fail-open (rule F).
            for r in held:
                evid = r.get("reason", "")
                seed_eligible(r, "HOLD", evid)

            # KEEP: confident residential -> seeded.
            for r in kept:
                evid = r.get(RF.EVIDENCE_FIELD, "")
                seed_eligible(r, "KEEP", evid)
            flush()
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            net_batch.clear()
            excl_batch.clear()
            raise

        per_county[name] = {
            "expected": expected, "physical_fetched": physical,
            "unique_addr_zip": uniq_addr, "reqs": reqs, "keep": c_keep,
            "hold": c_hold, "drop": c_drop, "net_new": cc_ct["net"],
            "net_new_keep": cc_ct["net_keep"], "net_new_hold": cc_ct["net_hold"],
            "delivered": cc_ct["deliv"], "review": cc_ct["rev"],
            "dup_canonical": cc_ct["dup"], "seconds": round(time.time() - ct0, 1)}
        tot_acq += uniq_addr
        tot_keep += c_keep
        tot_hold += c_hold
        tot_drop += c_drop
        n_net += cc_ct["net"]
        n_net_keep += cc_ct["net_keep"]
        n_net_hold += cc_ct["net_hold"]
        n_delivered += cc_ct["deliv"]
        n_review += cc_ct["rev"]
        n_dup += cc_ct["dup"]
        say(f"  [{ci:2d}/{len(COUNTIES)}] {name:<12} expect {expected:>7,} "
            f"phys {physical:>7,} uniq {uniq_addr:>7,} | keep {c_keep:>6,} "
            f"hold {c_hold:>6,} drop {c_drop:>5,} | net {cc_ct['net']:>6,} "
            f"(K {cc_ct['net_keep']:>6,}/H {cc_ct['net_hold']:>6,}) "
            f"deliv {cc_ct['deliv']:>4} dup {cc_ct['dup']:>4} rev {cc_ct['rev']:>3}"
            f"  {per_county[name]['seconds']}s")
    uni.flush()
    os.fsync(uni.fileno())
    uni.close()
    os.replace(universe_tmp, UNIVERSE_OUT)
    _fsync_parent(UNIVERSE_OUT)

    # ------------------------------------------------------- seeded verify
    say("\nSECTION 3  SEEDED TABLE COUNTS")
    nb = conn.execute("SELECT COUNT(*) FROM baseline").fetchone()[0]
    na = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
    nr = conn.execute("SELECT COUNT(*) FROM residential_screen").fetchone()[0]
    nrs = conn.execute("SELECT COUNT(*) FROM residential_screen "
                       "WHERE status='SCREENED_RESIDENTIAL'").fetchone()[0]
    ncd = conn.execute("SELECT COUNT(*) FROM current_disposition").fetchone()[0]
    nev = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    nex = conn.execute("SELECT COUNT(*) FROM scope_excluded").fetchone()[0]
    nb_coord = conn.execute("SELECT COUNT(*) FROM baseline "
                            "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchone()[0]
    nac0 = conn.execute("SELECT COUNT(*) FROM assignments "
                        "WHERE completed=0 AND attempts=0").fetchone()[0]
    nbk = conn.execute("SELECT COUNT(*) FROM baseline "
                       "WHERE scope_reason='NET_NEW_STATEWIDE_KEEP'").fetchone()[0]
    nbh = conn.execute("SELECT COUNT(*) FROM baseline "
                       "WHERE scope_reason='NET_NEW_STATEWIDE_HOLD_FAILOPEN'").fetchone()[0]
    incremental_resume = bool(_seeded_counties)
    say(f"  baseline {nb:,} (KEEP {nbk:,} / HOLD_FAILOPEN {nbh:,}) | assignments "
        f"{na:,} (completed=0&attempts=0 {nac0:,}) | residential_screen {nr:,} "
        f"(SCREENED_RESIDENTIAL {nrs:,})")
    say(f"  current_disposition {ncd:,} | evidence {nev:,} | scope_excluded {nex:,}")
    say(f"  baseline rows with lat/lon: {nb_coord:,} of {nb:,}")

    # -------------------------------------------------------- reconciliation
    say("\nSECTION 4  RECONCILIATION")
    ids = []
    durable = durable_accounting(conn)
    if incremental_resume:
        # Invocation counters are deliberately zero when every county is
        # resume-skipped. Comparing those counters to persisted totals caused a
        # correct, complete database to fail forever. On resume, validate the
        # durable state and every county's committed acquisition cache instead.
        cache_bad = 0
        cache_total = 0
        for county in COUNTIES:
            cf = os.path.join(CACHE_DIR, county + ".json")
            df = cf + ".done"
            try:
                cd = json.load(open(cf))
                cache_total += 1
                if (not os.path.exists(df) or cd["physical"] != cd["expected"] or
                        len(cd["kept"]) + len(cd["held"]) + len(cd["dropped"]) != cd["uniq_addr"]):
                    cache_bad += 1
            except Exception:
                cache_bad += 1
        ids.extend([
            ("resume: every configured county has a reconciled acquisition cache",
             len(COUNTIES), cache_total, cache_bad,
             f"{cache_total}/{len(COUNTIES)} caches; bad={cache_bad}"),
        ])
    else:
        ids.extend([
            ("eligible(keep+hold) = net_new + delivered + dup + review",
             tot_keep + tot_hold, n_net + n_delivered + n_dup + n_review,
             (tot_keep + tot_hold) - (n_net + n_delivered + n_dup + n_review),
             f"{tot_keep + tot_hold:,} = net {n_net:,} + deliv {n_delivered:,} + "
             f"dup {n_dup:,} + review {n_review:,}"),
            ("net_new = net_keep + net_hold", n_net, n_net_keep + n_net_hold,
             n_net - (n_net_keep + n_net_hold),
             f"{n_net:,} = keep {n_net_keep:,} + hold {n_net_hold:,}"),
        ])
    # These two identities must always use committed state. Invocation counters
    # are zero for resume-skipped counties and only cover newly processed
    # counties during a partial resume; comparing them to cumulative tables was
    # the root cause of both false reconciliation failures.
    ids.extend([
        ("baseline == net_new", durable["baseline"], durable["net_new"],
         durable["baseline"] - durable["net_new"],
         f"{durable['baseline']:,} baseline / {durable['net_new']:,} persisted net-new"),
        ("scope_excluded == delivered", durable["scope_excluded"], durable["delivered"],
         durable["scope_excluded"] - durable["delivered"],
         f"{durable['scope_excluded']:,} excluded / {durable['delivered']:,} delivered"),
    ])
    ids.extend([
        ("assignments == baseline", nb, na, nb - na, f"{na:,} assignments"),
        ("assignments all NEVER_ASKED (completed=0 & attempts=0)",
         na, nac0, na - nac0, f"{nac0:,} completed=0&attempts=0"),
        ("residential_screen == baseline (all SCREENED_RESIDENTIAL)",
         nb, nrs, nb - nrs, f"{nrs:,} SCREENED_RESIDENTIAL"),
        ("scope_reason keep+hold == baseline", nb, nbk + nbh,
         nb - (nbk + nbh), f"{nbk:,}+{nbh:,}"),
        ("no sweep started: current_disposition empty", 0, ncd, ncd,
         f"{ncd:,} dispositions"),
        ("no evidence rows", 0, nev, nev, f"{nev:,} evidence"),
        ("100% net-new rows carry a rooftop coordinate", nb, nb_coord,
         nb - nb_coord, f"{nb_coord:,} with lat/lon"),
        ("no orphan assignment", 0,
         conn.execute("SELECT COUNT(*) FROM assignments a LEFT JOIN baseline b "
                      "USING(canonical_key) WHERE b.canonical_key IS NULL")
         .fetchone()[0], None, "assignments without baseline"),
    ])
    conn.execute("BEGIN")
    fails = 0
    for nm, lhs, rhs, un, detail in ids:
        if un is None:
            un = rhs
        v = "PASS" if un == 0 else "FAIL"
        fails += (un != 0)
        conn.execute("INSERT OR REPLACE INTO reconciliation VALUES(?,?,?,?,?,?)",
                     (nm, lhs, rhs, un, v, detail))
        say(f"  [{v}] {nm:56s} unaccounted={un}   {detail}")
    conn.execute("COMMIT")

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    sess.close()

    summary = {
        "built": utc(), "run_id": RUN_ID, "state": STATE, "root": ROOT, "db": DB,
        "source": "NC OneMap AddressNC (NC 911 board NG911 statewide address points)",
        "source_url": NC_ADDR,
        "pin_source": PIN_SRC, "canonicalizer": C.CANONICALIZER_VERSION,
        "scope": (f"North Carolina authoritative county denominator: "
                  f"{len(COUNTIES)} counties ({_scope})"),
        "configured_counties": COUNTIES,
        "historical_footprint_counties": FOOTPRINT_COUNTIES,
        "seed_policy": ("keep+hold seeded (fail-open); only confident-DROP "
                        "excluded. Rule F / fail-open / task req(2). "
                        "scope_reason distinguishes KEEP vs HOLD_FAILOPEN."),
        "acquired_unique_addr_zip": tot_acq,
        "residential_keep": tot_keep, "residential_hold": tot_hold,
        "residential_drop": tot_drop,
        "canonicalization_review": n_review, "duplicate_canonical": n_dup,
        "excluded_delivered_nc": durable["delivered"],
        "delivered_nc_keys_available": len(nc_delivered),
        "net_new_seeded": durable["net_new"],
        "net_new_keep": nbk, "net_new_hold": nbh,
        "reconciliation_failures": fails,
        "seconds": round(time.time() - t0, 1),
        "per_county": per_county,
    }
    write_json_atomic(ROOT + "work/build_summary.json", summary)
    say(f"\nRECONCILIATION FAILURES = {fails}   NET-NEW SEEDED = {durable['net_new']:,} "
        f"(keep {nbk:,} + hold {nbh:,})   "
        f"acq {tot_acq:,} keep {tot_keep:,} hold {tot_hold:,} drop {tot_drop:,}   "
        f"done in {summary['seconds']}s")
    log_f.close()
    return 0 if fails == 0 else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SourceUnavailable as exc:
        print(f"SOURCE_RETRY_SCHEDULED {exc.failure.classification}: "
              f"{exc.failure.detail}", file=sys.stderr, flush=True)
        sys.exit(75)
