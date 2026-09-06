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

from master_sheet import is_terminal_shipment_status as _is_terminal_shipment_status

logger = logging.getLogger(__name__)


def _screenshot(page, step_name: str, logs_folder: str) -> None:
    """
    Saves a screenshot to logs/screenshots/ on error. No-ops without a
    logs_folder. Captures the full scrollable page, not just the viewport --
    a viewport-only capture missed the actual failure point entirely on a
    real "never rendered" case (confirmed live 2026-09-02, FBA19GR6H9VX):
    the relevant section was below the fold.
    """
    if not logs_folder:
        return
    try:
        folder = Path(logs_folder) / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_step = "".join(c if c.isalnum() or c in "-_." else "_" for c in step_name)
        page.screenshot(path=str(folder / f"{ts}_{safe_step}.png"), full_page=True)
    except Exception as e:
        logger.debug(f"Screenshot failed ({step_name}): {e}")


# US-style renders "Jul 1, 2026"; EU/FR-region shipments render the
# day-first "1 Jul 2026" (no comma) instead -- both need to be matched here.
_WINDOW_DATE = r"(?:[A-Za-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Za-z]+ \d{4})"
_WINDOW_PATTERN = re.compile(rf"Delivery window:\s*({_WINDOW_DATE})\s*-\s*({_WINDOW_DATE})")

# LTL/FTL shipments (Method: "Less than and full truckload") render their
# delivery window as the VALUE ATTRIBUTE of a disabled <kat-input
# data-testid="arrival-delivery-window-input">, e.g. "Sep 20 - Sep 26, 2026"
# -- confirmed live (2026-09-07, FBA19M5MX8MR). Unlike the plain-text
# "Delivery window: <date>, <year> - <date>, <year>" label the standard SPD
# flow renders, this is never a text node at all (inner_text/get_by_text
# can't see it), and only the END date carries a year.
_LTL_WINDOW_INPUT_SELECTOR = "kat-input[data-testid='arrival-delivery-window-input']"


def _parse_ltl_window_input_value(value: str, fba_id: str = "", today=None) -> dict:
    """
    Parses the LTL/FTL delivery-window <kat-input> value (e.g.
    "Sep 20 - Sep 26, 2026") into {"window_start": date, "window_end": date}.
    The start date has no year of its own -- inferred the same way
    _parse_flexible_date already infers a bare month/day's year elsewhere.
    Returns None if the value doesn't split into exactly two parseable dates.
    """
    if not value:
        return None
    parts = [p.strip() for p in value.split(" - ")]
    if len(parts) != 2:
        logger.warning(f"  {fba_id}: LTL delivery-window input value in unexpected shape: {value!r}")
        return None
    start = _parse_flexible_date(parts[0], today=today)
    end = _parse_flexible_date(parts[1], today=today)
    if start is None or end is None:
        logger.warning(f"  {fba_id}: LTL delivery-window input value couldn't be parsed: {value!r}")
        return None
    return {"window_start": start, "window_end": end}


def _read_ltl_style_window(page, tab, fba_id: str) -> dict:
    """
    Fallback for LTL/FTL shipments: reads the delivery window from the
    <kat-input> described above instead of the plain-text label the standard
    SPD flow uses. `tab` is the already-located "Shipment ID: {fba_id}"
    locator -- scoped up to its enclosing shipment card (the smallest common
    ancestor confirmed live to bound exactly one shipment's own window,
    2026-09-07) so the right sibling shipment's window is read, not
    whichever renders first on the page. Returns None if this shipment has
    no such input (i.e. it's genuinely not an LTL/FTL-style page).
    """
    card = tab.first.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' shipment-module ')][1]")
    window_input = card.locator(_LTL_WINDOW_INPUT_SELECTOR)
    if window_input.count() == 0:
        return None
    value = window_input.first.get_attribute("value")
    return _parse_ltl_window_input_value(value, fba_id=fba_id)

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
    # UK/EU "Delivery window:" pages spell September as the 4-letter "Sept"
    # instead of the 3-letter "%b" abbreviation every other month uses --
    # confirmed live (2026-09-02) on amazon.co.uk and amazon.de. Normalize
    # before matching rather than adding a whole extra format list entry.
    date_str = re.sub(r"\bSept\b", "Sep", date_str, flags=re.IGNORECASE)
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
      the next 6 days (about to lock) -- push it out to the week right after
      the window's real end (whatever that window's actual length is) so it
      doesn't lock on a guess; the weekly sync cadence will re-verify this
      shipment next Saturday and can adjust further if needed. A window
      exactly 7 days out is deliberately left alone: it still has a full
      week of runway, and treating it as urgent made a run that slipped one
      day past its Saturday schedule push shipments a week early (confirmed
      live 2026-09-06, FBA15M2N9CHZ/FBA15M85HW20).

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

    if (window_start - today).days < 7:
        target_start, _ = _week_bounds(window_end + timedelta(days=1))
        return {"action": "push_one_week", "target_week_start": target_start}

    return {"action": "none", "target_week_start": None}


def _merge_overdue_with_newly_locked(pre_run_overdue: set, this_run_outcomes: dict) -> list:
    """
    Merges pre-run overdue shipments with any newly discovered locked outcomes this run.

    Args:
        pre_run_overdue: Set of FBA IDs that were already overdue before this run
                         (window_start < today at time of run start).
        this_run_outcomes: Dict mapping FBA ID to outcome string for every candidate checked.

    Returns:
        Sorted list of all FBA IDs that should be flagged as overdue in the summary:
        the union of pre_run_overdue and any FBA IDs with a "locked" outcome this run.
    """
    newly_locked = {fba_id for fba_id, outcome in this_run_outcomes.items() if outcome == "locked"}
    return sorted(pre_run_overdue | newly_locked)


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

        window_start_raw = entry.get("delivery_window_start") or ""
        if hasattr(window_start_raw, "strftime"):
            # openpyxl returns a real datetime/date object instead of a string
            # if Excel auto-converted an ISO-looking text cell on save -- a
            # real risk since the master sheet is a file the user opens and
            # re-saves in Excel.
            window_start_str = window_start_raw.strftime("%Y-%m-%d")
        else:
            window_start_str = str(window_start_raw).strip()

        if not window_start_str:
            candidates.append(fba_id)
            continue

        try:
            window_start = datetime.strptime(window_start_str, "%Y-%m-%d").date()
        except ValueError:
            # A malformed/unparseable value shouldn't crash the whole run --
            # treat it the same as "never checked" so it gets a fresh live read.
            candidates.append(fba_id)
            continue
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
        # Each collapsed step (Step 1, Step 1b, Step 2, Step 3, Final step...)
        # has its own "View" link, and they render a moment after the rest of
        # the page paints -- wait for the last one to actually be there rather
        # than checking count() once against a guessed fixed delay. The step
        # count varies by shipment method -- confirmed live (2026-09-06,
        # FBA19L4ZZS14): SPD/FIST-Carrier workflows give Step 1b its own
        # separate View, making 5 steps instead of the usual 4, so a hardcoded
        # nth(3) landed on Step 3 instead of Final step. "Final step" is
        # always the last collapsed section regardless of how many precede
        # it, so .last is robust to that variation.
        views.last.wait_for(state="visible", timeout=15000)
    except Exception:
        # Confirmed live (2026-09-01): same root cause as the empty-tracking-
        # form case below, just caught one step earlier -- when tracking was
        # entered through the newer inbound-shipment tracking page instead of
        # this workflow, Step 4 here never gets confirmed, so it's still
        # showing the raw "Tracking information must be provided" carrier
        # form instead of collapsing into a "View" summary link. There's no
        # Final-step View link to wait for in that case; flag it distinctly
        # so it isn't chased as a scrape/timing bug.
        stale_workflow = page.get_by_text("Tracking information must be provided", exact=False).count() > 0
        if stale_workflow:
            logger.warning(
                f"  {fba_id}: workflow page's Step 4 still shows an unconfirmed carrier form -- "
                f"tracking for this shipment wasn't entered through this workflow, so "
                f"Amazon never renders a delivery window here (not a scrape failure)"
            )
        else:
            logger.warning(f"  {fba_id}: workflow page never rendered its 'Tracking details' section")
        _screenshot(page, f"window_no_tracking_section_{fba_id}", logs_folder)
        return None
    views.last.click()

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
        # Before giving up, check for the LTL/FTL-style window (a <kat-input>
        # value attribute, never a text node -- see _read_ltl_style_window)
        # -- confirmed live 2026-09-07, FBA19M5MX8MR. This is why
        # "text=Delivery window:" never appears for these shipments even
        # though a real window is right there on the page.
        ltl_result = _read_ltl_style_window(page, tab, fba_id)
        if ltl_result:
            return ltl_result

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
        # The regex matched -- Amazon rendered a "Delivery window: X - Y"
        # label -- but the date text inside it didn't fit any known format.
        # Distinct from the "couldn't be parsed" case above (where the whole
        # label never matched): here we at least have the raw text, so log
        # it verbatim instead of silently returning None with no trace.
        logger.warning(
            f"  {fba_id}: 'Delivery window' dates found but couldn't be parsed: "
            f"{match.group(1)!r} - {match.group(2)!r}"
        )
        _screenshot(page, f"window_dates_unparseable_{fba_id}", logs_folder)
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


def _read_ltl_carrier_managed_checkbox(page, fba_id: str):
    """
    LTL/FTL shipments have no "Edit window" control at all -- there's no
    modal to open. Their own "Allow <carrier> to update my delivery window"
    checkbox sits disabled right next to the window's <kat-input> instead
    (confirmed live 2026-09-07, FBA19M5MX8MR: 5 sibling shipments, all
    Carrier: FIST Carriers, all checked). Checked means carrier-managed, the
    same meaning as the standard flow's modal checkbox -- just discoverable
    without ever finding an "Edit window" link to click. Scoped to fba_id's
    own card via the same .shipment-module ancestor _read_ltl_style_window
    uses, since a workflow page can list several sibling shipments' checkboxes
    at once. Returns None if this shipment has no such checkbox at all (not
    an LTL/FTL-style page for this shipment).
    """
    tab = page.get_by_text(f"Shipment ID: {fba_id}", exact=False)
    if tab.count() == 0:
        return None
    card = tab.first.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' shipment-module ')][1]")
    checkbox = card.get_by_role("checkbox", name=re.compile("update my delivery window", re.IGNORECASE))
    if checkbox.count() == 0:
        return None
    return checkbox.first.is_checked()


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
    us -- so there's no point attempting the edit at all) -- or the LTL/FTL
    equivalent checkbox is checked (see _read_ltl_carrier_managed_checkbox;
    LTL/FTL has no "Edit window" link to find at all, so this is checked as
    a fallback rather than after opening a modal that doesn't exist here) --
    or "failed" for every other failure (the edit modal never opened -- the
    window turned out locked despite our own up-front check, the live UI is
    the final authority -- the target month couldn't be reached, the target
    day wasn't found, or Confirm still didn't become clickable for some
    other reason). Saves a screenshot to logs/screenshots/ on every
    non-"edited" path.
    """
    edit_link = page.locator("text=Edit window")
    if edit_link.count() == 0:
        ltl_carrier_managed = _read_ltl_carrier_managed_checkbox(page, fba_id)
        if ltl_carrier_managed is True:
            logger.info(f"  {fba_id}: LTL/FTL carrier-managed delivery window (auto-update checkbox checked) -- skipping")
            _screenshot(page, f"edit_ltl_carrier_managed_{fba_id}", logs_folder)
            return "carrier_managed"
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


def _select_delivery_window_sync_candidates(sheet: dict) -> dict:
    """
    Browser-free filter for run_delivery_window_sync (the old ad-hoc
    --sync-delivery-windows command): every master-sheet row not already
    Delivered, not flagged carrier-managed (Amazon owns that window
    permanently -- the newer weekly sync's select_weekly_candidates already
    excludes these; this old command predates that flag and must too, or it
    silently resurrects a permanently-skipped shipment), and with a known
    Workflow ID -- grouped by region.
    """
    pending_by_region = {}
    for fba_id, entry in sheet.items():
        if entry.get("tracking_status") == "Delivered" or entry.get("delivery_date_status") == "Delivered":
            continue
        if entry.get("delivery_date_status") == "carrier_managed":
            continue
        if not entry.get("workflow_id"):
            continue
        pending_by_region.setdefault(entry.get("region"), []).append(fba_id)
    return pending_by_region


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

    pending_by_region = _select_delivery_window_sync_candidates(sheet)

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
                # A "carrier_managed" outcome must persist as the permanent-skip
                # flag itself, not sync_window_for_shipment's own "pending" --
                # otherwise this old command silently undoes the flag and
                # resurrects the shipment into future candidate lists forever.
                entry["delivery_date_status"] = (
                    "carrier_managed" if result["outcome"] == "carrier_managed" else result["new_delivery_date_status"]
                )

            save_master_sheet(path, sheet)
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    return totals


def run_weekly_delivery_window_sync(config: dict) -> dict:
    """
    Standalone entry point for --weekly-delivery-sync. Refreshes carrier data,
    discovers new shipments' Workflow IDs, then only opens a browser page for
    shipments select_weekly_candidates() says are due this week -- persisting
    the live window (or the new target window on a successful edit) back to
    the master sheet after every shipment, and the master sheet itself after
    every region. Also writes logs/weekly_delivery_window_summary_<ts>.txt --
    on every run, including a zero-candidate week, a Chrome-profile-locked
    failure, or a mid-run crash, since a missing summary is indistinguishable
    from "the scheduled task never fired" the next morning.
    """
    from datetime import date as _date
    from tracking_status import run_check_tracking
    from workflow_discovery import run_workflow_discovery
    from master_sheet import load_master_sheet, save_master_sheet, MASTER_SHEET_PATH_DEFAULT
    from upload_tracking import create_browser_context, navigate_to_shipment, fetch_shipment_status
    from run import wait_for_login

    logs_folder = config.get("logs_folder", "logs")
    today = _date.today()
    errors = []

    # Seeded now so a summary can always be written -- even if an exception
    # below happens before some of these get their real values.
    totals = {
        "checked": 0, "not_due": 0, "carrier_managed_skipped": 0,
        "no_workflow": 0, "no_workflow_ids": [],
        "matched": 0, "edited": 0, "pushed_one_week": 0, "locked": 0,
        "no_action_needed": 0, "edit_failed": 0, "edit_failed_ids": [],
        "read_failed": 0, "read_failed_ids": [],
        "skipped_shipment_done": 0, "skipped_shipment_done_ids": [],
        "new_shipments": [], "overdue_shipments": [],
        "errors": errors,
    }

    playwright = None
    context = None
    try:
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
        overdue_ids = set(selection["overdue"])

        totals["checked"] = len(candidates)
        totals["not_due"] = len(selection["not_due"])
        totals["carrier_managed_skipped"] = len(selection["carrier_managed"])
        totals["no_workflow"] = len(selection["no_workflow"])
        totals["no_workflow_ids"] = selection["no_workflow"]
        totals["new_shipments"] = new_shipments
        totals["overdue_shipments"] = sorted(overdue_ids)

        if not candidates:
            return totals

        region_by_name = {r["name"]: r for r in config.get("regions", [])}
        by_region = {}
        for fba_id in candidates:
            by_region.setdefault(sheet[fba_id].get("region"), []).append(fba_id)

        playwright, context = create_browser_context(config)

        this_run_outcomes = {}
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

                # Refresh Amazon's own shipment-status badge every week so it
                # doesn't go stale after the one-time capture at workflow
                # discovery. Once it says the shipment is done, its delivery
                # window can no longer be edited -- skip the window read/edit
                # page visit entirely instead of letting it fall into a false
                # "read_failed" or "locked".
                if navigate_to_shipment(page, fba_id, base_url):
                    amazon_status = fetch_shipment_status(page)
                    if amazon_status is not None:
                        entry["amazon_shipment_status"] = amazon_status

                if _is_terminal_shipment_status(entry.get("amazon_shipment_status")):
                    this_run_outcomes[fba_id] = "shipment_done"
                    totals["skipped_shipment_done"] += 1
                    totals["skipped_shipment_done_ids"].append(fba_id)
                    continue

                tracking = str(entry.get("tracking", "")).strip()
                cached = tracking_cache.get(tracking, {})
                expected_str = cached.get("expected_delivery_date")
                expected_date = _parse_flexible_date(expected_str, today) if expected_str else None

                result = sync_window_for_shipment(
                    page, base_url, fba_id, entry["workflow_id"], expected_date, today, logs_folder=logs_folder
                )
                outcome = result["outcome"]
                this_run_outcomes[fba_id] = outcome
                if outcome == "read_failed":
                    # The only outcome where read_shipment_window itself failed --
                    # nothing was read, so there's nothing to persist.
                    totals["read_failed"] += 1
                    totals["read_failed_ids"].append(fba_id)
                else:
                    # Every other outcome means the live read succeeded (Task 3:
                    # result["window_start"] is only None on "read_failed"), so
                    # the persistence below always applies here -- including
                    # "edit_failed", where the read succeeded but the subsequent
                    # edit attempt didn't; the shipment was still genuinely
                    # checked this run and delivery_window_last_checked should
                    # reflect that.
                    if outcome == "edit_failed":
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

        totals["overdue_shipments"] = _merge_overdue_with_newly_locked(overdue_ids, this_run_outcomes)
    except RuntimeError as e:
        # e.g. a previous run's Chrome process crashed and left the automation
        # profile locked (spec: Error Handling table) -- report it in the
        # summary instead of crashing the whole scheduled task silently. This
        # also covers run_check_tracking/run_workflow_discovery hitting the
        # same locked-profile failure before create_browser_context below
        # ever runs, since they open their own browser contexts too.
        errors.append(str(e))
    except Exception as e:
        # Anything else unexpected -- e.g. a crash partway through the
        # per-shipment loop or save_master_sheet -- lands in the summary's
        # Errors section instead of vanishing with no summary written at all.
        errors.append(str(e))
    finally:
        if context is not None:
            try:
                context.close()
                playwright.stop()
            except Exception:
                pass
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_text = format_weekly_delivery_window_summary(totals)
        Path(logs_folder).joinpath(f"weekly_delivery_window_summary_{ts}.txt").write_text(summary_text, encoding="utf-8")

    return totals


def format_weekly_delivery_window_summary(result: dict) -> str:
    lines = [
        "=" * 60,
        f"WEEKLY DELIVERY WINDOW SYNC SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        f"Checked this week          : {result['checked']}   (window starting within 7 days, or never checked)",
        f"Skipped (not due)          : {result['not_due']}  (window further out -- no browser visit needed)",
        f"Skipped (carrier-managed)  : {result['carrier_managed_skipped']}",
    ]
    no_workflow_ids = result.get("no_workflow_ids", [])
    lines.append(
        f"Skipped (no workflow yet) : {result.get('no_workflow', 0)}"
        + (f"   -> {', '.join(no_workflow_ids)}" if no_workflow_ids else "")
    )
    skipped_shipment_done_ids = result.get("skipped_shipment_done_ids", [])
    lines.append(
        f"Skipped (shipment done)   : {result.get('skipped_shipment_done', 0)}"
        + (f"   -> {', '.join(skipped_shipment_done_ids)}" if skipped_shipment_done_ids else "")
    )
    lines.append("")
    lines.extend([
        f"Matched (already correct)  : {result['matched']}",
        f"Edited (moved to real date): {result['edited']}",
        f"Pushed 1 week (no date yet): {result['pushed_one_week']}",
    ])
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
