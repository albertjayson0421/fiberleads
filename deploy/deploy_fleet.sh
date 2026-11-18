#!/bin/bash
# Deploy the node bundle + each box's partition, then launch the sweeper. Robust SSH (array args).
cd /Users/masani/FLEET_5K || exit 1
KEY="$HOME/.ssh/fleet_key"
OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=20)
i=0
while read -r BOX; do
  [ -z "$BOX" ] && continue
  (
    ssh "${OPTS[@]}" "$BOX" 'mkdir -p ~/fl' 2>&1
    scp "${OPTS[@]}" -q node_bundle.tar.gz node_boot.sh "$BOX:fl/" 2>&1
    scp "${OPTS[@]}" -q "part_$i.jsonl" "$BOX:fl/part.jsonl" 2>&1
    R=$(ssh "${OPTS[@]}" "$BOX" 'bash ~/fl/node_boot.sh' 2>&1 | tail -1)
    echo "box $i ($BOX): $R"
  ) &
  i=$((i+1))
done < boxes.txt
wait
echo "=== deploy complete ==="
