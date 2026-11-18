"""
Address foundation — pulls EVERY address in a market from an authoritative
government source.

THE RULE THAT MATTERS MOST
-------------------------
The address list must NEVER come from the carrier's API. Carrier street lookups
cap at ~50 results per street and silently drop neighbors. That defect is what put
"missing neighbors" into delivered lists. The denominator always comes from a
government address-point source.

SOURCE PRIORITY
  1. County E911 address points  (best — includes structure type)
  2. State NG911 geocoder
  3. National Address Database (NAD)

RESIDENTIAL CLASSIFICATION
--------------------------
Where the county publishes a structure type, it is authoritative and used directly.
Codes that are not verified are FLAGGED for review — never assumed residential and
never silently dropped.
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

# Imported both ways in this tree: as `engine.address_source` from the CLI, and
# flat by pipeline/run_inman, which put this directory on sys.path first.
try:
    from . import endpoint_guard, fetch_guard
except ImportError:  # pragma: no cover - depends on how the caller imported us
    import endpoint_guard
    import fetch_guard

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# Placeholder city values that are NOT real municipalities. NAD's Inc_Muni uses
# "Not Stated" for any address outside an incorporated place — the single most
# common value in rural counties — and county E911 layers use "Unincorporated".
# THESE MUST NEVER REACH THE CARRIER QUERY. A junk city does not just make an ugly
# deliverable; it silently breaks Kinetic's address match and the whole town comes
# back "no-match" (Toledo IA, 2026-07-27: 883/945 addresses falsely dropped because
# every one was queried as city "Not Stated" — the real city recovered ~40% as
# serviceable). The real city comes from Post_City (the postal city, which is
# populated even when Inc_Muni is not) or, failing that, the market label.
_JUNK_CITY = {"", "NOT STATED", "NOTSTATED", "UNINCORPORATED", "UNKNOWN",
              "NONE", "N/A", "NA", "NULL"}


def _is_junk_city(value) -> bool:
    v = str(value or "").strip().upper().rstrip(">").strip()
    if v in _JUNK_CITY:
        return True
    # Catch misspellings of "unincorporated" seen in real NAD data
    # ("Unicorporated", "Unincorporaed") and any "not stated" variant.
    return v.startswith("UNINCORP") or v.startswith("UNICORP") or "NOT STATED" in v


def clean_city(primary, fallback=None) -> str:
    """Return a real municipality name, never a placeholder. Prefer the primary
    (Inc_Muni); if it is junk, fall back to the postal city; if both are junk,
    return "" so the caller fills it from the market label or a reverse-geocode."""
    if not _is_junk_city(primary):
        return str(primary).strip().title()
    if not _is_junk_city(fallback):
        return str(fallback).strip().title()
    return ""


# Verified residential structure codes (Spartanburg County E911 schema).
RESIDENTIAL_CODES = {
    "SF": "Single Family",
    "MH": "Mobile Home",
    "MF": "Multi-Family",
    "DU": "Duplex",
}
# Verified NON-residential.
NONRESIDENTIAL_CODES = {
    "NR": "Non-Residential",
}
# Everything else is UNVERIFIED -> flagged, never guessed.


class _Response:
    """A urllib response wearing the two attributes fetch_guard reads.

    The guard is written against the requests-style objects the rest of the
    engine uses; this module reaches ArcGIS through urllib, so adapt the
    response here rather than keep a second copy of the check.
    """

    __slots__ = ("status_code", "_body")

    def __init__(self, raw):
        self.status_code = getattr(raw, "status", None)
        self._body = raw.read()

    def json(self):
        return json.loads(self._body)


class ArcGISAddressSource:
    """Generic puller for an ArcGIS FeatureServer/MapServer address-point layer."""

    def __init__(self, url, field_map, page_size=2000, name="ArcGIS"):
        self.url = url.rstrip("/")
        self.fm = field_map
        self.page_size = page_size
        self.name = name
        # ONE CIRCUIT PER HOST, NOT PER LAYER OR PER SOURCE OBJECT.
        #
        # What goes silent is a machine, not a layer: when the county server
        # stops accepting connections every layer on it stops together, and
        # counting them apart would let the same dead host be hammered once per
        # layer. Keying on the host also keeps the counters separate where it
        # matters -- a dead Spartanburg server must never stop the NAD pull,
        # which is a different service entirely. The bare hostname is what the
        # watchdog prints, so it reads as the thing that is actually down.
        self.endpoint = urllib.parse.urlsplit(self.url).netloc or self.url

    def _query(self, params, tries=6, timeout=120, require="features"):
        """One query that either answers or raises. `require` names the key that
        carries the answer: "features" for a record pull, "count" for a count."""
        last = None
        for _ in range(tries):
            # ASK THE CIRCUIT BEFORE OPENING A SOCKET.
            #
            # The sleep below is a per-caller backoff, and a per-caller backoff
            # is exactly what could not stop the 2026-08-27 firewalling: this
            # source is pulled from threads, so twenty callers each waiting a
            # polite two seconds is still ten connects a second at a host that
            # has stopped answering. The guard is the only thing that counts
            # them together. EndpointDown is deliberately NOT caught here: it is
            # a RuntimeError like this method's own "query failed" refusal, so
            # every caller already treats it as "no answer, work unfinished"
            # rather than as an empty result.
            endpoint_guard.before_attempt(self.endpoint)
            try:
                req = urllib.request.Request(
                    f"{self.url}/query?{urllib.parse.urlencode(params)}",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp = _Response(urllib.request.urlopen(req, timeout=timeout, context=_CTX))
            except urllib.error.HTTPError as exc:
                # THE HOST ANSWERED. urllib raises on 4xx/5xx, so a 429 or a 503
                # arrives here as an exception even though the service is up and
                # talking to us. Counting it as silence would trip the circuit on
                # a working host and stop a pull that was merely being throttled.
                endpoint_guard.record_success(self.endpoint)
                last = exc
                time.sleep(2)
                continue
            except Exception as exc:
                # SILENCE. Refused connection, timed-out read, dropped body --
                # nothing came back at all. This is the only thing that counts
                # toward the circuit.
                endpoint_guard.record_failure(self.endpoint)
                last = exc
                time.sleep(2)
                continue
            # The bytes arrived, whatever they say. Streak reset before the body
            # is judged: a refusal envelope is still the host answering.
            endpoint_guard.record_success(self.endpoint)
            try:
                # A REFUSED QUERY IS NOT AN EMPTY ONE, AND ARCGIS REFUSES WITH
                # HTTP 200.
                #
                # The body of a refusal is {"error": {...}} with no features
                # array — checked against the NAD view on 2026-08-27, which
                # answers an invalid field with HTTP 200 carrying code 400. That
                # parsed cleanly, so nothing raised and neither caller below
                # could tell it from a real answer: `.get("features", [])` made
                # it [] and the old `return {}` on exhaustion did the same. In
                # `_tile` that reads as "this tile is empty farmland", and
                # because the tiles are a quadtree, one refusal at depth 1
                # silently deleted a quarter of the county from the pull and the
                # market still totalled up plausibly. The guard raises instead,
                # which makes the sleep below a real retry and stops a pull that
                # never got its answer rather than shrinking it.
                return fetch_guard.ok_json(resp, require=require, where=self.name)
            except Exception as exc:
                last = exc
                time.sleep(2)
        raise RuntimeError(
            f"{self.name}: query failed after {tries} attempts — {last}. "
            f"Refusing to continue: a query that never answered is not an empty "
            f"result, and reading it as one silently drops addresses."
        )

    def count(self, where):
        return self._query({"where": where, "returnCountOnly": "true", "f": "json"},
                           require="count").get("count", 0)

    def fetch(self, where, on_progress=None):
        """Page through every matching record. Verifies the total to catch truncation."""
        expected = self.count(where)
        out, offset = [], 0
        while True:
            fields = ",".join(v for v in self.fm.values() if v)
            data = self._query({
                "where": where, "outFields": fields, "returnGeometry": "true",
                "outSR": "4326", "resultOffset": str(offset),
                "resultRecordCount": str(self.page_size), "f": "json",
            })
            feats = data.get("features", [])
            for f in feats:
                a = f.get("attributes", {}) or {}
                g = f.get("geometry", {}) or {}
                out.append(self._normalize(a, g))
            if on_progress:
                on_progress(len(out), expected)
            if len(feats) < self.page_size:
                break
            offset += self.page_size
        # Truncation guard: a short page mid-stream silently loses addresses.
        if expected and len(out) < expected * 0.99:
            raise RuntimeError(
                f"{self.name}: pulled {len(out)} of {expected} expected — "
                f"source truncated the result. Refusing to continue with a partial "
                f"address list (this would silently drop neighbors)."
            )
        return out

    def _normalize(self, a, g):
        def val(key):
            fld = self.fm.get(key)
            return (a.get(fld) if fld else None)

        struct = str(val("struct_type") or "").strip().upper()
        if struct in RESIDENTIAL_CODES:
            residential, res_note = True, RESIDENTIAL_CODES[struct]
        elif struct in NONRESIDENTIAL_CODES:
            residential, res_note = False, NONRESIDENTIAL_CODES[struct]
        elif struct == "":
            residential, res_note = None, "no structure type published"
        else:
            residential, res_note = None, f"unverified structure code '{struct}'"

        return {
            "address": _build_address(a, self.fm),
            "unit": str(val("unit") or "").strip(),
            # Repair placeholder cities at the source, using the postal city as
            # fallback, so a junk value ("Not Stated"/"Unincorporated") can never
            # reach the carrier query and silently no-match the whole town.
            "city": clean_city(val("city"), val("post_city")),
            "zip": str(val("zip") or "").strip(),
            "lat": g.get("y"),
            "lon": g.get("x"),
            "struct_type": struct,
            "residential": residential,       # True / False / None(=flag)
            "residential_note": res_note,
            "source": self.name,
        }


class NADAddressSource(ArcGISAddressSource):
    """
    USDOT National Address Database — the Esri-hosted national view.

    Used where a state publishes its NG911 address points only through NAD.
    Iowa is the case in hand: the state portal has no address layer at all, and
    Appanoose and Wayne counties publish nothing themselves, so NAD (sourced from
    those counties' own NG911 submissions, `AddAuth` = the county sheriff) is the
    authoritative route to them.

    TWO TRAPS, both verified the hard way:

    1. `resultRecordCount` and `orderByFields` return HTTP 400 on this view, and an
       attribute-only `where` against ~98 million rows times out. The only method
       that works is a bounding box split recursively until every tile is under the
       2,000-record cap. That is what `fetch_bbox` does — the tile split IS the
       paging.

    2. `Zip_Code` is NULL for every Appanoose County record (their NG911 submission
       omitted it), so `Zip_Code='52544'` returns zero — not because Centerville is
       missing, but because the field is empty. Filter those markets on
       `County` + `Inc_Muni` instead. Never assume an empty result means no data.
    """

    MAX_TILE = 1900          # stay under the service's 2,000 cap

    def _tile(self, where, bbox, out, depth=0):
        params = {"where": where, "outFields": "*", "returnGeometry": "true",
                  "outSR": "4326", "f": "json", "inSR": "4326",
                  "geometryType": "esriGeometryEnvelope",
                  "spatialRel": "esriSpatialRelIntersects",
                  "geometry": "{},{},{},{}".format(*bbox)}
        # `_query` raises on a refusal now instead of returning {}, so an empty
        # `feats` here means this tile really is empty ground — not that NAD said
        # no and this quadrant of the county quietly left the pull.
        data = self._query(params, timeout=180)
        feats = data.get("features", [])

        # A full tile may be truncated — split it rather than accept a short read.
        if len(feats) >= self.MAX_TILE and depth < 12:
            x0, y0, x1, y1 = bbox
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            for sub in ((x0, y0, mx, my), (mx, y0, x1, my),
                        (x0, my, mx, y1), (mx, my, x1, y1)):
                self._tile(where, sub, out, depth + 1)
            return

        for f in feats:
            a = f.get("attributes", {}) or {}
            g = f.get("geometry", {}) or {}
            key = (a.get("UUID") or
                   (a.get("Add_Number"), a.get("St_Name"), g.get("x"), g.get("y")))
            out[key] = self._normalize(a, g)

    def fetch_bbox(self, where, bbox, on_progress=None):
        # The same total check `fetch` runs, which this path has never had. The
        # tile split is the paging here, and a quadtree has no short-page signal
        # to compare against, so without an independent total any tile that came
        # back light — the depth cap reached, a dedupe collision, a service
        # hiccup that outlasted its retries — leaves a hole in the market that
        # looks exactly like a smaller town. The count must carry the bbox: an
        # attribute-only count against this ~98-million-row view is one of the
        # two traps in the class docstring and comes back refused.
        # The count is the independent check, but it is not always obtainable:
        # on a wide bbox this view answers the count with a refusal after about
        # a minute. That must not fail the market — sourcing can still proceed —
        # but it also must not be mistaken for a verified-complete pull. So a
        # refused count is recorded as "no independent total", and the coverage
        # assertion below simply does not run rather than passing vacuously.
        try:
            expected = self._query({
                "where": where, "returnCountOnly": "true", "f": "json", "inSR": "4326",
                "geometryType": "esriGeometryEnvelope",
                "spatialRel": "esriSpatialRelIntersects",
                "geometry": "{},{},{},{}".format(*bbox),
            }, require="count").get("count", 0)
        except Exception as exc:
            expected = 0
            self.count_unavailable = True
            print(f"    {self.name}: no independent total for this bbox "
                  f"({type(exc).__name__}) — tiling will run, but its "
                  f"completeness is NOT verified against a count", flush=True)

        out = {}
        self._tile(where, bbox, out)
        if expected and len(out) < expected * 0.99:
            raise RuntimeError(
                f"{self.name}: tiled {len(out)} of {expected} expected — the "
                f"split lost records. Refusing to continue with a partial address "
                f"list (a tile that went missing is a whole quadrant of the "
                f"market, not empty country)."
            )
        if on_progress:
            on_progress(len(out), len(out))
        return list(out.values())


SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
    "COURT": "CT", "CIRCLE": "CIR", "BOULEVARD": "BLVD", "PLACE": "PL",
    "TERRACE": "TER", "TRAIL": "TRL", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "POINT": "PT", "COVE": "CV", "BEND": "BND", "ALLEY": "ALY", "EXTENSION": "EXT",
    "RUN": "RUN", "WAY": "WAY", "RIDGE": "RDG", "LOOP": "LOOP", "PATH": "PATH",
}
DIRECTION = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}


def _build_address(a, fm):
    """Assemble a clean, abbreviated, uppercase address the carrier will match."""
    def f(key):
        fld = fm.get(key)
        v = a.get(fld) if fld else None
        return "" if v is None else str(v).strip()

    num = f("house_number")
    suffix_num = f("house_suffix")
    if suffix_num:
        num = f"{num} {suffix_num}"
    parts = [
        num,
        DIRECTION.get(f("pre_dir").upper(), f("pre_dir").upper()),
        f("street_name").upper(),
        SUFFIX.get(f("street_type").upper(), f("street_type").upper()),
        DIRECTION.get(f("post_dir").upper(), f("post_dir").upper()),
    ]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------- market registry
MARKETS = {
    "inman_sc": {
        "label": "Inman SC",
        "state": "SC",
        "area_note": "ZIP 29349 · Spartanburg County",
        "where": "Post_Code='29349'",
        "source": lambda: ArcGISAddressSource(
            "https://maps.spartanburgcounty.org/server/rest/services/GIS/Address_Points/MapServer/0",
            field_map={
                "house_number": "Add_Number", "house_suffix": "Add_NumSuf",
                "pre_dir": "St_PreDir", "street_name": "St_Name",
                "street_type": "St_PosType", "post_dir": "St_PosDir",
                "unit": "Unit", "city": "Inc_Muni", "zip": "Post_Code",
                "struct_type": "StructType",
            },
            name="Spartanburg County E911",
        ),
    },
}

# ---------------------------------------------------------------- Iowa markets
NAD_URL = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
           "Address_Points_from_National_Address_Database_view/FeatureServer/0")
NAD_FIELDS = {
    "house_number": "Add_Number", "house_suffix": "AddNum_Suf",
    "pre_dir": "St_PreDir", "street_name": "St_Name",
    "street_type": "St_PosTyp", "post_dir": "St_PosDir",
    "unit": "Unit", "city": "Inc_Muni", "zip": "Zip_Code",
    # Post_City (postal city) is the fallback when Inc_Muni is "Not Stated" — it is
    # populated for essentially every address and carries the real town name.
    "post_city": "Post_City",
    "struct_type": None,       # Iowa populates no structure type — see note below
}


def _nad():
    return NADAddressSource(NAD_URL, field_map=NAD_FIELDS,
                            name="USDOT National Address Database (Iowa NG911)")


# Appanoose and Wayne counties, generous bbox — the quadtree narrows it.
_IOWA_BBOX = (-93.30, 40.55, -92.65, 41.05)

MARKETS.update({
    "centerville_ia": {
        "label": "Centerville IA", "state": "IA",
        "area_note": "Centerville · Appanoose County",
        # Appanoose's Zip_Code column is empty — filtering on ZIP returns nothing.
        "where": "County='Appanoose' AND Inc_Muni='Centerville'",
        "bbox": _IOWA_BBOX, "default_zip": "52544",
        "residential_source": None,
        "source": _nad,
    },
    "moravia_ia": {
        "label": "Moravia IA", "state": "IA",
        "area_note": "Moravia · Appanoose County",
        "where": "County='Appanoose' AND Inc_Muni='Moravia'",
        "bbox": _IOWA_BBOX, "default_zip": "52571",
        "residential_source": None,
        "source": _nad,
    },
    "seymour_ia": {
        "label": "Seymour IA", "state": "IA",
        "area_note": "ZIP 52590 · Wayne County",
        "where": "Zip_Code='52590'",       # Wayne populates ZIP normally
        "bbox": _IOWA_BBOX, "default_zip": "52590",
        "residential_source": "wayne_parcels",
        "source": _nad,
    },
})


def load_market(key, on_progress=None):
    """Pull every address for a registered market, with a residential summary."""
    m = MARKETS[key]
    src = m["source"]()
    if m.get("bbox"):
        rows = src.fetch_bbox(m["where"], m["bbox"], on_progress=on_progress)
    else:
        rows = src.fetch(m["where"], on_progress=on_progress)
    for r in rows:
        r["state"] = m["state"]
        # Last-resort city repair: if both Inc_Muni and Post_City were junk (city
        # came back ""), fall back to the market's own town name. clean_city has
        # already handled the common Inc_Muni="Not Stated" case via Post_City.
        if _is_junk_city(r["city"]):
            r["city"] = m["label"].rsplit(" ", 1)[0]
        if not r["zip"] and m.get("default_zip"):
            # Appanoose omits ZIP entirely; the market defines it instead of
            # leaving a blank the carrier query would choke on.
            r["zip"] = m["default_zip"]
    return rows, summarize(rows)


def summarize(rows):
    return {
        "total": len(rows),
        "residential": sum(1 for r in rows if r["residential"] is True),
        "non_residential": sum(1 for r in rows if r["residential"] is False),
        "flagged_unknown": sum(1 for r in rows if r["residential"] is None),
        "struct_types": dict(Counter(r["struct_type"] for r in rows)),
        "missing_coords": sum(1 for r in rows if not r["lat"] or not r["lon"]),
    }
