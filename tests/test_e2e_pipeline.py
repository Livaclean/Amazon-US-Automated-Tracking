import os
import sys
import json
import subprocess
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.e2e


def _has_config():
    return (PROJECT_ROOT / "config.json").exists()


def _run_pipeline(*args, timeout=120):
    """Run run.py as a subprocess with auto-Enter for input() prompts."""
    cmd = [sys.executable, str(PROJECT_ROOT / "run.py")] + list(args)
    result = subprocess.run(
        cmd,
        input=b"\n",
        capture_output=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    return result


def test_collect_only(test_logger):
    """--collect-only should fetch tracking IDs without uploading to Amazon."""
    if not _has_config():
        pytest.skip("config.json not found — cannot run pipeline")

    # Check if there are Excel files in input/
    input_dir = PROJECT_ROOT / "input"
    excel_files = list(input_dir.glob("*.xls")) + list(input_dir.glob("*.xlsx"))
    if not excel_files:
        pytest.skip("No Excel files in input/ — cannot test pipeline")

    test_logger.info("Running: python run.py --collect-only")
    result = _run_pipeline("--collect-only")
    test_logger.info(f"Return code: {result.returncode}")
    test_logger.info(f"Stdout (last 500 chars): {result.stdout[-500:]}")
    if result.stderr:
        test_logger.warning(f"Stderr: {result.stderr[-500:]}")

    # Should produce tracking_ids JSON in logs/
    logs_dir = PROJECT_ROOT / "logs"
    json_files = list(logs_dir.glob("tracking_ids_*.json"))
    test_logger.info(f"Tracking ID JSON files found: {len(json_files)}")

    # Should exit without error (0) or gracefully
    assert result.returncode == 0 or b"No FBA shipments found" in result.stdout


def test_check_only(test_logger):
    """--check-only should check Amazon status without uploading."""
    if not _has_config():
        pytest.skip("config.json not found — cannot run pipeline")

    input_dir = PROJECT_ROOT / "input"
    excel_files = list(input_dir.glob("*.xls")) + list(input_dir.glob("*.xlsx"))
    if not excel_files:
        pytest.skip("No Excel files in input/ — cannot test pipeline")

    test_logger.info("Running: python run.py --check-only")
    result = _run_pipeline("--check-only")
    test_logger.info(f"Return code: {result.returncode}")
    test_logger.info(f"Stdout (last 500 chars): {result.stdout[-500:]}")

    # Should mention "CHECK COMPLETE" or "No FBA shipments"
    output = result.stdout.decode("utf-8", errors="replace")
    assert "CHECK COMPLETE" in output or "No FBA shipments" in output or result.returncode == 0


def test_from_json_structure(test_logger):
    """Verify tracking_ids JSON from --collect-only has valid structure (no browser needed)."""
    if not _has_config():
        pytest.skip("config.json not found — cannot verify JSON structure")

    # Find a tracking_ids JSON from a previous run
    logs_dir = PROJECT_ROOT / "logs"
    json_files = sorted(logs_dir.glob("tracking_ids_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not json_files:
        pytest.skip("No tracking_ids JSON in logs/ — run --collect-only first")

    json_path = json_files[0]
    test_logger.info(f"Validating JSON structure: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Validate structure: {fba_id: {"parent": [...], "sub_ids": [...]}}
    assert isinstance(data, dict), "JSON root should be a dict"
    for fba_id, entry in data.items():
        assert isinstance(fba_id, str), f"FBA key should be string: {fba_id}"
        assert "parent" in entry, f"Missing 'parent' key in {fba_id}"
        assert "sub_ids" in entry, f"Missing 'sub_ids' key in {fba_id}"
        assert isinstance(entry["sub_ids"], list), f"sub_ids should be list in {fba_id}"
    test_logger.info(f"JSON valid: {len(data)} FBA entries")
