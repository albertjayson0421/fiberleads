# ============================================================================
#  RUN THIS IN GOOGLE CLOUD SHELL (console.cloud.google.com → "Activate Cloud
#  Shell" >_ icon top-right). It is the ONE step I cannot run for you: I have
#  no gcloud auth on your Mac, so instance creation must come from your
#  authenticated session. Paste the whole block.
#
#  It replaces the 12 shared-core e2-small workers with 12 e2-highcpu-2
#  (2 sustained vCPUs each, same 2 GB RAM), reusing the same image/network.
# ============================================================================

# 0. confirm which project + that Compute is on
gcloud config get-value project
gcloud services enable compute.googleapis.com

# 1. read your CURRENT quotas (paste the output back to me — I cannot read them)
gcloud compute regions describe us-central1 \
  --format="table(quotas.metric,quotas.limit,quotas.usage)" | grep -iE "CPUS|IN_USE_ADDRESSES|DISK"
gcloud compute project-info describe \
  --format="table(quotas.metric,quotas.limit,quotas.usage)" | grep -iE "CPUS_ALL_REGIONS"

# 2. delete the 12 e2-small (names fl-1..fl-12 in us-central1-a; adjust if yours differ)
for i in $(seq 1 12); do
  gcloud compute instances delete fl-$i --zone=us-central1-a -q 2>/dev/null &
done; wait

# 3. recreate 12 as e2-highcpu-2, same debian-12 image, public IP so my Mac can SSH
for i in $(seq 1 12); do
  gcloud compute instances create fl-$i \
    --machine-type=e2-highcpu-2 \
    --image-family=debian-12 --image-project=debian-cloud \
    --zone=us-central1-a 2>/dev/null &
done; wait

# 4. re-authorize the fleet SSH key (only needed if project metadata was reset)
gcloud compute project-info add-metadata \
  --metadata=ssh-keys="fl:$(cat ~/.ssh/fleet_key.pub 2>/dev/null)" 2>/dev/null || \
  echo "NOTE: fleet_key.pub lives on the Mac, not Cloud Shell — skip if key already in metadata"

# 5. print the new external IPs, formatted for my boxes.txt
echo "=== PASTE EVERYTHING BELOW THIS LINE BACK TO ME ==="
gcloud compute instances list --filter="name~^fl-" \
  --format="value(EXTERNAL_IP)" | sed 's/^/fl@/'
