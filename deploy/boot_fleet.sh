#!/bin/bash
# Re-push node_boot.sh (bundle+partition already on boxes) and run it: install deps + launch.
cd /Users/masani/FLEET_5K || exit 1
KEY="$HOME/.ssh/fleet_key"
OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=25)
i=0
while read -r BOX; do
  [ -z "$BOX" ] && continue
  (
    scp "${OPTS[@]}" -q node_boot.sh "$BOX:fl/" 2>&1
    R=$(ssh "${OPTS[@]}" "$BOX" 'bash ~/fl/node_boot.sh' 2>&1 | tail -1)
    echo "box $i ($BOX): $R"
  ) &
  i=$((i+1))
done < boxes.txt
wait
echo "=== boot complete ==="
