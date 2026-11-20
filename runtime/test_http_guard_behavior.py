
import asyncio
from test_http_guard import LocalOnlySession


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeSession:
    def __init__(self, status_code=200):
        self.calls = []
        self.closed = False
        self.close_calls = 0
        self.status_code = status_code

    async def post(self, url, *args, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.status_code)

    async def close(self):
        self.close_calls += 1
        self.closed = True


async def main():
    passed = 0
    failures = 0

    def check(name, condition):
        nonlocal passed, failures
        if condition:
            passed += 1
            print("PASS", name)
        else:
            failures += 1
            print("FAIL", name)

    fake = FakeSession()
    session = LocalOnlySession(fake, "http://127.0.0.1:8765")

    # Allowed localhost request.
    try:
        await session.post("http://127.0.0.1:8765/search")
        allowed = True
    except Exception:
        allowed = False

    check("localhost allowed", allowed)

    # Requests to other origins must never reach the underlying session.
    blocked_urls = [
        ("external host blocked", "https://example.com/search"),
        ("wrong port blocked", "http://127.0.0.1:8766/search"),
        ("credentials blocked",
         "http://user:pass@127.0.0.1:8765/search"),
    ]

    for name, url in blocked_urls:
        before = len(fake.calls)
        try:
            await session.post(url)
            blocked = False
        except (RuntimeError, ValueError):
            blocked = True

        check(name, blocked and len(fake.calls) == before)

    check(
        "underlying request disables redirects",
        len(fake.calls) == 1
        and fake.calls[0][1].get("allow_redirects") is False
    )

    # Redirect responses must be rejected.
    redirect_fake = FakeSession(status_code=302)
    redirect_session = LocalOnlySession(
        redirect_fake, "http://127.0.0.1:8765"
    )

    try:
        await redirect_session.post("http://127.0.0.1:8765/redirect")
        redirect_blocked = False
    except RuntimeError:
        redirect_blocked = True

    check(
        "redirect response rejected",
        redirect_blocked
        and len(redirect_fake.calls) == 1
        and redirect_fake.calls[0][1].get("allow_redirects") is False
    )

    # Invalid base origins must be rejected at construction.
    invalid_origins = [
        "https://127.0.0.1:8765",
        "http://example.com:8765",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:8765",
    ]

    for origin in invalid_origins:
        try:
            LocalOnlySession(FakeSession(), origin)
            rejected = False
        except ValueError:
            rejected = True

        check("invalid base origin rejected: " + origin, rejected)

    # Closing repeatedly must close the underlying session only once.
    close_calls = []

    close_fake = FakeSession()
    close_session = LocalOnlySession(
        close_fake,
        "http://127.0.0.1:8765",
        on_close=lambda: close_calls.append("closed")
    )

    await close_session.close()
    await close_session.close()

    check(
        "repeated close is idempotent",
        close_fake.closed
        and close_fake.close_calls == 1
        and close_calls == ["closed"]
    )

    await session.close()
    await redirect_session.close()

    print(
        "RESULT: %d passed, %d failed" % (passed, failures)
    )
    return failures


if __name__ == "__main__":
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    raise SystemExit(asyncio.run(main()))
