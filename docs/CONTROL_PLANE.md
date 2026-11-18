# Control Plane — how a pull runs in the cloud

How the monthly pull runs as a cloud job you trigger. This is the target for your **[P1] "package the
pull as a Cloud Run Job"** task.

## Target design (GCP-native)
```
 you --(1) trigger the job-->  Cloud Run Job (fiberleads-run, in the prod project)
                                 |  runs as a dedicated service account
                                 |  the platform injects the Kinetic/proxy/SSH credentials
                                 |     from Secret Manager into the job at runtime
                                 |  executes fleet_scheduler.py over the fleet
                                 v
        <--(3) logs + ledger --  writes the ledger + logs to a results bucket you can read
```
- **(1) Trigger:** `gcloud run jobs execute fiberleads-run --args=...` with `roles/run.invoker` on the job. You start a run; the platform handles the credentials.
- **(2) Execute:** the job's service account has `secretmanager.secretAccessor` on the three secrets, so the credentials are present inside the job's process for its lifetime and injected by the platform, not handled by a person.
- **(3) Observe:** logs stream to Cloud Logging; the ledger lands in a results bucket you can `objects.get`/`list`.

## Build order (your P1)
1. Package the frozen build as a container image (reuse the `.devcontainer/Dockerfile` base).
2. Create the `fiberleads-run` Cloud Run Job in the prod project, deployed with
   `--service-account=<the prod runner service account>` and the Secret Manager bindings.
3. The owner grants you `roles/run.invoker` on that job so you can trigger it.
4. Make it resumable/idempotent and point its logs + ledger at a results bucket you can read.
5. (Stretch) add a **Cloud Scheduler** monthly trigger so a pull runs unattended.

## Before the Job exists
Develop and test entirely in the **`fiberleads-dev`** project with dev credentials (a throwaway proxy
sub-account + the offline fixtures in the repo) — no production credential is needed for ~95% of the
work. A real production run is arranged with the owner.
