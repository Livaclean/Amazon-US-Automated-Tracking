import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_region_filtering_excludes_other_regions():
    """parse_and_filter_by_region should put CA rows in CA, not US."""
    from parse_excel import load_fc_prefixes, is_region_fc

    us_prefixes = {"BNA", "PHX", "IND"}
    ca_prefixes = {"YVR", "YYZ", "PRTO"}

    rows = [
        {"fc_code": "BNA6", "fba_id": "FBA001", "tracking_num": "1Z000", "carrier": "UPS", "row_number": 2},
        {"fc_code": "YVR3", "fba_id": "FBA002", "tracking_num": "1Z111", "carrier": "UPS", "row_number": 3},
        {"fc_code": "PRTO5", "fba_id": "FBA003", "tracking_num": "1Z222", "carrier": "UPS", "row_number": 4},
    ]

    us_rows = [r for r in rows if is_region_fc(r["fc_code"], us_prefixes)]
    ca_rows = [r for r in rows if is_region_fc(r["fc_code"], ca_prefixes)]

    assert len(us_rows) == 1
    assert us_rows[0]["fba_id"] == "FBA001"
    assert len(ca_rows) == 2
    assert {r["fba_id"] for r in ca_rows} == {"FBA002", "FBA003"}


def test_4letter_prefix_matching():
    """PRTO prefix should match fc_code PRTO5."""
    from parse_excel import is_region_fc
    prefixes = {"PRTO", "YVR", "YYZ"}
    assert is_region_fc("PRTO5", prefixes)
    assert is_region_fc("YVR2", prefixes)
    assert not is_region_fc("BNA6", prefixes)
