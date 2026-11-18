# GCP QUICKSTART — your ~10–15 minutes (copy-paste), then I take over

## 0. One-time account (only step that needs you personally)
- Go to console.cloud.google.com → sign in with a Google account → accept the **$300 free credit** (asks for a card for identity only; the credit means $0 charge).
- Install the gcloud CLI on your Mac: `curl https://sdk.cloud.google.com | bash` then `exec -l $SHELL` and `gcloud init` (pick/create a project, region us-central1).

## 1. Enable Compute + make an SSH key (copy-paste)
```
gcloud services enable compute.googleapis.com
ssh-keygen -t ed25519 -f ~/.ssh/fleet_key -N ""            # the key my deploy uses
# authorize that key on all future instances:
gcloud compute project-info add-metadata \
  --metadata=ssh-keys="fl:$(cat ~/.ssh/fleet_key.pub)"
```

## 2. Spin up the fleet — START WITH 10 (copy-paste)
```
for i in $(seq 1 10); do
  gcloud compute instances create fl-$i \
    --machine-type=e2-small --image-family=debian-12 --image-project=debian-cloud \
    --zone=us-central1-a --no-address 2>/dev/null &
done; wait
# install python + curl_cffi on each, and print reachable IPs:
gcloud compute instances list --format="value(name)" | grep '^fl-' > /tmp/fl_names.txt
```
(If `--no-address` gives you trouble reaching them, drop it so each box gets a public IP.)

## 3. Hand me the boxes
Create `/Users/masani/FLEET_5K/boxes.txt`, one per line as `fl@<external-ip>`:
```
gcloud compute instances list --filter="name~^fl-" \
  --format="value(EXTERNAL_IP)" | sed 's/^/fl@/' > /Users/masani/FLEET_5K/boxes.txt
cat /Users/masani/FLEET_5K/boxes.txt
```
Then tell me "boxes ready." I run:
```
bash /Users/masani/FLEET_5K/deploy.sh boxes.txt     # I deploy + launch each on its slice
# watch ~2 min, I read the live per-node rate, then you scale 10 → 20 → 40
bash /Users/masani/FLEET_5K/collect.sh boxes.txt     # merge results into your master answer file
```

## Notes I'll handle
- I'll ship each box only the baseline + work-queue (small), not the 2.5 GB evidence table, so uploads are fast.
- I ramp 10 → 20 → 40 watching the error rate, and stop at the last clean count — that's your true live rate.
- Cost tracker: 40 × e2-small × 1 hr ≈ $3–5 of the $300 credit. I'll tell you the running total.

## Tear down when done (stop the credit burn)
```
gcloud compute instances list --filter="name~^fl-" --format="value(name)" \
  | xargs -I{} gcloud compute instances delete {} --zone=us-central1-a -q
```
