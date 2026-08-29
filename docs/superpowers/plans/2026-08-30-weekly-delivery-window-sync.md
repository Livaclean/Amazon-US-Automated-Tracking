# Weekly Delivery Window Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scheduled weekly mode (`--weekly-delivery-sync`) that only opens a browser for shipments whose Amazon delivery window is about to lock (or was never checked), refreshing carrier data and discovering new shipments first, nudging unconfirmed windows forward by exactly one week instead of guessing two, and permanently skipping carrier-managed (FIST) shipments — plus the Windows Task Scheduler entry to run it every Saturday 10pm.

**Architecture:** `master_sheet.py` gains 3 persisted columns (window start/end/last-checked) so a pure, browser-free filter function can decide locally which shipments are due this week. `delivery_window_sync.py` gains that filter function and a new orchestration function that chains `run_check_tracking()` → `run_workflow_discovery()` → the filter → the existing per-shipment `sync_window_for_shipment()`/`apply_window_edit()` (renaming their 2-week guess to a 1-week one) → persists results back to the master sheet → writes a summary file. `run.py` wires in the new CLI flag. A new `.bat` wrapper (no interactive `pause`) is the Task Scheduler action.

**Tech Stack:** Python 3, pytest, openpyxl (master sheet), Playwright (already managed by existing `upload_tracking.py` code — reused, not modified), Windows Task Scheduler (`schtasks`/`Register-ScheduledTask`, via PowerShell).

**Spec:** `docs/superpowers/specs/2026-08-30-weekly-delivery-window-sync-design.md`

## Global Constraints

- Master-sheet date columns use `YYYY-MM-DD`; the existing `Last Checked`-style timestamp columns use `YYYY-MM-DD HH:MM` (spec: Master Sheet Schema Changes) — match this exactly for the 3 new columns.
- `push_two_weeks` is renamed `push_one_week` **everywhere** it appears (action string, outcome string, `_bump` mapping, summary label, tests) — no code path may still reference `push_two_weeks` after this plan (spec: Decision Logic Changes).
- The existing `--sync-delivery-windows` full-scan command keeps its current candidate scope (every non-Delivered row) — only its push amount changes to 1 week, shared via the same `decide_window_action()` (spec: Overview, "stays as-is... minus one shared change").
- Never blank out a previously-recorded `Delivery Window Start/End` on a transient `read_failed` — only overwrite on a successful read or edit (spec: Error Handling table).
- No new dependencies. Follow existing module conventions: plain functions, lazy `from x import y` inside functions that need Playwright/openpyxl (matches `run_delivery_window_sync`'s existing style), tests in `tests/` using the existing fake-Playwright-locator pattern from `tests/test_delivery_window_sync.py`.

---

## Task 1: Master sheet schema — 3 new delivery-window columns

**Files:**
- Modify: `master_sheet.py:13-38` (`MASTER_SHEET_COLUMNS`, `_FIELD_ORDER`), `master_sheet.py:63-71` (`_STATUS_FIELDS`), `master_sheet.py:94-103` (`populate_from_input`'s new-row defaults)
- Test: `tests/test_master_sheet.py`

**Interfaces:**
- Produces: three new dict keys usable by any caller of `load_master_sheet`/`save_master_sheet`: `delivery_window_start`, `delivery_window_end`, `delivery_window_last_checked` (all plain strings, same convention as every other master-sheet field — no date objects stored).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_master_sheet.py`:

```python
def test_master_sheet_columns_include_delivery_window_fields():
    from master_sheet import MASTER_SHEET_COLUMNS
    assert "Delivery Window Start" in MASTER_SHEET_COLUMNS
    assert "Delivery Window End" in MASTER_SHEET_COLUMNS
    assert "Delivery Window Last Checked" in MASTER_SHEET_COLUMNS


def test_save_and_load_round_trips_delivery_window_fields(tmp_path):
    from master_sheet import save_master_sheet, load_master_sheet
    path = str(tmp_path / "master.xlsx")
    sheet = {
        "FBA001": {
            "fba_id": "FBA001", "tracking": "1Z001", "carrier": "UPS",
            "name": "", "destination": "", "ctns": "", "shipping_way": "",
            "notes": "", "region": "US", "tracking_status": "pending",
            "delivery_date_status": "pending", "label_created_date": "",
            "expected_delivery_date": "", "status": "", "last_checked": "",
            "workflow_id": "wf-1",
            "delivery_window_start": "2026-09-06",
            "delivery_window_end": "2026-09-12",
            "delivery_window_last_checked": "2026-08-30 22:00",
        }
    }
    save_master_sheet(path, sheet)
    loaded = load_master_sheet(path)
    assert loaded["FBA001"]["delivery_window_start"] == "2026-09-06"
    assert loaded["FBA001"]["delivery_window_end"] == "2026-09-12"
    assert loaded["FBA001"]["delivery_window_last_checked"] == "2026-08-30 22:00"


def test_populate_from_input_new_row_has_blank_delivery_window_fields(tmp_config):
    """Extends the existing test_populate_from_input_creates_pending_rows_for_new_shipments
    (tests/test_master_sheet.py:171) pattern -- reuses its own _write_input_sheet helper,
    not a mock, matching how every other populate_from_input test in this file works."""
    tmp_config = _write_input_sheet(tmp_config)
    sheet = populate_from_input(tmp_config, {})
    row = sheet["FBA_CL1"]
    assert row["delivery_window_start"] == ""
    assert row["delivery_window_end"] == ""
    assert row["delivery_window_last_checked"] == ""
```

This reuses `_write_input_sheet` and `tmp_config`, already defined at the top of `tests/test_master_sheet.py` (lines 17-41) — do not reimplement or mock `build_check_list`; `populate_from_input` imports it locally from `tracking_status` inside the function body, so it isn't a `master_sheet`-module attribute a monkeypatch could reach anyway.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_master_sheet.py -k delivery_window -v`
Expected: FAIL — `AssertionError` (columns/fields don't exist yet) or `KeyError`.

- [ ] **Step 3: Add the columns**

In `master_sheet.py`, extend `MASTER_SHEET_COLUMNS` and `_FIELD_ORDER`:

```python
MASTER_SHEET_COLUMNS = [
    "Tracking Status", "Delivery Date Status", "Tracking Number", "Carrier",
    "FBA ID", "Shipment Name", "Destination", "Ctns", "Shipping Way",
    "Notes (source)", "Label Created Date", "Expected Delivery Date",
    "Current Status", "Last Checked", "Region", "Workflow ID",
    "Delivery Window Start", "Delivery Window End", "Delivery Window Last Checked",
]

_FIELD_ORDER = [
    ("tracking_status", "Tracking Status"),
    ("delivery_date_status", "Delivery Date Status"),
    ("tracking", "Tracking Number"),
    ("carrier", "Carrier"),
    ("fba_id", "FBA ID"),
    ("name", "Shipment Name"),
    ("destination", "Destination"),
    ("ctns", "Ctns"),
    ("shipping_way", "Shipping Way"),
    ("notes", "Notes (source)"),
    ("label_created_date", "Label Created Date"),
    ("expected_delivery_date", "Expected Delivery Date"),
    ("status", "Current Status"),
    ("last_checked", "Last Checked"),
    ("region", "Region"),
    ("workflow_id", "Workflow ID"),
    ("delivery_window_start", "Delivery Window Start"),
    ("delivery_window_end", "Delivery Window End"),
    ("delivery_window_last_checked", "Delivery Window Last Checked"),
]
```

Add the three keys to `_STATUS_FIELDS` (they're owned by the sync job, never refreshed from the supplier sheet):

```python
_STATUS_FIELDS = [
    "tracking_status", "delivery_date_status", "label_created_date",
    "expected_delivery_date", "status", "last_checked", "workflow_id",
    "delivery_window_start", "delivery_window_end", "delivery_window_last_checked",
]
```

In `populate_from_input`, add the three defaults to the new-row branch alongside the existing ones:

```python
            row["delivery_window_start"] = ""
            row["delivery_window_end"] = ""
            row["delivery_window_last_checked"] = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_master_sheet.py -k delivery_window -v`
Expected: PASS

- [ ] **Step 5: Run the full master-sheet test file to check nothing else broke**

Run: `python -m pytest tests/test_master_sheet.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add master_sheet.py tests/test_master_sheet.py
git commit -m "feat: add delivery-window columns to the master sheet"
```

---

## Task 2: Rename `push_two_weeks` to `push_one_week`, target the window's own start

**Files:**
- Modify: `delivery_window_sync.py:107-150` (`decide_window_action`)
- Test: `tests/test_delivery_window_sync.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `decide_window_action(...)` now returns `{"action": "push_one_week", "target_week_start": date}` instead of `{"action": "push_two_weeks", ...}` for the "no usable expected date, window locking soon" case. `target_week_start` is now computed from `window_start + timedelta(days=7)` (normalized to that week's Sunday via the existing `_week_bounds` helper), not from `today + timedelta(days=14)`.

- [ ] **Step 1: Read the current implementation**

Confirmed current code at `delivery_window_sync.py:107-147` (`decide_window_action`) — the branch this task changes is exactly:

```python
    if (window_start - today).days <= 7:
        target_start, _ = _week_bounds(today + timedelta(days=14))
        return {"action": "push_two_weeks", "target_week_start": target_start}
```

Also update the function's docstring (lines 108-129), which documents `"push_two_weeks"` by name and describes the "two weeks" behavior in prose — both need to change to `"push_one_week"` / "one week" for accuracy.

- [ ] **Step 2: Update the existing tests to the new name/target first (they currently encode the old behavior)**

In `tests/test_delivery_window_sync.py`, find every test with `push_two_weeks` in its name or asserted action string (`test_decide_window_action_push_two_weeks_when_no_expected_date_and_window_starts_soon`, `test_decide_window_action_push_two_weeks_boundary_exactly_seven_days`, `test_decide_window_action_stale_expected_date_falls_back_to_push_two_weeks`, and the `sync_window_for_shipment`/summary tests using `"pushed"`/`"push_two_weeks"`). Rename them to `push_one_week` and change their expected `target_week_start` to `window_start + 7 days` (week-normalized) instead of the old `today + 14 days` value. For example:

```python
@pytest.mark.unit
def test_decide_window_action_push_one_week_when_no_expected_date_and_window_starts_soon():
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 22),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    # window_start + 7 days = Aug 23, 2026 (already a Sunday -- window_start's
    # own week-alignment carries through, so no extra _week_bounds shift needed)
    assert result == {"action": "push_one_week", "target_week_start": date(2026, 8, 23)}


@pytest.mark.unit
def test_decide_window_action_push_one_week_boundary_exactly_seven_days():
    # window_start=Aug 17 is deliberately NOT Sunday-aligned (unlike a real
    # Amazon window) -- this test isolates the (window_start - today).days <= 7
    # trigger boundary, same as the original push_two_weeks version of this
    # test did; it never asserted week-alignment precision, so neither does this.
    result = decide_window_action(
        window_start=date(2026, 8, 17), window_end=date(2026, 8, 23),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result["action"] == "push_one_week"
    # window_start (Aug 17, a Monday) + 7 days = Aug 24 (Monday) -> _week_bounds
    # normalizes that to its containing week: Aug 23 (Sun) - Aug 29 (Sat).
    assert result["target_week_start"] == date(2026, 8, 23)


@pytest.mark.unit
def test_decide_window_action_stale_expected_date_falls_back_to_push_one_week():
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 22),
        expected_delivery_date=date(2026, 8, 2), today=date(2026, 8, 10),
    )
    assert result == {"action": "push_one_week", "target_week_start": date(2026, 8, 23)}
```

Also update `test_sync_window_for_shipment_push_two_weeks_success` → rename to `..._push_one_week_success` (the `totals` dict key itself, `"pushed"`, is unchanged — only the action string and the printed label text change, in this same task's Step 4 below).

- [ ] **Step 3: Run the renamed tests to verify they fail**

Run: `python -m pytest tests/test_delivery_window_sync.py -k push_one_week -v`
Expected: FAIL (function still returns `push_two_weeks` / old target date)

- [ ] **Step 4: Implement the rename + new target computation**

In `decide_window_action` (`delivery_window_sync.py:143-145`), replace:

```python
    if (window_start - today).days <= 7:
        target_start, _ = _week_bounds(today + timedelta(days=14))
        return {"action": "push_two_weeks", "target_week_start": target_start}
```

with:

```python
    if (window_start - today).days <= 7:
        target_start, _ = _week_bounds(window_start + timedelta(days=7))
        return {"action": "push_one_week", "target_week_start": target_start}
```

(`_week_bounds` is already imported/defined in this file — reuse it exactly as the existing `edit` branch does, don't hand-roll Sunday alignment.) Also update the docstring's `Returns` line (`"push_two_weeks"` → `"push_one_week"`) and its bullet describing that action (currently: *"'push_two_weeks': no expected date yet, and the window starts within the next 7 days (about to lock) -- push it out two weeks so it doesn't lock on a guess while we wait for a real date."* → change to one week, and drop "while we wait for a real date" framing since the weekly cadence itself is now the reason a smaller nudge is safe — the same shipment gets re-verified next Saturday rather than needing a bigger one-shot buffer).

The **existing** `format_delivery_window_sync_summary` (`delivery_window_sync.py:499-511`, used by the old ad-hoc `--sync-delivery-windows` full-scan command — a *different* function from Task 5's new `format_weekly_delivery_window_summary`) also has a now-inaccurate label at line 506:

```python
        f"Pushed 2 weeks (no date yet, was about to lock): {result['pushed']}",
```

Change it to:

```python
        f"Pushed 1 week (no date yet, was about to lock): {result['pushed']}",
```

The `result['pushed']` key itself is unchanged, but `run_delivery_window_sync`'s `_bump` helper (`delivery_window_sync.py:455-457`) must be updated too, or the mapping silently falls through to `outcome` itself and `totals["push_one_week"]` would accumulate instead of `totals["pushed"]`, breaking this summary line. Change:

```python
    def _bump(outcome):
        key = {"edit": "updated", "push_two_weeks": "pushed"}.get(outcome, outcome)
        totals[key] = totals.get(key, 0) + 1
```

to:

```python
    def _bump(outcome):
        key = {"edit": "updated", "push_one_week": "pushed"}.get(outcome, outcome)
        totals[key] = totals.get(key, 0) + 1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_delivery_window_sync.py -v`
Expected: all PASS (this also re-verifies every other test in the file wasn't broken by the rename)

- [ ] **Step 6: Search for any remaining `push_two_weeks` reference**

```bash
grep -rn "push_two_weeks" --include=*.py . | grep -v __pycache__
```

Expected: no output. If anything remains (e.g. `run.py`'s summary printing, `sync_window_for_shipment`'s docstring), fix it now before committing.

- [ ] **Step 7: Commit**

```bash
git add delivery_window_sync.py tests/test_delivery_window_sync.py
git commit -m "refactor: push_two_weeks -> push_one_week, target the window's own start"
```

---

## Task 3: `sync_window_for_shipment` returns the window dates for persistence

**Files:**
- Modify: `delivery_window_sync.py` (`sync_window_for_shipment`, ~lines 368-416 pre-Task-2 numbering — re-locate after Task 2's edits)
- Test: `tests/test_delivery_window_sync.py`

**Interfaces:**
- Consumes: `read_shipment_window(...) -> {"window_start": date, "window_end": date} | None` (unchanged), `decide_window_action(...) -> {"action": str, "target_week_start": date | None}` (Task 2's version), `apply_window_edit(...) -> "edited" | "carrier_managed" | "failed"` (unchanged, already tri-state from v0.8.2).
- Produces: `sync_window_for_shipment(...)` result dict gains two new keys on every outcome: `"window_start"` and `"window_end"` (both `date | None`) — the *live* window as last read from Amazon (or the *new* target window on a successful `edit`/`push_one_week`), so the caller (Task 5) can persist it without a second read. `None` for both only on `read_failed` (nothing was ever read).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_delivery_window_sync.py`, near the existing `sync_window_for_shipment` tests:

```python
@pytest.mark.unit
def test_sync_window_for_shipment_matched_includes_window_dates(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 9, 15), today=date(2026, 8, 10),
    )
    assert result["window_start"] == date(2026, 9, 13)
    assert result["window_end"] == date(2026, 9, 19)


@pytest.mark.unit
def test_sync_window_for_shipment_read_failed_has_none_window_dates(monkeypatch):
    monkeypatch.setattr(delivery_window_sync, "read_shipment_window", lambda *a, **kw: None)
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result["window_start"] is None
    assert result["window_end"] is None


@pytest.mark.unit
def test_sync_window_for_shipment_edit_success_returns_new_target_window(monkeypatch):
    """On a successful edit, the caller needs the NEW window (what it was
    just changed to), not the stale one that was read before the edit --
    that's what gets persisted to the master sheet."""
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target, **kw: "edited")
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 1),
    )
    # target_week_start for this expected_delivery_date/today pair is Aug 2, 2026
    # (matches test_decide_window_action_edit_when_expected_date_before_window)
    assert result["window_start"] == date(2026, 8, 2)
    assert result["window_end"] == date(2026, 8, 8)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_delivery_window_sync.py -k "includes_window_dates or has_none_window_dates or returns_new_target_window" -v`
Expected: FAIL — `KeyError: 'window_start'`

- [ ] **Step 3: Implement**

In `sync_window_for_shipment`, thread the window dates through every return point, and update the docstring (it still says `push_two_weeks` from before Task 2 — Task 2's Step 6 grep should already have caught this, but confirm it here too). The full function after this change:

```python
def sync_window_for_shipment(page, base_url: str, fba_id: str, workflow_id: str, expected_delivery_date, today, logs_folder: str = None) -> dict:
    """
    Reads fba_id's current delivery window, decides what to do via
    decide_window_action(), and applies an edit if one is called for.
    Returns {"outcome": ..., "new_delivery_date_status": "updated" | "pending",
    "window_start": date | None, "window_end": date | None} -- the window
    dates are the live-read window on every outcome except a successful
    "edit"/"push_one_week", where they're the *new* target window instead
    (what the shipment now shows on Amazon), so the caller can persist
    whichever is current without a second read. Both None only on
    "read_failed" (nothing was ever read).

    Outcomes: "read_failed" (couldn't read the current window), "matched"
    (expected date already inside the window -- confirmed correct, no edit
    needed), "no_action_needed" (no expected date yet, window not urgent),
    "locked" (window's start date has passed, can't be edited), "edit" /
    "push_one_week" (the corresponding decide_window_action action was
    successfully applied), "carrier_managed" (the shipment's carrier owns
    delivery-window updates -- Amazon disables manual edits for it, so this
    isn't a failure, just not ours to touch), "edit_failed" (the live edit
    didn't go through for any other reason).

    Status is "updated" only for "matched" and a successful "edit" -- both
    mean the window now demonstrably reflects a real expected date.
    "push_one_week" stays "pending": it's a nudge re-verified next week,
    not a real resolution.
    """
    window = read_shipment_window(page, workflow_id, fba_id, base_url, logs_folder=logs_folder)
    if window is None:
        return {"outcome": "read_failed", "new_delivery_date_status": "pending",
                "window_start": None, "window_end": None}

    decision = decide_window_action(window["window_start"], window["window_end"], expected_delivery_date, today)
    action = decision["action"]

    if action == "locked":
        return {"outcome": "locked", "new_delivery_date_status": "pending",
                "window_start": window["window_start"], "window_end": window["window_end"]}

    if action == "none":
        # A stale (strictly-past) expected date doesn't confirm the window is
        # correct -- decide_window_action() ignored it the same way -- so
        # "matched" would overclaim confidence we don't actually have.
        has_usable_expected_date = expected_delivery_date is not None and expected_delivery_date >= today
        outcome = "matched" if has_usable_expected_date else "no_action_needed"
        status = "updated" if has_usable_expected_date else "pending"
        return {"outcome": outcome, "new_delivery_date_status": status,
                "window_start": window["window_start"], "window_end": window["window_end"]}

    # action is "edit" or "push_one_week"
    edit_result = apply_window_edit(page, decision["target_week_start"], fba_id=fba_id, logs_folder=logs_folder)
    target_start = decision["target_week_start"]
    target_end = target_start + timedelta(days=6) if target_start else None
    if edit_result == "carrier_managed":
        return {"outcome": "carrier_managed", "new_delivery_date_status": "pending",
                "window_start": window["window_start"], "window_end": window["window_end"]}
    if edit_result != "edited":
        return {"outcome": "edit_failed", "new_delivery_date_status": "pending",
                "window_start": window["window_start"], "window_end": window["window_end"]}
    status = "updated" if action == "edit" else "pending"
    return {"outcome": action, "new_delivery_date_status": status,
            "window_start": target_start, "window_end": target_end}
```

Note: `carrier_managed` and `edit_failed` return the *stale* (pre-edit) window dates — the edit never actually went through, so what's still showing on Amazon is what was last read, not the target that was attempted. Only a genuinely successful `edit`/`push_one_week` returns the *new* target window.

`timedelta` must be imported at the top of `delivery_window_sync.py` if it isn't already (`from datetime import datetime, timedelta` — check first; it's likely already there from `apply_window_edit`'s existing month math).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_delivery_window_sync.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add delivery_window_sync.py tests/test_delivery_window_sync.py
git commit -m "feat: sync_window_for_shipment returns window dates for persistence"
```

---

## Task 4: `select_weekly_candidates` — pure, browser-free filter

**Files:**
- Modify: `delivery_window_sync.py` (new function, no browser/openpyxl dependency — pure logic)
- Test: `tests/test_delivery_window_sync.py`

**Interfaces:**
- Consumes: `sheet: dict` (same shape `load_master_sheet` returns — `{fba_id: {field: value}}`, with `delivery_window_start` as a `"YYYY-MM-DD"` string or `""`), `today: date`.
- Produces: `select_weekly_candidates(sheet: dict, today: date) -> dict` returning:
  ```python
  {
      "candidates": [fba_id, ...],       # needs a browser check this run
      "overdue": {fba_id, ...},          # subset of candidates whose recorded window_start already passed
      "not_due": [fba_id, ...],          # window recorded, further than 7 days out -- skipped
      "no_workflow": [fba_id, ...],      # no workflow_id -- skipped (needs discovery first)
      "carrier_managed": [fba_id, ...],  # delivery_date_status == "carrier_managed" -- skipped permanently
  }
  ```
  Rows with `tracking_status == "Delivered"` or `delivery_date_status == "Delivered"` are excluded from every bucket entirely.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_delivery_window_sync.py`:

```python
# --- select_weekly_candidates ------------------------------------------------

def _row(**overrides):
    row = {
        "fba_id": "FBA_DEFAULT", "workflow_id": "wf-1",
        "tracking_status": "pending", "delivery_date_status": "pending",
        "delivery_window_start": "", "delivery_window_end": "",
    }
    row.update(overrides)
    return row


@pytest.mark.unit
def test_select_weekly_candidates_includes_never_checked_shipment():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="")}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_includes_window_starting_within_seven_days():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="2026-08-30")}  # +1 day
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_includes_window_starting_exactly_seven_days_out():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="2026-09-05")}  # +7 days
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_excludes_window_starting_eight_days_out():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="2026-09-06")}  # +8 days
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == []
    assert result["not_due"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_flags_past_window_start_as_overdue_but_still_a_candidate():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="2026-08-20")}  # in the past
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]
    assert result["overdue"] == {"FBA001"}


@pytest.mark.unit
def test_select_weekly_candidates_excludes_delivered_shipments_entirely():
    sheet = {
        "FBA001": _row(fba_id="FBA001", tracking_status="Delivered", delivery_window_start=""),
        "FBA002": _row(fba_id="FBA002", delivery_date_status="Delivered", delivery_window_start="2026-08-30"),
    }
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    for bucket in result.values():
        assert "FBA001" not in bucket
        assert "FBA002" not in bucket


@pytest.mark.unit
def test_select_weekly_candidates_skips_missing_workflow_id():
    sheet = {"FBA001": _row(fba_id="FBA001", workflow_id="", delivery_window_start="")}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == []
    assert result["no_workflow"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_skips_carrier_managed_permanently():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_date_status="carrier_managed", delivery_window_start="2026-08-30")}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == []
    assert result["carrier_managed"] == ["FBA001"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_delivery_window_sync.py -k select_weekly_candidates -v`
Expected: FAIL — `ImportError`/`NameError` (function doesn't exist yet). Add `select_weekly_candidates` to this test file's import block at the top first (`from delivery_window_sync import (..., select_weekly_candidates)`), then re-run to confirm the real failure mode.

- [ ] **Step 3: Implement**

Add to `delivery_window_sync.py` (near `decide_window_action`, since it's the same kind of pure decision logic):

```python
def select_weekly_candidates(sheet: dict, today) -> dict:
    """
    Browser-free local filter deciding which master-sheet rows need a live
    Amazon check this week: never-checked rows, and rows whose recorded
    delivery window starts within the next 7 days (about to lock). Rows with
    a window recorded further out are skipped -- they'll surface again once
    they're within 7 days on a future run. Rows already Delivered are
    excluded entirely; rows already flagged carrier-managed or missing a
    Workflow ID are skipped (the latter needs discovery first, run
    separately before this filter).
    """
    candidates = []
    overdue = set()
    not_due = []
    no_workflow = []
    carrier_managed = []

    for fba_id, entry in sheet.items():
        if entry.get("tracking_status") == "Delivered" or entry.get("delivery_date_status") == "Delivered":
            continue
        if entry.get("delivery_date_status") == "carrier_managed":
            carrier_managed.append(fba_id)
            continue
        if not entry.get("workflow_id"):
            no_workflow.append(fba_id)
            continue

        window_start_str = entry.get("delivery_window_start") or ""
        if not window_start_str:
            candidates.append(fba_id)
            continue

        window_start = datetime.strptime(window_start_str, "%Y-%m-%d").date()
        days_out = (window_start - today).days
        if days_out < 0:
            candidates.append(fba_id)
            overdue.add(fba_id)
        elif days_out <= 7:
            candidates.append(fba_id)
        else:
            not_due.append(fba_id)

    return {
        "candidates": candidates,
        "overdue": overdue,
        "not_due": not_due,
        "no_workflow": no_workflow,
        "carrier_managed": carrier_managed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_delivery_window_sync.py -k select_weekly_candidates -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add delivery_window_sync.py tests/test_delivery_window_sync.py
git commit -m "feat: add select_weekly_candidates, the local pre-filter for the weekly sync"
```

---

## Task 5: `run_weekly_delivery_window_sync` orchestration + summary formatting

**Files:**
- Modify: `delivery_window_sync.py` (two new functions: `run_weekly_delivery_window_sync`, `format_weekly_delivery_window_summary`)
- Test: `tests/test_delivery_window_sync.py` (unit-test `format_weekly_delivery_window_summary` only — `run_weekly_delivery_window_sync` itself needs a real/fake browser and is left to live verification, matching this file's existing convention: `run_delivery_window_sync` has no direct unit test either, only its pure helpers do)

**Interfaces:**
- Consumes: `run_check_tracking(config) -> CheckTrackingResult` (`tracking_status.py`, existing), `run_workflow_discovery(config) -> dict` (`workflow_discovery.py`, existing — already saves discovered `workflow_id`s to the master sheet itself), `select_weekly_candidates(sheet, today) -> dict` (Task 4), `sync_window_for_shipment(...) -> dict` with `window_start`/`window_end` keys (Task 3), `load_master_sheet`/`save_master_sheet` (`master_sheet.py`).
- Produces: `run_weekly_delivery_window_sync(config: dict) -> dict` returning a totals dict (see Step 3 below) plus `"new_shipments": [fba_id, ...]` and `"overdue_shipments": [fba_id, ...]` and `"errors": [str, ...]`. `format_weekly_delivery_window_summary(result: dict) -> str` (pure formatter, unit-tested).

- [ ] **Step 1: Write the failing test for the summary formatter**

```python
@pytest.mark.unit
def test_format_weekly_delivery_window_summary_includes_all_sections():
    text = format_weekly_delivery_window_summary({
        "checked": 14, "not_due": 121, "carrier_managed_skipped": 6,
        "matched": 3, "edited": 2, "pushed_one_week": 5,
        "no_action_needed": 1,
        "new_shipments": ["FBA19ABCDEF1", "FBA19ABCDEF2"],
        "overdue_shipments": ["FBA19XYZ1234"],
        "locked": 0, "read_failed": 2,
        "read_failed_ids": ["FBA15GDQMSCT", "FBA15GDT80ZL"],
        "edit_failed": 1, "edit_failed_ids": ["FBA15GDT80ZL"],
        "errors": [],
    })
    assert "Checked this week" in text
    assert "14" in text
    assert "FBA19ABCDEF1" in text
    assert "FBA19XYZ1234" in text
    assert "FBA15GDQMSCT" in text
    assert "Edit failed" in text


@pytest.mark.unit
def test_format_weekly_delivery_window_summary_includes_errors_section_when_present():
    text = format_weekly_delivery_window_summary({
        "checked": 0, "not_due": 0, "carrier_managed_skipped": 0,
        "matched": 0, "edited": 0, "pushed_one_week": 0, "no_action_needed": 0,
        "new_shipments": [], "overdue_shipments": [], "locked": 0,
        "read_failed": 0, "read_failed_ids": [],
        "edit_failed": 0, "edit_failed_ids": [],
        "errors": ["Could not log in to CA -- skipped 3 shipment(s)"],
    })
    assert "Could not log in to CA" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_delivery_window_sync.py -k format_weekly_delivery_window_summary -v`
Expected: FAIL — `ImportError`/`NameError`

- [ ] **Step 3: Implement `format_weekly_delivery_window_summary`**

```python
def format_weekly_delivery_window_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        f"WEEKLY DELIVERY WINDOW SYNC SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        f"Checked this week          : {result['checked']}   (window starting within 7 days, or never checked)",
        f"Skipped (not due)          : {result['not_due']}  (window further out -- no browser visit needed)",
        f"Skipped (carrier-managed)  : {result['carrier_managed_skipped']}",
        "",
        f"Matched (already correct)  : {result['matched']}",
        f"Edited (moved to real date): {result['edited']}",
        f"Pushed 1 week (no date yet): {result['pushed_one_week']}",
    ]
    new_shipments = result.get("new_shipments", [])
    lines.append(f"Newly discovered & recorded: {len(new_shipments)}" + (f"   -> {', '.join(new_shipments)}" if new_shipments else ""))
    overdue = result.get("overdue_shipments", [])
    lines.append(f"Overdue (missed lock / needs attention): {len(overdue)}" + (f"  -> {', '.join(overdue)}" if overdue else ""))
    lines.append(f"Locked (can't be edited):   {result['locked']}")
    lines.append(f"No action needed:           {result.get('no_action_needed', 0)}")
    read_failed_ids = result.get("read_failed_ids", [])
    lines.append(f"Read failed:                {result['read_failed']}" + (f"   -> {', '.join(read_failed_ids)}" if read_failed_ids else ""))
    edit_failed_ids = result.get("edit_failed_ids", [])
    lines.append(f"Edit failed:                {result.get('edit_failed', 0)}" + (f"   -> {', '.join(edit_failed_ids)}" if edit_failed_ids else ""))
    errors = result.get("errors", [])
    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors:
            lines.append(f"  - {e}")
    lines.append("=" * 60)
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_delivery_window_sync.py -k format_weekly_delivery_window_summary -v`
Expected: PASS

- [ ] **Step 5: Implement `run_weekly_delivery_window_sync`**

No new test for this step (browser-dependent orchestration — see Interfaces note above; verified live in Task 9). Add to `delivery_window_sync.py`:

```python
def run_weekly_delivery_window_sync(config: dict) -> dict:
    """
    Standalone entry point for --weekly-delivery-sync. Refreshes carrier data,
    discovers new shipments' Workflow IDs, then only opens a browser page for
    shipments select_weekly_candidates() says are due this week -- persisting
    the live window (or the new target window on a successful edit) back to
    the master sheet after every shipment, and the master sheet itself after
    every region. Also writes logs/weekly_delivery_window_summary_<ts>.txt.
    """
    from datetime import date as _date
    from tracking_status import run_check_tracking
    from workflow_discovery import run_workflow_discovery
    from master_sheet import load_master_sheet, save_master_sheet, MASTER_SHEET_PATH_DEFAULT
    from upload_tracking import create_browser_context
    from run import wait_for_login

    logs_folder = config.get("logs_folder", "logs")
    today = _date.today()
    errors = []

    logger.info("Refreshing carrier tracking data...")
    run_check_tracking(config)

    logger.info("Discovering Workflow IDs for any new shipments...")
    run_workflow_discovery(config)

    path = config.get("master_sheet_path", MASTER_SHEET_PATH_DEFAULT)
    sheet = load_master_sheet(path)
    tracking_cache_path = config.get("tracking_status_cache")
    from tracking_status import load_status_cache, STATUS_CACHE_PATH_DEFAULT
    tracking_cache = load_status_cache(tracking_cache_path or STATUS_CACHE_PATH_DEFAULT)

    selection = select_weekly_candidates(sheet, today)
    candidates = selection["candidates"]
    new_shipments = [fba_id for fba_id in candidates if not sheet[fba_id].get("delivery_window_start")]
    overdue_shipments = sorted(selection["overdue"])

    totals = {
        "checked": len(candidates), "not_due": len(selection["not_due"]),
        "carrier_managed_skipped": len(selection["carrier_managed"]),
        "matched": 0, "edited": 0, "pushed_one_week": 0, "locked": 0,
        "no_action_needed": 0, "edit_failed": 0, "edit_failed_ids": [],
        "read_failed": 0, "read_failed_ids": [],
        "new_shipments": new_shipments, "overdue_shipments": overdue_shipments,
        "errors": errors,
    }
    if not candidates:
        return totals

    region_by_name = {r["name"]: r for r in config.get("regions", [])}
    by_region = {}
    for fba_id in candidates:
        by_region.setdefault(sheet[fba_id].get("region"), []).append(fba_id)

    try:
        playwright, context = create_browser_context(config)
    except RuntimeError as e:
        # e.g. a previous run's Chrome process crashed and left the automation
        # profile locked (spec: Error Handling table) -- report it in the
        # summary instead of crashing the whole scheduled task silently.
        errors.append(str(e))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_text = format_weekly_delivery_window_summary(totals)
        Path(logs_folder).joinpath(f"weekly_delivery_window_summary_{ts}.txt").write_text(summary_text, encoding="utf-8")
        return totals

    try:
        page = context.new_page()
        for region_name, fba_ids in by_region.items():
            region = region_by_name.get(region_name)
            if not region:
                errors.append(f"No config entry for region {region_name!r} -- skipped {len(fba_ids)} shipment(s)")
                continue
            base_url = region["amazon_url"]
            if not wait_for_login(page, region_name, base_url):
                errors.append(f"Could not log in to {region_name} -- skipped {len(fba_ids)} shipment(s)")
                continue

            for fba_id in fba_ids:
                entry = sheet[fba_id]
                tracking = str(entry.get("tracking", "")).strip()
                cached = tracking_cache.get(tracking, {})
                expected_str = cached.get("expected_delivery_date")
                expected_date = _parse_flexible_date(expected_str, today) if expected_str else None

                result = sync_window_for_shipment(
                    page, base_url, fba_id, entry["workflow_id"], expected_date, today, logs_folder=logs_folder
                )
                outcome = result["outcome"]
                if outcome == "read_failed":
                    totals["read_failed"] += 1
                    totals["read_failed_ids"].append(fba_id)
                elif outcome == "edit_failed":
                    totals["edit_failed"] += 1
                    totals["edit_failed_ids"].append(fba_id)
                else:
                    key = {"matched": "matched", "edit": "edited", "push_one_week": "pushed_one_week",
                           "locked": "locked", "no_action_needed": "no_action_needed"}.get(outcome)
                    if key:
                        totals[key] += 1
                    if outcome == "carrier_managed":
                        totals["carrier_managed_skipped"] += 1
                    entry["delivery_date_status"] = result["new_delivery_date_status"] if outcome != "carrier_managed" else "carrier_managed"
                    if result["window_start"]:
                        entry["delivery_window_start"] = result["window_start"].strftime("%Y-%m-%d")
                        entry["delivery_window_end"] = result["window_end"].strftime("%Y-%m-%d")
                        entry["delivery_window_last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M")

            save_master_sheet(path, sheet)
    finally:
        context.close()
        playwright.stop()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_text = format_weekly_delivery_window_summary(totals)
    Path(logs_folder).joinpath(f"weekly_delivery_window_summary_{ts}.txt").write_text(summary_text, encoding="utf-8")
    return totals
```

Every outcome `sync_window_for_shipment` can return (Task 3: `"read_failed"`, `"locked"`, `"matched"`, `"no_action_needed"`, `"edit"`, `"push_one_week"`, `"carrier_managed"`, `"edit_failed"`) now lands in exactly one `totals` bucket — nothing is silently dropped, since the user explicitly wants to see "if anything went wrong" in the summary (`edit_failed` matters here: it's the bucket that would catch a *new* kind of failure distinct from the now-resolved carrier-managed case).

Update `format_weekly_delivery_window_summary` (Step 3 above) to also print `edit_failed`/`edit_failed_ids` and `no_action_needed`, and update this task's two summary tests to pass those keys too — re-run Step 2's tests once Step 5's totals shape is final to confirm the formatter doesn't `KeyError` on the fuller dict.

`Path` must be imported at the top of `delivery_window_sync.py` if not already present (it's used by `_screenshot` already — reuse the existing import).

- [ ] **Step 6: Run the full delivery_window_sync test file**

Run: `python -m pytest tests/test_delivery_window_sync.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add delivery_window_sync.py tests/test_delivery_window_sync.py
git commit -m "feat: add run_weekly_delivery_window_sync orchestration and summary writer"
```

---

## Task 6: Wire `--weekly-delivery-sync` into `run.py`

**Files:**
- Modify: `run.py` (argparse setup near `--sync-delivery-windows`'s definition, and the standalone-mode branch near `if args.sync_delivery_windows:`)
- Test: none (thin CLI wiring around Task 5's already-tested pieces — matches how `--sync-delivery-windows`/`--discover-workflows` themselves have no dedicated CLI-level test, only their underlying functions do)

**Interfaces:**
- Consumes: `run_weekly_delivery_window_sync(config)`, `format_weekly_delivery_window_summary(result)` (Task 5).

- [ ] **Step 1: Find the existing flag and branch to mirror**

```bash
grep -n "sync-delivery-windows\|sync_delivery_windows" run.py
```

- [ ] **Step 2: Add the argparse flag**

Immediately after the existing `--sync-delivery-windows` `add_argument(...)` call:

```python
    parser.add_argument(
        "--weekly-delivery-sync", action="store_true",
        help="Weekly scheduled mode: refresh carrier data, discover new shipments, and only "
             "check/edit delivery windows for shipments locking within 7 days. Writes a summary "
             "file to logs/. Intended to run from Windows Task Scheduler every Saturday.",
    )
```

- [ ] **Step 3: Add the standalone-mode branch**

Immediately after the existing `if args.sync_delivery_windows:` block:

```python
    if args.weekly_delivery_sync:
        from delivery_window_sync import run_weekly_delivery_window_sync, format_weekly_delivery_window_summary
        result = run_weekly_delivery_window_sync(config)
        print(format_weekly_delivery_window_summary(result))
        return
```

- [ ] **Step 4: Sanity-check the flag parses and dispatches**

Run: `python run.py --weekly-delivery-sync --help` (should list the new flag without error) and `python -c "import run"` (should import cleanly, no syntax error).

- [ ] **Step 5: Commit**

```bash
git add run.py
git commit -m "feat: wire --weekly-delivery-sync CLI flag"
```

---

## Task 7: `run_weekly_delivery_sync.bat` (unattended, no `pause`)

**Files:**
- Create: `run_weekly_delivery_sync.bat`

**Interfaces:** none (shell wrapper only).

- [ ] **Step 1: Read `check_tracking.bat` as the style reference**

```bash
cat check_tracking.bat
```

- [ ] **Step 2: Write the wrapper**

```bat
@echo off
title Amazon FBA Weekly Delivery Window Sync
color 0A
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not installed. Run setup.bat first. > logs\weekly_delivery_sync_launch_error.txt
    exit /b 1
)

python run.py --weekly-delivery-sync
```

No `pause` anywhere — this runs unattended from Task Scheduler with nothing present to press Enter; a `pause` would hang the task indefinitely. The Python `--version` failure case writes to a file instead of the console since nobody's watching the console either.

- [ ] **Step 3: Verify it runs standalone**

Run (interactively, from a normal terminal, to confirm the `.bat` itself is well-formed — not yet via Task Scheduler): `run_weekly_delivery_sync.bat`
Expected: launches `python run.py --weekly-delivery-sync` the same as running that command directly.

- [ ] **Step 4: Commit**

```bash
git add run_weekly_delivery_sync.bat
git commit -m "chore: add unattended .bat wrapper for the weekly delivery-window sync task"
```

---

## Task 8: Live verification (manual, not automated)

**Files:** none — this is a live-run checkpoint, not a code change.

- [ ] **Step 1:** With a real (or trimmed, per tonight's earlier BASL-verification pattern) `input/` file present so the master sheet has real rows, run `python run.py --weekly-delivery-sync` directly and confirm:
  - The console output and `logs/weekly_delivery_window_summary_<ts>.txt` both show sensible, matching counts.
  - `logs/shipment_tracking_master.xlsx` now has non-empty `Delivery Window Start`/`End`/`Last Checked` for every candidate that was checked.
  - A shipment already known to have `delivery_date_status == "carrier_managed"` (e.g. a FIST shipment from tonight's earlier session) is *not* re-visited (no browser navigation logged for it) — confirms the local pre-filter is actually skipping it, not just re-discovering `carrier_managed` every time.
  - A shipment whose recorded window is more than 7 days out is *not* visited either.
- [ ] **Step 2:** Re-run it a second time immediately after. Confirm shipments that were just `matched`/`edited` don't flip to some other outcome from a stale read, and nothing crashes on a second consecutive run.
- [ ] **Step 3:** Report the live counts back before moving to Task 9 (Task Scheduler) — that's a machine-level change worth a final go-ahead given the actual behavior is now visible, not just planned.

---

## Task 9: Windows Task Scheduler entry (confirm settings with the user first)

**Files:** none — machine configuration, not part of the git repo.

This task is deliberately last and requires the user's explicit confirmation of two settings before creation, per the spec's Windows Task Scheduler section:

1. **"Run only when user is logged on"** (required for the visible/non-headless Chrome window to actually render — a task running in Session 0 cannot show a GUI window at all). Confirm the user understands: if the PC is powered-on-but-locked at catch-up time, the task still won't run until the account is actually logged in.
2. Exact trigger time confirmed: **Saturday, 10:00 PM**, with **"Run task as soon as possible after a scheduled start is missed"** enabled.

- [ ] **Step 1:** Confirm both settings above with the user in chat before running anything.
- [ ] **Step 2:** Create the task via PowerShell (`Register-ScheduledTask`), pointed at `run_weekly_delivery_sync.bat` in the project directory, using the confirmed trigger and logon-required setting.
- [ ] **Step 3:** Verify the task was created: `Get-ScheduledTask -TaskName "<name>"` shows it, and `Get-ScheduledTaskInfo` shows the correct next run time.
- [ ] **Step 4:** Tell the user how to test it immediately without waiting for Saturday (`Start-ScheduledTask -TaskName "<name>"`), and how to check its last-run result (`Get-ScheduledTaskInfo`'s `LastTaskResult`).
