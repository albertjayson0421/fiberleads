#!/bin/bash
# Runs ON each box: ensure curl_cffi, then run 4 GIL-free processes over 4 sub-partitions.
set -u
cd "$HOME/fl" || exit 1
tar xzf node_bundle.tar.gz 2>/dev/null
if ! python3 -c "import curl_cffi" 2>/dev/null; then
  sudo apt-get update -qq >/dev/null 2>&1
  sudo apt-get install -y -qq python3-pip >/dev/null 2>&1
  sudo python3 -m pip install --break-system-packages curl_cffi >/dev/null 2>&1
fi
if ! python3 -c "import curl_cffi" 2>/dev/null; then echo "CURL_CFFI_FAILED"; exit 1; fi
pkill -f node_run.py 2>/dev/null; sleep 1
python3 - <<'PY'
lines=[l for l in open("part.jsonl").read().splitlines() if l.strip()]
for k in range(4):
    open(f"sub_{k}.jsonl","w").write("\n".join(lines[k::4])+"\n")
PY
for k in 0 1 2 3; do
  nohup python3 node_run.py sub_$k.jsonl 12 results_$k.jsonl > run_$k.log 2>&1 &
done
echo "LAUNCHED_4X"
