"""Robust sequential collector for the Iowa remediation2 results (~/rem_out.jsonl on the 12
VMs). Streams each VM's file gzipped over ssh (tiny transfer), hard per-call timeout via
subprocess, ServerAliveInterval as the hang guard so a saturated VM can never block the run.
Also reports each VM's rem_out/tx_out line counts and whether Iowa/Texas are still alive."""
import subprocess, os, json, gzip, io, collections, sys
KEY = os.path.expanduser("~/.ssh/fleet_key")
BOXES = [l.strip().replace("fl@", "") for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
SSHOPTS = ["-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
           "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
OUT = "/Users/masani/iowa_pull/remediation2_results.jsonl"
os.makedirs("/Users/masani/iowa_pull/ledgers2", exist_ok=True)

def ssh_text(ip, cmd, to):
    try:
        r = subprocess.run(["ssh"] + SSHOPTS + ["fl@" + ip, cmd], capture_output=True, timeout=to)
        return r.stdout.decode("utf-8", "replace"), r.returncode
    except Exception as e:
        return "ERR:" + str(e)[:40], -1

total = 0; t = collections.Counter(); wf = open(OUT, "w")
for i, ip in enumerate(BOXES):
    # 1) status line
    st, _ = ssh_text(ip, ('echo "$(wc -l < ~/rem_out.jsonl 2>/dev/null)|$(wc -l < ~/tx_out.jsonl 2>/dev/null)|'
                          '$(pgrep -f "remediate2.py ~/rem_in">/dev/null&&echo IA_RUN||echo IA_DONE)|'
                          '$(pgrep -f "remediate2.py ~/tx_in">/dev/null&&echo TX_RUN||echo TX_STOP)"'), 30)
    st = st.strip()
    # 2) pull rem_out gzipped
    got = 0
    try:
        r = subprocess.run(["ssh"] + SSHOPTS + ["fl@" + ip, "gzip -c ~/rem_out.jsonl 2>/dev/null"],
                           capture_output=True, timeout=180)
        if r.returncode == 0 and r.stdout:
            raw = gzip.decompress(r.stdout).decode("utf-8", "replace")
            open("/Users/masani/iowa_pull/ledgers2/rem2_%02d.jsonl" % i, "w").write(raw)
            for line in raw.splitlines():
                line = line.strip()
                if not line: continue
                wf.write(line + "\n"); got += 1; total += 1
                try: t[json.loads(line).get("status")] += 1
                except Exception: pass
    except Exception as e:
        st += " PULLFAIL:" + str(e)[:30]
    print("  [%2d] %-16s status=%s pulled=%d" % (i, ip, st, got), flush=True)
wf.close()
print("=== IOWA remediation2 collected: total=%d %s ===" % (total, dict(t.most_common())), flush=True)
