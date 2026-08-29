# Weekly Delivery Window Sync
**Date:** 2026-08-30
**Status:** Approved

## Overview

`--sync-delivery-windows` today re-checks *every* non-Delivered master-sheet shipment's Amazon delivery window on every manual run, regardless of whether that window is anywhere near locking. That's wasted browser traffic: only shipments whose window starts within the next 7 days actually need a decision this week.

This feature adds a new, scheduled weekly mode (`--weekly-delivery-sync`) that:
1. Persists each shipment's delivery window dates in the master sheet, so future runs can tell locally (no browser) whether a shipment needs checking this week.
2. Refreshes carrier tracking data and discovers new shipments' Workflow IDs first, so the check always works off current data.
3. Only opens a browser page for shipments whose window starts in the coming 7 days, or that have never been checked before.
4. Replaces the old "guess 2 weeks out" fallback with "nudge 1 week out" — since this now runs every week, a 1-week nudge is re-verified next Saturday instead of needing a bigger one-shot buffer.
5. Skips carrier-managed (FIST) shipments permanently once detected, instead of re-checking them every week.
6. Writes a short summary file after every run.
7. Runs automatically via Windows Task Scheduler every Saturday 10pm, catching up if the PC was off.

The existing `--sync-delivery-windows` (full scan, on demand) stays as-is for ad-hoc manual use, minus one shared change: `decide_window_action()`'s fallback push becomes 1 week instead of 2, everywhere, since two different "how far do we guess" defaults for the same decision function would be confusing (see Decision Logic below).

## How Master-Sheet Rows Come To Exist (context for this feature)

This feature does not create new master-sheet rows. `run_update_master_sheet()` (`master_sheet.py`) already runs as a side effect of every normal `python run.py` invocation — the user's regular "drop this week's supplier Excel file in `input/`, run `run.py`" ritual. That reuses the just-parsed Excel rows to add/update rows in `logs/shipment_tracking_master.xlsx`, keyed by FBA ID.

So new shipments show up in the master sheet whenever the user does their normal weekly tracking-upload run — not from this Saturday job. This job's "handle new shipments" responsibility is narrower: for any master-sheet row that's missing a Workflow ID or a recorded delivery window (because it's new since the last Saturday, or was never resolved), discover its Workflow ID and read+record its window for the first time, regardless of whether that window happens to be starting soon.

## Command Structure

New flag: `python run.py --weekly-delivery-sync`. Standalone entry point (same pattern as `--sync-delivery-windows`, `--check-tracking`) — returns after printing/writing its summary, no interaction with the normal upload pipeline.

## Data Flow

1. **Refresh carrier data.** Call `run_check_tracking(config)` (same function `--check-tracking` uses) to refresh `logs/tracking_status.xlsx` with current carrier expected-delivery dates.
2. **Discover new Workflow IDs.** For every master-sheet row missing a `workflow_id`, run the same discovery `--discover-workflows` already does (reuse `workflow_discovery.py`'s function, not a copy).
3. **Load the master sheet** and build the weekly candidate list: rows that are not already Delivered (`tracking_status` and `delivery_date_status` both != `"Delivered"`), not already flagged `carrier_managed` (see below), and where:
   - `window_start` is unset (never checked), **or**
   - `window_start` is between `today + 1 day` and `today + 7 days` inclusive.

   Rows whose recorded `window_start` is further out are skipped entirely this run — no browser visit. Rows whose recorded `window_start` is in the past (older than today) are still included, tagged as `overdue` in the summary — this shouldn't happen if the job runs every week, but must not be silently dropped if a week was missed.
4. **For each candidate, open the browser** (grouped by region, one login per region, same convention as `run_delivery_window_sync`) and:
   a. Read the live window via `read_shipment_window()` (unchanged — already has the stale-workflow detection and screenshot-on-failure from v0.8.1-0.8.3).
   b. If reading fails → outcome `read_failed`, window fields in the master sheet left untouched (don't blank out a previously-good value on a transient failure).
   c. Otherwise decide via `decide_window_action()` (see Decision Logic) using the live window and the freshly-refreshed `expected_delivery_date` from step 1's cache:
      - `locked` → shouldn't happen with reliable weekly cadence; logged and flagged `overdue` in the summary since it means either a missed week or something needs manual attention.
      - `none` (expected date exists and falls inside the window) → outcome `matched`, no edit.
      - `edit` (expected date exists and falls outside the window) → `apply_window_edit()` moves the window to the week containing the real expected date.
      - `push_one_week` (no usable expected date) → `apply_window_edit()` moves the window forward exactly 7 days from its *current* start (not from `today`), landing on the next Sunday-Saturday block.
      - `apply_window_edit()` can itself return `carrier_managed` (checkbox detected) → outcome `carrier_managed`; this shipment's row gets `delivery_date_status = "carrier_managed"` so the step-3 filter skips it — no browser visit — on every future run.
5. **Persist after every shipment:** whatever window dates were just read (or the new target dates on a successful edit) into the master sheet's `Delivery Window Start` / `Delivery Window End` columns, plus `Delivery Window Last Checked = now`, plus `delivery_date_status` per the outcome. Save the master sheet after each region (same crash-safety convention `run_delivery_window_sync` already uses).
6. **Write the summary** (see Notification / Summary Output) after all regions are done.

## Master Sheet Schema Changes

Three new columns appended to `MASTER_SHEET_COLUMNS` / `_FIELD_ORDER` in `master_sheet.py` (after `Workflow ID`, the current last column):

| Column header | Dict key | Format |
|---|---|---|
| Delivery Window Start | `delivery_window_start` | `YYYY-MM-DD` |
| Delivery Window End | `delivery_window_end` | `YYYY-MM-DD` |
| Delivery Window Last Checked | `delivery_window_last_checked` | `YYYY-MM-DD HH:MM` (same style as existing `Last Checked`) |

`delivery_date_status` (existing column) gains one new possible value: `"carrier_managed"`, alongside today's `"updated"` / `"pending"`.

## Decision Logic Changes

`decide_window_action(window_start, window_end, expected_delivery_date, today)`:
- The `push_two_weeks` action/outcome is renamed `push_one_week` throughout (`delivery_window_sync.py`, `format_delivery_window_sync_summary`, `run_delivery_window_sync`'s `_bump` mapping, all tests).
- Its target-week computation changes from "2 weeks after `today`" to "1 week after the *current* `window_start`" (`window_start + timedelta(days=7)`, then normalized to that week's Sunday via `_week_bounds` same as today's code already does for the `edit` action).
- The `(window_start - today).days <= 7` eligibility gate for choosing `push_one_week` vs `none` is **unchanged** — it still only fires when the window is genuinely close to locking. For `--weekly-delivery-sync` this gate is always true by construction (step 3 already filtered to exactly this zone); for the old `--sync-delivery-windows` full scan, it still gates correctly against shipments whose window isn't urgent yet.

## New CLI Flag Wiring (`run.py`)

Add `--weekly-delivery-sync` next to the existing `--sync-delivery-windows` in the argparse setup, and a standalone-mode branch (same shape as the existing `if args.sync_delivery_windows:` branch) that calls the new `run_weekly_delivery_window_sync(config)` and prints/writes its summary, then returns.

## Notification / Summary Output

Printed to console and also written to `logs/weekly_delivery_window_summary_<timestamp>.txt` (so it's inspectable after an unattended run closes its window):

```
============================================================
WEEKLY DELIVERY WINDOW SYNC SUMMARY - 2026-09-05 22:00
============================================================
Checked this week  : 14   (window starting within 7 days, or never checked)
Skipped (not due)   : 121  (window further out — no browser visit needed)
Skipped (carrier-managed): 6

Matched (already correct):  3
Edited (moved to real date): 2
Pushed 1 week (no date yet): 5
Newly discovered & recorded: 2   -> FBA19ABCDEF1, FBA19ABCDEF2
Overdue (missed lock / needs attention): 1  -> FBA19XYZ1234
Locked (can't be edited):   0
Read failed:                2   -> FBA15GDQMSCT, FBA15GDT80ZL
============================================================
```

"Newly discovered & recorded" = candidates whose `window_start` was unset before this run and got a value for the first time. "Overdue" = candidates whose *previously recorded* `window_start` was already in the past when this run started (missed-week or manual-intervention signal).

## Windows Task Scheduler

Not part of the Python codebase — a machine-level scheduled task, created once, out of band from `git`. Requirements gathered from the user:
- Trigger: **Weekly, every Saturday, 10:00 PM.**
- **"Run task as soon as possible after a scheduled start is missed"** enabled, so a missed trigger (PC off) catches up once the PC is back on.
- Chrome must run **visibly** (non-headless, matching today's `config.json` `headless: false`) — this requires the task to run in an interactive desktop session. On modern Windows, a task configured to "run whether user is logged on or not" runs in a non-interactive Session 0 and **cannot show a GUI window at all** — Chrome would either fail to launch visibly or the task would need `headless: true` (contradicting "run it so it's visible"). The task must instead be configured **"Run only when user is logged on."** Practical implication to confirm with the user: if the PC is merely powered-on-but-locked (no one logged in) at catch-up time, the task still can't show a browser window until the account is actually logged in — "run it as soon as its on" and "make it visible" are only both satisfiable if the account auto-logs-in or is already logged in when the PC powers on.
- Action: run a new `run_weekly_delivery_sync.bat` (mirrors `check_tracking.bat`'s style, minus the interactive `pause` calls, since nothing is present to press Enter unattended) that calls `python run.py --weekly-delivery-sync`.

## Error Handling / Edge Cases

| Situation | Behavior |
|---|---|
| Master-sheet row's recorded `window_start` is in the past at time of weekly check | Included in this run's candidates anyway (not silently skipped), flagged `overdue` in the summary |
| Row still has no `workflow_id` after step 2's discovery (no "Send to Amazon (view)" link found) | Window columns left blank, `delivery_date_status` unchanged, counted under a `no_workflow` bucket in the summary — matches today's `--discover-workflows` "Unresolved" behavior |
| `read_shipment_window()` fails (stale/empty workflow page, or any other read failure) | `read_failed`; existing window fields in the master sheet are **left as last known**, not blanked — a transient failure shouldn't erase a previously good value |
| `apply_window_edit()` detects the carrier-managed checkbox | `delivery_date_status = "carrier_managed"` persisted; this row is excluded from the candidate list on every future run without a browser visit |
| A shipment goes from `carrier_managed` back to needing manual management (carrier drops the shipment, hypothetically) | Out of scope — not something Amazon's UI signals in a way this pipeline can detect; would need the person to notice on Amazon directly and there is no code-side recovery path |
| Previous run's Chrome process crashed and left the automation profile locked | `create_browser_context()` already raises a clear `RuntimeError` ("Chrome profile is already in use...") — this propagates into the summary's error section; no automatic process-killing added, matching the existing manual-recovery convention for this error elsewhere in the codebase |
