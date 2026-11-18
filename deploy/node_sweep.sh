#!/bin/bash
# Runs on ONE cloud box. Args: NODE_IDX NODE_COUNT WORKERS SECONDS
# Sweeps only rows where (rowid % NODE_COUNT)==NODE_IDX -> disjoint, no double-work.
# Global DataImpulse rotation; results written to this box's local ledger, collected later.
set -u
IDX=${1:?node idx}; N=${2:?node count}; W=${3:-128}; SECS=${4:-2400}
ENG="$HOME/fl/engine"; DB="$HOME/fl/run.db"; ROOT="$HOME/fl/root/"
mkdir -p "$ROOT"
export FL_DI_GEO="" FL_SESSION_SEARCHES=14
cd "$ENG" || exit 1
# mark non-owned rows completed so this box only works its disjoint slice
python3 - "$DB" "$IDX" "$N" <<'PY'
import sqlite3,sys
db,idx,n=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
c=sqlite3.connect(db,timeout=120); c.execute("PRAGMA journal_mode=WAL")
c.execute("UPDATE assignments SET completed=1 WHERE (rowid % ?)<>?",(n,idx)); c.commit(); c.close()
print(f"node {idx}/{n}: owns its 1/{n} slice")
PY
END=$(( $(date +%s) + SECS ))
while [ "$(date +%s)" -lt "$END" ]; do
  python3 mp_launch.py --db "$DB" --run-id tx --root "$ROOT" \
    --procs 4 --workers $((W/4)) --seconds 150 --ramp 15 --mint-rate 60 --label "n$IDX" || true
done
