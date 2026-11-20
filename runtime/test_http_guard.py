
from urllib.parse import urlsplit


def _get_origin(url):
    """Parse and validate an HTTP URL origin."""
    try:
        parsed = urlsplit(url)
        return (
            parsed,
            (parsed.scheme, parsed.hostname, parsed.port),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid HTTP URL") from exc


class LocalOnlySession:
    """Test-only HTTP wrapper: permits one localhost origin only."""

    def __init__(self, session, base_url, on_close=None):
        base, origin = _get_origin(base_url)

        if (
            origin[0] != "http"
            or origin[1] not in {"127.0.0.1", "localhost"}
            or origin[2] is None
            or base.username is not None
            or base.password is not None
        ):
            raise ValueError(
                "Offline tests require an explicit localhost HTTP origin"
            )

        self._session = session
        self._origin = origin
        self._on_close = on_close
        self._closed = False

    async def post(self, url, *args, **kwargs):
        target, origin = _get_origin(url)

        if origin != self._origin:
            raise RuntimeError(
                "Offline network guard rejected destination: " + str(origin)
            )

        if target.username is not None or target.password is not None:
            raise RuntimeError("Credentials in request URLs are forbidden")

        kwargs["allow_redirects"] = False
        response = await self._session.post(url, *args, **kwargs)

        if 300 <= response.status_code < 400:
            raise RuntimeError(
                "Offline network guard rejected HTTP redirects"
            )

        return response

    async def close(self):
        if self._closed:
            return

        self._closed = True
        try:
            await self._session.close()
        finally:
            if self._on_close:
                self._on_close()
