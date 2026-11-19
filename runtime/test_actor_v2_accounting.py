import os
import sys
import json
import asyncio
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "engine"), HERE]
os.environ.setdefault("FL_CONFIG", os.path.join(HERE, "config"))

import fleet_actor_v2 as F

P = FAIL = 0

def check(name, condition):
    global P, FAIL
    if condition:
        P += 1
        print(" PASS", name)
    else:
        FAIL += 1
        print(" FAIL", name)

class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


async def test_cancelled_actor_recovers_lease():
    F.M.reset()
    retry_file = tempfile.NamedTemporaryFile(
        mode="w+", encoding="utf-8", delete=False
    )
    ledger_file = tempfile.NamedTemporaryFile(
        mode="w+", encoding="utf-8", delete=False
    )

    retry_path = retry_file.name
    ledger_path = ledger_file.name

    F.M.retry_fp = retry_file
    F.M.ledger_fp = ledger_file

    actor = F.Actor(1, 0)
    actor.alive = True
    actor.s = FakeSession()
    F.M.active = 1

    queue = asyncio.Queue()
    job = {"ck": "CANCEL-1"}
    queue.put_nowait(job)

    started = asyncio.Event()
    original_run_one = F.run_one

    async def fake_run_one(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    F.run_one = fake_run_one

    try:
        task = asyncio.create_task(
            F.actor_task(
                actor, queue, None,
                asyncio.get_running_loop().time() + 60,
                10, 10, 10, None,
                asyncio.get_running_loop().time(), 0
            )
        )

        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        retry_file.flush()
        retry_file.seek(0)
        rows = [json.loads(line) for line in retry_file if line.strip()]

        check(
            "cancelled leased job gets one durable retry",
            len(rows) == 1
            and rows[0]["ck"] == "CANCEL-1"
            and rows[0]["phase"] == "cancelled"
        )
        check(
            "retry accounting contains the job exactly once",
            F.M.retryable == 1 and F.M.retry_keys == {"CANCEL-1"}
        )
        check(
            "actor active count returns to zero",
            F.M.active == 0
        )
        check(
            "actor session closes on cancellation",
            actor.s.closed
        )
        check(
            "actor lease is cleared",
            actor.current_job is None
        )

    finally:
        F.run_one = original_run_one
        if not retry_file.closed:
            retry_file.close()
        if not ledger_file.closed:
            ledger_file.close()
        for path in (retry_path, ledger_path):
            try:
                os.unlink(path)
            except OSError:
                pass

if os.name == "nt":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

asyncio.run(test_cancelled_actor_recovers_lease())

print("\nRESULT: %d passed, %d failed" % (P, FAIL))
sys.exit(1 if FAIL else 0)