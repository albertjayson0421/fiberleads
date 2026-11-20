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

def test_retry_superseded_by_final():
    F.M.reset()
    actor = F.Actor(1, 0)
    actor.plan = "TEST_PLAN"

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.json1")
        retry_path = os.path.join(tmp, "retry.json1")

        try:
            with open(ledger_path, "w", encoding="utf-8") as ledger, \
                 open(retry_path, "w", encoding="utf-8") as retry:

                F.M.ledger_fp = ledger
                F.M.retry_fp = retry

                job = {"ck": "FINAL-1"}
                F.record_retryable(job, "search", actor, "test_timeout")

                F.record_final(
                    "FINAL-1",
                    "SERVICEABLE_FIBER",
                    actor,
                    {"t": 1.0, "evidence_sha256": "test-hash"}
                )

                F.record_final(
                    "FINAL-1",
                    "SERVICEABLE_FIBER",
                    actor,
                    {"t": 2.0, "evidence_sha256": "test-hash"}
                )
        finally:
            F.M.ledger_fp = None
            F.M.retry_fp = None
        with open(ledger_path, encoding="utf-8") as fp:
            rows = [
                json.loads(line)
                for line in fp
                if line.strip()
            ]
        recon = F.cold_reopen(ledger_path)

        check(
            "terminal decision supersedes retry exactly once",
            F.M.retryable == 0
            and not F.M.retry_keys
            and F.M.seen == {"FINAL-1": "SERVICEABLE_FIBER"}
        )

        check(
            "duplicate terminal does not write another ledger row",
            len(rows) == 1 and F.M.dups == 1
        )
        check(
            "terminal cold-reopen matches memory",
            recon["matches_memory"]
        )

def test_retry_superseded_by_rejection():
    F.M.reset()
    actor = F.Actor(2, 0)
    actor.plan = "TEST_PLAN"

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger_json1")
        retry_path = os.path.join(tmp, "retry_json1")

        try:
            with open(ledger_path, "w", encoding="utf-8") as ledger, \
                 open(retry_path, "w", encoding="utf-8") as retry:

                F.M.ledger_fp = ledger
                F.M.retry_fp = retry

                job = {"ck": "REJECT-1"}
                F.record_retryable(job, "search", actor, "test_timeout")
                F.record_rejected(job, actor, "non_definitive_answer", 1.5)

                F.record_rejected(job, actor, "duplicate", 2.0)
        finally:
            F.M.ledger_fp = None
            F.M.retry_fp = None
        with open(ledger_path, encoding="utf-8") as fp:
            rows = [
                json.loads(line)
                for line in fp
                if line.strip()
            ]
        recon = F.cold_reopen(ledger_path)

        check(
            "completed rejection supersedes retry",
            F.M.retryable == 0
            and not F.M.retry_keys
            and F.M.rejected == 1
        )
        check(
            "duplicate rejection does not increment durable count",
            len(rows) == 1
            and rows[0]["class"] == "COMPLETE_REJECTED"
            and F.M.by_class["COMPLETE_REJECTED"] == 1
        )
        check(
            "rejection cold-reopen matches memory",
            recon["matches_memory"]
        )

def test_cold_reopen_detects_corruption():
    F.M.reset()

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.json1")
        row = {
            "ck": "DUP-1",
            "class": "SERVICEABLE_FIBER",
            "counted": True
        }

        with open(ledger_path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(row) + "\n")
            fp.write(json.dumps(row) + "\n")

        F.M.seen = {"DUP-1": "SERVICEABLE_FIBER"}
        F.M.counted = 1

        recon = F.cold_reopen(ledger_path)

        check(
            "cold-reopen detects duplicate ledger keys",
            recon["reopened_file_dups"] == 1
            and not recon["matches_memory"]
        )

        with open(ledger_path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(row) + "\n")

        F.M.counted = 0
        recon = F.cold_reopen(ledger_path)

        check(
            "cold-reopen detects counter mismatch",
            not recon["matches_memory"]
        )


if os.name == "nt":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

asyncio.run(test_cancelled_actor_recovers_lease())

test_retry_superseded_by_final()
test_retry_superseded_by_rejection()
test_cold_reopen_detects_corruption()

print("\nRESULT: %d passed, %d failed" % (P, FAIL))
sys.exit(1 if FAIL else 0)