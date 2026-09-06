import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from shipment_status import (
    _backfill_delivered_shipment_status,
    _pending_fba_ids_by_region,
    format_populate_shipment_status_summary,
)


def _row(region="US", tracking_status="pending", delivery_date_status="pending", amazon_shipment_status=""):
    return {
        "region": region,
        "tracking_status": tracking_status,
        "delivery_date_status": delivery_date_status,
        "amazon_shipment_status": amazon_shipment_status,
    }


# --- _backfill_delivered_shipment_status ---------------------------------------

@pytest.mark.unit
def test_backfill_sets_delivered_when_tracking_status_delivered():
    sheet = {"FBA001": _row(tracking_status="Delivered")}
    result = _backfill_delivered_shipment_status(sheet)
    assert result["FBA001"]["amazon_shipment_status"] == "Delivered"


@pytest.mark.unit
def test_backfill_sets_delivered_when_delivery_date_status_delivered():
    sheet = {"FBA001": _row(tracking_status="pending", delivery_date_status="Delivered")}
    result = _backfill_delivered_shipment_status(sheet)
    assert result["FBA001"]["amazon_shipment_status"] == "Delivered"


@pytest.mark.unit
def test_backfill_leaves_pending_shipments_untouched():
    sheet = {"FBA001": _row(tracking_status="pending", delivery_date_status="pending")}
    result = _backfill_delivered_shipment_status(sheet)
    assert result["FBA001"]["amazon_shipment_status"] == ""


@pytest.mark.unit
def test_backfill_does_not_mutate_input():
    sheet = {"FBA001": _row(tracking_status="Delivered")}
    _backfill_delivered_shipment_status(sheet)
    assert sheet["FBA001"]["amazon_shipment_status"] == ""


@pytest.mark.unit
def test_backfill_does_not_downgrade_existing_terminal_amazon_status():
    """Regression test: a shipment already stamped amazon_shipment_status
    'Closed' by an earlier live check (workflow discovery or the weekly
    delivery-window sync) must not be silently replaced with the generic
    'Delivered' just because carrier tracking also says Delivered -- that
    would discard more specific information without ever checking Amazon
    again."""
    sheet = {"FBA001": _row(tracking_status="Delivered", amazon_shipment_status="Closed")}
    result = _backfill_delivered_shipment_status(sheet)
    assert result["FBA001"]["amazon_shipment_status"] == "Closed"


# --- _pending_fba_ids_by_region -------------------------------------------------

@pytest.mark.unit
def test_pending_fba_ids_by_region_groups_by_region():
    sheet = {
        "FBA001": _row(region="US"),
        "FBA002": _row(region="CA"),
    }
    result = _pending_fba_ids_by_region(sheet)
    assert result == {"US": ["FBA001"], "CA": ["FBA002"]}


@pytest.mark.unit
def test_pending_fba_ids_by_region_excludes_delivered_tracking_status():
    sheet = {
        "FBA001": _row(region="US", tracking_status="Delivered"),
        "FBA002": _row(region="US"),
    }
    result = _pending_fba_ids_by_region(sheet)
    assert result == {"US": ["FBA002"]}


@pytest.mark.unit
def test_pending_fba_ids_by_region_excludes_delivered_delivery_date_status():
    sheet = {
        "FBA001": _row(region="US", delivery_date_status="Delivered"),
        "FBA002": _row(region="US"),
    }
    result = _pending_fba_ids_by_region(sheet)
    assert result == {"US": ["FBA002"]}


@pytest.mark.unit
def test_pending_fba_ids_by_region_empty_sheet_returns_empty_dict():
    assert _pending_fba_ids_by_region({}) == {}


@pytest.mark.unit
def test_pending_fba_ids_by_region_excludes_terminal_amazon_status_even_if_carrier_not_delivered():
    """Regression test: a shipment's amazon_shipment_status can already be a
    terminal Amazon status ('Closed'/'Receiving') from an earlier live check
    even when carrier tracking hasn't caught up to 'Delivered' yet (e.g. a
    stale LTL/FTL carrier feed). Re-visiting it live gives no new
    information -- it should be excluded the same as a carrier-Delivered row."""
    sheet = {
        "FBA001": _row(region="US", tracking_status="pending", amazon_shipment_status="Closed"),
        "FBA002": _row(region="US", tracking_status="pending", amazon_shipment_status="Receiving"),
        "FBA003": _row(region="US"),
    }
    result = _pending_fba_ids_by_region(sheet)
    assert result == {"US": ["FBA003"]}


# --- format_populate_shipment_status_summary ------------------------------------

@pytest.mark.unit
def test_format_populate_shipment_status_summary_includes_counts():
    text = format_populate_shipment_status_summary({
        "backfilled_delivered": 460, "checked": 106, "found": 90, "not_found": 16, "skipped": 3,
    })
    assert "460" in text
    assert "106" in text
    assert "90" in text
    assert "16" in text
    assert "3" in text
