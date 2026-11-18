# ============================================================================
#  RUN IN GOOGLE CLOUD SHELL (console.cloud.google.com -> ">_" Activate Cloud
#  Shell). Adds MORE workers to the existing 12 (fl-1..fl-12), same type/zone,
#  as SPOT instances (~$0.015/hr each, fine for a few-hour burst). It does NOT
#  touch the 12 already running. Paste the whole block.
#
#  Set ADD to how many EXTRA machines you want (12 -> 24 total; 24 -> 36 total).
#  NOTE: each is 2 vCPU. GCP default region quota is often 24 vCPU (=12 VMs), so
#  adding 12 more likely needs a quota bump first (IAM & Admin -> Quotas ->
#  "CPUs" in us-central1 -> raise). The quota check below tells you the ceiling.
# ============================================================================
ADD=12                      # <-- change this (extra machines to add)
ZONE=us-central1-a

gcloud services enable compute.googleapis.com

# quota headroom (CPUS in the region). Each VM needs 2.
echo "=== CPU quota (limit vs usage) — each new VM needs 2 ==="
gcloud compute regions describe ${ZONE%-*} \
  --format="value(quotas.metric,quotas.limit,quotas.usage)" | grep -iE "^CPUS"

# create fl-13 .. fl-(12+ADD) as SPOT
for i in $(seq 13 $((12+ADD))); do
  gcloud compute instances create fl-$i \
    --machine-type=e2-highcpu-2 \
    --image-family=debian-12 --image-project=debian-cloud \
    --zone=$ZONE \
    --provisioning-model=SPOT --instance-termination-action=DELETE 2>/dev/null &
done; wait

# make sure the fleet SSH key is authorized project-wide (harmless if already set)
gcloud compute project-info add-metadata \
  --metadata=ssh-keys="fl:$(cat ~/.ssh/fleet_key.pub 2>/dev/null)" 2>/dev/null || true

echo ""
echo "=== PASTE EVERYTHING BELOW THIS LINE BACK TO ME (the new IPs) ==="
gcloud compute instances list --filter="name~^fl-1[3-9]|name~^fl-[2-9][0-9]" \
  --format="value(EXTERNAL_IP)" | sed 's/^/fl@/'
