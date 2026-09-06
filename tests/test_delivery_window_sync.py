import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import delivery_window_sync
from delivery_window_sync import (
    _parse_flexible_date,
    _parse_ltl_window_input_value,
    _week_bounds,
    decide_window_action,
    sync_window_for_shipment,
    apply_window_edit,
    read_shipment_window,
    format_delivery_window_sync_summary,
    select_weekly_candidates,
    format_weekly_delivery_window_summary,
    _merge_overdue_with_newly_locked,
    _is_terminal_shipment_status,
)


# --- _parse_flexible_date ------------------------------------------------------

@pytest.mark.unit
def test_parse_flexible_date_m_d_yy():
    assert _parse_flexible_date("7/1/26") == date(2026, 7, 1)


@pytest.mark.unit
def test_parse_flexible_date_mm_dd_yyyy():
    assert _parse_flexible_date("08/08/2026") == date(2026, 8, 8)


@pytest.mark.unit
def test_parse_flexible_date_iso():
    assert _parse_flexible_date("2026-06-10") == date(2026, 6, 10)


@pytest.mark.unit
def test_parse_flexible_date_month_name_with_year():
    assert _parse_flexible_date("Aug 23, 2026") == date(2026, 8, 23)


@pytest.mark.unit
def test_parse_flexible_date_day_month_year_no_comma():
    # EU/FR region shipments render "Delivery window:" dates this way
    # (day-month-year, no comma) rather than the US "Month Day, Year" style.
    assert _parse_flexible_date("1 Jul 2026") == date(2026, 7, 1)
    assert _parse_flexible_date("14 Jul 2026") == date(2026, 7, 14)


@pytest.mark.unit
def test_parse_flexible_date_day_sept_year_four_letter_abbreviation():
    """Regression test: found live (2026-09-02) that UK/EU (amazon.co.uk,
    amazon.de) 'Delivery window:' pages spell September as the 4-letter
    'Sept' instead of the standard 3-letter 'Sep' abbreviation every other
    month uses -- silently unparseable before this fix, which is exactly
    what read_shipment_window's dates-found-but-unparseable warning
    (delivery_window_sync.py) surfaced for FBA15M2N9CHZ and others."""
    assert _parse_flexible_date("13 Sept 2026") == date(2026, 9, 13)
    assert _parse_flexible_date("6 Sept 2026") == date(2026, 9, 6)


@pytest.mark.unit
def test_parse_flexible_date_weekday_month_day_no_year_infers_current_year():
    # "today" is well within the same year as the inferred date -- no rollover needed
    result = _parse_flexible_date("Friday, July 17", today=date(2026, 7, 10))
    assert result == date(2026, 7, 17)


@pytest.mark.unit
def test_parse_flexible_date_no_year_rolls_to_next_year_if_far_in_past():
    # "today" is late in the year; a bare "Jan 5" almost certainly means next year
    result = _parse_flexible_date("Jan 5", today=date(2026, 11, 20))
    assert result == date(2027, 1, 5)


@pytest.mark.unit
def test_parse_flexible_date_none_or_blank_returns_none():
    assert _parse_flexible_date(None) is None
    assert _parse_flexible_date("") is None


@pytest.mark.unit
def test_parse_flexible_date_unparseable_returns_none():
    assert _parse_flexible_date("not a date") is None


# --- _screenshot ----------------------------------------------------------------

class _FakeScreenshotPage:
    def __init__(self):
        self.calls = []

    def screenshot(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.unit
def test_screenshot_captures_full_page(tmp_path):
    """Regression test: a viewport-only screenshot (the default) missed the
    actual failure point on FBA19GR6H9VX's page (2026-09-02) because the
    relevant section renders below the fold -- the one still-unexplained
    'Delivery window never rendered' pattern stayed undiagnosable because of
    this. full_page=True must be passed explicitly."""
    page = _FakeScreenshotPage()
    delivery_window_sync._screenshot(page, "some_step", str(tmp_path))

    assert len(page.calls) == 1
    assert page.calls[0]["full_page"] is True


@pytest.mark.unit
def test_screenshot_noop_without_logs_folder():
    page = _FakeScreenshotPage()
    delivery_window_sync._screenshot(page, "some_step", None)
    assert page.calls == []


# --- _week_bounds ----------------------------------------------------------------

@pytest.mark.unit
def test_week_bounds_returns_sunday_to_saturday():
    # Aug 8, 2026 is a Saturday; the week is Aug 2 (Sun) - Aug 8 (Sat)
    start, end = _week_bounds(date(2026, 8, 8))
    assert start == date(2026, 8, 2)
    assert end == date(2026, 8, 8)


@pytest.mark.unit
def test_week_bounds_for_a_sunday():
    # Amazon's own window starts, e.g. Sep 13, 2026, are themselves Sundays
    start, end = _week_bounds(date(2026, 9, 13))
    assert start == date(2026, 9, 13)
    assert end == date(2026, 9, 19)


@pytest.mark.unit
def test_week_bounds_for_a_wednesday():
    start, end = _week_bounds(date(2026, 8, 5))  # Wednesday
    assert start == date(2026, 8, 2)
    assert end == date(2026, 8, 8)


# --- decide_window_action --------------------------------------------------------

@pytest.mark.unit
def test_decide_window_action_locked_when_start_date_passed():
    result = decide_window_action(
        window_start=date(2026, 8, 9), window_end=date(2026, 8, 15),
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 10),
    )
    assert result == {"action": "locked", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_locked_when_start_date_is_today():
    # Amazon's real cutoff behavior: the window becomes locked ON its start date, not after
    result = decide_window_action(
        window_start=date(2026, 8, 10), window_end=date(2026, 8, 16),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result["action"] == "locked"


@pytest.mark.unit
def test_decide_window_action_none_when_expected_date_inside_window():
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=date(2026, 9, 15), today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_edit_when_expected_date_before_window():
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 1),
    )
    assert result == {"action": "edit", "target_week_start": date(2026, 8, 2)}


@pytest.mark.unit
def test_decide_window_action_edit_when_expected_date_after_window():
    result = decide_window_action(
        window_start=date(2026, 7, 19), window_end=date(2026, 7, 25),
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 7, 1),
    )
    assert result == {"action": "edit", "target_week_start": date(2026, 8, 2)}


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
def test_decide_window_action_none_when_no_expected_date_and_window_far_out():
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_none_boundary_exactly_seven_days():
    # A window exactly 7 days out is NOT yet urgent -- it still has a full
    # week of runway and will be re-evaluated (and pushed if still needed)
    # once it's actually close to locking. Real incident (2026-09-06): a run
    # delayed one day past its Saturday schedule turned an 8-day-out window
    # into a 7-day-out one and wrongly pushed it a week early. window_start
    # =Aug 17 is deliberately NOT Sunday-aligned (unlike a real Amazon
    # window) -- this test isolates the (window_start - today).days trigger
    # boundary and never asserted week-alignment precision.
    result = decide_window_action(
        window_start=date(2026, 8, 17), window_end=date(2026, 8, 23),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_push_one_week_boundary_exactly_six_days():
    # One day closer to locking than the "none" boundary above -- now urgent
    # enough to push. window_start=Aug 17 (Monday) + 7 days = Aug 24 (Monday)
    # -> _week_bounds normalizes that to its containing week: Aug 23 (Sun).
    result = decide_window_action(
        window_start=date(2026, 8, 17), window_end=date(2026, 8, 23),
        expected_delivery_date=None, today=date(2026, 8, 11),
    )
    assert result["action"] == "push_one_week"
    assert result["target_week_start"] == date(2026, 8, 23)


@pytest.mark.unit
def test_decide_window_action_push_one_week_target_clears_a_two_week_window():
    # Real incident (2026-09-06, FBA15M2N9CHZ/FBA15M85HW20): some shipments'
    # real Amazon window spans two calendar weeks, not one. Computing the
    # push target as window_start + 7 days lands inside that still-active
    # window (Amazon's calendar then has no separate, clickable day there --
    # "not found or not selectable"). The target must clear the window's
    # real end instead, however long the window actually is.
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 29),
        expected_delivery_date=None, today=date(2026, 8, 11),
    )
    assert result["action"] == "push_one_week"
    assert result["target_week_start"] == date(2026, 8, 30)


@pytest.mark.unit
def test_decide_window_action_none_boundary_eight_days_out():
    result = decide_window_action(
        window_start=date(2026, 8, 18), window_end=date(2026, 8, 24),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_stale_expected_date_falls_back_to_none():
    # An overdue "In Transit" package's cached expected date has already
    # passed -- Amazon's calendar won't let us pick a past target week, so
    # a stale date must be treated the same as not having one at all.
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=date(2026, 8, 2), today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_stale_expected_date_falls_back_to_push_one_week():
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 22),
        expected_delivery_date=date(2026, 8, 2), today=date(2026, 8, 10),
    )
    assert result == {"action": "push_one_week", "target_week_start": date(2026, 8, 23)}


@pytest.mark.unit
def test_decide_window_action_expected_date_equal_to_today_is_not_stale():
    # today itself is still a usable expected date -- only strictly-past dates
    # are unusable (the day hasn't ended yet).
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=date(2026, 8, 10), today=date(2026, 8, 10),
    )
    assert result == {"action": "edit", "target_week_start": date(2026, 8, 9)}


# --- apply_window_edit ------------------------------------------------------

class _FakeLocator:
    """Minimal fake satisfying the .count/.first/.click/.wait_for/.get_attribute/
    .is_checked calls apply_window_edit makes on a Playwright locator."""

    def __init__(self, count=1, click_raises=None, aria_label=None, checked=False, locator_return=None):
        self._count = count
        self.click_raises = click_raises
        self._aria_label = aria_label
        self._checked = checked
        self.click_calls = 0
        self._locator_return = locator_return

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def click(self, timeout=None):
        self.click_calls += 1
        if self.click_raises:
            raise self.click_raises

    def wait_for(self, state=None, timeout=None):
        pass

    def get_attribute(self, name):
        return self._aria_label

    def is_checked(self):
        return self._checked

    def locator(self, selector):
        # By default, simulates "no LTL-style delivery-window input here" --
        # tests that need one pass locator_return to chain further fakes.
        if self._locator_return is not None:
            return self._locator_return
        return _FakeLocator(count=0)


class _FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _FakeWindowEditPage:
    """Minimal fake satisfying apply_window_edit's page calls, with the
    calendar already showing the target month so _navigate_calendar_to_month
    returns immediately without needing real navigation."""

    def __init__(self, target_year: int, target_month: int, confirm_click_raises=None,
                 carrier_checkbox_count: int = 0, carrier_checkbox_checked: bool = False):
        next_month = target_month + 1 if target_month < 12 else 1
        next_year = target_year if target_month < 12 else target_year + 1
        self._cal_rgt = _FakeLocator(aria_label=f"{date(next_year, next_month, 1).strftime('%B')} {next_year}")
        self._edit_link = _FakeLocator(count=1)
        self._confirm_btn = _FakeLocator(count=1, click_raises=confirm_click_raises)
        self._day_btn = _FakeLocator(count=1)
        self._carrier_checkbox = _FakeLocator(count=carrier_checkbox_count, checked=carrier_checkbox_checked)
        self.keyboard = _FakeKeyboard()

    def locator(self, selector):
        if selector == "text=Edit window":
            return self._edit_link
        if selector == ".cal-rgt":
            return self._cal_rgt
        raise AssertionError(f"unexpected locator selector: {selector}")

    def get_by_text(self, text, exact=False):
        assert text == "Confirm new delivery window"
        return self._confirm_btn

    def get_by_role(self, role, name=None, exact=False):
        if role == "checkbox":
            return self._carrier_checkbox
        assert role == "button"
        return self._day_btn

    def wait_for_timeout(self, ms):
        pass


@pytest.mark.unit
def test_apply_window_edit_returns_failed_when_confirm_button_never_becomes_clickable():
    """Regression test: a live run hit Amazon's 'Confirm new delivery window'
    button staying visible-but-disabled (element resolved but 'not enabled')
    for the full 30s Playwright timeout. The uncaught TimeoutError crashed the
    entire --sync-delivery-windows run instead of being reported as this
    shipment's 'edit_failed' outcome, as sync_window_for_shipment's own
    docstring promises. apply_window_edit must catch it and return "failed"."""
    class _TimeoutError(Exception):
        pass

    page = _FakeWindowEditPage(2026, 9, confirm_click_raises=_TimeoutError("Timeout 30000ms exceeded"))

    result = apply_window_edit(page, date(2026, 9, 1))

    assert result == "failed"
    assert page._confirm_btn.click_calls == 1
    assert page.keyboard.pressed == ["Escape"]


@pytest.mark.unit
def test_apply_window_edit_returns_edited_on_successful_confirm():
    page = _FakeWindowEditPage(2026, 9)

    result = apply_window_edit(page, date(2026, 9, 1))

    assert result == "edited"


@pytest.mark.unit
def test_apply_window_edit_returns_carrier_managed_when_checkbox_checked():
    """Regression test: every live 'edit_failed' traced back to the same root
    cause -- Amazon's edit modal has an 'Allow <carrier> to update my delivery
    window' checkbox, checked by default for carriers like FIST. While it's
    checked, day-selection works fine but Confirm never becomes clickable,
    because the carrier integration owns the window. apply_window_edit should
    detect this up front and skip straight to "carrier_managed" instead of
    wasting a full calendar-navigation + 10s confirm-timeout on a shipment
    that can never be manually edited."""
    page = _FakeWindowEditPage(2026, 9, carrier_checkbox_count=1, carrier_checkbox_checked=True)

    result = apply_window_edit(page, date(2026, 9, 1))

    assert result == "carrier_managed"
    # Never got as far as clicking the day button or attempting confirm.
    assert page._day_btn.click_calls == 0
    assert page._confirm_btn.click_calls == 0
    assert page.keyboard.pressed == ["Escape"]


@pytest.mark.unit
def test_apply_window_edit_proceeds_normally_when_checkbox_present_but_unchecked():
    page = _FakeWindowEditPage(2026, 9, carrier_checkbox_count=1, carrier_checkbox_checked=False)

    result = apply_window_edit(page, date(2026, 9, 1))

    assert result == "edited"
    assert page._confirm_btn.click_calls == 1
    assert page._confirm_btn.click_calls == 1


# --- read_shipment_window ----------------------------------------------------

class _FakeLocatorGroup:
    """Minimal fake for a multi-element locator (e.g. the per-step 'View'
    links), supporting .nth(i) and .last -> a _FakeLocator."""

    def __init__(self, items):
        self._items = items

    def nth(self, i):
        return self._items[i]

    @property
    def last(self):
        return self._items[-1]


class _RaisingLocator(_FakeLocator):
    """A _FakeLocator whose wait_for always raises, simulating a selector
    that never appears within the timeout."""

    def wait_for(self, state=None, timeout=None):
        raise TimeoutError("selector never appeared")


class _FakeReadWindowPage:
    """Minimal fake satisfying read_shipment_window's page calls, up through
    tab selection, so only the final 'Delivery window:' wait_for_selector
    fails -- isolating the stale-workflow detection added after that failure."""

    def __init__(self, enter_tracking_ids_count: int):
        self._enter_tracking_ids_count = enter_tracking_ids_count
        self._onboarding_modal_close = _RaisingLocator(count=0)  # never appears -- fine, no-op
        self._view_link = _FakeLocator(count=1)
        self._tab = _FakeLocator(count=1)
        self._enter_tracking_ids = _FakeLocator(count=enter_tracking_ids_count)

    def goto(self, url, timeout=None):
        pass

    def wait_for_load_state(self, state=None, timeout=None):
        pass

    def locator(self, selector):
        assert selector == "kat-modal[visible='true']"

        class _ModalLocator:
            def locator(_self, sub_selector):
                return self._onboarding_modal_close
        return _ModalLocator()

    def get_by_text(self, text, exact=False):
        if text == "View":
            return _FakeLocatorGroup([self._view_link] * 4)
        if text.startswith("Shipment ID:"):
            return self._tab
        if text == "Enter tracking IDs":
            return self._enter_tracking_ids
        raise AssertionError(f"unexpected get_by_text: {text!r}")

    def wait_for_selector(self, selector, timeout=None):
        if selector == "text=Track shipment":
            return
        if selector == "text=Delivery window:":
            raise TimeoutError("Delivery window never appeared")
        raise AssertionError(f"unexpected wait_for_selector: {selector!r}")


@pytest.mark.unit
def test_read_shipment_window_logs_stale_workflow_when_tracking_form_empty(caplog):
    """Regression test: confirmed live (2026-08-30) that some shipments'
    'Send to Amazon' workflow page never picked up tracking entered through
    the newer inbound-shipment tracking page -- it shows an empty, unfilled
    'Enter tracking IDs' form with no Delivery window UI at all, no matter
    how long you wait. This isn't a scrape bug, so it should be logged
    distinctly instead of the generic 'never rendered' message that implies
    a timing/selector problem worth re-investigating."""
    page = _FakeReadWindowPage(enter_tracking_ids_count=1)

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result is None
    assert any("wasn't entered through this workflow" in r.message for r in caplog.records)
    assert not any("never rendered after selecting its tab" in r.message for r in caplog.records)


@pytest.mark.unit
def test_read_shipment_window_logs_generic_message_when_not_stale_workflow(caplog):
    page = _FakeReadWindowPage(enter_tracking_ids_count=0)

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result is None
    assert any("never rendered after selecting its tab" in r.message for r in caplog.records)
    assert not any("wasn't entered through this workflow" in r.message for r in caplog.records)


class _FakeReadWindowPageNoTrackingSection:
    """Minimal fake where the 4th 'View' link never renders -- isolating the
    stale-workflow detection added at that earlier failure point."""

    def __init__(self, tracking_info_needed_count: int):
        self._tracking_info_needed_count = tracking_info_needed_count
        self._onboarding_modal_close = _RaisingLocator(count=0)
        self._view_link = _RaisingLocator(count=1)

    def goto(self, url, timeout=None):
        pass

    def wait_for_load_state(self, state=None, timeout=None):
        pass

    def locator(self, selector):
        assert selector == "kat-modal[visible='true']"

        class _ModalLocator:
            def locator(_self, sub_selector):
                return self._onboarding_modal_close
        return _ModalLocator()

    def get_by_text(self, text, exact=False):
        if text == "View":
            return _FakeLocatorGroup([self._view_link] * 4)
        if text == "Tracking information must be provided":
            return _FakeLocator(count=self._tracking_info_needed_count)
        raise AssertionError(f"unexpected get_by_text: {text!r}")


@pytest.mark.unit
def test_read_shipment_window_logs_stale_workflow_when_step4_unconfirmed(caplog):
    """Regression test: confirmed live (2026-09-01) that some shipments never
    get as far as rendering the 4th 'View' link at all, because Step 4 on
    Amazon's 'Send to Amazon' page is still showing the raw, unconfirmed
    'Tracking information must be provided' carrier form -- the same
    tracking-wasn't-entered-through-this-workflow cause as the empty-form
    case, just caught one step earlier."""
    page = _FakeReadWindowPageNoTrackingSection(tracking_info_needed_count=1)

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result is None
    assert any("wasn't entered through this workflow" in r.message for r in caplog.records)
    assert not any("never rendered its 'Tracking details' section" in r.message for r in caplog.records)


@pytest.mark.unit
def test_read_shipment_window_logs_generic_message_when_step4_confirmed_but_views_missing(caplog):
    page = _FakeReadWindowPageNoTrackingSection(tracking_info_needed_count=0)

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result is None
    assert any("never rendered its 'Tracking details' section" in r.message for r in caplog.records)
    assert not any("wasn't entered through this workflow" in r.message for r in caplog.records)


class _FakeReadWindowPageWindowFound(_FakeReadWindowPage):
    """Extends _FakeReadWindowPage past the 'Delivery window:' wait -- it
    renders successfully, but the body text served by inner_text() is
    controlled by the test so the date-parsing step can be exercised."""

    def __init__(self, body_text: str):
        super().__init__(enter_tracking_ids_count=0)
        self._body_text = body_text

    def wait_for_selector(self, selector, timeout=None):
        if selector in ("text=Track shipment", "text=Delivery window:"):
            return
        raise AssertionError(f"unexpected wait_for_selector: {selector!r}")

    def inner_text(self, selector):
        assert selector == "body"
        return self._body_text


@pytest.mark.unit
def test_read_shipment_window_logs_when_dates_found_but_unparseable(caplog):
    """Regression test: found live (2026-09-01) that 6 of a run's 45
    read_failed outcomes had zero corresponding warning in the log --
    traced to this exact gap. Unlike the sibling case where the
    'Delivery window:' text never renders at all (which does log +
    screenshot), a matched-but-unparseable date silently returned None
    with no diagnostic trail whatsoever, making it impossible to tell
    from the log which shipments hit it or why."""
    page = _FakeReadWindowPageWindowFound(
        "some page chrome Delivery window: 32 Fooember 2026 - 33 Fooember 2026 more chrome"
    )

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result is None
    assert any("couldn't be parsed" in r.message and "FBA001" in r.message for r in caplog.records)


@pytest.mark.unit
def test_read_shipment_window_succeeds_when_dates_parse():
    page = _FakeReadWindowPageWindowFound(
        "some page chrome Delivery window: Sep 1, 2026 - Sep 14, 2026 more chrome"
    )

    result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result == {"window_start": date(2026, 9, 1), "window_end": date(2026, 9, 14)}


@pytest.mark.unit
def test_parse_ltl_window_input_value_parses_single_trailing_year():
    """Regression test: confirmed live (2026-09-07, FBA19M5MX8MR) that LTL/FTL
    shipments render their delivery window as a <kat-input value="Sep 20 -
    Sep 26, 2026">, not the SPD flow's "Delivery window: Sep 20, 2026 -
    Sep 26, 2026" text -- only the END date carries a year."""
    result = _parse_ltl_window_input_value("Sep 20 - Sep 26, 2026", today=date(2026, 9, 6))
    assert result == {"window_start": date(2026, 9, 20), "window_end": date(2026, 9, 26)}


@pytest.mark.unit
def test_parse_ltl_window_input_value_none_on_malformed_value():
    assert _parse_ltl_window_input_value("garbage", today=date(2026, 9, 6)) is None
    assert _parse_ltl_window_input_value("", today=date(2026, 9, 6)) is None


@pytest.mark.unit
def test_parse_ltl_window_input_value_none_on_unparseable_dates():
    assert _parse_ltl_window_input_value("Fooember 99 - Sep 26, 2026", today=date(2026, 9, 6)) is None


class _FakeReadWindowPageLTLStyle(_FakeReadWindowPage):
    """Extends _FakeReadWindowPage so the colon-text wait still times out
    (as it always does for LTL/FTL), but the shipment's tab locator chains
    to a real LTL-style <kat-input> value via .locator() -- confirmed live
    (2026-09-07) this is where LTL/FTL shipments' window actually lives."""

    def __init__(self, input_value: str):
        super().__init__(enter_tracking_ids_count=0)
        window_input = _FakeLocator(count=1, aria_label=input_value)
        card = _FakeLocator(count=1, locator_return=window_input)
        self._tab = _FakeLocator(count=1, locator_return=card)

    def wait_for_selector(self, selector, timeout=None):
        if selector == "text=Track shipment":
            return
        if selector == "text=Delivery window:":
            raise TimeoutError("Delivery window never appeared")
        raise AssertionError(f"unexpected wait_for_selector: {selector!r}")


@pytest.mark.unit
def test_read_shipment_window_falls_back_to_ltl_style_input(caplog):
    """Regression test: confirmed live (2026-09-07, FBA19M5MX8MR) that LTL/FTL
    shipments never render the plain-text 'Delivery window:' label the
    standard SPD flow waits for -- their window is a <kat-input> value
    attribute instead. read_shipment_window must fall back to reading that
    rather than reporting a bogus 'never rendered' failure."""
    page = _FakeReadWindowPageLTLStyle("Sep 20 - Sep 26, 2026")

    with caplog.at_level("WARNING", logger="delivery_window_sync"):
        result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result == {"window_start": date(2026, 9, 20), "window_end": date(2026, 9, 26)}
    assert not any("never rendered after selecting its tab" in r.message for r in caplog.records)


@pytest.mark.unit
def test_read_shipment_window_clicks_last_view_link_not_fixed_index():
    """Regression test: confirmed live (2026-09-06, FBA19L4ZZS14) that some
    shipment methods (SPD / FIST Carriers) give Step 1b its own separate
    'View' link, producing 5 collapsed-step View links instead of the usual
    4 -- a hardcoded nth(3) then clicked Step 3 instead of Final step,
    expanding the wrong section and causing a false 'Shipment ID: ...' tab
    match against Step 3's plain-text shipment card. 'Final step' is always
    the last collapsed section regardless of how many precede it, so the
    fix must click .last, not a fixed index -- this verifies the last (and
    only the last) View link gets clicked even when there are 5."""
    page = _FakeReadWindowPageWindowFound(
        "some page chrome Delivery window: Sep 1, 2026 - Sep 14, 2026 more chrome"
    )
    view_items = [_FakeLocator(count=1) for _ in range(5)]
    page.get_by_text = lambda text, exact=False: (
        _FakeLocatorGroup(view_items) if text == "View" else
        page._tab if text.startswith("Shipment ID:") else
        page._enter_tracking_ids if text == "Enter tracking IDs" else
        (_ for _ in ()).throw(AssertionError(f"unexpected get_by_text: {text!r}"))
    )

    result = read_shipment_window(page, "wf-1", "FBA001", "https://x")

    assert result == {"window_start": date(2026, 9, 1), "window_end": date(2026, 9, 14)}
    assert view_items[-1].click_calls == 1
    assert all(v.click_calls == 0 for v in view_items[:-1])


# --- sync_window_for_shipment ----------------------------------------------------

@pytest.mark.unit
def test_sync_window_for_shipment_read_failed(monkeypatch):
    monkeypatch.setattr(delivery_window_sync, "read_shipment_window", lambda *a, **kw: None)
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"outcome": "read_failed", "new_delivery_date_status": "pending",
                      "window_start": None, "window_end": None}


@pytest.mark.unit
def test_sync_window_for_shipment_matched_no_action_needed(monkeypatch):
    # expected date already inside the window -- confirmed correct, no edit needed
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 9, 15), today=date(2026, 8, 10),
    )
    assert result == {"outcome": "matched", "new_delivery_date_status": "updated",
                      "window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)}


@pytest.mark.unit
def test_sync_window_for_shipment_no_action_needed_no_expected_date(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"outcome": "no_action_needed", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)}


@pytest.mark.unit
def test_sync_window_for_shipment_locked(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 8, 9), "window_end": date(2026, 8, 15)},
    )
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 10),
    )
    assert result == {"outcome": "locked", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 8, 9), "window_end": date(2026, 8, 15)}


@pytest.mark.unit
def test_sync_window_for_shipment_edit_success(monkeypatch):
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
    assert result == {"outcome": "edit", "new_delivery_date_status": "updated",
                      "window_start": date(2026, 8, 2), "window_end": date(2026, 8, 8)}


@pytest.mark.unit
def test_sync_window_for_shipment_edit_failed(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target, **kw: "failed")
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 1),
    )
    assert result == {"outcome": "edit_failed", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)}


@pytest.mark.unit
def test_sync_window_for_shipment_carrier_managed(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target, **kw: "carrier_managed")
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 1),
    )
    assert result == {"outcome": "carrier_managed", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)}


@pytest.mark.unit
def test_sync_window_for_shipment_push_one_week_success(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 8, 16), "window_end": date(2026, 8, 22)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target, **kw: "edited")
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    # push_one_week is a stopgap, not a real resolution -- stays "pending" so
    # it keeps getting rechecked for a real expected date.
    # target_week_start for this "no date, window starting soon" case is Aug 23
    # (window_start Aug 16 + 7 days = Aug 23, which is already a Sunday)
    assert result == {"outcome": "push_one_week", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 8, 23), "window_end": date(2026, 8, 29)}


@pytest.mark.unit
def test_sync_window_for_shipment_stale_expected_date_reports_no_action_needed_not_matched(monkeypatch):
    # An overdue expected date can't be used to confirm the window is
    # correct -- "matched" would overclaim confidence we don't have.
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)},
    )
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 2), today=date(2026, 8, 10),
    )
    assert result == {"outcome": "no_action_needed", "new_delivery_date_status": "pending",
                      "window_start": date(2026, 9, 13), "window_end": date(2026, 9, 19)}


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


# --- format_delivery_window_sync_summary ------------------------------------------

@pytest.mark.unit
def test_format_delivery_window_sync_summary_includes_counts():
    text = format_delivery_window_sync_summary({
        "matched": 2, "updated": 0, "pushed": 0, "locked": 1,
        "no_action_needed": 3, "carrier_managed": 4, "read_failed": 1, "edit_failed": 0,
    })
    assert "2" in text
    assert "1" in text
    assert "3" in text
    assert "Carrier-managed (skipped):  4" in text


@pytest.mark.unit
def test_format_delivery_window_sync_summary_defaults_carrier_managed_when_absent():
    """Backward compat: a totals dict from before carrier_managed existed
    shouldn't KeyError."""
    text = format_delivery_window_sync_summary({
        "matched": 0, "updated": 0, "pushed": 0, "locked": 0,
        "no_action_needed": 0, "read_failed": 0, "edit_failed": 0,
    })
    assert "Carrier-managed (skipped):  0" in text


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


@pytest.mark.unit
def test_select_weekly_candidates_handles_datetime_object_in_window_start_cell():
    """Regression test: load_master_sheet does zero type normalization on cell
    values, and this file is one the user opens and re-saves in Excel -- if
    Excel auto-converts an ISO-looking text cell into a real date on save,
    openpyxl returns a datetime object instead of a string for that cell.
    strptime() on a non-string used to raise TypeError, crashing the whole
    run before any browser opened."""
    from datetime import datetime as _datetime
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start=_datetime(2026, 8, 30))}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_handles_date_object_in_window_start_cell():
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start=date(2026, 8, 30))}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


@pytest.mark.unit
def test_select_weekly_candidates_treats_garbled_date_string_as_candidate_not_crash():
    """A genuinely unparseable value shouldn't crash select_weekly_candidates
    -- treat it the same as "never checked" so it gets a fresh live read."""
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_window_start="not-a-date")}
    result = select_weekly_candidates(sheet, today=date(2026, 8, 29))
    assert result["candidates"] == ["FBA001"]


# --- _select_delivery_window_sync_candidates (old --sync-delivery-windows command) ---

@pytest.mark.unit
def test_select_delivery_window_sync_candidates_skips_carrier_managed_permanently():
    """Regression test: the old --sync-delivery-windows command predates the
    carrier_managed permanent-skip flag -- without this exclusion, running it
    would re-visit these shipments and reset their flag back to "pending",
    silently undoing the permanent skip and resurrecting them into the weekly
    candidate list forever."""
    sheet = {"FBA001": _row(fba_id="FBA001", delivery_date_status="carrier_managed", region="US")}
    result = delivery_window_sync._select_delivery_window_sync_candidates(sheet)
    assert result == {}


@pytest.mark.unit
def test_select_delivery_window_sync_candidates_includes_normal_pending_shipment():
    sheet = {"FBA001": _row(fba_id="FBA001", region="US")}
    result = delivery_window_sync._select_delivery_window_sync_candidates(sheet)
    assert result == {"US": ["FBA001"]}


@pytest.mark.unit
def test_select_delivery_window_sync_candidates_excludes_delivered_and_no_workflow():
    sheet = {
        "FBA001": _row(fba_id="FBA001", region="US", tracking_status="Delivered"),
        "FBA002": _row(fba_id="FBA002", region="US", delivery_date_status="Delivered"),
        "FBA003": _row(fba_id="FBA003", region="US", workflow_id=""),
    }
    result = delivery_window_sync._select_delivery_window_sync_candidates(sheet)
    assert result == {}


# --- _is_terminal_shipment_status ---------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("status", ["Delivered", "Closed", "Receiving"])
def test_is_terminal_shipment_status_true_for_terminal_statuses(status):
    """Once Amazon's own shipment status is Delivered, Closed, or Receiving
    (confirmed live 2026-09-02 as a real status value -- Amazon's warehouse
    has started checking the shipment in), its delivery window can no longer
    be edited -- the weekly sync should stop trying."""
    assert _is_terminal_shipment_status(status) is True


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Shipped", "Working", "In transit", "", None])
def test_is_terminal_shipment_status_false_for_non_terminal_statuses(status):
    assert _is_terminal_shipment_status(status) is False


# --- format_weekly_delivery_window_summary ------------------------------------------

@pytest.mark.unit
def test_format_weekly_delivery_window_summary_includes_all_sections():
    text = format_weekly_delivery_window_summary({
        "checked": 14, "not_due": 121, "carrier_managed_skipped": 6,
        "no_workflow": 2, "no_workflow_ids": ["FBA20AAAAAA1", "FBA20AAAAAA2"],
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
    assert "Skipped (no workflow yet)" in text
    assert "FBA20AAAAAA1" in text
    assert "FBA20AAAAAA2" in text


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


@pytest.mark.unit
def test_format_weekly_delivery_window_summary_includes_skipped_shipment_done_section():
    text = format_weekly_delivery_window_summary({
        "checked": 0, "not_due": 0, "carrier_managed_skipped": 0,
        "matched": 0, "edited": 0, "pushed_one_week": 0, "no_action_needed": 0,
        "new_shipments": [], "overdue_shipments": [], "locked": 0,
        "read_failed": 0, "read_failed_ids": [],
        "edit_failed": 0, "edit_failed_ids": [],
        "skipped_shipment_done": 2, "skipped_shipment_done_ids": ["FBA001", "FBA002"],
        "errors": [],
    })
    assert "Skipped (shipment done)" in text
    assert "2" in text
    assert "FBA001" in text
    assert "FBA002" in text


# --- _merge_overdue_with_newly_locked -------------------------------------------

@pytest.mark.unit
def test_merge_overdue_with_newly_locked_includes_pre_run_overdue():
    result = _merge_overdue_with_newly_locked({"FBA001"}, {"FBA002": "matched"})
    assert result == ["FBA001"]


@pytest.mark.unit
def test_merge_overdue_with_newly_locked_includes_this_run_locked_outcomes():
    result = _merge_overdue_with_newly_locked(set(), {"FBA001": "locked", "FBA002": "matched"})
    assert result == ["FBA001"]


@pytest.mark.unit
def test_merge_overdue_with_newly_locked_dedupes_when_both_apply():
    result = _merge_overdue_with_newly_locked({"FBA001"}, {"FBA001": "locked"})
    assert result == ["FBA001"]


@pytest.mark.unit
def test_merge_overdue_with_newly_locked_ignores_non_locked_outcomes():
    result = _merge_overdue_with_newly_locked(set(), {
        "FBA001": "matched", "FBA002": "edited", "FBA003": "read_failed",
        "FBA004": "edit_failed", "FBA005": "carrier_managed", "FBA006": "no_action_needed",
    })
    assert result == []
