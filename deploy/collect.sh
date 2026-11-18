#!/bin/bash
# Usage: collect.sh boxes.txt  -> merges every box's terminal results into the master answer file.
# Idempotent (INSERT OR IGNORE by canonical_key) -> zero duplicate finals.
set -u
BOXES=${1:?boxes.txt}; KEY=${FLEET_KEY:-~/.ssh/fleet_key}
MASTER="/Users/masani/tx_ledger_recovered.db"; i=0
while read -r BOX; do
  [ -z "$BOX" ] && continue
  scp -i "$KEY" "$BOX:fl/run.db" "/tmp/node_$i.db" 2>/dev/null || { i=$((i+1)); continue; }
  sqlite3 "$MASTER" "ATTACH '/tmp/node_$i.db' AS n; \
    INSERT OR IGNORE INTO current_disposition SELECT * FROM n.current_disposition \
      WHERE status IN ('CURRENT_FIBER_PLAN_POPULATING','CURRENT_NOT_FOUND','CURRENT_NONFIBER','CURRENT_UNRESOLVED'); \
    INSERT OR IGNORE INTO evidence SELECT * FROM n.evidence; DETACH n;" 2>/dev/null
  i=$((i+1))
done < "$BOXES"
echo "merged $i nodes."
echo "fiber leads: $(sqlite3 "$MASTER" "SELECT COUNT(*) FROM current_disposition WHERE status='CURRENT_FIBER_PLAN_POPULATING';")"
echo "definitive answers: $(sqlite3 "$MASTER" "SELECT COUNT(*) FROM current_disposition WHERE status IN ('CURRENT_FIBER_PLAN_POPULATING','CURRENT_NOT_FOUND','CURRENT_NONFIBER');")"
