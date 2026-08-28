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


# ---------------------------------------------------------------------------
# upload_tracking_to_shipment — pad_to_fill (unsupported-carrier duplication)
# ---------------------------------------------------------------------------

class _FakeInput:
    """Minimal fake satisfying the get_attribute/evaluate/click/fill calls
    upload_tracking_to_shipment makes on each tracking <input>."""

    def __init__(self, value=""):
        self.value = value

    def get_attribute(self, name):
        return self.value if name == "value" else None

    def evaluate(self, script):
        return self.value

    def click(self):
        pass

    def fill(self, value):
        self.value = value


class _FakeFrame:
    """Minimal fake satisfying the tracking-iframe calls upload_tracking_to_shipment
    makes: wait_for_selector, evaluate (scroll), query_selector_all (inputs),
    query_selector (Update all button — a _FakeInput doubles as a clickable button)."""

    def __init__(self, inputs):
        self._inputs = inputs

    def wait_for_selector(self, selector, timeout=None):
        pass

    def evaluate(self, script):
        pass

    def query_selector_all(self, selector):
        return self._inputs

    def query_selector(self, selector):
        return _FakeInput()


def test_upload_tracking_pad_to_fill_duplicates_single_id_across_all_empty_slots(monkeypatch):
    """BASL-style tracking: one main tracking number, four empty Amazon box
    slots — pad_to_fill should fill all four with the same tracking number
    instead of leaving three blank."""
    inputs = [_FakeInput() for _ in range(4)]
    monkeypatch.setattr(upload_tracking, "_get_tracking_context", lambda page, fba_id: _FakeFrame(inputs))
    page = _FakePage()

    result = upload_tracking.upload_tracking_to_shipment(
        page, ["76MZ10538249"], "FBA1", {"logs_folder": "logs"}, pad_to_fill=True,
    )

    assert [inp.value for inp in inputs] == ["76MZ10538249"] * 4
    assert result["succeeded"] == 4
    assert result["status"] == "success"


def test_upload_tracking_pad_to_fill_refills_remaining_slot_after_partial_previous_pass(monkeypatch):
    """One of two slots was already filled with the pallet tracking number by a
    prior run; the other is still empty. pad_to_fill must still fill the empty
    one with the same value rather than treating the pool as exhausted."""
    filled_input = _FakeInput(value="76MZ10927867")
    empty_input = _FakeInput()
    monkeypatch.setattr(
        upload_tracking, "_get_tracking_context",
        lambda page, fba_id: _FakeFrame([filled_input, empty_input]),
    )
    page = _FakePage()

    result = upload_tracking.upload_tracking_to_shipment(
        page, ["76MZ10927867"], "FBA2", {"logs_folder": "logs"}, pad_to_fill=True,
    )

    assert empty_input.value == "76MZ10927867"
    assert result["succeeded"] == 1
    assert result["already_existed"] == 1


def test_upload_tracking_without_pad_to_fill_only_fills_one_slot(monkeypatch):
    """Regression guard: default behavior (pad_to_fill=False) must still fill
    only as many slots as there are tracking IDs, leaving the rest empty for
    a real per-box shipment where a short pool means a genuine shortfall."""
    inputs = [_FakeInput() for _ in range(4)]
    monkeypatch.setattr(upload_tracking, "_get_tracking_context", lambda page, fba_id: _FakeFrame(inputs))
    page = _FakePage()

    result = upload_tracking.upload_tracking_to_shipment(
        page, ["1Z999AA10123456784"], "FBA3", {"logs_folder": "logs"},
    )

    filled_values = [inp.value for inp in inputs if inp.value]
    assert filled_values == ["1Z999AA10123456784"]
    assert result["succeeded"] == 1
    assert result["empty_slots_remaining"] == 3
