"""Auto-resume watcher. Every 15 min, ONE probe from one VM tests whether the carrier's
search endpoint answers (200) again — i.e. the Cloudflare challenge came down. Never a flood:
a single request per cycle. When search is clear, relaunches BOTH blocked sweeps (Texas
remaining + Iowa cities) via tx_dispatch, then exits. Writes status to cf_watch.log."""
import subprocess, os, json, time, hashlib, random, sys
KEY=os.path.expanduser("~/.ssh/fleet_key")
OPTS=["-i",KEY,"-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10","-o","BatchMode=yes","-o","ServerAliveInterval=5","-o","ServerAliveCountMax=3"]
BOX=[l.strip().replace("fl@","") for l in open("/Users/masani/FLEET_5K/boxes.txt") if l.strip()]
LOG=open("/Users/masani/iowa_pull/cf_watch.log","a",buffering=1)
def log(m): LOG.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")

PROBE=r'''
import os,sys,json,base64,hashlib,random
HERE=os.path.expanduser("~/merge_sr"); os.environ.setdefault("FL_CONFIG",os.path.join(HERE,"config"))
from curl_cffi import requests as cr
BASE="https://buy.gokinetic.com"; SESSION="/api/v1/auth/session?context=residential"; SEARCH="/api/v1/address/search"
BASIC=base64.b64encode(next(l.strip() for l in open(os.path.join(os.environ["FL_CONFIG"],"uqual.txt")) if l.strip()).encode()).decode()
pools=[p for p in json.load(open(os.path.join(HERE,"residential.json")))["pools"] if str(p.get("name","")).startswith("dataimpulse")]
p=pools[0]; u=str(p["user"])+"__cr.us__sid."+hashlib.sha256(os.urandom(16)).hexdigest()[:12]
px=f'http://{u}:{p["pass"]}@{p["host"]}:{p["port"]}'
xhr={"Sec-Fetch-Site":"same-origin","Content-Type":"application/json","Accept":"application/json"}
s=cr.Session(impersonate="chrome124",proxies={"http":px,"https":px})
r=s.post(BASE+SESSION,headers={**xhr,"Authorization":"Basic "+BASIC},data="{}",timeout=30)
tok=None
try: tok=r.json().get("token")
except Exception: pass
if not tok:
    print("AUTH_%s" % r.status_code); raise SystemExit
sr=s.post(BASE+SEARCH,headers={**xhr,"Authorization":"Bearer "+tok},
          data=json.dumps({"addressLine1":"514 NE 3RD ST","city":"","state":"TN","zip":"37917"}),timeout=30)
print(sr.status_code)
'''
def probe():
    ip=random.choice(BOX)
    try:
        r=subprocess.run(["ssh"]+OPTS+["fl@"+ip,"python3 - <<'EOF'\n"+PROBE+"\nEOF"],capture_output=True,timeout=60)
        out=r.stdout.decode().strip().splitlines()
        return out[-1] if out else "?"
    except Exception as e: return "ERR:"+str(e)[:30]

log("watcher started; probing search every 15 min (1 request/cycle)")
for cycle in range(400):  # ~100 hours max
    st=probe(); log(f"cycle {cycle}: search HTTP {st}")
    if st=="200":
        log("SEARCH CLEAR — relaunching Texas remaining + Iowa cities")
        subprocess.run(["python3","/Users/masani/iowa_pull/tx_dispatch.py","launch","/Users/masani/iowa_pull/tx_remaining.jsonl"],timeout=1200)
        log("relaunched Texas remaining. (cities queue staged at cities_sweep_queue.jsonl — launch after TX or as 2nd job)")
        log("watcher exiting; sweep resumed")
        break
    time.sleep(900)
