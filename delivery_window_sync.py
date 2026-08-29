# delivery_window_sync.py
"""
Keeps a shipment's Amazon "Send to Amazon" delivery window in sync with its
real carrier-reported expected delivery date: if the two disagree, moves the
window to cover the real date; if there's no real date yet and the window is
about to lock (Amazon stops allowing edits once a window's start date
arrives), pushes it two weeks out to buy time rather than let it lock on a
guess. Only acts on shipments not already marked "Delivered" in the master
sheet, and skips windows that have already locked.
"""
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _screenshot(page, step_name: str, logs_folder: str) -> None:
    """Saves a screenshot to logs/screenshots/ on error. No-ops without a logs_folder."""
    if not logs_folder:
        return
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_step = "".join(c if c.isalnum() or c in "-_." else "_" for c in step_name)
        page.screenshot(path=str(folder / f"{ts}_{safe_step}.png"))
    except Exception as e:
        logger.debug(f"Screenshot failed ({step_name}): {e}")


# US-style renders "Jul 1, 2026"; EU/FR-region shipments render the
# day-first "1 Jul 2026" (no comma) instead -- both need to be matched here.
_WINDOW_DATE = r"(?:[A-Za-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Za-z]+ \d{4})"
_WINDOW_PATTERN = re.compile(rf"Delivery window:\s*({_WINDOW_DATE})\s*-\s*({_WINDOW_DATE})")

_DATE_FORMATS_WITH_YEAR = [
    "%m/%d/%Y", "%m/%d/%y",
    "%Y-%m-%d",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y",
    "%d %b %Y", "%d %B %Y",
]
_DATE_FORMATS_NO_YEAR = [
    "%A, %B %d", "%a, %b %d",
    "%B %d", "%b %d",
]

# If a no-year date (e.g. "Jan 5") would land more than this many days in the
# past under the current year, it almost certainly means next year instead.
_NO_YEAR_PAST_ROLLOVER_DAYS = 60


def _parse_flexible_date(date_str, today=None):
    """
    Parses a date string in any of the formats carrier pages/APIs have been
    observed to return (see tracking_status.py's _DATE_PATTERN and the FedEx
    API's displayActDeliveryDt/displayEstDeliveryDt fields). Returns a
    datetime.date, or None if date_str is blank or unparseable.

    Formats with no year (e.g. "Friday, July 17", from UPS's delivered banner)
    are assumed to be in `today`'s year, rolling to next year if that would
    place the date more than ~2 months in the past -- a bare month/day this
    far behind almost certainly means the carrier meant next year.
    """
    if not date_str:
        return None
    date_str = str(date_str).strip()
    if not date_str:
        return None
    if today is None:
        today = datetime.now().date()

    for fmt in _DATE_FORMATS_WITH_YEAR:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    for fmt in _DATE_FORMATS_NO_YEAR:
        try:
            # Parse with an explicit placeholder year (1904, a leap year so
            # "Feb 29" doesn't fail either) instead of a year-less format --
            # Python 3.14+ deprecates strptime with no year in the format at
            # all. The real year is inferred and substituted right after.
            parsed = datetime.strptime(f"{date_str} 1904", f"{fmt} %Y")
        except ValueError:
            continue
        candidate = parsed.replace(year=today.year).date()
        if (today - candidate).days > _NO_YEAR_PAST_ROLLOVER_DAYS:
            candidate = candidate.replace(year=today.year + 1)
        return candidate

    return None


def _week_bounds(d):
    """Returns (Sunday, Saturday) of the calendar week containing date d -- matches
    the calendar-week convention Amazon's delivery windows always use."""
    days_since_sunday = (d.weekday() + 1) % 7  # Python: Monday=0 .. Sunday=6
    sunday = d - timedelta(days=days_since_sunday)
    saturday = sunday + timedelta(days=6)
    return sunday, saturday


def decide_window_action(window_start, window_end, expected_delivery_date, today) -> dict:
    """
    Pure decision, no I/O: given a shipment's current Amazon delivery window,
    its real expected delivery date (a date, or None if not known yet), and
    today's date, decides what to do. Returns
    {"action": "locked" | "none" | "edit" | "push_one_week", "target_week_start": date | None}.

    - "locked": the window's start date has already arrived. Amazon's own
      edit cutoff always equals the window's start date (confirmed against
      several real windows), so nothing can be done via this UI anymore.
    - "none": the expected date already falls within the current window, or
      there's no expected date yet and the window isn't starting soon enough
      to need a defensive push.
    - "edit": the expected date is known and falls outside the window -- move
      the window to the calendar week containing it.
    - "push_one_week": no expected date yet, and the window starts within
      the next 7 days (about to lock) -- push it out one week so it doesn't
      lock on a guess; the weekly sync cadence will re-verify this shipment
      next Saturday and can adjust further if needed.

    A strictly-past expected_delivery_date (an overdue "In Transit" package
    whose cached date has already gone by) is treated the same as having no
    expected date at all -- Amazon's calendar won't let us pick a past target
    week, so acting on stale info isn't possible.
    """
    if today >= window_start:
        return {"action": "locked", "target_week_start": None}

    if expected_delivery_date is not None and expected_delivery_date < today:
        expected_delivery_date = None

    if expected_delivery_date is not None:
        if window_start <= expected_delivery_date <= window_end:
            return {"action": "none", "target_week_start": None}
        target_start, _ = _week_bounds(expected_delivery_date)
        return {"action": "edit", "target_week_start": target_start}

    if (window_start - today).days <= 7:
        target_start, _ = _week_bounds(window_start + timedelta(days=7))
        return {"action": "push_one_week", "target_week_start": target_start}

    return {"action": "none", "target_week_start": None}


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


def _dismiss_onboarding_modal(page) -> None:
    """
    Amazon occasionally shows a "Save time with Send to Amazon" onboarding
    tour modal on this page (seen live -- a kat-modal overlay with a single
    close button, aria-label "close"). Its overlay intercepts pointer events
    for everything behind it, so a stray click on "View" times out entirely
    if it's not dismissed first. Absent on most visits -- a short wait, not
    an error, if it never appears.
    """
    modal = page.locator("kat-modal[visible='true']").locator("button[aria-label='close']")
    try:
        modal.first.wait_for(state="visible", timeout=3000)
    except Exception:
        return
    modal.first.click()


def read_shipment_window(page, workflow_id: str, fba_id: str, base_url: str, logs_folder: str = None) -> dict:
    """
    Navigates to the shipment's workflow page, opens the tracking-details
    section, selects fba_id's own tab, and reads its current delivery
    window. Returns {"window_start": date, "window_end": date} on success,
    or None if the workflow, the shipment's tab, or the window text
    couldn't be found/parsed. Saves a screenshot to logs/screenshots/ on
    every failure path so a "never rendered" warning has a page state to
    diagnose against instead of just a guess.
    """
    url = f"{base_url}/fba/sendtoamazon?wf={workflow_id}"
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("load", timeout=15000)
    except Exception as e:
        logger.warning(f"  {fba_id}: failed to load workflow page {url}: {e}")
        _screenshot(page, f"window_load_failed_{fba_id}", logs_folder)
        return None

    _dismiss_onboarding_modal(page)

    views = page.get_by_text("View", exact=True)
    try:
        # The 4 "View" links (Step 1/2/3/Final step) render a moment after the
        # rest of the page paints -- wait for the 4th to actually be there
        # rather than checking count() once against a guessed fixed delay.
        views.nth(3).wait_for(state="visible", timeout=15000)
    except Exception:
        logger.warning(f"  {fba_id}: workflow page never rendered its 'Tracking details' section")
        _screenshot(page, f"window_no_tracking_section_{fba_id}", logs_folder)
        return None
    views.nth(3).click()

    try:
        page.wait_for_selector("text=Track shipment", timeout=15000)
    except Exception:
        logger.warning(f"  {fba_id}: tracking-details section never rendered")
        _screenshot(page, f"window_no_track_shipment_{fba_id}", logs_folder)
        return None

    tab = page.get_by_text(f"Shipment ID: {fba_id}", exact=False)
    if tab.count() == 0:
        logger.warning(f"  {fba_id}: not found among this workflow's shipment tabs")
        _screenshot(page, f"window_tab_not_found_{fba_id}", logs_folder)
        return None
    tab.first.click()

    try:
        # "Delivery window" (no colon) also matches the hidden locked-window
        # tooltip's text ("...the delivery window is not in the future
        # anymore"), which never becomes visible when the window ISN'T
        # locked -- Playwright then waits forever on that wrong match. The
        # colon disambiguates: only the real "Delivery window: <dates>" label has it.
        page.wait_for_selector("text=Delivery window:", timeout=15000)
    except Exception:
        # Confirmed live (2026-08-30): this isn't a timing issue -- some
        # shipments' "Send to Amazon" workflow page never picked up the
        # tracking that was actually entered through the newer inbound-
        # shipment tracking page instead. That tab shows an empty, unfilled
        # "Enter tracking IDs" form with no Delivery window UI at all, no
        # matter how long you wait, because Amazon only renders the window
        # once tracking has been entered *through this same page*. There's
        # no selector fix for a section Amazon isn't rendering -- flag it
        # distinctly so it doesn't get investigated again as a scrape bug.
        stale_workflow = page.get_by_text("Enter tracking IDs", exact=False).count() > 0
        if stale_workflow:
            logger.warning(
                f"  {fba_id}: workflow page shows an empty/unfilled tracking form -- "
                f"tracking for this shipment wasn't entered through this workflow, so "
                f"Amazon never renders a delivery window here (not a scrape failure)"
            )
        else:
            logger.warning(f"  {fba_id}: 'Delivery window' never rendered after selecting its tab")
        _screenshot(page, f"window_no_delivery_window_{fba_id}", logs_folder)
        return None

    match = _WINDOW_PATTERN.search(page.inner_text("body"))
    if not match:
        logger.warning(f"  {fba_id}: 'Delivery window' text found but couldn't be parsed")
        _screenshot(page, f"window_unparseable_{fba_id}", logs_folder)
        return None

    start = _parse_flexible_date(match.group(1))
    end = _parse_flexible_date(match.group(2))
    if start is None or end is None:
        return None
    return {"window_start": start, "window_end": end}


def _current_calendar_month(page) -> tuple:
    """
    Returns (year, month) of the month the edit-window calendar is currently
    showing. Inferred from the "next month" nav button's own aria-label
    (e.g. aria-label="October 2026" on the button that navigates FROM
    September TO October) rather than reading the heading text directly,
    since the heading renders inside a shadow root that a plain text search
    can miss depending on how it's queried; the nav button's accessible name
    is reliably exposed either way.
    """
    aria = page.locator(".cal-rgt").first.get_attribute("aria-label")
    next_month = datetime.strptime(aria, "%B %Y")
    if next_month.month == 1:
        return next_month.year - 1, 12
    return next_month.year, next_month.month - 1


def _navigate_calendar_to_month(page, target_year: int, target_month: int) -> bool:
    """Clicks the edit-window calendar's month arrows until target_year/target_month is shown."""
    for _ in range(24):  # 2 years' worth of clicks, safety cap
        cur_year, cur_month = _current_calendar_month(page)
        if (cur_year, cur_month) == (target_year, target_month):
            return True
        if (target_year, target_month) > (cur_year, cur_month):
            page.locator(".cal-rgt").first.click()
        else:
            page.locator(".cal-lft").first.click()
        page.wait_for_timeout(400)
    return False


def apply_window_edit(page, target_week_start, fba_id: str = "", logs_folder: str = None) -> str:
    """
    Assumes the page is already showing a shipment's own tab with its current
    delivery window (i.e. right after read_shipment_window()). Opens "Edit
    window", navigates the calendar to target_week_start's month if needed,
    clicks that day, and confirms.

    Returns "edited" on success, "carrier_managed" if the modal's "Allow
    <carrier> to update my delivery window" checkbox is checked (Amazon
    permanently disables manual confirmation in that case -- confirmed live:
    the day-selection still works, but "Confirm new delivery window" never
    becomes clickable, because the carrier integration owns the window, not
    us -- so there's no point attempting the edit at all), or "failed" for
    every other failure (the edit modal never opened -- the window turned
    out locked despite our own up-front check, the live UI is the final
    authority -- the target month couldn't be reached, the target day wasn't
    found, or Confirm still didn't become clickable for some other reason).
    Saves a screenshot to logs/screenshots/ on every non-"edited" path.
    """
    edit_link = page.locator("text=Edit window")
    if edit_link.count() == 0:
        logger.warning("  No 'Edit window' link on this shipment's tab")
        _screenshot(page, f"edit_no_link_{fba_id}", logs_folder)
        return "failed"
    edit_link.first.click()

    confirm_btn = page.get_by_text("Confirm new delivery window", exact=True)
    try:
        confirm_btn.wait_for(state="visible", timeout=5000)
    except Exception:
        logger.warning("  'Edit window' did not open a modal -- window is likely locked")
        _screenshot(page, f"edit_no_modal_{fba_id}", logs_folder)
        page.keyboard.press("Escape")
        return "failed"

    # Checked by default whenever the shipment's carrier supports it (seen
    # live for "FIST Carriers"). While checked, Confirm never becomes
    # clickable no matter what day is selected -- Amazon expects the
    # carrier's own integration to push window updates, not a manual save.
    # Checking for it up front avoids wasting a full calendar navigation +
    # 10s confirm-timeout on a shipment we can never actually edit.
    carrier_checkbox = page.get_by_role("checkbox", name=re.compile("update my delivery window", re.IGNORECASE))
    try:
        if carrier_checkbox.count() > 0 and carrier_checkbox.first.is_checked():
            logger.info(f"  {fba_id}: carrier-managed delivery window (auto-update checkbox checked) -- skipping")
            _screenshot(page, f"edit_carrier_managed_{fba_id}", logs_folder)
            page.keyboard.press("Escape")
            return "carrier_managed"
    except Exception as e:
        logger.debug(f"  {fba_id}: carrier-managed checkbox check failed (continuing): {e}")

    if not _navigate_calendar_to_month(page, target_week_start.year, target_week_start.month):
        logger.warning(f"  Could not navigate the calendar to {target_week_start.strftime('%B %Y')}")
        _screenshot(page, f"edit_month_nav_failed_{fba_id}", logs_folder)
        page.keyboard.press("Escape")
        return "failed"

    day_label = f"{target_week_start.strftime('%B')} {target_week_start.day}, {target_week_start.year}"
    day_btn = page.get_by_role("button", name=day_label, exact=False)
    if day_btn.count() == 0:
        logger.warning(f"  Target day {day_label!r} not found or not selectable")
        _screenshot(page, f"edit_day_not_found_{fba_id}", logs_folder)
        page.keyboard.press("Escape")
        return "failed"
    day_btn.first.click()
    page.wait_for_timeout(500)
    # Captured before the confirm-click attempt (not just on failure) so a
    # disabled-button failure can be compared against what the calendar
    # actually looked like right after the day was clicked.
    _screenshot(page, f"edit_after_day_click_{fba_id}", logs_folder)

    try:
        confirm_btn.click(timeout=10000)
    except Exception as e:
        logger.warning(f"  {fba_id}: 'Confirm new delivery window' never became clickable: {e}")
        _screenshot(page, f"edit_confirm_disabled_{fba_id}", logs_folder)
        page.keyboard.press("Escape")
        return "failed"
    page.wait_for_timeout(2000)
    return "edited"


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


def run_delivery_window_sync(config: dict) -> dict:
    """
    For every master-sheet shipment not already "Delivered" and with a known
    Workflow ID, syncs its Amazon delivery window against its real carrier-
    reported expected delivery date -- pulled from logs/tracking_status.xlsx's
    cache (matched by tracking number), not re-checked live here. One region
    at a time (its own browser login); saves the master sheet after each
    region so progress survives a mid-run crash.
    Returns counts by outcome (see sync_window_for_shipment's docstring).
    """
    from datetime import date as _date
    from master_sheet import load_master_sheet, save_master_sheet, MASTER_SHEET_PATH_DEFAULT
    from tracking_status import load_status_cache, STATUS_CACHE_PATH_DEFAULT
    from upload_tracking import create_browser_context
    from run import wait_for_login

    path = config.get("master_sheet_path", MASTER_SHEET_PATH_DEFAULT)
    sheet = load_master_sheet(path)
    tracking_cache_path = config.get("tracking_status_cache", STATUS_CACHE_PATH_DEFAULT)
    tracking_cache = load_status_cache(tracking_cache_path)
    region_by_name = {r["name"]: r for r in config.get("regions", [])}
    logs_folder = config.get("logs_folder", "logs")
    today = _date.today()

    pending_by_region = {}
    for fba_id, entry in sheet.items():
        if entry.get("tracking_status") == "Delivered" or entry.get("delivery_date_status") == "Delivered":
            continue
        if not entry.get("workflow_id"):
            continue
        pending_by_region.setdefault(entry.get("region"), []).append(fba_id)

    totals = {"matched": 0, "updated": 0, "pushed": 0, "locked": 0, "no_action_needed": 0, "carrier_managed": 0, "read_failed": 0, "edit_failed": 0}
    if not pending_by_region:
        return totals

    def _bump(outcome):
        key = {"edit": "updated", "push_one_week": "pushed"}.get(outcome, outcome)
        totals[key] = totals.get(key, 0) + 1

    playwright, context = create_browser_context(config)
    try:
        page = context.new_page()
        for region_name, fba_ids in pending_by_region.items():
            region = region_by_name.get(region_name)
            if not region:
                logger.warning(f"No config entry for region {region_name!r} -- skipping {len(fba_ids)} shipment(s)")
                for _ in fba_ids:
                    _bump("read_failed")
                continue

            base_url = region["amazon_url"]
            if not wait_for_login(page, region_name, base_url):
                logger.warning(f"Could not log in to {region_name} -- skipping {len(fba_ids)} shipment(s)")
                for _ in fba_ids:
                    _bump("read_failed")
                continue

            for fba_id in fba_ids:
                entry = sheet[fba_id]
                tracking = str(entry.get("tracking", "")).strip()
                cached = tracking_cache.get(tracking, {})
                expected_str = cached.get("expected_delivery_date")
                expected_date = _parse_flexible_date(expected_str, today) if expected_str else None

                result = sync_window_for_shipment(page, base_url, fba_id, entry["workflow_id"], expected_date, today, logs_folder=logs_folder)
                _bump(result["outcome"])
                entry["delivery_date_status"] = result["new_delivery_date_status"]

            save_master_sheet(path, sheet)
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    return totals


def format_delivery_window_sync_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        "DELIVERY WINDOW SYNC SUMMARY",
        "=" * 60,
        f"Matched (already correct):  {result['matched']}",
        f"Updated (edited to match):  {result['updated']}",
        f"Pushed 1 week (no date yet, was about to lock): {result['pushed']}",
        f"No action needed:           {result['no_action_needed']}",
        f"Locked (can't be edited):   {result['locked']}",
        f"Carrier-managed (skipped):  {result.get('carrier_managed', 0)}",
        f"Read failed:                {result['read_failed']}",
        f"Edit failed:                {result['edit_failed']}",
        "=" * 60,
    ]
    return "\n".join(lines)
