"""Auto-join new spot VMs to the Texas sweep once they're SSH-ready. Polls each new IP,
bootstraps it (curl_cffi + merge_sr bundle + remediate2 + zip_city), then gives the ready
new VMs a fresh even split of the REMAINING Texas addresses (input minus what the 12 have
already resolved). Minor overlap with the 12 is deduped at final collect. Does NOT touch
the running 12. Idempotent-ish; safe to re-run."""
import os, sys, json, subprocess, time
NEW_IPS = ["34.69.109.50","35.224.14.22","35.238.119.96","136.116.177.229","34.123.14.64","34.27.33.74","34.134.83.160","35.193.236.58"]
KEY = os.path.expanduser("~/.ssh/fleet_key")
SSH = ["ssh","-i",KEY,"-o","StrictHostKeyChecking=no","-o","ConnectTimeout=8","-o","BatchMode=yes"]
SCP = ["scp","-i",KEY,"-o","StrictHostKeyChecking=no","-o","ConnectTimeout=8","-o","BatchMode=yes","-q"]
BUNDLE = "/Users/masani/iowa_pull/merge_sr_bundle.tar.gz"

def ssh_ok(ip):
    try:
        r = subprocess.run(SSH+["fl@"+ip,"echo IN"], capture_output=True, text=True, timeout=15)
        return "IN" in r.stdout
    except Exception:
        return False

def bootstrap(ip):
    """install curl_cffi + deploy merge_sr + remediate2 + zip_city. Returns True on success."""
    try:
        subprocess.run(SCP+[BUNDLE,"fl@%s:~/merge_sr_bundle.tar.gz"%ip], check=True, timeout=60)
        subprocess.run(SCP+["/Users/masani/iowa_pull/remediate2.py","fl@%s:~/remediate2.py"%ip], check=True, timeout=30)
        subprocess.run(SCP+["/Users/masani/iowa_pull/zip_city_final.json","fl@%s:~/zip_city_final.json"%ip], check=True, timeout=30)
        boot = ("mkdir -p ~/merge_sr && tar xzf ~/merge_sr_bundle.tar.gz -C ~/merge_sr && "
                "python3 -c 'import curl_cffi' 2>/dev/null || { sudo apt-get update -qq >/dev/null 2>&1; "
                "sudo apt-get install -y -qq python3-pip >/dev/null 2>&1; "
                "sudo python3 -m pip install --break-system-packages curl_cffi >/dev/null 2>&1; }; "
                "python3 -c 'import curl_cffi' && echo BOOT_OK || echo BOOT_FAIL")
        r = subprocess.run(SSH+["fl@"+ip, boot], capture_output=True, text=True, timeout=300)
        return "BOOT_OK" in r.stdout
    except Exception as e:
        print("  bootstrap %s failed: %s"%(ip, str(e)[:50]), flush=True); return False

# 1. wait until at least a few new VMs are SSH-ready (up to ~12 min)
print("waiting for new VMs to accept SSH...", flush=True)
ready = []
for attempt in range(48):
    ready = [ip for ip in NEW_IPS if ssh_ok(ip)]
    print("  attempt %d: %d/%d ssh-ready %s"%(attempt+1, len(ready), len(NEW_IPS), ready), flush=True)
    if len(ready) >= 4: break
    time.sleep(15)
if not ready:
    print("NO new VMs became ready — proceeding with the 12; nothing joined.", flush=True); sys.exit(0)

# 2. bootstrap ready ones
booted = []
for ip in ready:
    print("bootstrapping %s ..."%ip, flush=True)
    if bootstrap(ip): booted.append(ip); print("  %s BOOTED"%ip, flush=True)
if not booted:
    print("no VM bootstrapped successfully", flush=True); sys.exit(0)
open("/Users/masani/iowa_pull/new_booted.txt","w").write("\n".join("fl@"+ip for ip in booted)+"\n")

# 3. remaining Texas = input minus resolved-so-far on the 12
resolved = set()
BOXES = [l.strip().replace("fl@","") for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
for ip in BOXES:
    try:
        r = subprocess.run(SSH+["fl@"+ip,"cat ~/tx_out.jsonl 2>/dev/null"], capture_output=True, text=True, timeout=60)
        for line in r.stdout.splitlines():
            try: resolved.add(json.loads(line)["ck"])
            except Exception: pass
    except Exception: pass
allrows = [l for l in open("/Users/masani/iowa_pull/tx_sweep_input.jsonl")]
remaining = [l for l in allrows if json.loads(l).get("ck") not in resolved]
print("Texas remaining (not yet resolved by the 12):", len(remaining), flush=True)

# 4. shard remaining across booted new VMs, launch a SECOND Texas job (tx2_in/tx2_out)
n = len(booted); sh = [[] for _ in range(n)]
for i, l in enumerate(remaining): sh[i % n].append(l)
os.makedirs("/Users/masani/iowa_pull/tx2_shards", exist_ok=True)
for i, ip in enumerate(booted):
    p = "/Users/masani/iowa_pull/tx2_shards/tx2_%02d.jsonl"%i; open(p,"w").writelines(sh[i])
    subprocess.run(SCP+[p,"fl@%s:~/tx2_in.jsonl"%ip], check=False, timeout=300)
    cmd = ("cd ~ && FL_NORM=abbrev setsid nohup python3 -u remediate2.py ~/tx2_in.jsonl ~/tx2_out.jsonl "
           "--threads 24 > ~/tx2.log 2>&1 </dev/null & disown; sleep 1; exit 0")
    try: subprocess.run(SSH+["-n","fl@"+ip, cmd], timeout=25)
    except Exception: pass
    print("  [%s] launched Texas-2 shard=%d"%(ip, len(sh[i])), flush=True)
print("NEW_VMS_JOINED: %d VMs now on the remaining %d Texas addresses"%(len(booted), len(remaining)), flush=True)
