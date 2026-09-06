import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import appointment_sync
from appointment_sync import (
    _extract_appointment_id_from_notes,
    needs_appointment_sync,
    _process_region_appointment_sync,
    format_appointment_sync_summary,
)


# --- _extract_appointment_id_from_notes ---------------------------------------

@pytest.mark.unit
def test_extract_appointment_id_from_notes_basic():
    assert _extract_appointment_id_from_notes("Appointment ID: 436480003997    Delivered On: 26.04.04") == "436480003997"


@pytest.mark.unit
def test_extract_appointment_id_from_notes_tight_spacing():
    assert _extract_appointment_id_from_notes("Appointment ID: 83299056997   Delivered On:07/15") == "83299056997"


@pytest.mark.unit
def test_extract_appointment_id_from_notes_no_colon():
    """Regression test: confirmed live in the master sheet that plenty of
    real notes (both AWD and regular FBA TRUCK shipments, e.g. FBA1972Q93K1)
    omit the colon entirely -- "Appointment ID 142628039989 ..." -- which the
    old colon-required pattern silently never matched."""
    assert _extract_appointment_id_from_notes("Appointment ID 142628039989   Delivered On:26.04.02") == "142628039989"


@pytest.mark.unit
def test_extract_appointment_id_from_notes_no_match_returns_none():
    assert _extract_appointment_id_from_notes("Making the appointment with Amazon of delivery") is None


@pytest.mark.unit
def test_extract_appointment_id_from_notes_blank_returns_none():
    assert _extract_appointment_id_from_notes("") is None
    assert _extract_appointment_id_from_notes(None) is None


# --- needs_appointment_sync ----------------------------------------------------

def _entry(carrier="TRUCK", tracking="/", notes="Appointment ID: 83299056997   Delivered On:07/15", fba_id="FBA001"):
    return {"carrier": carrier, "tracking": tracking, "notes": notes, "fba_id": fba_id}


@pytest.mark.unit
def test_needs_appointment_sync_true_for_truck_no_tracking_with_appointment_id():
    assert needs_appointment_sync(_entry()) is True


@pytest.mark.unit
def test_needs_appointment_sync_false_for_non_truck_carrier():
    assert needs_appointment_sync(_entry(carrier="UPS")) is False


@pytest.mark.unit
def test_needs_appointment_sync_false_when_real_tracking_already_present():
    assert needs_appointment_sync(_entry(tracking="1Z001")) is False


@pytest.mark.unit
def test_needs_appointment_sync_false_when_no_appointment_id_in_notes():
    assert needs_appointment_sync(_entry(notes="Making the appointment with Amazon of delivery")) is False


@pytest.mark.unit
def test_needs_appointment_sync_false_for_awd_star_prefix():
    # Live testing showed AWD shipment pages have no Pro/Freight field at all.
    assert needs_appointment_sync(_entry(fba_id="STAR-RJSSXHFN6ZS5X")) is False


@pytest.mark.unit
def test_needs_appointment_sync_true_when_tracking_blank_not_just_slash():
    assert needs_appointment_sync(_entry(tracking="")) is True


@pytest.mark.unit
def test_needs_appointment_sync_carrier_case_insensitive():
    assert needs_appointment_sync(_entry(carrier="truck")) is True


# --- _process_region_appointment_sync ------------------------------------------

def _row(tracking="/", tracking_status="pending"):
    return {"tracking": tracking, "tracking_status": tracking_status}


@pytest.mark.unit
def test_process_region_appointment_sync_success_updates_sheet(monkeypatch):
    monkeypatch.setattr(
        appointment_sync, "fill_pro_freight_number",
        lambda page, fba_id, base_url, appointment_id: {"status": "filled", "value": "83299056997"},
    )
    sheet = {"FBA001": _row()}
    result = _process_region_appointment_sync(page=None, base_url="https://x", items=[("FBA001", "83299056997")], sheet=sheet)

    assert result == {"filled": 1, "already_set": 0, "failed": 0}
    assert sheet["FBA001"]["tracking"] == "83299056997"
    assert sheet["FBA001"]["tracking_status"] == "updated"


@pytest.mark.unit
def test_process_region_appointment_sync_already_set_syncs_amazons_actual_value(monkeypatch):
    # Amazon already had a value (e.g. auto-filled via carrier integration) --
    # confirm the sheet gets synced to what Amazon actually has, not left stale.
    monkeypatch.setattr(
        appointment_sync, "fill_pro_freight_number",
        lambda page, fba_id, base_url, appointment_id: {"status": "already_set", "value": "999999"},
    )
    sheet = {"FBA001": _row()}
    result = _process_region_appointment_sync(page=None, base_url="https://x", items=[("FBA001", "83299056997")], sheet=sheet)

    assert result == {"filled": 0, "already_set": 1, "failed": 0}
    assert sheet["FBA001"]["tracking"] == "999999"
    assert sheet["FBA001"]["tracking_status"] == "updated"


@pytest.mark.unit
def test_process_region_appointment_sync_failure_leaves_sheet_untouched(monkeypatch):
    monkeypatch.setattr(
        appointment_sync, "fill_pro_freight_number",
        lambda page, fba_id, base_url, appointment_id: {"status": "nav_failed", "value": None},
    )
    sheet = {"FBA001": _row()}
    result = _process_region_appointment_sync(page=None, base_url="https://x", items=[("FBA001", "83299056997")], sheet=sheet)

    assert result == {"filled": 0, "already_set": 0, "failed": 1}
    assert sheet["FBA001"]["tracking"] == "/"
    assert sheet["FBA001"]["tracking_status"] == "pending"


# --- format_appointment_sync_summary --------------------------------------------

@pytest.mark.unit
def test_format_appointment_sync_summary_includes_counts():
    text = format_appointment_sync_summary({"filled": 7, "already_set": 3, "failed": 1})
    assert "7" in text
    assert "3" in text
    assert "1" in text
