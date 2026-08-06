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
