# Auto-Resolve Unmapped FC Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop shipments with unrecognized FC codes from silently vanishing from tracking uploads — auto-detect which market a new FC code belongs to, record the fix permanently, upload that shipment's tracking in the same run, and clearly report anything that couldn't be resolved.

**Architecture:** `parse_excel.py` gains a variant of its region-splitting function that also returns rows matching no region. A new `fc_resolver.py` module groups those by FC code, probes each candidate region's Seller Central (reusing `upload_tracking.navigate_to_shipment`) to find the right market, appends confirmed codes to the matching `fc_codes/*.txt` file, and folds the newly-matched shipments into the same run's processing. `run.py` wires this in right after the browser session opens, and prints a summary at the end of the run.

**Tech Stack:** Python 3, pytest, Playwright (browser already managed by existing `upload_tracking.py` / `run.py` code — no new browser-handling code, only reuse).

## Global Constraints

- No new CLI flags — this runs automatically as part of the default `python run.py` pipeline (spec: "Automatically, every normal run").
- Skip resolution for `--check-only`, `--check-tracking`, `--collect-only`, `--from-json`, `--discover`, `--discover-queue`, standalone `--verify`, `--only-fba`, and `--fba-list` — these are diagnostic/narrow-scope modes; resolution is out of scope for them (spec explicitly excludes the first four; `--discover`/`--discover-queue`/`--verify`-standalone are diagnostic and shouldn't mutate `fc_codes/*.txt`; `--only-fba`/`--fba-list` are narrow-scope by design and weren't part of the approved spec).
- Auto-added FC file entries use the **exact observed FC code**, never a shortened/guessed prefix (spec: precision over convenience).
- Applies to AWD (`STAR-` prefix) shipments too, using the existing `fc_codes/awd_fc_codes.txt` and `upload_tracking.navigate_to_shipment`'s existing AWD URL branch.
- Follow existing code style: plain functions + dataclasses, no new dependencies, tests live in `tests/` using the existing `pytest` conventions (see `tests/test_parse_excel.py`, `tests/test_run_regions.py`).

---

## Task 1: `parse_and_filter_by_region_full()` in `parse_excel.py`

**Files:**
- Modify: `parse_excel.py` (add new function, refactor `parse_and_filter_by_region` to delegate to it — `parse_excel.py:230-264`)
- Test: `tests/test_parse_excel.py`

**Interfaces:**
- Produces: `parse_and_filter_by_region_full(config: dict) -> tuple[dict, list]` — first element identical in shape to today's `parse_and_filter_by_region(config) -> dict` (`{"US": {"FBA123": [...]}, ...}`); second element is `unmatched_rows: list[dict]`, each dict having the same keys as rows from `load_excel_file` (`fc_code`, `fba_id`, `tracking_num`, `carrier`, `row_number`).
- `parse_and_filter_by_region(config)` keeps its exact existing signature/behavior (used by other callers — verified via `grep -rn parse_and_filter_by_region` before this task).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_parse_excel.py` (append near the other `parse_and_filter_by_region` tests):

```python
def test_parse_and_filter_by_region_full_returns_unmatched_rows(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA6", "FBA_US", None, None, "1Z001", "UPS"])
    ws.append([None, None, None, "ZZZ9", "FBA_UNKNOWN", None, None, "1Z002", "UPS"])

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    wb.save(input_dir / "test.xlsx")

    us_fc = tmp_path / "us.txt"
    us_fc.write_text("BNA\n")
    config = {
        "input_folder": str(input_dir),
        "column_fc_code": 3, "column_fba_id": 4,
        "column_tracking": 7, "column_carrier": 8,
        "regions": [{"name": "US", "amazon_url": "https://x", "fc_codes_file": str(us_fc)}],
    }

    from parse_excel import parse_and_filter_by_region_full
    region_dict, unmatched = parse_and_filter_by_region_full(config)

    assert "FBA_US" in region_dict["US"]
    assert len(unmatched) == 1
    assert unmatched[0]["fba_id"] == "FBA_UNKNOWN"
    assert unmatched[0]["fc_code"] == "ZZZ9"


def test_parse_and_filter_by_region_still_returns_region_dict_only(tmp_path):
    """parse_and_filter_by_region() must keep its existing return shape — no unmatched_rows leak in."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["A", "B", "C", "D_fc", "E_fba", "F", "G", "H_tracking", "I_carrier"])
    ws.append([None, None, None, "BNA6", "FBA_US", None, None, "1Z001", "UPS"])

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    wb.save(input_dir / "test.xlsx")

    us_fc = tmp_path / "us.txt"
    us_fc.write_text("BNA\n")
    config = {
        "input_folder": str(input_dir),
        "column_fc_code": 3, "column_fba_id": 4,
        "column_tracking": 7, "column_carrier": 8,
        "regions": [{"name": "US", "amazon_url": "https://x", "fc_codes_file": str(us_fc)}],
    }

    from parse_excel import parse_and_filter_by_region
    region_dict = parse_and_filter_by_region(config)
    assert isinstance(region_dict, dict)
    assert "FBA_US" in region_dict["US"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parse_excel.py -k "region_full or still_returns" -v`
Expected: FAIL — `parse_and_filter_by_region_full` doesn't exist (ImportError/AttributeError).

- [ ] **Step 3: Implement**

In `parse_excel.py`, replace the existing `parse_and_filter_by_region` function (`parse_excel.py:230-264`) with:

```python
def parse_and_filter_by_region_full(config: dict) -> tuple:
    """
    Same as parse_and_filter_by_region, but also returns the raw rows that
    didn't match any region's FC code prefixes.
    Returns: (region_dict, unmatched_rows)
      - region_dict: {"US": {"FBA123": [...]}, ...} — identical shape to parse_and_filter_by_region()
      - unmatched_rows: [{"fc_code": ..., "fba_id": ..., "tracking_num": ..., "carrier": ..., "row_number": ...}, ...]
    """
    regions = config.get("regions", [])
    if not regions:
        logger.warning("No 'regions' key in config — falling back to US-only parse_and_filter()")
        return {"US": parse_and_filter(config)}, []

    excel_files = find_excel_files(config["input_folder"])
    if not excel_files:
        logger.warning(f"No Excel files found in {config['input_folder']}")
        return {r["name"]: {} for r in regions}, []

    all_rows = []
    for file_path in excel_files:
        logger.info(f"Reading: {file_path}")
        rows = load_excel_file(file_path, config)
        all_rows.extend(rows)
    logger.info(f"Loaded {len(all_rows)} total rows across {len(excel_files)} file(s)")

    result = {}
    matched_row_ids = set()
    for region in regions:
        name = region["name"]
        fc_file = region.get("fc_codes_file", "")
        prefixes = load_fc_prefixes(fc_file)
        if not prefixes:
            logger.warning(f"[{name}] No FC prefixes loaded from {fc_file!r}")
        region_rows = [r for r in all_rows if is_region_fc(r["fc_code"], prefixes)]
        logger.info(f"[{name}] {len(region_rows)} row(s) matched")
        matched_row_ids.update(id(r) for r in region_rows)
        result[name] = group_by_fba_id(region_rows)

    unmatched_rows = [r for r in all_rows if id(r) not in matched_row_ids]
    return result, unmatched_rows


def parse_and_filter_by_region(config: dict) -> dict:
    """
    Finds Excel files, loads all rows, then splits by region using each region's FC codes file.
    Returns: {"US": {"FBA123": [...]}, "CA": {"FBA456": [...]}, ...}
    Each region only contains FBA IDs whose FC code matches that region's prefixes.
    """
    region_dict, _ = parse_and_filter_by_region_full(config)
    return region_dict
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parse_excel.py -v`
Expected: All PASS, including the two new tests and every pre-existing `parse_and_filter_by_region` test (confirms no regression).

- [ ] **Step 5: Commit**

```bash
git add parse_excel.py tests/test_parse_excel.py
git commit -m "feat: return unmatched rows from region parsing

Rows whose FC code doesn't match any region currently vanish with no
trace. parse_and_filter_by_region_full() exposes them so callers can
act on it, while parse_and_filter_by_region() keeps its exact existing
behavior for all other callers."
```

---

## Task 2: `fc_resolver.py` — grouping, file-writing, merging, and summary formatting

**Files:**
- Create: `fc_resolver.py`
- Test: `tests/test_fc_resolver.py`

**Interfaces:**
- Consumes: `group_by_fba_id` from `parse_excel.py` (existing, `parse_excel.py:50`)
- Produces:
  - `FcMatch` dataclass — fields `fc_code: str`, `region: str`, `probe_fba_id: str`, `fba_ids: list`
  - `FcResolutionResult` dataclass — fields `resolved: list[FcMatch]`, `unresolved: list[dict]` (each `{"fc_code": str, "fba_ids": list[str]}`)
  - `group_unmatched_by_fc(unmatched_rows: list) -> dict` — `{"ITX3": [row, ...], ...}`
  - `append_fc_code_to_file(fc_codes_file: str, fc_code: str, probe_fba_id: str, today: str = None) -> None`
  - `merge_resolved_rows(resolved: list, unresolved_by_fc: dict, all_regions_data: dict) -> dict`
  - `format_fc_resolution_summary(result: FcResolutionResult, upload_results: list) -> str`
  - These are consumed by Task 3 (`probe_fc_codes`, same file) and Task 4 (`run.py` integration).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fc_resolver.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fc_resolver import (
    FcMatch,
    FcResolutionResult,
    group_unmatched_by_fc,
    append_fc_code_to_file,
    merge_resolved_rows,
    format_fc_resolution_summary,
)


def test_group_unmatched_by_fc_groups_and_uppercases():
    rows = [
        {"fc_code": "itx3", "fba_id": "FBA1"},
        {"fc_code": "ITX3", "fba_id": "FBA2"},
        {"fc_code": "mqj1", "fba_id": "FBA3"},
        {"fc_code": "", "fba_id": "FBA4"},
    ]
    grouped = group_unmatched_by_fc(rows)
    assert set(grouped.keys()) == {"ITX3", "MQJ1"}
    assert [r["fba_id"] for r in grouped["ITX3"]] == ["FBA1", "FBA2"]


def test_append_fc_code_to_file_adds_new_code(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\nPHX\n")
    append_fc_code_to_file(str(f), "ITX3", "FBA19K4G0NSQ", today="2026-08-07")
    content = f.read_text()
    assert "# auto-added 2026-08-07, confirmed via FBA19K4G0NSQ\nITX3" in content
    assert "BNA" in content
    assert "PHX" in content


def test_append_fc_code_to_file_is_idempotent(tmp_path):
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n# auto-added 2026-08-01, confirmed via FBA1\nITX3\n")
    append_fc_code_to_file(str(f), "itx3", "FBA_NEW", today="2026-08-07")
    content = f.read_text()
    assert content.upper().count("ITX3") == 1


def test_append_fc_code_to_file_creates_file_if_missing(tmp_path):
    f = tmp_path / "new_region.txt"
    append_fc_code_to_file(str(f), "MQJ1", "FBA_X", today="2026-08-07")
    assert "MQJ1" in f.read_text()


def test_append_fc_code_to_file_round_trips_through_load_fc_prefixes(tmp_path):
    """The auto-added format must still be recognized by parse_excel's own matcher —
    a comment on the same line as the code would corrupt the stored prefix, since
    load_fc_prefixes() only skips lines that START with '#'; it doesn't strip
    trailing inline comments."""
    from parse_excel import load_fc_prefixes, is_region_fc
    f = tmp_path / "us_fc_codes.txt"
    f.write_text("BNA\n")
    append_fc_code_to_file(str(f), "ITX3", "FBA19K4G0NSQ", today="2026-08-07")
    prefixes = load_fc_prefixes(str(f))
    assert is_region_fc("ITX3XXXX", prefixes)


def test_merge_resolved_rows_adds_shipments_to_correct_region():
    all_regions_data = {
        "US": {},
        "CA": {"FBA_EXISTING": [{"tracking": "1Z1", "carrier": "UPS", "row_number": 2}]},
    }
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "FBA1", "tracking_num": "1Z999", "carrier": "UPS", "row_number": 10},
        ],
    }
    resolved = [FcMatch(fc_code="ITX3", region="US", probe_fba_id="FBA1", fba_ids=["FBA1"])]

    merged = merge_resolved_rows(resolved, unresolved_by_fc, all_regions_data)

    assert "FBA1" in merged["US"]
    assert merged["US"]["FBA1"][0]["tracking"] == "1Z999"
    assert "FBA_EXISTING" in merged["CA"]  # untouched


def test_format_fc_resolution_summary_shows_uploaded_count():
    result = FcResolutionResult(
        resolved=[FcMatch(fc_code="ITX3", region="US", probe_fba_id="FBA1", fba_ids=["FBA1", "FBA2"])],
        unresolved=[{"fc_code": "XYZ9", "fba_ids": ["FBA9"]}],
    )
    upload_results = [
        {"fba_id": "FBA1", "status": "success"},
        {"fba_id": "FBA2", "status": "failed"},
    ]
    text = format_fc_resolution_summary(result, upload_results)
    assert "ITX3 -> US (confirmed via FBA1) - 1 shipment(s) uploaded" in text
    assert "XYZ9 - FBA9" in text


def test_format_fc_resolution_summary_empty_when_nothing_to_report():
    assert format_fc_resolution_summary(FcResolutionResult(), []) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fc_resolver.py -v`
Expected: FAIL — `fc_resolver` module doesn't exist yet (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `fc_resolver.py`:

```python
# fc_resolver.py
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from parse_excel import group_by_fba_id

logger = logging.getLogger(__name__)


@dataclass
class FcMatch:
    fc_code: str
    region: str
    probe_fba_id: str
    fba_ids: list = field(default_factory=list)


@dataclass
class FcResolutionResult:
    resolved: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)


def group_unmatched_by_fc(unmatched_rows: list) -> dict:
    """
    Groups unmatched rows by uppercased FC code.
    Returns {"ITX3": [row, row, ...], ...}. Rows with empty fc_code are skipped.
    """
    grouped = {}
    for row in unmatched_rows:
        fc = str(row.get("fc_code") or "").strip().upper()
        if not fc:
            continue
        grouped.setdefault(fc, []).append(row)
    return grouped


def append_fc_code_to_file(fc_codes_file: str, fc_code: str, probe_fba_id: str, today: str = None) -> None:
    """
    Appends fc_code (exact, uppercased, not a guessed prefix) to fc_codes_file, preceded
    by an auto-added comment on its OWN line. Creates the file if missing. No-op if the
    code (case-insensitive) is already present.

    The comment must NOT share a line with the code: parse_excel.load_fc_prefixes() only
    skips lines that start with "#" — it does not strip trailing inline comments — so a
    same-line comment would become part of the stored match prefix and the code would
    never match anything again.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    path = Path(fc_codes_file)
    existing_lines = []
    existing_codes = set()
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                existing_codes.add(stripped.split()[0].upper())

    if fc_code.upper() in existing_codes:
        logger.info(f"FC code {fc_code} already present in {fc_codes_file} — skipping")
        return

    existing_lines.append(f"# auto-added {today}, confirmed via {probe_fba_id}")
    existing_lines.append(fc_code.upper())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    logger.info(f"Auto-added FC code {fc_code} to {fc_codes_file}")


def probe_fc_codes(page, unresolved_by_fc: dict, configured_regions: list,
                    wait_for_login_fn, navigate_fn) -> FcResolutionResult:
    """
    For each region in configured_regions order: logs in once via wait_for_login_fn,
    then for every FC code not yet resolved, calls navigate_fn(page, representative_fba_id,
    region["amazon_url"]) to check whether the shipment exists there. The first region that
    returns True for a given FC code wins; that FC code is then skipped for later regions.
    wait_for_login_fn: callable(page, region_name, amazon_url, timeout_seconds=300) -> bool
    navigate_fn: callable(page, fba_id, base_url) -> bool
    Any FC code still unresolved after every region has been tried goes into `unresolved`.
    """
    result = FcResolutionResult()
    still_unresolved = dict(unresolved_by_fc)

    for region in configured_regions:
        if not still_unresolved:
            break
        region_name = region["name"]
        amazon_url = region["amazon_url"]

        logged_in = wait_for_login_fn(page, region_name, amazon_url, timeout_seconds=300)
        if not logged_in:
            logger.warning(f"[{region_name}] Login failed during FC resolution — skipping this region for probing")
            continue

        matched_this_region = []
        for fc_code, rows in still_unresolved.items():
            probe_fba_id = rows[0]["fba_id"]
            if navigate_fn(page, probe_fba_id, amazon_url):
                result.resolved.append(FcMatch(
                    fc_code=fc_code,
                    region=region_name,
                    probe_fba_id=probe_fba_id,
                    fba_ids=[r["fba_id"] for r in rows],
                ))
                matched_this_region.append(fc_code)

        for fc_code in matched_this_region:
            del still_unresolved[fc_code]

    for fc_code, rows in still_unresolved.items():
        result.unresolved.append({"fc_code": fc_code, "fba_ids": [r["fba_id"] for r in rows]})

    return result


def merge_resolved_rows(resolved: list, unresolved_by_fc: dict, all_regions_data: dict) -> dict:
    """
    For each FcMatch, groups its rows (looked up from unresolved_by_fc) by FBA ID and
    merges them into all_regions_data[region]. Mutates and returns all_regions_data.
    """
    for match in resolved:
        rows = unresolved_by_fc.get(match.fc_code, [])
        grouped = group_by_fba_id(rows)
        all_regions_data.setdefault(match.region, {}).update(grouped)
    return all_regions_data


def format_fc_resolution_summary(result: FcResolutionResult, upload_results: list) -> str:
    """
    Formats the end-of-run "NEW FC CODES" console/log section.
    upload_results: the run's final per-shipment result list (each dict has "fba_id" and "status");
    used to count how many of each match's shipments actually got uploaded successfully.
    Returns "" if there's nothing to report.
    """
    if not result.resolved and not result.unresolved:
        return ""

    uploaded_fba_ids = {r["fba_id"] for r in upload_results if r.get("status") == "success"}

    lines = ["=" * 60, "NEW FC CODES", "=" * 60]
    if result.resolved:
        lines.append("Auto-mapped this run:")
        for m in result.resolved:
            uploaded_count = sum(1 for fba in m.fba_ids if fba in uploaded_fba_ids)
            lines.append(
                f"  {m.fc_code} -> {m.region} (confirmed via {m.probe_fba_id}) - "
                f"{uploaded_count} shipment(s) uploaded"
            )
    if result.unresolved:
        if result.resolved:
            lines.append("")
        lines.append("UNRESOLVED - not found in any market, needs manual attention:")
        for u in result.unresolved:
            lines.append(f"  {u['fc_code']} - {', '.join(u['fba_ids'])}")
    lines.append("=" * 60)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fc_resolver.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add fc_resolver.py tests/test_fc_resolver.py
git commit -m "feat: add fc_resolver module for grouping, file-writing, and summary formatting"
```

---

## Task 3: `probe_fc_codes()` tests (browser-free, via dependency injection)

`probe_fc_codes` was implemented in Task 2 (it needs `FcMatch`/`FcResolutionResult` already defined there, and is small enough to write alongside them). This task adds its dedicated test coverage using fake `wait_for_login_fn`/`navigate_fn` callables — no real Playwright/browser needed, matching this codebase's existing preference for injectable fakes over mocking Playwright objects (see `tests/test_run_regions.py`).

**Files:**
- Test: `tests/test_fc_resolver.py` (append)

**Interfaces:**
- Consumes: `probe_fc_codes`, `FcResolutionResult` from `fc_resolver.py` (Task 2)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fc_resolver.py`:

```python
from fc_resolver import probe_fc_codes


def test_probe_fc_codes_matches_first_successful_region():
    regions = [
        {"name": "US", "amazon_url": "https://us.example"},
        {"name": "CA", "amazon_url": "https://ca.example"},
    ]
    unresolved_by_fc = {
        "ITX3": [
            {"fc_code": "ITX3", "fba_id": "FBA1"},
            {"fc_code": "ITX3", "fba_id": "FBA2"},
        ],
    }

    def fake_login(page, region_name, amazon_url, timeout_seconds=300):
        return True

    def fake_navigate(page, fba_id, base_url):
        return base_url == "https://ca.example"  # only CA "has" this shipment

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert len(result.resolved) == 1
    assert result.resolved[0].fc_code == "ITX3"
    assert result.resolved[0].region == "CA"
    assert result.resolved[0].fba_ids == ["FBA1", "FBA2"]
    assert result.unresolved == []


def test_probe_fc_codes_reports_unresolved_when_no_region_matches():
    regions = [{"name": "US", "amazon_url": "https://us.example"}]
    unresolved_by_fc = {"XYZ9": [{"fc_code": "XYZ9", "fba_id": "FBA9"}]}

    result = probe_fc_codes(None, unresolved_by_fc, regions, lambda *a, **k: True, lambda *a, **k: False)

    assert result.resolved == []
    assert result.unresolved == [{"fc_code": "XYZ9", "fba_ids": ["FBA9"]}]


def test_probe_fc_codes_skips_region_when_login_fails():
    regions = [
        {"name": "US", "amazon_url": "https://us.example"},
        {"name": "CA", "amazon_url": "https://ca.example"},
    ]
    unresolved_by_fc = {"ITX3": [{"fc_code": "ITX3", "fba_id": "FBA1"}]}

    def fake_login(page, region_name, amazon_url, timeout_seconds=300):
        return region_name == "CA"  # US login fails, CA succeeds

    def fake_navigate(page, fba_id, base_url):
        return True  # would match whichever region actually gets probed

    result = probe_fc_codes(None, unresolved_by_fc, regions, fake_login, fake_navigate)

    assert len(result.resolved) == 1
    assert result.resolved[0].region == "CA"  # US was skipped due to failed login
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fc_resolver.py -k probe_fc_codes -v`
Expected: FAIL if `probe_fc_codes` wasn't yet added in Task 2 (`ImportError`); if Task 2 already included it as written above, these should already PASS — run this step regardless to confirm the behavior explicitly before moving on.

- [ ] **Step 3: Implement (if not already present)**

`probe_fc_codes` is already defined in `fc_resolver.py` from Task 2's Step 3. No further changes needed — this step is a checkpoint, not new code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fc_resolver.py -v`
Expected: All PASS (full file, 10 tests total between Task 2 and Task 3).

- [ ] **Step 5: Commit**

```bash
git add tests/test_fc_resolver.py
git commit -m "test: cover probe_fc_codes region-matching and login-skip behavior"
```

---

## Task 4: Wire it into `run.py`

**Files:**
- Modify: `run.py`
  - Import block: `run.py:376`
  - Parse call: `run.py:423`
  - Early-exit guard: `run.py:464`
  - After browser launch: `run.py:575-576`
  - After `write_summary(...)`: `run.py:941`

**Interfaces:**
- Consumes: `parse_and_filter_by_region_full` (Task 1), `group_unmatched_by_fc`, `probe_fc_codes`, `append_fc_code_to_file`, `merge_resolved_rows`, `format_fc_resolution_summary` (Tasks 2-3), `navigate_to_shipment` (existing, `upload_tracking.py:211`), `wait_for_login` (existing, `run.py:201`)

Note: `run.py`'s `main()` isn't unit tested directly anywhere in this codebase (`tests/test_run_unit.py` imports individual helper functions, never calls `main()`) — this task's correctness is verified via the existing full test suite (no regressions) plus a manual smoke test in Task 5, consistent with how this project validated its previous `--verify` feature.

- [ ] **Step 1: Update the import block**

In `run.py:376`, change:

```python
    from parse_excel import parse_and_filter, parse_and_filter_by_region, categorize_shipments
```

to:

```python
    from parse_excel import parse_and_filter, parse_and_filter_by_region_full, categorize_shipments
```

And in `run.py:378-385`, add `navigate_to_shipment` to the existing `upload_tracking` import:

```python
    from upload_tracking import (
        create_browser_context,
        check_login_status,
        discover_page_elements,
        upload_all_shipments,
        check_all_shipments_on_amazon,
        get_slot_count,
        navigate_to_shipment,
    )
```

- [ ] **Step 2: Update the parse call to capture unmatched rows**

In `run.py:423`, change:

```python
    all_regions_data = parse_and_filter_by_region(config)
```

to:

```python
    all_regions_data, unmatched_rows = parse_and_filter_by_region_full(config)
```

- [ ] **Step 3: Extend the early-exit guard**

In `run.py:464`, change:

```python
    if not shipments_all and not no_excel_needed:
```

to:

```python
    if not shipments_all and not unmatched_rows and not no_excel_needed:
```

This prevents the tool from exiting before opening Chrome in the edge case where every row in the sheet has an unrecognized FC code (previously this would exit with "No FBA shipments found" and never get a chance to resolve them).

- [ ] **Step 4: Insert the resolution block after browser launch**

In `run.py`, locate:

```python
    page = context.new_page()
    results = []

    try:
        # Discovery mode - dumps page elements for first-run selector identification
        if args.discover:
```

Insert the resolution block between `results = []` and the `try:` line's first `if args.discover:` — i.e. as the first statement inside the `try:` block, before the discovery check:

```python
    page = context.new_page()
    results = []

    try:
        # FC code auto-resolution — runs before any mode branch below, but only for the
        # default full pipeline. Diagnostic/narrow-scope modes are skipped deliberately.
        fc_result = None
        skip_fc_resolution = (
            args.discover or getattr(args, 'discover_queue', False) or
            args.check_only or args.collect_only or
            args.only_fba or args.fba_list or
            (args.verify and not any([
                args.collect_only, args.check_only, args.from_json, args.discover,
                getattr(args, 'discover_queue', False),
            ]))
        )
        if unmatched_rows and not skip_fc_resolution:
            from fc_resolver import (
                group_unmatched_by_fc, probe_fc_codes, merge_resolved_rows,
                append_fc_code_to_file, format_fc_resolution_summary,
            )
            unresolved_by_fc = group_unmatched_by_fc(unmatched_rows)
            print(f"\n{len(unresolved_by_fc)} unrecognized FC code(s) found — checking which market they belong to...")
            fc_result = probe_fc_codes(page, unresolved_by_fc, configured_regions, wait_for_login, navigate_to_shipment)

            for match in fc_result.resolved:
                region_cfg = next(r for r in configured_regions if r["name"] == match.region)
                append_fc_code_to_file(region_cfg["fc_codes_file"], match.fc_code, match.probe_fba_id)
                print(f"  {match.fc_code} -> {match.region} (confirmed via {match.probe_fba_id})")

            if fc_result.resolved:
                all_regions_data = merge_resolved_rows(fc_result.resolved, unresolved_by_fc, all_regions_data)
                shipments_all = {}
                for region_data in all_regions_data.values():
                    shipments_all.update(region_data)
                shipments_raw, missing_tracking = categorize_shipments(shipments_all)
                added = sum(len(m.fba_ids) for m in fc_result.resolved)
                print(f"  +{added} shipment(s) added to this run's queue after auto-resolving "
                      f"{len(fc_result.resolved)} new FC code(s).")

            if fc_result.unresolved:
                print(f"  {len(fc_result.unresolved)} FC code(s) could not be matched to any "
                      f"market — see summary at end of run.")

        # Discovery mode - dumps page elements for first-run selector identification
        if args.discover:
```

- [ ] **Step 5: Print the end-of-run FC resolution summary**

In `run.py`, locate the block around line 941:

```python
        write_summary(results, config["logs_folder"])

    finally:
```

Change to:

```python
        write_summary(results, config["logs_folder"])

        if fc_result is not None:
            from fc_resolver import format_fc_resolution_summary
            summary_text = format_fc_resolution_summary(fc_result, results)
            if summary_text:
                print("\n" + summary_text)
                ts_fc = datetime.now().strftime("%Y%m%d_%H%M%S")
                fc_log = Path(config["logs_folder"]) / f"fc_resolution_{ts_fc}.txt"
                fc_log.write_text(summary_text, encoding="utf-8")

    finally:
```

- [ ] **Step 6: Run the full existing test suite to confirm no regressions**

Run: `pytest tests/ -m "not integration and not e2e and not slow" -v`
Expected: All PASS. (Integration/e2e/slow tests require a real browser or config — skip them here; Task 5 below covers real-world verification.)

- [ ] **Step 7: Commit**

```bash
git add run.py
git commit -m "feat: auto-resolve unmapped FC codes during the normal upload run

Rows whose FC code doesn't match any region used to vanish silently.
Now the pipeline probes each region's Seller Central to find the right
market, records the fix in fc_codes/*.txt, uploads the shipment in the
same run, and reports anything still unresolved at the end of the run."
```

---

## Task 5: Manual verification against today's real unresolved FC codes

This project's `fc_codes/us_fc_codes.txt` currently lacks `ITX3`, `IMO1`, `IMS1`, and `MQJ1` — the exact real-world case that motivated this feature (6 shipments silently dropped on 2026-08-07). This is the smoke test.

**Files:** none (manual verification only)

- [ ] **Step 1: Confirm the codes are still missing**

Run: `git diff --stat fc_codes/` should be empty, and:

```bash
grep -rn "ITX3\|IMO1\|IMS1\|MQJ1" fc_codes/
```

Expected: no matches (confirms the test case is still live).

- [ ] **Step 2: Run a real upload with the current input file**

Close Chrome, then run:

```bash
python run.py
```

Watch console output for the new `N unrecognized FC code(s) found — checking which market they belong to...` line, followed by `ITX3 -> US (confirmed via FBA...)` (or whichever region actually matches) for each of the four codes, and the `NEW FC CODES` summary block at the end of the run.

- [ ] **Step 3: Verify the fc_codes file was updated**

```bash
grep -n "ITX3\|IMO1\|IMS1\|MQJ1" fc_codes/us_fc_codes.txt
```

Expected: four new code lines, each preceded by its own `# auto-added <today's date>, confirmed via FBA...` comment line.

- [ ] **Step 4: Verify the previously-dropped shipments got tracking uploaded**

Check `logs/fc_resolution_<timestamp>.txt` for the uploaded counts, and spot-check one of the six FBA IDs (e.g. `FBA19JMCJ739`) directly on Amazon Seller Central to confirm tracking is now present.

- [ ] **Step 5: Run the tool again and confirm idempotency**

Run `python run.py` a second time. Expected: no `unrecognized FC code(s) found` line this time (the four codes are now recognized), and the six shipments are skipped via the normal done-cache (already complete) rather than re-triggering resolution.

- [ ] **Step 6: Commit the now-updated fc_codes file**

```bash
git add fc_codes/us_fc_codes.txt
git commit -m "chore: record auto-resolved FC codes from live verification run"
```

(If Step 2 resolved the codes to a region other than US, adjust the `git add` path accordingly.)
