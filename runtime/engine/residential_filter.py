"""RESIDENTIAL PRE-FILTER — runs BEFORE the serviceability sweep. Always.

ORDER IS A HARD PRECONDITION, NOT A PREFERENCE
----------------------------------------------
Filter residential FIRST, then sweep. Never the reverse, never "sweep everything
and filter afterwards". On the GA South pull, 72,106 addresses were swept and
then 4,473 non-residential ones were thrown away -- every one of those was a
carrier request that bought nothing and wall-clock the owner sat waiting on.

This screen is free: FEMA USA Structures is queried off the coordinate we
already have, costs no carrier request, and runs before a single address is
checked.

THREE OUTCOMES, NOT TWO. "WE COULD NOT TELL" IS ITS OWN ANSWER
--------------------------------------------------------------
KEEP  only on POSITIVE evidence that this address is somebody's home, and only
      when nothing in sight contradicts it: a FEMA footprint attributed to the
      pin, classed Residential, whose USE is a dwelling and whose floor area is
      a dwelling's -- with no non-residential building sharing its envelope. Or
      a county structure type, which FEMA is not allowed to overrule but IS
      allowed to contradict.
DROP  on a CONFIDENT non-residential answer about the building at the pin:
      Commercial, Industrial, Assembly, Government, Education, Agriculture,
      Utility and Misc, anything FEMA flags as an outbuilding, and the
      residential-CLASS buildings nobody lives in -- a nursing home, a
      dormitory, a motel.
HOLD  everything else. No building at the coordinate, an occupancy FEMA does
      not classify, a lookup that failed, a row with no coordinate at all, a
      home too far from the pin to be sure it is this address's building, an
      envelope whose buildings disagree with each other, a footprint too small
      to be a dwelling.

HOLD is written to a manifest with its reason, counted by the coverage
registry, and recoverable by deleting the file. It is not a deletion and it is
not an answer -- it is this screen saying out loud that it could not establish
what this address is.

EVERY KEPT ROW CARRIES ITS EVIDENCE, AND THE SWEEP REFUSES ROWS WITHOUT IT
--------------------------------------------------------------------------
A keep is stamped with `residential_evidence` naming the finding it rests on,
and `require_screened` -- called at the top of BOTH sweep entry points -- refuses
any queue row that has no stamp.

That assertion is the part that makes this class of bug non-recurrent. Every
rule below has lived in a comment before, and a comment cannot stop the next
edit from adding a keep path that skips it: the TX-FULL queue was built by a
version of this file whose docstring already promised three outcomes, and it
shipped 859,660 kept rows with no held manifest written at all. With the check
at the boundary, a row nobody established as a residence cannot reach the
carrier no matter what this screen does or fails to do.

WHY THE THIRD OUTCOME HAD TO EXIST
----------------------------------
This module used to have only KEEP and DROP, so every uncertainty fell into
KEEP. On the Texas statewide pull that made "we could not tell" the majority
verdict: of 859,660 addresses queued for the carrier, 532,059 were kept as "no
structure on file (rural home)" and 34,190 as "Unclassified" -- 65.9% of the
queue passed to the sweep with no positive evidence of a residence behind it.
Of 800 sampled addresses that came back SERVICEABLE, 43 stood on a building
this module's own DROP list names: schools, retail, a community center. Not one
of the 43 stood inside a footprint, so the index returned nothing for all 43 and
all 43 were kept as rural homes.

The keep-on-uncertainty rule was written for a real reason and the reason still
holds: on 2026-08-18 a stricter version kept only OCC_CLS == 'Residential' and
dropped 3,742 of 12,021 addresses, 2,739 of them purely for having no FEMA
footprint. FEMA's coverage is genuinely sparse in the rural ground these markets
are made of, and DELETING those rows deletes real homes.

But the lesson from that day was "do not DELETE what we cannot classify", and
KEEP was the wrong way to honour it. HOLD honours it properly: the rural home is
preserved on a manifest and recoverable, and the school is not swept and not
shipped. Do not restore the old rule by widening KEEP -- widen the EVIDENCE
instead (see FootprintIndex.near below, which resolves most of what used to be
unresolvable).

Commercial buildings that slip through here are still caught later by the SIZE
GATE and the occupancy check in rooftop_place.py, which run on the FINAL pin.
Those gates are a second line, never the first: this screen must not hand them
work it could have settled itself.
"""
from __future__ import annotations

import concurrent.futures as cf
import collections
import gzip
import hashlib
import time
import json
import math
import os
import queue
import threading

from curl_cffi import requests as cr

# Imported both ways in this tree: as `engine.residential_filter` from the CLI,
# and flat by rooftop_place, which puts this directory on sys.path first.
try:
    from . import endpoint_guard, fetch_guard
except ImportError:  # pragma: no cover - depends on how the caller imported us
    import endpoint_guard
    import fetch_guard

HERE = os.path.dirname(os.path.abspath(__file__))

FEMA = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
        "USA_Structures_View/FeatureServer/0/query")

#: The circuit-breaker name for the FEMA service. It names the ENDPOINT, not the
#: caller, and it is deliberately the same string rooftop.py, rooftop_place.py
#: and parcel_anchor.py use -- all four query this exact URL, so all four must
#: count into one process-wide streak.
#:
#: That is the whole lesson of 2026-08-27: every retry policy in this file is
#: individually reasonable, and with `workers=64` below, three polite retries
#: each is 192 requests arriving at a service that has already stopped
#: answering, with no thread able to see the other 63. A per-worker backoff
#: cannot fix that; only a counter they all share can. Splitting the name per
#: module would recreate the same blindness one level up -- four counters each
#: seeing a quarter of the rate that gets an IP refused.
_FEMA_ENDPOINT = "FEMA USA Structures"

#: The layer itself, for the one metadata read below.
_LAYER = FEMA.rsplit("/", 1)[0]

#: The attributes every footprint query asks for -- the per-address fallback,
#: the tile download and the disk cache key all read this one name, so the three
#: can never drift. A cache entry written under one field list must never be
#: served to a query that needs another: `_judge` would then read PRIM_OCC or
#: SQFEET as absent and hold a row it could have settled.
_FIELDS = "OCC_CLS,PRIM_OCC,OUTBLDG,SQFEET"

#: Paging by `resultOffset` is only well defined against a stable sort, and this
#: layer had none. The old download walked offsets with no `orderByFields` at
#: all, which left the service free to return the rows in a different order
#: between two pages of the same tile -- a building landing in both pages, or in
#: neither. It never surfaced because nothing counted the result. Ordering on
#: the object id makes the page boundaries exact, which is also what lets the
#: pages of one tile be fetched in parallel instead of one after another.
_ORDER = "OBJECTID"

# Confident non-residential. Anything NOT in this set is kept.
#: County/state E911 structure codes that mean "this is somebody's home".
#: Kept in step with address_source.RESIDENTIAL_CODES, which market_source calls
#: authoritative: "where a county states the structure type it is authoritative
#: and used directly -- never guessed."
COUNTY_RESIDENTIAL = {
    "SF": "Single Family",
    "MH": "Mobile Home",
    "MF": "Multi-Family",
    "DU": "Duplex",
    "ADU": "Accessory Dwelling",
}

#: County codes that mean "not somebody's home". Only codes the county states
#: outright -- anything it is silent about still goes to the footprint check.
COUNTY_NONRESIDENTIAL = {
    "NR": "Non-Residential (county)",
}

DROP_CLASSES = frozenset({
    "Commercial", "Industrial", "Assembly", "Government",
    "Education", "Agriculture", "Utility and Misc",
})

#: The one OCC_CLS value that is positive evidence of a home. Everything else is
#: either a confident drop (DROP_CLASSES) or something we could not establish.
RESIDENTIAL_CLASS = "Residential"

#: PRIM_OCC values that mean a household lives here. OCC_CLS alone is too coarse
#: to keep on.
#:
#: PRIM_OCC AND SQFEET WERE BEING DOWNLOADED ON EVERY REQUEST AND THROWN AWAY.
#: Both have been in the `outFields` of both queries for as long as this file has
#: existed and neither was read by a single decision. Measured on 30,000 rows of
#: the shipped TX-FULL queue, that cost: 38 keeps are a Nursing Home, an
#: Institutional Dormitory or a Temporary Lodging -- OCC_CLS Residential, and not
#: one of them somebody's home -- and 603 keeps stand on a "Residential"-classed
#: footprint under 700 sqft, the size of a shed or a detached garage. Two of
#: those came back SERVICEABLE and were shippable.
#:
#: Multi-family is on this list and must stay: rooftop_place says why -- taking
#: it off cost 67 China Grove leads, duplexes and small apartment buildings that
#: are each a household with a door a rep can knock.
RESIDENTIAL_PRIM = frozenset({
    "Single Family Dwelling", "Manufactured Home",
    "Multi - Family Dwelling", "Duplex",
})

#: Residential in CLASS, but nobody's home. A confident, positive statement of a
#: non-domestic use, so it drops rather than holds -- the same standing as an
#: OCC_CLS in DROP_CLASSES. 'Temporary Lodging' is the motel rooftop_place
#: already refuses at the final pin; catching it here saves the carrier request.
NOT_A_DWELLING_PRIM = frozenset({
    "Nursing Home", "Institutional Dormitory", "Temporary Lodging",
})

#: Below this many square feet a "Residential" footprint is a shed, a detached
#: garage or a well house rather than a dwelling.
#:
#: THIS IS THE ONLY WORKING OUTBUILDING SCREEN THERE IS. `OUTBLDG` is null on
#: every Texas footprint measured -- 3 independent pins by hand, 6,207 of 6,207
#: across four tiles -- and not one address in the 914,799 this module has
#: screened was ever dropped as an outbuilding. The promise in the docstring was
#: kept by a branch that has never once fired.
#:
#: It HOLDS, it does not drop, because the floor cuts through real housing:
#: measured on 1,500 queue rows, 3.14% of nearest-residential footprints are
#: under 700 sqft, and a single-wide manufactured home is 700-1,000 sqft. Size
#: is a proxy for evidence, never evidence -- the same rule rooftop_place states
#: for its own upper bound.
MIN_DWELLING_SQFT = 700.0

#: OUTBLDG values that actually mean "this is a shed / detached garage".
#:
#: The field is an esriFieldTypeString, not a boolean, and the old test was
#: `bool(info.get("OUTBLDG"))`. That is wrong in both directions. FEMA leaves the
#: field unpopulated on every Texas footprint sampled -- 6,207 of 6,207 across
#: four tiles -- so the branch was dead and not one address in 914,799 was ever
#: dropped as an outbuilding, despite this module promising it. And the day FEMA
#: fills it in with the string "No", `bool("No")` is True and the screen drops
#: the entire queue as sheds. Comparing against the values that mean yes is safe
#: whether the field is blank, populated, or populated with a negative.
OUTBLDG_TRUE = frozenset({"Y", "YES", "TRUE", "T", "1"})

#: Returned when the occupancy lookup itself failed, as distinct from "FEMA has
#: no building at this coordinate". They must never be conflated -- see
#: _structure_at and the batch guard in filter_residential.
QUERY_FAILED = object()

#: Stamped on every kept row, naming the positive finding the keep rests on.
#: `require_screened` refuses any row reaching a sweep without it.
EVIDENCE_FIELD = "residential_evidence"

#: If more than this share of a batch could not be evaluated at all, the filter
#: has not actually filtered anything and must not report success. This counts
#: LOOKUP FAILURES only -- a FEMA outage or a crash in the screen -- which is why
#: it can stay this tight.
MAX_UNEVALUATED = 0.05

#: If more than this share of a batch is HELD, the screen resolved essentially
#: nothing and the remainder is not a screened queue.
#:
#: Deliberately far looser than MAX_UNEVALUATED, and the two must not be merged.
#: Holding is now the CORRECT outcome for a coordinate with no building under it,
#: and FEMA's coverage is genuinely sparse in rural markets, so a third of a
#: legitimate market can be held. Setting this at 5% would block every rural pull
#: in the system, which is the opposite failure: no leads at all instead of some
#: bad ones. What this number catches is a screen that answered for almost
#: nobody -- the index failing to build, or a region FEMA has no data for at all.
#:
#: 0.90 was too loose to catch the thing it was written for: a batch 89% of which
#: this screen could not settle still certified as a screened queue and went
#: through silently. Measured on TX-FULL, 43.45% of the queue has no positive
#: residential evidence -- a number that has to make somebody stop and look, not
#: sail under a threshold. Above this share the run refuses and says so; raising
#: it is a decision an operator makes out loud with FL_ACCEPT_HELD_SHARE, not a
#: default that quietly absorbs anything.
#:
#: Raising it can never turn a held row into a swept one. It only decides whether
#: a batch this thin is allowed to certify.
MAX_HELD = float(os.environ.get("FL_ACCEPT_HELD_SHARE") or 0.50)

#: How far from an address point we are willing to attribute a building to it.
#: Matches the ~24 m envelope `_structure_at` has always used over the network,
#: so the offline index and the network fallback answer the same question.
NEAR_M = 24.0

#: How close a home has to be before its being nearby is evidence that THIS pin
#: is that home.
#:
#: NEAR_M IS THE RIGHT RADIUS FOR A DROP AND THE WRONG ONE FOR A KEEP, AND IT WAS
#: BEING USED FOR BOTH. Finding a school 20 m away is a reason to stop and look;
#: finding a house 20 m away is not a reason to say this pin is a house, because
#: at that range the nearest roof is routinely the neighbour's. That asymmetry is
#: how a vacant lot inherits the dwelling next door and gets swept.
#:
#: 10 m is measured, not chosen: on 1,500 rows of the shipped TX-FULL queue the
#: EDGE distance from the pin to the nearest residential footprint runs p50 0.0 m,
#: p85 8.9 m, p90 12.2 m. 10 m keeps 86.8% of the rows that have a residential
#: footprint near them and sends the other 13.2% to the held manifest for a second
#: look. Nothing is deleted by that; a row over the line is worked again.
#:
#: A pin standing INSIDE a footprint is not subject to this at all -- containment
#: is unambiguous and needs no radius.
KEEP_M = 10.0


class FilterNotApplied(RuntimeError):
    """The residential screen could not evaluate enough of the batch to certify it.

    Raised instead of returning a pass-through result. A screen that cannot see
    its input must BLOCK, never approve: the silent-approve version of this code
    is what let 813 commercial buildings reach a delivered East Texas file.
    """


class UnscreenedQueue(RuntimeError):
    """A queue reached a sweep carrying rows nobody established as residences."""


def require_screened(addresses: list[dict], where: str = "the sweep") -> list[dict]:
    """Refuse to let an unscreened row reach the carrier. Returns `addresses`.

    THIS IS THE PART THAT MAKES THE CLASS NON-RECURRENT, AND IT IS DELIBERATELY
    NOT INSIDE `filter_residential`.

    Every rule in this module has been stated in a comment before and every one
    of them was bypassed anyway, because a comment cannot stop the next edit from
    adding a keep path that skips it. That is not hypothetical: the shipped
    TX-FULL queue was built by a version of this file whose docstring already
    promised three outcomes and a held manifest. It produced 859,660 kept and
    55,139 dropped -- 914,799, exactly the number screened, with zero held and no
    held file written at all -- and 752,043 of those rows were put to the
    carrier. Measured afterwards, 3.83% of them stand on a building this module's
    own DROP list names and 43.45% have no positive residential evidence of any
    kind.

    Checked HERE, at the entry to the sweep, the guarantee stops depending on the
    screen being correct. A row with no `residential_evidence` cannot be asked
    about, whatever produced it and whatever it says about itself.

    It is also why the stamp is a per-row field and not a flag on the batch: a
    batch flag says "this list was screened once", which is exactly the claim the
    TX-FULL queue could have made truthfully while carrying retail stores.
    """
    missing = [a for a in addresses
               if not str(a.get(EVIDENCE_FIELD) or "").strip()]
    if not missing:
        return addresses
    ex = missing[0]
    raise UnscreenedQueue(
        f"{len(missing):,} of {len(addresses):,} rows reaching {where} carry no "
        f"'{EVIDENCE_FIELD}' — nothing establishes them as residences, so they "
        f"must not be asked about and must not ship. First one: "
        f"{ex.get('address', '?')} | {ex.get('city', '?')} {ex.get('zip', '')}. "
        f"\n\nA queue built before this stamp existed will always fail here, and "
        f"that is the point — those queues are the ones with the retail stores "
        f"in them. Re-screen it and sweep the result:\n"
        f"    python3 residential_filter.py <2_residential_queue.json> \\\n"
        f"        -o 2_residential_queue.json \\\n"
        f"        --dropped 2_dropped_nonresidential.json \\\n"
        f"        --held 2_held_unresolved.json\n"
        f"Carrier answers already on 3_sweep.csv are not wasted: a row that "
        f"re-screens clean keeps the answer it already has.")


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    return 6371000 * 2 * math.asin(math.sqrt(
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))


def _structure_at(session, lat: float, lon: float, dd: float = 0.00022):
    """EVERY FEMA structure near a coordinate, nearest first. [] if there is none.

    Returns the same shape as `FootprintIndex.envelope` -- a list of
    (edge_metres, attributes, pin_is_inside) -- so the network fallback and the
    offline index hand `_judge` the identical evidence and cannot reach different
    verdicts about the same pin. It used to return a single `dict`: the nearest
    building by CENTROID, with the rest of the envelope discarded before the
    caller could see it, and no distance attached to what survived.

    Both of those losses mattered. 44.15% of sampled queue rows have more than
    one building within this envelope and 1.15% hold buildings that flatly
    disagree -- a house and a school, a house and a retail shell -- so throwing
    away everything but the closest centroid made the verdict a coin flip. And
    with no distance on the survivor there was nothing to tell "the pin is on
    this house" from "there is a house 20 m up the street".

    Geometry is requested rather than centroids because the edge is what "is this
    pin at that building" means -- see `_dist_to_ring`.
    """
    env = f"{lon-dd},{lat-dd},{lon+dd},{lat+dd}"
    for _ in range(3):
        try:
            endpoint_guard.before_attempt(_FEMA_ENDPOINT)
            r = session.get(FEMA, params={
                "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": _FIELDS,
                "returnGeometry": "true", "outSR": "4326", "f": "json",
            }, impersonate="chrome124", timeout=25)
        except endpoint_guard.EndpointDown:
            # The circuit is open: nothing left this machine, so nothing was
            # asked about this address. QUERY_FAILED is already this function's
            # word for that, and it is the one that matters -- the caller holds
            # the row and counts it unevaluated, so a dead FEMA fails the batch
            # guard instead of passing 500,000 addresses to the carrier as
            # "no structure on file (rural home)".
            return QUERY_FAILED
        except Exception:
            # SILENCE ONLY. No response object exists, so nothing answered at
            # all -- a refused connection, a timeout, DNS. This is the streak
            # the circuit counts.
            endpoint_guard.record_failure(_FEMA_ENDPOINT)
            continue
        # FEMA SPOKE. Whatever it said -- 200, 429, 500, or the HTTP-200 error
        # body handled below -- a reply is proof the endpoint is alive, and the
        # streak resets. Counting a throttle as a failure would open the circuit
        # on a service that is working and stop a pull mid-market, which is a
        # worse outcome than the one being prevented.
        endpoint_guard.record_success(_FEMA_ENDPOINT)
        try:
            # A REFUSED QUERY IS NOT AN EMPTY ONE, AND FEMA REFUSES WITH HTTP 200.
            #
            # The body of a refusal is {"error": {...}} with no features array.
            # Read straight, `.get("features", [])` made that [] and this
            # function returned None -- the single most consequential verdict it
            # produces, since None becomes "keep: no structure on file (rural
            # home)" and counts as a real evaluation. Nothing raised, so the
            # retry above never retried and QUERY_FAILED below was unreachable:
            # a FEMA outage was indistinguishable from open country, and
            # `unevaluated` stayed 0 so the batch guard could not fire. The
            # guard raises on a refusal; a present-but-empty features array
            # still returns the genuine empty envelope.
            hits = []
            for f in fetch_guard.features(r, where="FEMA _structure_at"):
                rings = (f.get("geometry") or {}).get("rings") or []
                if not rings:
                    continue
                d = min(_dist_to_ring(lon, lat, ring) for ring in rings)
                inside = any(_point_in_ring(lon, lat, ring) for ring in rings)
                hits.append((d, f.get("attributes") or {}, inside))
            hits.sort(key=lambda h: (not h[2], h[0]))
            return hits
        except Exception:
            continue
    # Query failed outright after every retry. This is NOT the same as "FEMA has
    # no structure here", and conflating the two is how this filter silently
    # stopped filtering: both used to return None, so a FEMA outage looked
    # identical to open country and every address was kept. Return the sentinel
    # so the caller can count real failures and refuse to certify the batch.
    return QUERY_FAILED


# ---------------------------------------------------------------------------
# BULK FOOTPRINTS — one download for the whole market instead of one call per
# address.
#
# `_structure_at` costs an HTTP round trip per address. On a 178,642-address
# market that is roughly 90,000 requests and ~35 minutes of a person waiting,
# for a question every one of those requests answers from the SAME small set of
# buildings.
#
# Measured on China Grove: every FEMA footprint in the market bbox -- 112,308 of
# them -- downloads in about 90 seconds in 0.02-degree tiles. After that the
# question "is there a building under this pin, and what is it" is a
# point-in-polygon test against an in-memory grid, at hundreds of thousands per
# second.
#
# The per-address call stays as the fallback for anything the tiles could not
# cover, so a gap in the bulk fetch degrades to the old behaviour rather than to
# a silent approval.
# ---------------------------------------------------------------------------

#: Tile edge in degrees. 0.02 deg is ~2 km; small enough that a tile rarely hits
#: the service's 2,000-record page limit, large enough that a county is a few
#: hundred tiles rather than tens of thousands.
_TILE = 0.02

#: Tiles we are willing to ask for, whatever the area. A town lands well under
#: this; a whole state is scaled up to meet it. Not a request budget -- a tile
#: holding more than one page costs one request per page on top.
_TILE_BUDGET = 3000

#: Grid cell for the in-memory index, ~200 m.
_CELL = 0.002

#: Concurrent footprint requests. Unchanged from the tile-at-a-time download
#: this replaces: the fix below is what the twelve workers are given to do, not
#: how many of them there are. FEMA is a public service and this is not the
#: place to lean on it harder.
#:
#: That reasoning is now measured rather than asserted. 2026-08-27, through
#: _tile_page itself, over the same 200 real Broxton tiles in one warm process:
#:      6 workers    653/min   0% err
#:     12 workers  1,605/min   0% err
#:     24 workers  2,031/min   0% err
#: The bend is at 12, and it is sharp: 6 -> 12 buys 2.5x, 12 -> 24 buys 1.27x.
#: So 24 spends double the concurrent load on that public service to take a
#: quarter off the download, which is exactly the trade the paragraph above
#: refuses. 12 is the knee, not merely the number this inherited. 24 is the
#: highest setting anybody has run; above it the refusal behaviour is unknown.
_DOWNLOAD_WORKERS = 12

#: Rows per page. The service's own `maxRecordCount`, re-read from the layer at
#: run time by `_layer_meta` -- if FEMA ever lowers it, asking for 2,000 and
#: stepping the offset by 2,000 would step straight over the rows it withheld.
_PAGE = 2000

#: Where downloaded tiles are kept between runs. `FL_FOOTPRINT_CACHE=0` turns
#: the cache off entirely; any other value is used as the directory.
_CACHE_ENV = os.environ.get("FL_FOOTPRINT_CACHE")
_CACHE_ON = (_CACHE_ENV or "").strip().lower() not in ("0", "off", "no", "false")
_CACHE_DIR = (_CACHE_ENV if (_CACHE_ON and _CACHE_ENV) else
              os.path.join(os.path.dirname(os.path.dirname(HERE)),
                           "04_Data", "cache", "footprints"))

#: A cached tile is refused once it is this old, whatever FEMA's stamp says.
#: The vintage key below is the real guard; this is the backstop for the case it
#: cannot see -- FEMA re-surveying ground without moving `editingInfo`. Nothing
#: on disk gets to answer for the world indefinitely.
_CACHE_MAX_AGE_S = float(os.environ.get("FL_FOOTPRINT_CACHE_DAYS") or 30) * 86400

_META_LOCK = threading.Lock()
_META: list = []


def _layer_meta(log=print) -> tuple:
    """(vintage, page size) for the FEMA layer. Vintage is None if it won't say.

    THE CACHE MAY ONLY EVER ANSWER FOR THE VINTAGE IT WAS WRITTEN FROM.

    A footprint file sitting on disk is precisely the failure this module was
    rebuilt around -- not seeing is not the same as nothing being there -- if it
    can still answer for ground FEMA has re-surveyed since. So FEMA's own edit
    stamps are part of the cache key: the day the layer is edited, every entry
    written before it stops matching and is fetched again. And when the service
    will not state a vintage, the cache is neither read nor written for that
    run, because a file we cannot date is a file we cannot trust.

    Read once per process; both callers below share the result.
    """
    with _META_LOCK:
        if _META:
            return _META[0]
        vintage, page = None, _PAGE
        try:
            r = cr.Session().get(_LAYER, params={"f": "json"},
                                 impersonate="chrome124", timeout=30)
            body = fetch_guard.ok_json(r, require="editingInfo",
                                       where="FEMA layer metadata")
            ed = body.get("editingInfo") or {}
            stamps = [ed.get("dataLastEditDate"), ed.get("lastEditDate")]
            if any(s is not None for s in stamps):
                vintage = "-".join(str(s) for s in stamps)
            try:
                page = min(_PAGE, int(body.get("maxRecordCount") or _PAGE))
            except (TypeError, ValueError):
                page = _PAGE
        except Exception as exc:
            log(f"    FEMA would not state its data vintage "
                f"({type(exc).__name__}); the tile cache is off for this run")
        _META.append((vintage, page))
        return _META[0]


def _cache_path(vintage: str, bbox) -> str:
    s0, w0, n0, e0 = bbox
    key = f"{vintage}|{_FIELDS}|{s0:.6f},{w0:.6f},{n0:.6f},{e0:.6f}"
    return os.path.join(_CACHE_DIR,
                        hashlib.sha1(key.encode()).hexdigest() + ".json.gz")


def _cache_read(path: str):
    """The tile's rings, or None if there is nothing usable on disk."""
    try:
        if time.time() - os.path.getmtime(path) > _CACHE_MAX_AGE_S:
            return None
        with gzip.open(path, "rt") as fh:
            return [(ring, attrs) for ring, attrs in json.load(fh)]
    except Exception:
        # A truncated or unreadable entry is a miss, never an empty tile. The
        # caller re-downloads; it must not conclude the ground is bare.
        return None


def _cache_write(path: str, rings: list) -> None:
    """Persist ONE COMPLETE tile. Written via a temp file so a run killed
    mid-write cannot leave a half tile that later reads as a whole one."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with gzip.open(tmp, "wt", compresslevel=1) as fh:
            json.dump(rings, fh)
        os.replace(tmp, path)
    except Exception:
        pass          # the cache is an optimisation; it never fails a run


def _tile_count(bbox):
    """(bbox, exact feature count) -- or (bbox, None) if FEMA would not say.

    Asked ONLY of a tile whose first page came back truncated, never of every
    tile. Probing all of them costs a request each and buys nothing on a market
    that fits in one page per tile: China Grove is 494 tiles and 497 pages, so
    probing first doubled its request count to learn what the first page had
    already said, and its download went from ~11s to ~15s.

    On a tile that did overflow it buys two things the download cannot do
    without it: the exact page ranges, so the rest of that tile is fetched all
    at once instead of walking `resultOffset` until a short page arrives; and an
    independent denominator to reconcile the download against, so a tile that
    came back short is caught rather than assumed whole.

    None means the probe itself failed. The caller falls back to the serial
    walk -- it must never be read as an empty tile.
    """
    s0, w0, n0, e0 = bbox
    ses = cr.Session()
    for attempt in range(3):
        try:
            endpoint_guard.before_attempt(_FEMA_ENDPOINT)
            r = ses.get(FEMA, params={
                "geometry": f"{w0},{s0},{e0},{n0}",
                "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "returnCountOnly": "true", "f": "json",
            }, impersonate="chrome124", timeout=60)
        except endpoint_guard.EndpointDown:
            # Refused locally, no socket opened. `None` is already this
            # function's word for "the probe itself failed", and the caller
            # falls back to the serial walk on it -- it is never read as an
            # empty tile, which is the one reading that would delete buildings.
            return bbox, None
        except Exception:
            # SILENCE ONLY -- nothing came back to parse.
            endpoint_guard.record_failure(_FEMA_ENDPOINT)
            if attempt < 2:
                time.sleep(2 + attempt * 3)
            continue
        # The service answered; the streak resets whatever the answer was.
        endpoint_guard.record_success(_FEMA_ENDPOINT)
        try:
            # Same refusal trap as everywhere else in this file: FEMA refuses
            # with HTTP 200 and a body carrying no 'count' at all, and reading
            # that as 0 would declare an inhabited tile empty and skip it.
            return bbox, int(fetch_guard.ok_json(
                r, require="count", where="FEMA tile count")["count"])
        except Exception:
            if attempt < 2:
                time.sleep(2 + attempt * 3)
    return bbox, None


def _tile_page(args):
    """One page of one tile: (tile index, rings, features read, more to come).

    `features read` is None when the page failed after every retry; `more` is
    True when the service said it had to truncate, i.e. this tile needs pages
    beyond this one.

    Splitting a page out of `_tile_footprints` is the whole point. Paging inside
    a tile used to be serial by construction -- one thread walking `resultOffset`
    until a short page came back -- so the biggest tile in a state was one thread
    doing 133 requests in a row and no amount of tile-level parallelism could
    shorten it. Measured on the Texas statewide download, that single tile took
    344s of a 1,081s download. Once the tile's count is known every remaining
    page is an independent job and they all go into the same pool.
    """
    i, bbox, offset, page = args
    s0, w0, n0, e0 = bbox
    ses = cr.Session()
    for attempt in range(4):
        try:
            endpoint_guard.before_attempt(_FEMA_ENDPOINT)
            r = ses.get(FEMA, params={
                "geometry": f"{w0},{s0},{e0},{n0}",
                "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                "outFields": _FIELDS, "returnGeometry": "true",
                "orderByFields": _ORDER, "resultOffset": offset,
                "resultRecordCount": page, "f": "json",
            }, impersonate="chrome124", timeout=90)
        except endpoint_guard.EndpointDown:
            # Refused locally. This page was never asked for, so it exits by the
            # same door an exhausted page uses: `features read = None`, which
            # marks the tile FAILED. A failed tile withdraws its claim to have
            # been surveyed, so `covers()` keeps sending its addresses to the
            # per-address lookup instead of reading the gap as open country.
            #
            # This runs under a 12-thread pool over thousands of tiles, which is
            # exactly the shape of 2026-08-27: without the shared counter, one
            # dead FEMA means every remaining tile spends four attempts on it.
            return i, [], None, False
        except Exception:
            # SILENCE ONLY -- no HTTP response at all.
            endpoint_guard.record_failure(_FEMA_ENDPOINT)
            if attempt < 3:          # nothing left to retry after the last
                time.sleep(2 + attempt * 3)
            continue
        endpoint_guard.record_success(_FEMA_ENDPOINT)
        try:
            # Refused pages raise here rather than returning [], for the reason
            # spelled out in `_tile_footprints`: an unsurveyed tile that reports
            # itself complete switches the per-address fallback off and every
            # address under it is kept as open country.
            body = fetch_guard.ok_json(r, require="features",
                                       where="FEMA footprint page")
            feats = body.get("features") or []
            # DELIBERATELY WIDER THAN THE OLD STOP CONDITION, WHICH WAS
            # `short page OR no exceededTransferLimit`. Either signal on its own
            # now means "go and count this tile": a page that came back exactly
            # full is suspicious whether or not the flag was set, and the flag is
            # the service saying outright that it truncated. The worst case is
            # one wasted count probe on a tile that happens to be an exact
            # multiple of the page size; the case it removes is a truncated tile
            # read as a whole one, which is a building the screen never sees.
            more = bool(body.get("exceededTransferLimit")) or len(feats) >= page
            return i, [(ring, f.get("attributes") or {})
                       for f in feats
                       for ring in ((f.get("geometry") or {}).get("rings") or [])
                       ], len(feats), more
        except Exception:
            if attempt < 3:          # nothing left to retry after the last
                time.sleep(2 + attempt * 3)
    return i, [], None, False


def _tile_footprints(args):
    """(rings, complete). `complete` is False if any page of this tile failed --
    an incomplete tile means 'no building here' is not something we can claim.

    Now the FALLBACK, for a tile whose count probe was refused. It walks the
    offsets one page at a time and stops on a short page, which needs no count
    and therefore still works when `_tile_count` could not get one.
    """
    s0, w0, n0, e0 = args
    out, offset = [], 0
    ses = cr.Session()
    while True:
        page = None
        for attempt in range(4):
            try:
                endpoint_guard.before_attempt(_FEMA_ENDPOINT)
                r = ses.get(FEMA, params={
                    "geometry": f"{w0},{s0},{e0},{n0}",
                    "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                    "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                    "outFields": _FIELDS,
                    "returnGeometry": "true", "orderByFields": _ORDER,
                    "resultOffset": offset,
                    "resultRecordCount": _PAGE, "f": "json",
                }, impersonate="chrome124", timeout=90)
            except endpoint_guard.EndpointDown:
                # Refused locally: this tile was not surveyed. It leaves by the
                # incomplete door below (`page` stays None -> `return out, False`)
                # for the same reason spelled out there -- an unsurveyed tile
                # that reports itself complete switches the per-address fallback
                # off and every address under it is kept as open country.
                return out, False
            except Exception:
                # SILENCE ONLY -- no HTTP response at all.
                endpoint_guard.record_failure(_FEMA_ENDPOINT)
                page = None
                if attempt < 3:      # nothing left to retry after the last
                    time.sleep(2 + attempt * 3)
                continue
            endpoint_guard.record_success(_FEMA_ENDPOINT)
            try:
                # A TILE THAT WAS REFUSED WAS NOT SURVEYED, AND FEMA REFUSES
                # WITH HTTP 200.
                #
                # {"error": {...}} parses fine, so nothing raised: the loop broke
                # on the first attempt, `feats` was [], and the tile returned
                # complete=True -- "I surveyed this and it holds no buildings".
                # That True ANDs into `complete`, so FootprintIndex.covers()
                # answers for the whole market offline and the per-address
                # fallback is switched off. Every address under the refused tile
                # is then kept as "no structure on file" with not one building
                # ever looked at, and it never counts as unevaluated. Raising
                # here makes the sleep below a real retry, and a tile that
                # exhausts its attempts comes back complete=False.
                page = fetch_guard.ok_json(r, require="features",
                                           where="FEMA footprint tile")
                break
            except Exception:
                page = None
                if attempt < 3:          # nothing left to retry after the last
                    time.sleep(2 + attempt * 3)
        if not page:
            return out, False
        feats = page.get("features") or []
        for f in feats:
            for ring in ((f.get("geometry") or {}).get("rings") or []):
                out.append((ring, f.get("attributes") or {}))
        if len(feats) < _PAGE or not page.get("exceededTransferLimit"):
            return out, True
        offset += _PAGE


def _is_outbuilding(info: dict) -> bool:
    """True only when FEMA actually says 'outbuilding'. See OUTBLDG_TRUE."""
    return str((info or {}).get("OUTBLDG") or "").strip().upper() in OUTBLDG_TRUE


def _dist_to_ring(px: float, py: float, ring) -> float:
    """Metres from a point to the nearest EDGE of a footprint.

    Distance to the centroid is the wrong measure here and it hides exactly the
    buildings this screen must catch: the pin beside a 33,000 sqft retail shell
    is 5 m from its wall and 60 m from its middle, so a centroid test calls it
    open ground. The edge is what "is this pin at that building" means.
    """
    mx = 111320.0 * math.cos(math.radians(py))   # metres per degree of longitude
    my = 110540.0                                # metres per degree of latitude
    best = float("inf")
    n = len(ring)
    for i in range(n):
        ax = (ring[i][0] - px) * mx
        ay = (ring[i][1] - py) * my
        bx = (ring[(i + 1) % n][0] - px) * mx
        by = (ring[(i + 1) % n][1] - py) * my
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < best:
            best = d
    return best


def _point_in_ring(px: float, py: float, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > py) != (yj > py)) and (
                px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


class FootprintIndex:
    """Every building in the market, answerable offline.

    `covers(lat, lon)` is the important part. When every tile downloaded
    cleanly, this index has SEEN the whole market -- so a point with no building
    under it genuinely has no building, and asking the network again would spend
    a request to be told what we already know. That was 38% of addresses still
    going over the wire for nothing.

    If any tile failed, `complete` is False and the index only answers hits;
    misses fall through to the network, because an incomplete download must
    never be read as an empty one. Same principle as QUERY_FAILED above: not
    seeing is not the same as nothing being there.

    `covers()` may only ever suppress a REQUEST, never a piece of evidence. It
    used to do both: a complete download made `at()` returning None final, and
    the caller took that as "no building here" without ever asking about the
    buildings around the pin, which is offline and free. That is how 532,059
    Texas addresses were passed to the carrier as rural homes with a classified
    building sitting metres away. Ask this index everything it can answer first;
    `covers()` decides only whether a miss still needs the network.

    `envelope()` is what the screen actually calls -- it answers containment and
    proximity in one pass, and hands back EVERY building rather than a winner,
    because the screen has to be able to see when the buildings disagree.
    `at()` and `near()` remain for callers that want only one of those.
    """

    def __init__(self, rings: list, bbox=None, complete: bool = False):
        self.rings = rings
        self.bbox = bbox
        self.complete = complete
        self.boxes = []
        self.grid: dict = collections.defaultdict(list)
        for i, (ring, attrs) in enumerate(rings):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            box = (min(xs), min(ys), max(xs), max(ys))
            self.boxes.append(box)
            for gx in range(int(box[0] // _CELL), int(box[2] // _CELL) + 1):
                for gy in range(int(box[1] // _CELL), int(box[3] // _CELL) + 1):
                    self.grid[(gx, gy)].append(i)

    def __len__(self):
        return len(self.rings)

    def covers(self, lat: float, lon: float) -> bool:
        """True when a miss here can be trusted as 'there is no building'."""
        if not self.complete or not self.bbox:
            return False
        s0, w0, n0, e0 = self.bbox
        return s0 <= lat <= n0 and w0 <= lon <= e0

    def contains(self, lat: float, lon: float) -> bool:
        """True only when this point is INSIDE a building polygon.

        Deliberately a different name from `covers`, because the two answer
        opposite questions and one delivery gate has already been wired to the
        wrong one. `covers` says "we downloaded the ground here", which is true
        across an entire market once its tiles land; this says "there is a roof
        under this pin", which is what standard R18 proves before a lead ships.
        A gate asking `covers` passes every pin in the market and tests no
        polygon at all.

        `at()` returns the building's attributes and is what the screen wants;
        this returns the yes/no the gate wants, off the same test, so the two
        can never disagree about the same pin.
        """
        return self.at(lat, lon) is not None

    def at(self, lat: float, lon: float) -> dict | None:
        """The building this pin stands on, or None."""
        for i in self.grid.get((int(lon // _CELL), int(lat // _CELL)), ()):
            x0, y0, x1, y1 = self.boxes[i]
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                ring, attrs = self.rings[i]
                if _point_in_ring(lon, lat, ring):
                    return attrs
        return None

    def envelope(self, lat: float, lon: float, radius_m: float = NEAR_M) -> list:
        """EVERY building within radius_m of the pin: (edge_m, attrs, inside).

        Containing buildings first, then nearest edge first. Empty list means
        the ground around this pin genuinely holds no building.

        `at()` alone is not enough to screen an address, and assuming it was is
        what let schools and retail into a Texas sweep. E911 address points are
        published at the parcel frontage or the driveway, not on the roof, so
        the pin stands INSIDE a footprint for barely a third of a real queue --
        37.9% of a 1,500-row Texas sample. Every one of the rest used to resolve
        to "no structure on file", which meant a retail store 5 m away was
        invisible to the screen.

        WHY EVERY BUILDING AND NOT JUST THE CLOSEST. This returned only the
        nearest, and the caller had no way to know anything else was there. On a
        2,000-row census of the shipped TX-FULL queue, 44.15% of pins have more
        than one building inside this envelope -- up to twelve -- and on 1.15%
        those buildings contradict each other: a dwelling and a school, a
        dwelling and a retail shell. The verdict on all of them was decided by
        which centroid happened to be nearer, so 4122 Texas Boulevard was kept
        beside an 8,380 sqft retail unit and 1409 S 4TH beside a 56,188 sqft
        school. Handing the caller the whole envelope is what lets it say
        "these disagree" instead of picking a winner.

        The grid is already built and already in memory, so returning all of
        them costs nothing over returning one.
        """
        r_lat = radius_m / 110540.0
        r_lon = radius_m / max(1.0, 111320.0 * math.cos(math.radians(lat)))
        hits: list = []
        seen: set[int] = set()
        for gx in range(int((lon - r_lon) // _CELL), int((lon + r_lon) // _CELL) + 1):
            for gy in range(int((lat - r_lat) // _CELL), int((lat + r_lat) // _CELL) + 1):
                for i in self.grid.get((gx, gy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    x0, y0, x1, y1 = self.boxes[i]
                    if (lon < x0 - r_lon or lon > x1 + r_lon
                            or lat < y0 - r_lat or lat > y1 + r_lat):
                        continue
                    ring, attrs = self.rings[i]
                    d = _dist_to_ring(lon, lat, ring)
                    if d <= radius_m:
                        hits.append((d, attrs, _point_in_ring(lon, lat, ring)))
        hits.sort(key=lambda h: (not h[2], h[0]))
        return hits

    def near(self, lat: float, lon: float, radius_m: float = NEAR_M) -> dict | None:
        """The nearest building within radius_m, or None. Kept for callers that
        only want the closest; the screen itself uses `envelope`, because the
        closest building on its own cannot show a disagreement."""
        hits = self.envelope(lat, lon, radius_m)
        return hits[0][1] if hits else None


def build_footprint_index(addresses: list[dict], log=print) -> FootprintIndex | None:
    """Download every footprint covering these addresses. None if it fails."""
    pts = [(float(a["lat"]), float(a["lon"])) for a in addresses
           if a.get("lat") not in (None, "") and a.get("lon") not in (None, "")]
    if not pts:
        return None
    south = min(p[0] for p in pts) - 0.01
    north = max(p[0] for p in pts) + 0.01
    west = min(p[1] for p in pts) - 0.01
    east = max(p[1] for p in pts) + 0.01
    # TILE SIZE SCALES WITH THE AREA. A FIXED ONE DOES NOT SURVIVE A STATE.
    #
    # 0.02 degrees is right for a town: ~2 km, small enough that a tile rarely
    # hits the service's 2,000-record page limit. Across the whole of Texas the
    # same constant asked for 229,417 tiles -- twelve hours of requests before a
    # single address was screened.
    #
    # The tiles are only a way to page a big query, and the code below already
    # pages WITHIN a tile, so a larger tile costs nothing but more pages. What
    # matters is keeping the request count bounded, so the tile is sized from
    # the actual bounding box to land near _TILE_BUDGET tiles however large the
    # area is.
    span = max(north - south, east - west)
    tile = max(_TILE, span / max(1.0, _TILE_BUDGET ** 0.5))

    # ONLY DOWNLOAD TILES THAT ACTUALLY CONTAIN AN ADDRESS.
    #
    # Walking the whole bounding box is right for a town, where the box IS the
    # town. Across a market the size of a state it is nearly all empty country
    # between towns: screening 9,098 Texas leads asked for 2,090 tiles and sat
    # on that download for sixteen minutes, because the leads sit in a few
    # dozen towns and the box around them covers half of Texas.
    #
    # Addresses cluster, so snapping each one to its tile and fetching only
    # that set collapses the download to the inhabited part of the market. This
    # is the same fix rooftop_place.prime() already carries -- the bug existed
    # in both modules and was only fixed in one.
    keys = {(int(math.floor(la / tile)), int(math.floor(lo / tile)))
            for la, lo in pts}
    tiles = [(k[0] * tile, k[1] * tile, (k[0] + 1) * tile, (k[1] + 1) * tile)
             for k in sorted(keys)]
    log(f"    downloading every building in the market — {len(tiles):,} occupied "
        f"tiles (one pass, instead of one request per address)")
    rings: list = []
    complete = True
    t0 = time.time()

    # STEP 1 — ANYTHING ALREADY ON DISK FOR THIS FEMA VINTAGE COSTS NOTHING.
    #
    # Nothing was cached, anywhere, and every caller entered through
    # `filter_residential`, which calls this function unconditionally. So the
    # same state came down twice on 2026-08-27 -- 656 tiles in 1,199s and then
    # 654 tiles in 1,081s, 38 minutes and ~4.9 GB of wire for byte-identical
    # buildings -- because the second run was a re-screen of the first run's
    # market. A re-screen is the normal case, not the exception: every threshold
    # change in this file forces one.
    #
    # Only a tile that came back WHOLE is ever written (see the reconcile below),
    # so a cache hit can be trusted the way a clean download can. A partial tile
    # on disk would be far worse than no cache: it would make `covers()` answer
    # for ground nobody surveyed.
    vintage, page_size = _layer_meta(log)
    if not _CACHE_ON:
        vintage = None          # FL_FOOTPRINT_CACHE=0: never read, never write
    pending: list = []
    cached = 0
    for bbox in tiles:
        got = _cache_read(_cache_path(vintage, bbox)) if vintage else None
        if got is None:
            pending.append(bbox)
        else:
            rings.extend(got)
            cached += 1
    if cached:
        log(f"      {cached:,}/{len(tiles):,} tiles already on disk for FEMA "
            f"vintage {vintage} — {len(rings):,} buildings, no requests")

    # STEP 2 — THE FIRST PAGE OF EVERY TILE, EXACTLY AS BEFORE.
    #
    # Most tiles hold fewer buildings than one page and are finished right here
    # for the single request they have always cost. That is why the count probe
    # is not asked of everything: on a town it would double the request count to
    # learn what the first page already says.
    per_tile: dict = collections.defaultdict(list)
    read: collections.Counter = collections.Counter()
    failed: set = set()
    overflow: list = []
    done = 0
    if pending:
        with cf.ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as ex:
            for i, got, n, more in ex.map(
                    _tile_page, [(i, b, 0, page_size)
                                 for i, b in enumerate(pending)]):
                per_tile[i].extend(got)
                if n is None:
                    failed.add(i)
                else:
                    read[i] += n
                if more:
                    overflow.append(i)
                done += 1
                if done % 250 == 0:
                    log(f"      {done:,}/{len(pending):,} tiles — "
                        f"{sum(len(v) for v in per_tile.values()) + len(rings):,}"
                        f" buildings")

    # STEP 3 — THE TILES THAT OVERFLOWED, ALL THEIR REMAINING PAGES AT ONCE.
    #
    # This is where the download's floor was. A tile that overflows was paged by
    # ONE thread walking `resultOffset` until a short page came back, so the
    # largest tile in Texas was 133 requests end to end and no amount of
    # tile-level parallelism could shorten it -- 344s of a 1,081s download.
    # Counting it first turns those 132 remaining pages into 132 independent
    # jobs that share the pool with every other tile's.
    #
    # The count is also the independent denominator: an overflowed tile is only
    # complete when the pages read back exactly the number of features FEMA
    # said were in it. Short is short whether a page was refused, the ordering
    # failed to hold the rows still, or FEMA edited underneath us -- and any of
    # those makes this tile stop claiming to be surveyed, which is what keeps
    # `covers()` falling back to the per-address lookup instead of reading a gap
    # as open country.
    counts: dict = {}
    serial: list = []
    if overflow:
        with cf.ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as ex:
            for i, (_bbox, n) in zip(overflow, ex.map(
                    _tile_count, [pending[i] for i in overflow])):
                if n is None:
                    serial.append(i)     # probe refused — walk it the old way
                else:
                    counts[i] = n
        jobs = [(i, pending[i], off, page_size)
                for i in sorted(counts)
                for off in range(page_size, counts[i], page_size)]
        if jobs:
            log(f"      {len(overflow):,} tiles hold more than one page "
                f"({sum(counts.values()):,} buildings) — fetching their "
                f"remaining {len(jobs):,} pages {_DOWNLOAD_WORKERS} at a time "
                f"instead of one after another")
            done = 0
            with cf.ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as ex:
                for i, got, n, _more in ex.map(_tile_page, jobs):
                    per_tile[i].extend(got)
                    if n is None:
                        failed.add(i)
                    else:
                        read[i] += n
                    done += 1
                    if done % 250 == 0:
                        log(f"      {done:,}/{len(jobs):,} pages — "
                            f"{sum(len(v) for v in per_tile.values()) + len(rings):,}"
                            f" buildings")

    # STEP 4 — TILES WHOSE COUNT PROBE WAS REFUSED, WALKED THE OLD WAY.
    # Their first page is discarded and re-fetched rather than merged, so the
    # tile is one uninterrupted walk and cannot end up holding a page twice.
    if serial:
        log(f"      {len(serial):,} tiles would not state a count — walking "
            f"their pages one at a time")
        with cf.ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as ex:
            for i, (got, ok) in zip(serial, ex.map(
                    _tile_footprints, [pending[i] for i in serial])):
                per_tile[i] = got
                failed.discard(i)
                if not ok:
                    failed.add(i)
                counts.pop(i, None)

    # RECONCILE, THEN BANK WHAT CAME BACK WHOLE.
    for i, bbox in enumerate(pending):
        got = per_tile.get(i) or []
        # Rings from a short tile are kept -- they are real buildings and real
        # evidence. It is the CLAIM of completeness that is withdrawn.
        rings.extend(got)
        expected = counts.get(i)
        whole = i not in failed and (expected is None or read[i] == expected)
        complete = complete and whole
        if whole and vintage:
            _cache_write(_cache_path(vintage, bbox), got)

    if not rings:
        log("    no footprints came back — falling back to per-address lookups")
        return None
    idx = FootprintIndex(rings, bbox=(south, west, north, east), complete=complete)
    log(f"    {len(idx):,} buildings held in memory "
        f"({time.time() - t0:.0f}s) — the screen is now offline"
        + ("" if complete else "; some tiles failed, so empty spots are still "
                               "confirmed over the network"))
    return idx


def _judge(hits) -> tuple[str, str, str]:
    """(verdict, why, evidence) for one pin, from EVERY building around it.

    `hits` is what `FootprintIndex.envelope` and `_structure_at` both return:
    (edge_metres, attributes, pin_is_inside), containing buildings first then
    nearest first. An empty list means the ground here genuinely holds no
    building -- never that we failed to look, which is QUERY_FAILED and is the
    caller's to handle before it gets here.

    ONE TABLE, ONE PLACE. This decision used to be an if/elif chain inside the
    worker loop, which is why it drifted: the county branch 100 lines above it
    reached a keep without ever passing through here, and the two disagreed
    about the same address in silence. Every verdict this module produces about
    a building now comes out of this function, so a rule added here cannot be
    bypassed by a path that predates it.

    THE NEGATIVE ANSWERS AT THE TOP ARE UNCHANGED ON PURPOSE. An outbuilding and
    a DROP_CLASSES occupancy on the subject building drop exactly as they always
    have, at exactly the same NEAR_M radius. Everything new is on the KEEP side,
    which is where the hole was: the drop rules were guarded and the keep rule
    was not, so "we could not tell" fell out of the bottom as a pass.
    """
    if not hits:
        return "hold", "no building at this coordinate", ""

    d, info, inside = hits[0]
    occ = (info or {}).get("OCC_CLS") or ""
    prim = str((info or {}).get("PRIM_OCC") or "").strip()

    if _is_outbuilding(info):
        return "drop", "outbuilding", ""
    if occ in DROP_CLASSES:
        return "drop", occ, ""
    if occ != RESIDENTIAL_CLASS:
        # "Unclassified" and a blank OCC_CLS are not the same thing -- one is
        # FEMA declining to say, the other is FEMA saying nothing at all -- and
        # neither is evidence of a home. Named apart so the manifest shows which
        # of the two it was.
        return "hold", (f"FEMA occupancy {occ!r}" if occ
                        else "FEMA returned no occupancy class"), ""

    # Past here FEMA calls the nearest building a home. That is where the screen
    # used to stop, and stopping there is what put vacant lots, sheds and a
    # nursing home into the queue.
    if not inside:
        # A HOUSE NEARBY IS NOT THE SAME CLAIM AS "THIS PIN IS A HOUSE".
        # Containment is exempt: a pin standing inside the footprint needs no
        # radius and cannot be the neighbour's.
        if d > KEEP_M:
            return "hold", (f"nearest home is over {KEEP_M:.0f} m from the pin — "
                            f"it may be the neighbour's, not this address's"), ""
        contra = [h for h in hits[1:]
                  if _is_outbuilding(h[1])
                  or (h[1].get("OCC_CLS") or "") in DROP_CLASSES]
        if contra:
            # MIXED EVIDENCE IS NOT EVIDENCE. Both of these buildings are inside
            # the envelope and only one of them can be this address. Deciding it
            # by which centroid is nearer is a coin flip, and the flip landed
            # wrong 16 times in 2,000 rows -- homes kept beside a retail shell
            # and a 56,188 sqft school. Held, not dropped: the pin may genuinely
            # be the house, and this screen does not get to guess either way.
            other = (contra[0][1].get("OCC_CLS") or "an unnamed class")
            return "hold", (f"buildings at this pin disagree — a home and one "
                            f"FEMA calls {other}, both within {NEAR_M:.0f} m"), ""

    if prim in NOT_A_DWELLING_PRIM:
        # Residential in CLASS, and a positive statement that nobody lives here.
        return "drop", prim, ""
    if prim not in RESIDENTIAL_PRIM:
        return "hold", (f"FEMA calls this Residential but its use is "
                        f"{prim or 'not stated'}"), ""

    sqft = (info or {}).get("SQFEET")
    try:
        area = float(sqft)
    except (TypeError, ValueError):
        area = 0.0
    if not area:
        # Measured populated on 2,817 of 2,817 Residential footprints in Texas,
        # so this is the rare case -- and rooftop_place holds on it too rather
        # than skipping a check it cannot run.
        return "hold", "building area unknown — the size check could not run", ""
    if area < MIN_DWELLING_SQFT:
        return "hold", (f"building under {MIN_DWELLING_SQFT:.0f} sqft — shed or "
                        f"detached-garage sized, not established as a dwelling"), ""

    return "keep", RESIDENTIAL_CLASS, (
        f"FEMA {occ}/{prim}, {area:.0f} sqft, "
        + ("pin inside the footprint" if inside else f"{d:.1f} m from the pin"))


def filter_residential(addresses: list[dict], workers: int = 64,
                       progress=None, progress_log=None
                       ) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Screen an address list down to addresses established as residences.

    Returns (kept, dropped, held, counts). Each address needs 'lat' and 'lon'.

    `kept` is positively established as somebody's home and may be swept. Every
    kept row is stamped with `residential_evidence` naming the finding it rests
    on, and the sweep refuses rows without one -- see `require_screened`.
    `dropped` was answered non-residential and must not be.
    `held` could not be established either way -- it must not be swept and must
    not ship, and every row carries a 'reason' saying what we could not settle.
    The caller is required to write `held` to a manifest; the row is set aside,
    not deleted, and deleting the manifest puts it back in play.
    """
    # ONE DOWNLOAD FOR THE WHOLE MARKET, THEN ANSWER OFFLINE.
    #
    # Below, every address that has no county code costs an HTTP round trip.
    # That is ~90,000 requests and roughly 35 minutes on a 178,642-address
    # market -- all of them asking about the same few hundred thousand
    # buildings. Fetching those buildings once turns the screen into arithmetic.
    index = None
    try:
        index = build_footprint_index(addresses, log=(progress_log or print))
    except Exception as exc:
        (progress_log or print)(
            f"    bulk building download did not run ({type(exc).__name__}); "
            f"falling back to one lookup per address")

    jobs: queue.Queue = queue.Queue()
    for a in addresses:
        jobs.put(a)

    kept: list[dict] = []
    dropped: list[dict] = []
    held: list[dict] = []
    counts: collections.Counter = collections.Counter()
    lock = threading.Lock()
    done = [0]
    unevaluated = [0]
    total = len(addresses)

    def _hold(a: dict, why: str) -> None:
        """Set an address aside with a stated reason. Caller holds `lock`.

        Separate from `dropped` on purpose. A dropped row was ANSWERED -- FEMA
        or the county said what the building is. A held row is this screen
        admitting a gap in its own evidence, and the two must never read alike
        in a manifest a person is going to act on.
        """
        counts[f"hold: {why}"] += 1
        held.append({**a, "reason": f"residence not established: {why}"})
        done[0] += 1

    def work():
        session = cr.Session()
        while True:
            try:
                a = jobs.get_nowait()
            except queue.Empty:
                return

            # AN ADDRESS NEVER LEAVES THIS FUNCTION UNACCOUNTED FOR.
            #
            # The body below raises for reasons the narrow excepts inside it
            # do not catch, and an uncaught one killed the worker thread with
            # an address in its hand. Nothing reconciled the totals, so the
            # address was simply gone: 3,151 of China Grove's 178,642 --
            # 2,442 of them in China Grove itself, a THIRD of the town --
            # reached neither the queue nor the dropped list, and so were
            # never asked about fibre at all.
            #
            # A screen that cannot evaluate an address HOLDS it. Keeping it was
            # the old rule and it is what this whole module exists to undo: a
            # kept row goes to the carrier, and the gates that run later on the
            # final pin only run if the sweep answered at all.
            try:

                # THE COORDINATE GATE COMES FIRST. NOTHING DECIDES ANYTHING
                # BEFORE IT.
                #
                # Screening reads FEMA occupancy off the coordinate, so a row
                # without one cannot be screened at all -- by the county branch
                # below either, which is why this moved above it. It used to sit
                # 30 lines lower, so a row carrying a county code of SF and no
                # lat/lon was KEPT and swept, in flat contradiction of the
                # comment beside it saying such a row must be held. TX-FULL never
                # triggered it (0 of 859,660 rows carry struct_type at all), but
                # it is live for every county-sourced market, and Scurry TX is
                # what it looks like when it fires: 771,859 Census-enumerated
                # addresses with no coordinate, which cannot be placed on a roof
                # and mostly do not exist.
                #
                # HELD, NOT DEFERRED-AND-SWEPT. These rows used to be kept and
                # swept on the promise that the carrier would return a coordinate
                # and they would be screened afterwards. That puts an unscreened
                # row in front of the carrier, which is the one thing the order
                # of these two stages exists to prevent. Held here, the row is
                # recoverable the moment a coordinate is sourced for it.
                try:
                    lat_f, lon_f = float(a["lat"]), float(a["lon"])
                except (KeyError, TypeError, ValueError):
                    with lock:
                        _hold(a, "no coordinate to screen against")
                    continue

                county = str(a.get("struct_type") or "").strip().upper()
                if county in COUNTY_NONRESIDENTIAL:
                    with lock:
                        counts[f"drop: county says {COUNTY_NONRESIDENTIAL[county]}"] += 1
                        dropped.append({**a, "reason": COUNTY_NONRESIDENTIAL[county]})
                        done[0] += 1
                        if progress and done[0] % 500 == 0:
                            progress(done[0], total, len(kept))
                    continue

                # ATTRIBUTE THE PIN TO A BUILDING BEFORE JUDGING IT, AND USE
                # EVERY FREE PIECE OF EVIDENCE BEFORE GIVING UP.
                #
                # The in-memory grid answers containment and proximity in one
                # pass at no cost, so both get asked. The network is only for
                # what the download could not cover -- `covers()` may suppress a
                # REQUEST, never a piece of evidence.
                county_says_home = county in COUNTY_RESIDENTIAL
                try:
                    hits = index.envelope(lat_f, lon_f) if index is not None else []
                    if (not hits and not county_says_home
                            and not (index is not None
                                     and index.covers(lat_f, lon_f))):
                        # Nothing found AND this spot was not fully downloaded,
                        # so we cannot tell "empty" from "not fetched". Ask.
                        #
                        # Not for a county-stated dwelling, though: the cross-
                        # check below is worth having only because the index
                        # makes it free. Paying a round trip per county row to
                        # second-guess the 911 authority would put 156,692 Inman
                        # addresses back on the wire to change almost none of
                        # them -- the cost that made the old code return early.
                        hits = _structure_at(session, lat_f, lon_f)
                except (KeyError, TypeError, ValueError):
                    hits = []

                if hits is QUERY_FAILED:
                    # A failed lookup is not a finding about this address. Held,
                    # so an outage cannot put unscreened rows in front of the
                    # carrier, and counted, so the batch guard below can refuse
                    # to certify a run that never actually screened anything.
                    with lock:
                        unevaluated[0] += 1
                        _hold(a, "occupancy lookup failed")
                    continue

                verdict, why, evidence = _judge(hits)

                # THE COUNTY'S OWN STRUCTURE TYPE OUTRANKS A THIRD-PARTY
                # FOOTPRINT -- BUT IT IS EVIDENCE, NOT AN EXEMPTION FROM BEING
                # CHECKED.
                #
                # Where a county publishes StructType it is the 911 authority
                # saying what the building IS, per address. FEMA's OCC_CLS is a
                # national third-party classification resolved from a footprint
                # within ~24 m, so a farmhouse beside a barn is convicted by its
                # neighbour. Inman SC, 2026-08-24: 8,584 of 16,735 dropped
                # addresses (51%) were called dwellings by the county and thrown
                # away anyway, because this function never read the field. Eight
                # consecutive homes on Farmstead Trl, county StructType SF, were
                # dropped as "Agriculture" -- and all eight return FIBER.
                #
                # So the county still wins over a FEMA hold or a FEMA drop, and
                # nothing the county calls a dwelling is deleted. What changed is
                # that it no longer RETURNS before FEMA is consulted. It used to,
                # and that made the code below unreachable for these rows: a
                # county row sitting on a footprint FEMA calls Commercial/Retail
                # Trade was kept silently, with no cross-check that could ever
                # catch a stale or miscoded county record.
                #
                # A flat contradiction is HELD -- not kept, because we have two
                # authorities saying opposite things and no basis to pick; not
                # dropped, because the county is the 911 record and Inman is what
                # deleting on FEMA's word costs. Only the offline index feeds
                # this cross-check (see the fallback above), so it adds no
                # network requests at all: on Inman, 156,692 addresses are still
                # settled without a round trip.
                if county in COUNTY_RESIDENTIAL:
                    said = COUNTY_RESIDENTIAL[county]
                    if verdict == "drop":
                        verdict, why, evidence = "hold", (
                            f"county says {said} but FEMA says {why} at this "
                            f"pin — two authorities, opposite answers"), ""
                    else:
                        verdict, why = "keep", f"county says {said}"
                        evidence = f"county structure type {county} ({said})"

                with lock:
                    if verdict == "hold":
                        _hold(a, why)
                    else:
                        counts[f"{verdict}: {why}"] += 1
                        if verdict == "keep":
                            # THE STAMP IS WHAT THE SWEEP CHECKS FOR. A keep with
                            # nothing named behind it is exactly the row this
                            # module exists to stop, and `require_screened`
                            # refuses it at the sweep entry point.
                            kept.append({**a, EVIDENCE_FIELD: evidence})
                        else:
                            dropped.append({**a, "reason": why})
                        done[0] += 1
                    if progress and done[0] % 500 == 0:
                        progress(done[0], total, len(kept))
            except Exception as exc:
                # A crash in the screen is a gap in our evidence, not a verdict
                # about this address. Held rather than kept, so a bug in here can
                # never be the reason an unscreened row reaches the carrier.
                with lock:
                    unevaluated[0] += 1
                    _hold(a, f"screen error ({type(exc).__name__})")

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # BATCH GUARD. If most of the batch could not be evaluated, this function
    # did not screen anything -- it just handed back what it was given. Saying
    # "filtered" at that point is the exact lie that shipped commercial
    # buildings, so raise instead of returning a pass-through.
    # THE COUNT MUST RECONCILE. This is the check that would have caught the
    # silent loss above on the day it happened, instead of a month later.
    if len(kept) + len(dropped) + len(held) != total:
        missing = total - len(kept) - len(dropped) - len(held)
        raise FilterNotApplied(
            f"residential screen was handed {total:,} addresses and returned "
            f"{len(kept):,} kept + {len(dropped):,} dropped + {len(held):,} held "
            f"= {len(kept) + len(dropped) + len(held):,}. {missing:,} addresses "
            f"are unaccounted for -- they would never be asked about fibre and "
            f"nothing downstream would notice. Refusing to return a queue that "
            f"has silently lost leads.")

    if total and unevaluated[0] / total > MAX_UNEVALUATED:
        raise FilterNotApplied(
            f"residential screen could not evaluate {unevaluated[0]} of {total} "
            f"addresses ({unevaluated[0]/total:.0%}) -- occupancy lookups are failing, "
            f"so nothing has actually been filtered. Refusing to report a screened "
            f"queue. Fix the lookup and re-run; do NOT sweep this queue.")

    # THE GUARD HAS TO BE ABLE TO SEE THE FAILURE THAT ACTUALLY HAPPENED.
    #
    # On the Texas statewide run, 532,059 addresses were unresolvable and
    # `unevaluated` finished at 0, because the only thing it counted was a
    # refused lookup and no lookup was refused -- the dominant failure mode was
    # structurally invisible to the check written to catch it. Held rows are
    # counted here, against their own threshold, so the number that means "this
    # screen resolved almost nobody" can no longer read as a clean run.
    if total and len(held) / total > MAX_HELD:
        raise FilterNotApplied(
            f"residential screen could not establish a residence for "
            f"{len(held):,} of {total:,} addresses ({len(held)/total:.0%}) and "
            f"kept only {len(kept):,}. That is over the {MAX_HELD:.0%} limit: "
            f"not a screened queue, but a screen that answered for almost "
            f"nobody -- most likely no building data was loaded for this area. "
            f"Refusing to certify it. Check the footprint download and re-run; "
            f"do NOT sweep this queue. If this share really is expected for this "
            f"market, say so out loud with FL_ACCEPT_HELD_SHARE=<0-1> — it "
            f"changes only whether the batch certifies, never what is kept.")

    return kept, dropped, held, dict(counts)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Residential pre-filter (run BEFORE the sweep)")
    p.add_argument("queue_json", help="JSON list of addresses with lat/lon")
    p.add_argument("-o", "--out", required=True, help="where to write the kept addresses")
    p.add_argument("--dropped", help="where to write the dropped ones (recommended)")
    p.add_argument("--held", help="where to write the ones that could not be "
                                  "established either way (recommended)")
    p.add_argument("-w", "--workers", type=int, default=64)
    args = p.parse_args()

    with open(args.queue_json) as fh:
        addrs = json.load(fh)
    print(f"screening {len(addrs)} addresses ...", flush=True)

    def prog(d, t, k):
        print(f"  {d}/{t}  kept {k}", flush=True)

    kept, dropped, held, counts = filter_residential(addrs, args.workers, prog)
    with open(args.out, "w") as fh:
        json.dump(kept, fh)
    if args.dropped:
        with open(args.dropped, "w") as fh:
            json.dump(dropped, fh)
    if args.held:
        with open(args.held, "w") as fh:
            json.dump(held, fh)
    elif held:
        # Held rows exist to be looked at and worked. Losing them because the
        # caller forgot a flag would make the whole outcome pointless, so say so
        # rather than letting the run look complete.
        print(f"\nWARNING: {len(held)} address(es) were HELD and no --held path "
              f"was given, so they were not written anywhere.")

    print(f"\nKEPT    {len(kept)}")
    print(f"DROPPED {len(dropped)}")
    print(f"HELD    {len(held)}   (not swept, not shipped, not deleted)")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"   {k:44} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
