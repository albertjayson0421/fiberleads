"""Durable SQLite result store — batched writes + hard, restart-safe deduplication.

Replaces the JSONL append writer for the pipeline's durable results (repair item #7/#9):
- ONE terminal decision per premise key, enforced by a PRIMARY KEY(ck) at the DB level, so a
  duplicate durable write is rejected by the engine, not by an in-memory set that dies on restart.
- Batched transactions (no per-row fsync): buffer rows, commit in one executemany per flush.
- INCOMPLETE_RETRYABLE is NOT a durable decision; it is recorded in a separate table and can never
  collide with or overwrite a terminal row.
- Restart-safe: done_keys() reads committed terminal keys back after a cold reopen; resume skips them.
- Reconciliation: counts() returns per-class durable totals + unique retryable, for conservation
  against the address universe (durable_unique + retryable_only + unattempted == universe).

WAL + synchronous=NORMAL: durable across process crash, fast enough for the batched writer.
"""
import sqlite3, json, time

TERMINAL = {"SERVICEABLE_FIBER", "DEFINITIVE_NONFIBER", "EXCLUDED_EXISTING", "COMPLETE_REJECTED",
            "DEFINITIVE_OUT_OF_TERRITORY", "DEFINITIVE_NO_ORDERABLE_FIBER", "EXCLUDED_PENDING_ORDER",
            "COMPLETE_NOT_IN_KINETIC"}


class DurableStore:
    def __init__(self, path):
        self.cx = sqlite3.connect(path, timeout=30)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=NORMAL")
        self.cx.execute("CREATE TABLE IF NOT EXISTS durable("
                        "ck TEXT PRIMARY KEY, class TEXT NOT NULL, t REAL, ev TEXT, updated REAL)")
        self.cx.execute("CREATE TABLE IF NOT EXISTS retryable("
                        "ck TEXT, phase TEXT, t REAL, updated REAL)")
        self.cx.execute("CREATE INDEX IF NOT EXISTS ix_dur_class ON durable(class)")
        self.cx.commit()
        self._buf = []
        self.inserted = 0
        self.dup_rejected = 0
        self.retry_written = 0

    def done_keys(self):
        """Committed terminal keys — restart-safe resume set."""
        return set(r[0] for r in self.cx.execute("SELECT ck FROM durable"))

    def add(self, row):
        """Buffer a row (durable or retryable). Flush batches it."""
        self._buf.append(row)

    def flush(self):
        if not self._buf:
            return (0, 0)
        dur = [r for r in self._buf if r.get("class") in TERMINAL]
        ret = [r for r in self._buf if r.get("class") == "INCOMPLETE_RETRYABLE"]
        ins = dup = 0
        cur = self.cx.cursor()
        now = time.time()
        for r in dur:
            try:
                cur.execute("INSERT INTO durable(ck,class,t,ev,updated) VALUES(?,?,?,?,?)",
                            (r["ck"], r["class"], r.get("t"),
                             json.dumps({k: v for k, v in r.items() if k not in ("ck", "class", "t")},
                                        separators=(",", ":")) if len(r) > 3 else None, now))
                ins += 1
            except sqlite3.IntegrityError:
                dup += 1  # PRIMARY KEY(ck) -> hard zero-duplicate guarantee for durable decisions
        if ret:
            cur.executemany("INSERT INTO retryable(ck,phase,t,updated) VALUES(?,?,?,?)",
                            [(r.get("ck"), r.get("phase"), r.get("t"), now) for r in ret])
        self.cx.commit()
        self.inserted += ins
        self.dup_rejected += dup
        self.retry_written += len(ret)
        self._buf = []
        return (ins, dup)

    def counts(self):
        by_class = dict(self.cx.execute("SELECT class,COUNT(*) FROM durable GROUP BY class").fetchall())
        durable_unique = sum(by_class.values())
        retry_rows = self.cx.execute("SELECT COUNT(*) FROM retryable").fetchone()[0]
        # unique retryable keys that never reached a terminal decision (must be re-queued)
        retry_only = self.cx.execute(
            "SELECT COUNT(DISTINCT ck) FROM retryable WHERE ck NOT IN (SELECT ck FROM durable)").fetchone()[0]
        return {"durable_unique": durable_unique, "by_class": by_class,
                "retry_rows": retry_rows, "retry_only_unique": retry_only,
                "dup_rejected": self.dup_rejected}

    def close(self):
        self.flush()
        self.cx.close()
