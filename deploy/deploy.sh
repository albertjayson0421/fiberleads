#!/bin/bash
# Usage: deploy.sh boxes.txt   (boxes.txt = one "user@ip" per line; free GCP/Oracle instances)
# Ships engine + ledger to each box, launches node_sweep on its disjoint partition.
# No root needed on the boxes (everything under $HOME/fl).
set -u
BOXES=${1:?boxes.txt}; N=$(grep -c . "$BOXES")
ENG="/Volumes/T7/Masani Files/FiberLeads/04_Data/reruns/SC-Inman-2026-09-01"
DB="/Users/masani/tx_ledger_recovered.db"; KEY=${FLEET_KEY:-~/.ssh/fleet_key}
i=0
while read -r BOX; do
  [ -z "$BOX" ] && continue
  echo "[$i/$N] provisioning $BOX ..."
  ssh -i "$KEY" -o StrictHostKeyChecking=no "$BOX" "mkdir -p \$HOME/fl/engine \$HOME/fl/root && pip3 -q install curl_cffi 2>/dev/null || pip3 -q install --user curl_cffi 2>/dev/null"
  rsync -az -e "ssh -i $KEY" "$ENG"/ "$BOX:fl/engine/"
  rsync -az -e "ssh -i $KEY" "$DB" "$BOX:fl/run.db"
  rsync -az -e "ssh -i $KEY" /Users/masani/FLEET_5K/node_sweep.sh "$BOX:fl/"
  ssh -i "$KEY" "$BOX" "nohup bash \$HOME/fl/node_sweep.sh $i $N 128 2400 >\$HOME/fl/n$i.log 2>&1 &"
  i=$((i+1))
done < "$BOXES"
echo "launched $i nodes. Watch a node: ssh <box> 'grep settled= fl/n<idx>.log | tail'"
echo "Collect: bash collect.sh $BOXES"
