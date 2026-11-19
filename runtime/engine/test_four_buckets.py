
"""Offline tests for classifier four-bucket mapping and SQL agreement."""
import os
import sys
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import classifier as CL

P = F = 0


def check(name, condition):
    global P, F
    if condition:
        P += 1
        print("  PASS", name)
    else:
        F += 1
        print("  FAIL", name)


print("[four-bucket mapping: every declared pair]")
for (status, sub_status), expected in CL.FOUR_BUCKETS.items():
    actual = CL.four_buckets(status, sub_status)
    check(
        "%s / %s -> %s, closes=%s"
        % (status, sub_status, expected[0], expected[1]),
        actual == expected,
    )
    check(
        "closes_address agrees: %s / %s" % (status, sub_status),
        CL.closes_address(status, sub_status) is expected[1],
    )

print("[HTTP sub-status normalization]")
for status in (CL.UNREAD, CL.FAILED_RETRYABLE):
    for http_sub in ("http_401", "http_403", "http_429", "http_503"):
        check(
            "%s / %s uses its declared bucket" % (status, http_sub),
            CL.four_buckets(status, http_sub) == CL.FOUR_BUCKETS[(status, "")],
        )
        check(
            "%s / %s remains open" % (status, http_sub),
            CL.closes_address(status, http_sub) is False,
        )

print("[unknown dispositions fail closed]")
check(
    "unknown status does not close",
    CL.closes_address("UNRECOGNIZED_STATUS", "") is False,
)
check(
    "unknown unresolved sub-status does not close",
    CL.closes_address(CL.UNRESOLVED, "UNRECOGNIZED_SUB_STATUS") is False,
)

raised = False
try:
    CL.four_buckets("UNRECOGNIZED_STATUS", "")
except KeyError:
    raised = True
check("four_buckets raises for unknown pair", raised)

print("[CLOSED_SQL agrees with Python mapping]")
db = sqlite3.connect(":memory:")
db.execute("""
    CREATE TABLE dispositions (
        status TEXT NOT NULL,
        sub_status TEXT
    )
""")

for (status, sub_status), expected in CL.FOUR_BUCKETS.items():
    db.execute(
        "INSERT INTO dispositions(status, sub_status) VALUES (?, ?)",
        (status, sub_status),
    )

rows = db.execute(
    "SELECT status, sub_status, %s AS sql_closed FROM dispositions"
    % CL.CLOSED_SQL
).fetchall()

for status, sub_status, sql_closed in rows:
    expected = CL.closes_address(status, sub_status)
    check(
        "SQL agrees: %s / %s" % (status, sub_status),
        bool(sql_closed) is expected,
    )

db.close()

print("\nRESULT: %d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
