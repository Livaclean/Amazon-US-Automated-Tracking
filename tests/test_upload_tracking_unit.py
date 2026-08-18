import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import upload_tracking
from upload_tracking import navigate_to_shipment, check_amazon_tracking_status
from upload_tracking import check_all_shipments_on_amazon


class _FakePage:
    """Minimal fake satisfying navigate_to_shipment's calls: goto,
    wait_for_load_state, .url, query_selector (login check), content()."""

    def __init__(self, url="https://sellercentral.amazon.com/fba/inbound-shipment/summary/FBA1/tracking",
                 html=""):
        self.url = url
        self._html = html

    def goto(self, url, timeout=None):
        pass

    def wait_for_load_state(self, state, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def query_selector(self, selector):
        return None

    def content(self):
        return self._html


# ---------------------------------------------------------------------------
# navigate_to_shipment — cross-marketplace redirect detection
# ---------------------------------------------------------------------------

def test_navigate_to_shipment_returns_false_for_genuine_not_found():
    page = _FakePage(html="<html>Shipment not found</html>")
    assert navigate_to_shipment(page, "FBA1", "https://sellercentral.amazon.com") is False


def test_navigate_to_shipment_returns_false_for_cross_marketplace_redirect():
    """Real page text captured live: Amazon shows this instead of a 'not found'
    error when a shipment belongs to a different marketplace than the URL
    probed — e.g. probing a Canada shipment against sellercentral.amazon.com."""
    html = (
        "<html>Error: The shipment you're trying to open is for Canada. "
        "Please switch to Amazon.ca to work on it.</html>"
    )
    page = _FakePage(html=html)
    assert navigate_to_shipment(page, "FBA1", "https://sellercentral.amazon.com") is False


def test_navigate_to_shipment_returns_true_for_normal_page():
    page = _FakePage(html="<html>Tracking details for shipment FBA1</html>")
    assert navigate_to_shipment(page, "FBA1", "https://sellercentral.amazon.com") is True


# ---------------------------------------------------------------------------
# check_amazon_tracking_status — detection-failure vs genuine status
# ---------------------------------------------------------------------------

def test_check_amazon_tracking_status_returns_check_failed_when_no_tracking_context(monkeypatch):
    """Navigation succeeds (right shipment, right marketplace) but the tracking
    iframe/inputs can never be located — must NOT be reported as 'not_found'
    (which check_all_shipments_on_amazon treats as already-complete and
    permanently caches as done)."""
    monkeypatch.setattr(upload_tracking, "navigate_to_shipment", lambda page, fba_id, base_url: True)
    monkeypatch.setattr(upload_tracking, "_get_tracking_context", lambda page, fba_id: None)
    page = _FakePage()
    status = check_amazon_tracking_status(page, "FBA1", {})
    assert status == "check_failed"


def test_check_amazon_tracking_status_returns_not_found_when_navigation_fails(monkeypatch):
    monkeypatch.setattr(upload_tracking, "navigate_to_shipment", lambda page, fba_id, base_url: False)
    page = _FakePage()
    status = check_amazon_tracking_status(page, "FBA1", {})
    assert status == "not_found"


# ---------------------------------------------------------------------------
# check_all_shipments_on_amazon — routing by status
# ---------------------------------------------------------------------------

def test_check_all_shipments_routes_check_failed_to_needs_upload_not_already_complete(monkeypatch):
    monkeypatch.setattr(upload_tracking, "check_amazon_tracking_status", lambda page, fba_id, config: "check_failed")
    shipments = {"FBA1": [{"tracking": "1Z001", "carrier": "UPS"}]}
    needs_upload, already_complete = check_all_shipments_on_amazon(shipments, {}, page=None)
    assert "FBA1" in needs_upload
    assert "FBA1" not in already_complete


def test_check_all_shipments_still_routes_complete_to_already_complete(monkeypatch):
    monkeypatch.setattr(upload_tracking, "check_amazon_tracking_status", lambda page, fba_id, config: "complete")
    shipments = {"FBA1": [{"tracking": "1Z001", "carrier": "UPS"}]}
    needs_upload, already_complete = check_all_shipments_on_amazon(shipments, {}, page=None)
    assert "FBA1" in already_complete
    assert "FBA1" not in needs_upload


def test_check_all_shipments_still_routes_not_found_to_already_complete(monkeypatch):
    """Unchanged behavior: a shipment Amazon genuinely rejects (closed/delivered,
    or doesn't exist) still doesn't need a tracking upload attempt."""
    monkeypatch.setattr(upload_tracking, "check_amazon_tracking_status", lambda page, fba_id, config: "not_found")
    shipments = {"FBA1": [{"tracking": "1Z001", "carrier": "UPS"}]}
    needs_upload, already_complete = check_all_shipments_on_amazon(shipments, {}, page=None)
    assert "FBA1" in already_complete
