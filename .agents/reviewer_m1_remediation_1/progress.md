# Progress Update

- Last visited: 2026-06-14T16:23:02Z
- Status: Investigating codebase and running tests.
- Findings:
  - Fails on 15 unit tests in `tests/exchanges/base_onchain/` due to `MagicMock` not being awaitable for `w3.provider.disconnect()`.
  - Fails on 5 E2E tests in `tests/e2e/test_tier1_features.py` due to USDC payment verification issue, 500 error instead of 400, and reversed condition in block pagination.
