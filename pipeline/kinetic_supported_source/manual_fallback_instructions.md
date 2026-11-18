# Manual-browser fallback — small priority queue only

`MANUAL_SPOT_CHECK_ONLY = TRUE`. Automated production is disabled (the search endpoint is
browser-behavioral-bound). For a SMALL, high-priority queue, a human can check addresses one at
a time in a normal browser — this is ordinary human use, not automation.

**Do not automate this.** No scripting the browser, no CAPTCHA/behavioral-token handling, no bulk
loops. It is for a handful to low-hundreds of addresses while a supported source is being set up.

## Priority queue provided
`samples/centerville_priority_manual_100.csv` — 100 Centerville (52544) addresses
(of 3,518 queued). Columns include blank `manual_result`, `plan_name`, `checked_utc` to fill in.

## Steps (per address, by a person)
1. Open https://www.gokinetic.com in a normal browser.
2. In "check availability", type the address; select the exact match from the dropdown.
3. Click "Check plans" / "Show me what's available".
4. Read the result:
   - a fiber plan populates with a plan name → **SERVICEABLE** (record the plan name).
   - "call to order" / no orderable plan → **CALL_TO_ORDER**.
   - already a customer / account prompt → **EXISTING**.
   - no fiber / not available → **NOT_SERVICEABLE**.
   - address needs fixing → **NEEDS_FIX** (try the exact USPS form once).
5. Fill `manual_result`, `plan_name`, `checked_utc` (UTC) in the CSV.

## Turning manual results into leads
Filled CSV → the canonical importer (rename your `manual_result` per the field map or add a
`disposition` column), then dedup against prior delivered and rooftop-pin the SERVICEABLE ones
with the existing pipeline. SERVICEABLE + non-customer → deliverable; everything else complete →
rejected; blank/failed → still unresolved.

This fallback does not scale to the statewide workload — it is a stopgap for a small priority
list only. The real fix is a supported source (see `access_request_email.md`).
