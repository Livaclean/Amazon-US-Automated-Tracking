# Auto-Resolve Unmapped FC Codes
**Date:** 2026-08-07
**Status:** Approved

## Overview

Today, `parse_and_filter_by_region()` in `parse_excel.py` matches each Excel row to a region by checking whether its FC code starts with a known prefix in that region's `fc_codes/*.txt` file. Rows whose FC code doesn't match *any* region are silently dropped — never written to `shipments_with_tracking`, `shipments_missing_tracking`, or any log. This was discovered on 2026-08-07 when 6 real shipments (FC codes `ITX3`, `IMO1`, `IMS1`, `MQJ1`) vanished from processing with no trace.

This feature closes that gap: on every normal run, any row with an unrecognized FC code triggers an automatic probe against each region's Amazon Seller Central to determine the correct market, permanently records the fix, and folds the shipment into that region's normal processing for the same run. Anything that can't be resolved is called out clearly at the end of the run.

## Command Structure

No new flags. This runs automatically as part of the default pipeline (`python run.py`, with or without `--regions`, `--skip-carrier`, etc.) whenever unmatched rows exist. Runs with zero unmatched rows pay no extra cost — the probing step only launches when there is something to resolve. Not triggered by `--check-only`, `--check-tracking`, `--collect-only`, or `--from-json` (these don't do full region processing / uploads).

## Data Flow

1. **Compute unmatched rows** — `parse_and_filter_by_region()` is extended to also return the flat `all_rows` list it already builds internally. In `run.py`, after building `all_regions_data`, diff `all_rows` against the union of all matched region dicts to get `unmatched_rows`.
2. **Group by unique FC code** — dedupe `unmatched_rows` by `fc_code` (case-insensitive). Each unique FC code is probed once, using its first row's FBA ID as the representative.
3. **Probe each region** — for each unresolved FC code, iterate `config["regions"]` in order. For each region, open (or reuse, if a session for that domain is already open) a browser page logged into that region's `amazon_url`, and call the existing `navigate_to_shipment(page, fba_id, base_url)` from `upload_tracking.py` — it already knows the AWD (`STAR-` prefix) vs. standard FBA URL pattern and already returns `False` on a "not found" page. First region where it returns `True` is the match; stop probing further regions for that FC code.
4. **On match** — append the FC code to that region's `fc_codes/<region>_fc_codes.txt` (see Auto-Fix below), then re-run `is_region_fc` matching for every unmatched row sharing that FC code and merge them into that region's entry in `all_regions_data`. They now flow through carrier scraping + upload exactly like any other row in that region — no separate upload path.
5. **On no match in any region** — leave all `fc_codes/*.txt` files untouched; carry the FC code and its associated FBA ID(s) forward to the end-of-run notification.

## Auto-Fix Behavior

On a confirmed match, append the matched region's fc_codes file using the **exact FC code as observed** (not a shortened/guessed prefix), with its comment on its own line above the code — never trailing on the same line, since the existing `load_fc_prefixes()` matcher only skips lines that *start* with `#` and does not strip inline comments, so a same-line comment would corrupt the stored match prefix:

```
# auto-added 2026-08-07, confirmed via FBA19K4G0NSQ
ITX3
```

Using the exact observed code (rather than inferring a shorter shared prefix like `ITX`) keeps matching precise — no risk of the auto-added entry accidentally sweeping in unrelated FCs that happen to share a shorter prefix. A human can consolidate into a shorter prefix later if they choose; the tool never does this automatically.

## Notification / Summary Output

Appended after the existing `TRACKING UPLOAD SUMMARY` block, only printed when there's something to report:

```
============================================================
NEW FC CODES
============================================================
Auto-mapped this run:
  ITX3 -> US (confirmed via FBA19K4G0NSQ) - 2 shipment(s) uploaded
  MQJ1 -> US (confirmed via FBA19KCLDHG4) - 1 shipment(s) uploaded

UNRESOLVED - not found in any market, needs manual attention:
  XYZ9 - FBA19ABCDEF1, FBA19ABCDEF2
============================================================
```

Also written to `logs/fc_resolution_<timestamp>.txt` so it isn't lost if the console output scrolls by or the window closes.

## Error Handling / Edge Cases

| Situation | Behavior |
|-----------|----------|
| Probed FBA ID isn't live yet in any region (e.g. shipment not yet created on Amazon) | Reads as "not found" everywhere → falls into UNRESOLVED bucket. Safe default; no wrong guess is recorded. |
| FC code matches an existing prefix that's already in a *different* region's file | Not expected to occur (existing region matching already prevents this from being "unmatched"); out of scope. |
| Region's browser session fails to open / login times out during probing | Log warning, skip that region for this probe, continue to next region in order; if all regions fail to load, treat as unresolved for this run (will retry next run). |
| Two unmatched rows share an FC code but the representative FBA ID's probe fails while a sibling shipment would have succeeded (e.g. representative was cancelled) | Accepted rare-case risk — not worth extra complexity. Shows up as unresolved; user can re-run and it'll pick a different representative if the row order changes, or investigate manually. |
| AWD (`STAR-` prefix) shipments with unmapped FC codes | Same detection/fix flow applies, using `navigate_to_shipment`'s existing AWD URL branch and `fc_codes/awd_fc_codes.txt`. |

## Changes to Existing Files

### `parse_excel.py`
- `parse_and_filter_by_region(config)` — also return the flat `all_rows` list (e.g. as a second return value or under an `"_all_rows"` key) so callers can compute unmatched rows without re-parsing.
- New helper: `find_unmatched_rows(all_rows, matched_by_region) -> list[dict]`

### `run.py`
- After building `all_regions_data`: compute unmatched rows, group by FC code, run the resolution pass (new function, e.g. `resolve_unmatched_fcs(...)` — likely lives in a new small module `fc_resolver.py` to keep `run.py` from growing further).
- Merge newly-resolved rows into `all_regions_data` before the main per-region upload loop runs.
- After the main upload loop: print the `NEW FC CODES` summary section and write `logs/fc_resolution_<timestamp>.txt` if there's anything to report.

### New module: `fc_resolver.py`
- `resolve_unmatched_fcs(unmatched_rows, configured_regions, config) -> FcResolutionResult`
- `FcResolutionResult` fields: `resolved: list[dict]` (`{fc_code, region, fba_id, row_count}`), `unresolved: list[dict]` (`{fc_code, fba_ids}`)
- Reuses `navigate_to_shipment` from `upload_tracking.py` and `is_region_fc` / `load_fc_prefixes` from `parse_excel.py` — no duplicated matching logic.

### No changes
- `upload_tracking.py`, `fetch_sub_tracking.py`, `verify_tracking.py`, `highlight_excel.py` — reused as-is.
