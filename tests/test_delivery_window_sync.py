import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import delivery_window_sync
from delivery_window_sync import (
    _parse_flexible_date,
    _week_bounds,
    decide_window_action,
    sync_window_for_shipment,
    apply_window_edit,
    format_delivery_window_sync_summary,
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
def test_decide_window_action_push_two_weeks_when_no_expected_date_and_window_starts_soon():
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 22),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    # today + 14 days = Aug 24, 2026 (Monday) -> week is Aug 23 (Sun) - Aug 29 (Sat)
    assert result == {"action": "push_two_weeks", "target_week_start": date(2026, 8, 23)}


@pytest.mark.unit
def test_decide_window_action_none_when_no_expected_date_and_window_far_out():
    result = decide_window_action(
        window_start=date(2026, 9, 13), window_end=date(2026, 9, 19),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"action": "none", "target_week_start": None}


@pytest.mark.unit
def test_decide_window_action_push_two_weeks_boundary_exactly_seven_days():
    result = decide_window_action(
        window_start=date(2026, 8, 17), window_end=date(2026, 8, 23),
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result["action"] == "push_two_weeks"


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
def test_decide_window_action_stale_expected_date_falls_back_to_push_two_weeks():
    result = decide_window_action(
        window_start=date(2026, 8, 16), window_end=date(2026, 8, 22),
        expected_delivery_date=date(2026, 8, 2), today=date(2026, 8, 10),
    )
    assert result == {"action": "push_two_weeks", "target_week_start": date(2026, 8, 23)}


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

    def __init__(self, count=1, click_raises=None, aria_label=None, checked=False):
        self._count = count
        self.click_raises = click_raises
        self._aria_label = aria_label
        self._checked = checked
        self.click_calls = 0

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


# --- sync_window_for_shipment ----------------------------------------------------

@pytest.mark.unit
def test_sync_window_for_shipment_read_failed(monkeypatch):
    monkeypatch.setattr(delivery_window_sync, "read_shipment_window", lambda *a, **kw: None)
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    assert result == {"outcome": "read_failed", "new_delivery_date_status": "pending"}


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
    assert result == {"outcome": "matched", "new_delivery_date_status": "updated"}


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
    assert result == {"outcome": "no_action_needed", "new_delivery_date_status": "pending"}


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
    assert result == {"outcome": "locked", "new_delivery_date_status": "pending"}


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
    assert result == {"outcome": "edit", "new_delivery_date_status": "updated"}


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
    assert result == {"outcome": "edit_failed", "new_delivery_date_status": "pending"}


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
    assert result == {"outcome": "carrier_managed", "new_delivery_date_status": "pending"}


@pytest.mark.unit
def test_sync_window_for_shipment_push_two_weeks_success(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 8, 16), "window_end": date(2026, 8, 22)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target, **kw: "edited")
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=None, today=date(2026, 8, 10),
    )
    # push_two_weeks is a stopgap, not a real resolution -- stays "pending" so
    # it keeps getting rechecked for a real expected date.
    assert result == {"outcome": "push_two_weeks", "new_delivery_date_status": "pending"}


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
    assert result == {"outcome": "no_action_needed", "new_delivery_date_status": "pending"}


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
