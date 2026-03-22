import pytest
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fetch_sub_tracking import (
    normalize_carrier,
    extract_ups_tracking_from_text,
    extract_fedex_tracking_from_text,
    deduplicate_tracking_numbers,
)


def test_normalize_ups_variations():
    for name in ["UPS", "ups", "Ups", "United Parcel Service"]:
        assert normalize_carrier(name) == "ups"

def test_normalize_fedex_variations():
    for name in ["FedEx", "fedex", "FEDEX", "Federal Express"]:
        assert normalize_carrier(name) == "fedex"

def test_normalize_unknown():
    assert normalize_carrier("DHL") == "unknown"
    assert normalize_carrier("") == "unknown"
    assert normalize_carrier(None) == "unknown"


def test_extract_ups_basic():
    text = "Package 1: 1Z999AA10123456784  Package 2: 1Z999AA10123456785"
    result = extract_ups_tracking_from_text(text)
    assert "1Z999AA10123456784" in result
    assert "1Z999AA10123456785" in result

def test_extract_ups_excludes_master():
    master = "1ZMASTER0000000001"
    text = f"Shipment: {master}  Package: 1Z999AA10123456784"
    result = extract_ups_tracking_from_text(text, exclude=master)
    assert master.upper() not in result
    assert "1Z999AA10123456784" in result

def test_extract_ups_empty():
    assert extract_ups_tracking_from_text("") == []

def test_extract_ups_no_matches():
    assert extract_ups_tracking_from_text("no tracking numbers here") == []


def test_extract_fedex_12digit():
    text = "Tracking: 123456789012  Also: 987654321098"
    result = extract_fedex_tracking_from_text(text)
    assert "123456789012" in result
    assert "987654321098" in result

def test_extract_fedex_20digit():
    text = "Tracking: 12345678901234567890"
    result = extract_fedex_tracking_from_text(text)
    assert "12345678901234567890" in result

def test_extract_fedex_excludes_short():
    # 10-digit numbers should NOT match (min is 12)
    text = "Call 1234567890 for support"
    result = extract_fedex_tracking_from_text(text)
    assert "1234567890" not in result


def test_deduplicate_basic():
    result = deduplicate_tracking_numbers(["1Z001", "1Z002", "1Z001"])
    assert result == ["1Z001", "1Z002"]

def test_deduplicate_preserves_order():
    result = deduplicate_tracking_numbers(["1Z003", "1Z001", "1Z002"])
    assert result == ["1Z003", "1Z001", "1Z002"]

# ---------------------------------------------------------------------------
# NEW: Edge case coverage for all regex branches
# ---------------------------------------------------------------------------

def test_extract_ups_1z_format():
    """UPS pattern should match 1Z + 16 alphanumeric = 18 total chars."""
    text = "Package: 1ZABCDEFGH12345678"
    result = extract_ups_tracking_from_text(text)
    assert "1ZABCDEFGH12345678" in result


def test_extract_fedex_15digit():
    """FedEx pattern should match 15-digit tracking numbers."""
    text = "Tracking: 123456789012345"
    result = extract_fedex_tracking_from_text(text)
    assert "123456789012345" in result


def test_extract_fedex_22digit():
    """FedEx pattern should match 22-digit tracking numbers."""
    text = "Tracking: 1234567890123456789012"
    result = extract_fedex_tracking_from_text(text)
    assert "1234567890123456789012" in result


def test_deduplicate_empty():
    """deduplicate_tracking_numbers should handle empty list."""
    result = deduplicate_tracking_numbers([])
    assert result == []


def test_deduplicate_none_input():
    """deduplicate_tracking_numbers should handle None-like input."""
    result = deduplicate_tracking_numbers(None)
    assert result == []
