# TODOs

## P1: GitHub Actions CI
Add `.github/workflows/test.yml` running `pytest -m unit` on every push/PR.
Unit tests are fast (<5s), no external deps. Catches regressions automatically.
**Depends on:** This PR (tests must exist first).

## P2: Test Coverage Reporting
Add `pytest-cov` to requirements.txt. Run `pytest --cov=. --cov-report=html`.
Shows exactly which lines are untested.
**Depends on:** P1 (CI should display coverage).

## P3: Refactor sys.exit to Custom Exceptions
`load_config()` and `ensure_folders()` call `sys.exit(1)` on error.
Refactor to raise `ConfigError`/`SetupError` instead.
Improves testability (catch exception vs. SystemExit).
**Depends on:** Nothing.
