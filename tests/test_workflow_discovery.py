import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import workflow_discovery
from workflow_discovery import (
    _extract_workflow_id_from_url,
    _extract_sibling_fba_ids,
    _extract_workflow_from_page,
    discover_workflow_for_shipment,
    _process_region_discoveries,
    format_workflow_discovery_summary,
)


# --- _extract_workflow_id_from_url -------------------------------------------

@pytest.mark.unit
def test_extract_workflow_id_from_url_basic():
    url = "https://sellercentral.amazon.com/fba/sendtoamazon?wf=wfb6b77412-f879-4203-8c93-f27ba68feac8"
    assert _extract_workflow_id_from_url(url) == "wfb6b77412-f879-4203-8c93-f27ba68feac8"


@pytest.mark.unit
def test_extract_workflow_id_from_url_step_variant():
    url = "https://sellercentral.amazon.com/fba/sendtoamazon/enter_tracking_details_step?wf=wf48a7d09f-3648-4f0d-b48a-bf8383357f39"
    assert _extract_workflow_id_from_url(url) == "wf48a7d09f-3648-4f0d-b48a-bf8383357f39"


@pytest.mark.unit
def test_extract_workflow_id_from_url_missing_param_returns_none():
    assert _extract_workflow_id_from_url("https://sellercentral.amazon.com/fba/inbound-shipment/summary/FBA123/tracking") is None


@pytest.mark.unit
def test_extract_workflow_id_from_url_with_trailing_params():
    url = "https://sellercentral.amazon.de/fba/sendtoamazon?wf=wf-eu-123&foo=bar"
    assert _extract_workflow_id_from_url(url) == "wf-eu-123"


# --- _extract_sibling_fba_ids -------------------------------------------------

@pytest.mark.unit
def test_extract_sibling_fba_ids_basic():
    link_texts = [
        "FBA settings", "FBA Inventory", "", "",
        "FBA19K4G0K6R - IMO1", "FBA19K4KTDZ6 - IMS1", "FBA19K4G0NSQ - ITX3",
        "FBA19K4HMP6Y - MCC1", "FBA19K4G19WY - ORF2",
    ]
    assert _extract_sibling_fba_ids(link_texts) == [
        "FBA19K4G0K6R", "FBA19K4KTDZ6", "FBA19K4G0NSQ", "FBA19K4HMP6Y", "FBA19K4G19WY",
    ]


@pytest.mark.unit
def test_extract_sibling_fba_ids_handles_awd_star_prefix():
    link_texts = ["STAR-RJSSXHFN6ZS5X - MCC1", "STAR-XWKACRYUPW7HS - IUSF"]
    assert _extract_sibling_fba_ids(link_texts) == ["STAR-RJSSXHFN6ZS5X", "STAR-XWKACRYUPW7HS"]


@pytest.mark.unit
def test_extract_sibling_fba_ids_single_shipment_workflow():
    link_texts = ["FBA settings", "FBA19FHMLG11 - RMN3"]
    assert _extract_sibling_fba_ids(link_texts) == ["FBA19FHMLG11"]


@pytest.mark.unit
def test_extract_sibling_fba_ids_dedupes_preserving_order():
    link_texts = ["FBA001 - IND9", "FBA002 - RFD2", "FBA001 - IND9"]
    assert _extract_sibling_fba_ids(link_texts) == ["FBA001", "FBA002"]


@pytest.mark.unit
def test_extract_sibling_fba_ids_no_matches_returns_empty_list():
    assert _extract_sibling_fba_ids(["FBA settings", "FBA Inventory", ""]) == []


# --- _extract_workflow_from_page ---------------------------------------------

class _FakeWorkflowPage:
    def __init__(self, url, link_texts):
        self.url = url
        self._link_texts = link_texts

    def locator(self, selector):
        assert selector == "a"
        return self

    def all_inner_texts(self):
        return self._link_texts


@pytest.mark.unit
def test_extract_workflow_from_page_multi_shipment():
    page = _FakeWorkflowPage(
        url="https://sellercentral.amazon.com/fba/sendtoamazon?wf=wf-abc-123",
        link_texts=["FBA settings", "FBA19K4G0K6R - IMO1", "FBA19K4KTDZ6 - IMS1"],
    )
    result = _extract_workflow_from_page(page)
    assert result == {"workflow_id": "wf-abc-123", "fba_ids": ["FBA19K4G0K6R", "FBA19K4KTDZ6"]}


@pytest.mark.unit
def test_extract_workflow_from_page_no_workflow_id_returns_none():
    page = _FakeWorkflowPage(url="https://sellercentral.amazon.com/fba/inbound-shipment/summary/FBA123/tracking", link_texts=[])
    assert _extract_workflow_from_page(page) is None


# --- discover_workflow_for_shipment -------------------------------------------

class _FakeLinkLocator:
    def __init__(self, present):
        self._present = present

    def count(self):
        return 1 if self._present else 0

    @property
    def first(self):
        return self

    def click(self):
        pass


class _FakeDiscoveryPage(_FakeWorkflowPage):
    def __init__(self, url, link_texts, has_send_to_amazon_link=True):
        super().__init__(url, link_texts)
        self._has_link = has_send_to_amazon_link

    def get_by_text(self, text, exact=True):
        assert text == "Send to Amazon (view)"
        return _FakeLinkLocator(self._has_link)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, selector, timeout=None):
        # Simulates Playwright: raises if the text never appears in link_texts.
        if selector == "text=Track shipment" and not any("- " in t for t in self._link_texts):
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {selector!r}")


@pytest.mark.unit
def test_discover_workflow_for_shipment_success(monkeypatch):
    import upload_tracking

    def fake_navigate(page, fba_id, base_url):
        assert fba_id == "FBA19K4G0K6R"
        assert base_url == "https://sellercentral.amazon.com"
        return True

    monkeypatch.setattr(upload_tracking, "navigate_to_shipment", fake_navigate)

    page = _FakeDiscoveryPage(
        url="https://sellercentral.amazon.com/fba/sendtoamazon?wf=wfb6b77412-f879-4203-8c93-f27ba68feac8",
        link_texts=["FBA19K4G0K6R - IMO1", "FBA19K4KTDZ6 - IMS1", "FBA19K4G0NSQ - ITX3"],
    )
    result = discover_workflow_for_shipment(page, "FBA19K4G0K6R", "https://sellercentral.amazon.com")
    assert result == {
        "workflow_id": "wfb6b77412-f879-4203-8c93-f27ba68feac8",
        "fba_ids": ["FBA19K4G0K6R", "FBA19K4KTDZ6", "FBA19K4G0NSQ"],
    }


@pytest.mark.unit
def test_discover_workflow_for_shipment_navigation_fails_returns_none(monkeypatch):
    import upload_tracking
    monkeypatch.setattr(upload_tracking, "navigate_to_shipment", lambda page, fba_id, base_url: False)

    page = _FakeDiscoveryPage(url="", link_texts=[])
    assert discover_workflow_for_shipment(page, "FBA_MISSING", "https://sellercentral.amazon.com") is None


@pytest.mark.unit
def test_discover_workflow_for_shipment_no_send_to_amazon_link_returns_none(monkeypatch):
    import upload_tracking
    monkeypatch.setattr(upload_tracking, "navigate_to_shipment", lambda page, fba_id, base_url: True)

    page = _FakeDiscoveryPage(
        url="https://sellercentral.amazon.com/fba/inbound-shipment/summary/FBA123/tracking",
        link_texts=[],
        has_send_to_amazon_link=False,
    )
    assert discover_workflow_for_shipment(page, "FBA123", "https://sellercentral.amazon.com") is None


# --- _process_region_discoveries ----------------------------------------------

def _row(workflow_id=""):
    return {"workflow_id": workflow_id}


@pytest.mark.unit
def test_process_region_discoveries_single_shipment_workflow(monkeypatch):
    monkeypatch.setattr(
        workflow_discovery, "discover_workflow_for_shipment",
        lambda page, fba_id, base_url: {"workflow_id": "wf-1", "fba_ids": ["FBA001"]},
    )
    sheet = {"FBA001": _row()}
    result = _process_region_discoveries(page=None, base_url="https://x", fba_ids=["FBA001"], sheet=sheet)

    assert result == {"discovered": 1, "resolved_via_sibling": 0, "unresolved": 0}
    assert sheet["FBA001"]["workflow_id"] == "wf-1"


@pytest.mark.unit
def test_process_region_discoveries_multi_shipment_workflow_skips_siblings(monkeypatch):
    calls = []

    def fake_discover(page, fba_id, base_url):
        calls.append(fba_id)
        return {"workflow_id": "wf-multi", "fba_ids": ["FBA001", "FBA002", "FBA003"]}

    monkeypatch.setattr(workflow_discovery, "discover_workflow_for_shipment", fake_discover)
    sheet = {"FBA001": _row(), "FBA002": _row(), "FBA003": _row()}
    result = _process_region_discoveries(page=None, base_url="https://x", fba_ids=["FBA001", "FBA002", "FBA003"], sheet=sheet)

    # Only the first FBA ID should trigger an actual page visit -- the other
    # two get resolved from that same discovery and must be skipped in the loop.
    assert calls == ["FBA001"]
    assert result == {"discovered": 1, "resolved_via_sibling": 2, "unresolved": 0}
    assert sheet["FBA001"]["workflow_id"] == "wf-multi"
    assert sheet["FBA002"]["workflow_id"] == "wf-multi"
    assert sheet["FBA003"]["workflow_id"] == "wf-multi"


@pytest.mark.unit
def test_process_region_discoveries_counts_unresolved_on_failed_discovery(monkeypatch):
    monkeypatch.setattr(workflow_discovery, "discover_workflow_for_shipment", lambda page, fba_id, base_url: None)
    sheet = {"FBA001": _row()}
    result = _process_region_discoveries(page=None, base_url="https://x", fba_ids=["FBA001"], sheet=sheet)

    assert result == {"discovered": 0, "resolved_via_sibling": 0, "unresolved": 1}
    assert sheet["FBA001"]["workflow_id"] == ""


@pytest.mark.unit
def test_process_region_discoveries_skips_fba_id_already_resolved_before_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workflow_discovery, "discover_workflow_for_shipment",
        lambda page, fba_id, base_url: calls.append(fba_id) or {"workflow_id": "wf-new", "fba_ids": [fba_id]},
    )
    sheet = {"FBA001": _row(workflow_id="wf-already-known"), "FBA002": _row()}
    result = _process_region_discoveries(page=None, base_url="https://x", fba_ids=["FBA001", "FBA002"], sheet=sheet)

    assert calls == ["FBA002"]
    assert result == {"discovered": 1, "resolved_via_sibling": 0, "unresolved": 0}
    assert sheet["FBA001"]["workflow_id"] == "wf-already-known"  # untouched


@pytest.mark.unit
def test_process_region_discoveries_ignores_sibling_not_in_sheet(monkeypatch):
    monkeypatch.setattr(
        workflow_discovery, "discover_workflow_for_shipment",
        lambda page, fba_id, base_url: {"workflow_id": "wf-x", "fba_ids": ["FBA001", "FBA_NOT_IN_SHEET"]},
    )
    sheet = {"FBA001": _row()}
    result = _process_region_discoveries(page=None, base_url="https://x", fba_ids=["FBA001"], sheet=sheet)

    assert result == {"discovered": 1, "resolved_via_sibling": 0, "unresolved": 0}
    assert "FBA_NOT_IN_SHEET" not in sheet


# --- format_workflow_discovery_summary ----------------------------------------

@pytest.mark.unit
def test_format_workflow_discovery_summary_includes_counts():
    text = format_workflow_discovery_summary({"discovered": 5, "resolved_via_sibling": 12, "unresolved": 2})
    assert "5" in text
    assert "12" in text
    assert "2" in text
