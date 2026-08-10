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
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target: True)
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
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target: False)
    result = sync_window_for_shipment(
        page=None, base_url="https://x", fba_id="FBA001", workflow_id="wf-1",
        expected_delivery_date=date(2026, 8, 8), today=date(2026, 8, 1),
    )
    assert result == {"outcome": "edit_failed", "new_delivery_date_status": "pending"}


@pytest.mark.unit
def test_sync_window_for_shipment_push_two_weeks_success(monkeypatch):
    monkeypatch.setattr(
        delivery_window_sync, "read_shipment_window",
        lambda *a, **kw: {"window_start": date(2026, 8, 16), "window_end": date(2026, 8, 22)},
    )
    monkeypatch.setattr(delivery_window_sync, "apply_window_edit", lambda page, target: True)
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
        "no_action_needed": 3, "read_failed": 1, "edit_failed": 0,
    })
    assert "2" in text
    assert "1" in text
    assert "3" in text
