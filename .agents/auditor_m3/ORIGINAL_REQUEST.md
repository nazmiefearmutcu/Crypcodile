## 2026-06-14T22:26:15Z

You are a forensic auditor assigned to verify the integrity of the Milestone 3 (Multi-level orderbook depth calculations) implementation in `src/crypcodile/exchanges/base_onchain/normalize.py` and the related tests.

Review the implementation to ensure it is authentic, correct, and contains no cheating:
- Check for hardcoded test results, fake mock verification logic, or bypasses.
- Run `uv run pytest` to ensure everything compiles and all 760 tests pass.
- Write your verdict and detailed evidence report to `/Users/nazmi/Crypcodile/.agents/auditor_m3/handoff.md`. If clean, declare the verdict as CLEAN. If cheating or integrity violations are detected, declare INTEGRITY VIOLATION with detailed evidence.
