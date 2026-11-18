"""One rule: a failed request must never be readable as an answer.

This module exists because the same defect was found eighteen times across
seven engine modules, and once in production on 2026-08-27, when 1,678 of 1,767
imagery sheets were written blank and then read back as "36,042 pins have no
imagery" -- a refusal wearing the costume of a finding.

The trap is specific and it is not exotic.  ArcGIS services answer a refused
query with **HTTP 200** and a body like::

    {"error": {"code": 400, "message": "Cannot perform query."}}

That parses without raising.  ``d.get("features", [])`` then yields ``[]``, and
every caller downstream reads the empty list as "I looked, and there is nothing
there."  No exception fires, so retry loops never retry, sentinels are never
reached, and the reconciliation guards that exist precisely to catch an outage
balance perfectly and stay silent.

So a response is only an answer when it carries the key that holds the answer.
Anything else raises, and the caller must decide -- retry, hold the row, fail
the batch -- but it can no longer mistake silence for a negative.
"""

from __future__ import annotations

import json
from typing import Any


class FetchRefused(RuntimeError):
    """The service did not answer. Distinct from answering 'nothing here'."""


class UrllibResponse:
    """A ``urllib`` response wearing the two attributes the guards read.

    The guards below are written against the requests-style objects most of the
    engine uses -- ``.status_code`` and ``.json()``.  ``urllib.request.urlopen``
    hands back neither: it has ``.status`` (or ``.getcode()``) and ``.read()``.

    That mismatch is the whole reason the urllib callers grew their own
    ``json.loads(r.read())`` instead of routing through here, and a private
    parse in each caller is the duplicated rule this module exists to replace --
    the copy nobody updates when a new dialect of refusal turns up.  So adapt
    the response once, here, and let every urllib caller reach the same
    ``ok_json`` / ``features`` / ``rows`` with the same raise-on-refusal
    contract.

    The body is read eagerly because a urllib response is a single-use stream
    opened inside a ``with`` block: by the time a caller catches the refusal and
    wants to report it, the socket it would have to re-read is closed.
    """

    __slots__ = ("status_code", "_body")

    def __init__(self, raw: Any):
        status = getattr(raw, "status", None)
        if status is None:                     # addinfourl and older shims
            getcode = getattr(raw, "getcode", None)
            status = getcode() if callable(getcode) else None
        self.status_code = status
        self._body = raw.read()

    def json(self) -> Any:
        return json.loads(self._body)


def ok_json(resp: Any, require: str = "features", where: str = "") -> dict:
    """Return the parsed body, or raise FetchRefused.

    ``require`` names the key that carries the answer.  A body without it did
    not answer, no matter what status code rode along with it.  Pass
    ``require=""`` to check only that the body is a well-formed, error-free
    object.
    """
    tag = f" [{where}]" if where else ""

    status = getattr(resp, "status_code", None)
    if status is not None and status != 200:
        raise FetchRefused(f"HTTP {status}{tag}")

    try:
        body = resp.json()
    except Exception as exc:                       # not JSON at all
        raise FetchRefused(f"unparseable body{tag}: {type(exc).__name__}") from exc

    if not isinstance(body, dict):
        raise FetchRefused(f"body is {type(body).__name__}, not an object{tag}")

    _raise_if_error(body, tag)

    if require and require not in body:
        raise FetchRefused(f"no {require!r} in response{tag} -- keys={sorted(body)[:6]}")

    return body


def _raise_if_error(body: dict, tag: str = "") -> None:
    """Detect an error envelope, whichever dialect the service speaks.

    ArcGIS nests it under ``error``.  Socrata (the FCC Form 477 host) puts its
    text at top level under ``message`` with no ``error`` key at all, so a
    check for ``error`` alone sails straight past it.
    """
    if "error" in body:
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else err
        code = err.get("code") if isinstance(err, dict) else "?"
        raise FetchRefused(f"service error {code}{tag}: {msg}")

    # Socrata dialect: a bare {"message": ..., "errorCode": ...} envelope.
    if "message" in body and not any(
            k in body for k in ("features", "results", "rows", "data")):
        raise FetchRefused(f"service error{tag}: {body.get('message')}")


def rows(resp: Any, where: str = "") -> list:
    """A list-returning endpoint's rows, or raise FetchRefused.

    Socrata answers a successful query with a bare JSON **array**, so routing
    it through ok_json would reject every good response as "body is list, not
    an object".  A genuinely empty array is a real answer and is returned as
    ``[]`` -- which matters, because the caller pages until a short page and
    must be able to tell an empty final page from a refused one.
    """
    tag = f" [{where}]" if where else ""

    status = getattr(resp, "status_code", None)
    if status is not None and status != 200:
        raise FetchRefused(f"HTTP {status}{tag}")

    try:
        body = resp.json()
    except Exception as exc:
        raise FetchRefused(f"unparseable body{tag}: {type(exc).__name__}") from exc

    if isinstance(body, dict):
        _raise_if_error(body, tag)
        raise FetchRefused(f"expected a list, got an object{tag} -- keys={sorted(body)[:6]}")

    if not isinstance(body, list):
        raise FetchRefused(f"body is {type(body).__name__}, not a list{tag}")

    return body


def features(resp: Any, where: str = "") -> list:
    """The feature list from a response that genuinely answered.

    An empty list returned from here is real: the service looked and found
    nothing.  That is the whole point of routing through ok_json first.
    """
    return ok_json(resp, require="features", where=where).get("features") or []
