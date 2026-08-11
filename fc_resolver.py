# fc_resolver.py
import logging
import re
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

    if not re.fullmatch(r"[A-Z0-9-]{2,}", fc_code.upper()):
        logger.warning(f"FC code {fc_code!r} doesn't look like a valid prefix — not writing to {fc_codes_file}")
        return

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


def _dedupe_fba_ids(rows: list) -> list:
    """
    Normalizes a group of unmatched rows into the distinct FBA IDs they represent,
    mirroring parse_excel.group_by_fba_id: splits combined IDs like "STAR-A/STAR-B"
    into separate IDs, drops Walmart IDs ending in "WFA" and TikTok IDs starting
    with "IBR", and de-duplicates while preserving first-seen order.
    """
    seen = []
    for row in rows:
        fba_id_raw = str(row.get("fba_id") or "").strip()
        for part in fba_id_raw.split("/"):
            part = part.strip()
            if not part or part.upper().endswith("WFA") or part.upper().startswith("IBR"):
                continue
            if part not in seen:
                seen.append(part)
    return seen


def probe_fc_codes(page, unresolved_by_fc: dict, configured_regions: list,
                    wait_for_login_fn, navigate_fn, login_timeout_seconds: int = 60) -> FcResolutionResult:
    """
    For each region in configured_regions order: logs in once via wait_for_login_fn,
    then for every FC code not yet resolved, calls navigate_fn(page, representative_fba_id,
    region["amazon_url"]) to check whether the shipment exists there. The first region that
    returns True for a given FC code wins; that FC code is then skipped for later regions.
    wait_for_login_fn: callable(page, region_name, amazon_url, timeout_seconds=300) -> bool
    navigate_fn: callable(page, fba_id, base_url) -> bool
    Any FC code still unresolved after every region has been tried goes into `unresolved`.

    STAR-prefixed FBA IDs (AWD shipments) are resolved directly to the region named "AWD",
    without probing — navigate_to_shipment already routes STAR- IDs to the AWD URL pattern
    regardless of which region's amazon_url is passed, so probing would falsely match
    whichever non-AWD region happens to share AWD's amazon_url (typically US).

    Non-AWD FC codes are never resolved to a region whose amazon_url is shared by another
    configured region (e.g. UK/EU/FR sharing amazon.de) — a successful probe there is
    genuinely ambiguous since navigate_to_shipment can't tell such regions apart, so it's
    left unresolved rather than silently attributed to whichever region is tried first.
    """
    result = FcResolutionResult()
    still_unresolved = dict(unresolved_by_fc)

    url_to_regions = {}
    for region in configured_regions:
        if region["name"] == "AWD":
            continue
        url_to_regions.setdefault(region["amazon_url"], []).append(region["name"])

    awd_region = next((r for r in configured_regions if r["name"] == "AWD"), None)

    if awd_region is not None:
        awd_fc_codes = [fc for fc, rows in still_unresolved.items()
                         if rows[0]["fba_id"].startswith("STAR-")]
        for fc_code in awd_fc_codes:
            rows = still_unresolved.pop(fc_code)
            result.resolved.append(FcMatch(
                fc_code=fc_code,
                region=awd_region["name"],
                probe_fba_id=rows[0]["fba_id"],
                fba_ids=_dedupe_fba_ids(rows),
            ))

    for region in configured_regions:
        if not still_unresolved:
            break
        region_name = region["name"]
        amazon_url = region["amazon_url"]
        if region_name == "AWD":
            continue

        logged_in = wait_for_login_fn(page, region_name, amazon_url, timeout_seconds=login_timeout_seconds)
        if not logged_in:
            logger.warning(f"[{region_name}] Login failed during FC resolution — skipping this region for probing")
            continue

        ambiguous = len(url_to_regions[amazon_url]) > 1
        matched_this_region = []
        for fc_code, rows in still_unresolved.items():
            probe_fba_id = rows[0]["fba_id"]
            if navigate_fn(page, probe_fba_id, amazon_url):
                if ambiguous:
                    logger.warning(
                        f"FC code {fc_code} matched on {amazon_url}, which is shared by "
                        f"{url_to_regions[amazon_url]} — cannot disambiguate; leaving unresolved"
                    )
                    continue
                result.resolved.append(FcMatch(
                    fc_code=fc_code,
                    region=region_name,
                    probe_fba_id=probe_fba_id,
                    fba_ids=_dedupe_fba_ids(rows),
                ))
                matched_this_region.append(fc_code)

        for fc_code in matched_this_region:
            del still_unresolved[fc_code]

    for fc_code, rows in still_unresolved.items():
        result.unresolved.append({"fc_code": fc_code, "fba_ids": _dedupe_fba_ids(rows)})

    return result


def merge_resolved_rows(resolved: list, unresolved_by_fc: dict, all_regions_data: dict) -> dict:
    """
    For each FcMatch, groups its rows (looked up from unresolved_by_fc) by FBA ID and
    merges them into all_regions_data[region]. Extends existing entry lists rather than
    replacing them, so two resolved matches that happen to touch the same FBA ID don't
    clobber each other. Mutates and returns all_regions_data.
    """
    for match in resolved:
        rows = unresolved_by_fc.get(match.fc_code, [])
        grouped = group_by_fba_id(rows)
        target = all_regions_data.setdefault(match.region, {})
        for fba_id, entries in grouped.items():
            existing = target.setdefault(fba_id, [])
            existing.extend(e for e in entries if e not in existing)
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
        lines.append("UNRESOLVED - not found in the market(s) checked, needs manual attention:")
        for u in result.unresolved:
            lines.append(f"  {u['fc_code']} - {', '.join(u['fba_ids'])}")
    lines.append("=" * 60)
    return "\n".join(lines)
