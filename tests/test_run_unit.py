import os
import sys
import json
import time
import logging
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from run import (
    setup_logging,
    load_config,
    ensure_folders,
    cleanup_logs,
    write_summary,
    write_region_summary,
    write_shipment_records,
    collect_updated_row_numbers,
)


@pytest.mark.unit
def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(str(tmp_path))
        log_files = list(tmp_path.glob("tracking_upload_*.log"))
        assert len(log_files) == 1
    finally:
        root.handlers = original_handlers
        root.level = original_level


@pytest.mark.unit
def test_setup_logging_dual_handlers(tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(str(tmp_path))
        has_file = any(isinstance(h, logging.FileHandler) for h in root.handlers)
        has_stream = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert has_file
        assert has_stream
    finally:
        root.handlers = original_handlers
        root.level = original_level


@pytest.mark.unit
def test_load_config_reads_real_json(tmp_path):
    config_data = {"input_folder": "input", "output_folder": "output", "logs_folder": "logs"}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    result = load_config(str(config_file))
    assert result["input_folder"] == "input"
    assert result["output_folder"] == "output"


@pytest.mark.unit
def test_load_config_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        load_config(str(tmp_path / "nonexistent.json"))
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_load_config_applies_defaults(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"input_folder": "input"}', encoding="utf-8")
    result = load_config(str(config_file))
    assert result["headless"] is False
    assert result["column_fc_code"] == 3
    assert result["column_fba_id"] == 4
    assert result["column_tracking"] == 7
    assert result["column_carrier"] == 8
    assert result["delay_between_shipments_seconds"] == 2
    assert result["us_fc_codes_file"] == "fc_codes/us_fc_codes.txt"


@pytest.mark.unit
def test_ensure_folders_creates_dirs(tmp_config):
    ensure_folders(tmp_config)
    assert Path(tmp_config["input_folder"]).is_dir()
    assert Path(tmp_config["output_folder"]).is_dir()
    assert Path(tmp_config["logs_folder"]).is_dir()
    assert (Path(tmp_config["logs_folder"]) / "screenshots").is_dir()


@pytest.mark.unit
def test_ensure_folders_clears_old_screenshots(tmp_config):
    ss_dir = Path(tmp_config["logs_folder"]) / "screenshots"
    ss_dir.mkdir(parents=True)
    (ss_dir / "old_screenshot.png").write_text("fake png")
    ensure_folders(tmp_config)
    assert not (ss_dir / "old_screenshot.png").exists()


@pytest.mark.unit
def test_ensure_folders_missing_key_exits():
    with pytest.raises(SystemExit) as exc_info:
        ensure_folders({"output_folder": "out", "logs_folder": "logs"})
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_cleanup_logs_deletes_debug_files(tmp_path):
    (tmp_path / "fedex_page_123.txt").write_text("debug")
    (tmp_path / "ups_page_456.txt").write_text("debug")
    (tmp_path / "debug_test.txt").write_text("debug")
    cleanup_logs(str(tmp_path))
    assert not (tmp_path / "fedex_page_123.txt").exists()
    assert not (tmp_path / "ups_page_456.txt").exists()
    assert not (tmp_path / "debug_test.txt").exists()


@pytest.mark.unit
def test_cleanup_logs_keeps_done_caches(tmp_path):
    (tmp_path / "completed_fba_US.txt").write_text("FBA001\nFBA002\n")
    cleanup_logs(str(tmp_path))
    assert (tmp_path / "completed_fba_US.txt").exists()


@pytest.mark.unit
def test_cleanup_logs_keeps_recent_3(tmp_path):
    base_time = time.time() - 100
    for i in range(5):
        f = tmp_path / f"summary_20260322_{100000 + i:06d}.txt"
        f.write_text(f"summary {i}")
        os.utime(str(f), (base_time + i * 2, base_time + i * 2))
    cleanup_logs(str(tmp_path))
    remaining = sorted(tmp_path.glob("summary_*.txt"))
    assert len(remaining) == 3


@pytest.mark.unit
def test_write_summary_creates_file(tmp_path, sample_results):
    write_summary(sample_results, str(tmp_path))
    files = list(tmp_path.glob("summary_*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Total FBA shipments: 4" in content
    assert "[OK]" in content
    assert "[PARTIAL]" in content
    assert "[FAILED]" in content
    assert "[SKIP]" in content


@pytest.mark.unit
def test_write_region_summary_creates_file(tmp_path, sample_results):
    write_region_summary("US", sample_results, str(tmp_path), "20260322_120000")
    files = list(tmp_path.glob("summary_US_*.txt"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "REGION: US" in content


@pytest.mark.unit
def test_write_shipment_records_creates_two_files(tmp_path):
    has = {"FBA001": [{"tracking": "1Z001", "carrier": "UPS"}]}
    missing = ["FBA002", "FBA003"]
    write_shipment_records(has, missing, str(tmp_path))
    with_files = list(tmp_path.glob("shipments_with_tracking_*.txt"))
    missing_files = list(tmp_path.glob("shipments_missing_tracking_*.txt"))
    assert len(with_files) == 1
    assert len(missing_files) == 1
    assert "FBA001" in with_files[0].read_text(encoding="utf-8")
    assert "FBA002" in missing_files[0].read_text(encoding="utf-8")


@pytest.mark.unit
def test_collect_updated_row_numbers_success():
    shipments = {
        "FBA001": [{"tracking": "1Z001", "carrier": "UPS", "row_number": 2}],
        "FBA002": [{"tracking": "1Z002", "carrier": "UPS", "row_number": 3},
                    {"tracking": "1Z003", "carrier": "UPS", "row_number": 4}],
        "FBA003": [{"tracking": "1Z004", "carrier": "UPS", "row_number": 5}],
    }
    results = [
        {"fba_id": "FBA001", "status": "success"},
        {"fba_id": "FBA002", "status": "partial"},
        {"fba_id": "FBA003", "status": "failed"},
    ]
    rows = collect_updated_row_numbers(shipments, results)
    assert rows == {2, 3, 4}


@pytest.mark.unit
def test_collect_updated_row_numbers_all_failed():
    shipments = {"FBA001": [{"tracking": "1Z001", "carrier": "UPS", "row_number": 2}]}
    results = [{"fba_id": "FBA001", "status": "failed"}]
    rows = collect_updated_row_numbers(shipments, results)
    assert rows == set()
