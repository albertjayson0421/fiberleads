# pipeline/_legacy — quarantined scripts

These are one-off / superseded pipeline scripts moved here on 2026-09-13 during the clean-clone
refactor. They are **quarantined, not deleted** (owner rule: nothing is deleted until a full run
works fine without it). Move a script back to `pipeline/` if a run turns out to need it.

Quarantined (owner-approved categories):
- One-off charts: build_competition_chart, build_competition_chart2, build_chart_clean
- Fairfield / fiber-group one-offs: build_fg_candidates, build_fg_queue, diag_fairfield
- Diagnostics / canaries / probes: enr_probe, cf_watch, norm_canary, stable_client_test,
  native_client, remediation_canary
- One-off lead-add scripts: add_recovered_leads, add_r2_leads
- Dead: capture_pricing (we no longer capture the dollar price)

Deliberately NOT moved yet:
- Per-market forks (iowa_*, tx_*, build_iowa_deliverable, build_tx_partial): left in place; the
  path-config work collapses these into one path, so they fall away on their own.
- VM sweep/collect (iowa_dispatch, remed_dispatch, join_new, finalize, remcollect): confirm the
  newer fleet setup covers these before quarantining.

Kept as reusable stages in pipeline/: build_nc_statewide, residential_run, remediate2,
prior_delivered, build_delivery, verify_deliverable, finalize_remediation, fetch_zipcity.
