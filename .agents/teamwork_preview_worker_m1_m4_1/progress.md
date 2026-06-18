# Progress Log

Last visited: 2026-06-14T17:11:45+03:00

## Checklist
- [x] Investigate existing `connector.py` code and `mcp_server.py` implementation <!-- id: 0 -->
- [x] Fix bugs in `connector.py` (flipped pools pricing, reserve, swap parsing, WELL-WETH check, polling loop hang, block timestamp caching) <!-- id: 1 -->
- [x] Fix bugs in `mcp_server.py` tool handler (`get_onchain_price`) <!-- id: 2 -->
- [x] Create `tests/exchanges/base_onchain/test_connector.py` with mock Web3/contracts (4+ unit tests) <!-- id: 3 -->
- [x] Verify unit tests pass via `uv run pytest` <!-- id: 4 -->
- [x] Create showcase script `examples/collect_base_onchain.py` with `--dry-run` or similar check <!-- id: 5 -->
- [x] Bump version in `pyproject.toml` to `0.1.0` <!-- id: 6 -->
- [x] Update `README.md` for Base on-chain support <!-- id: 7 -->
- [x] Verify `uv build` succeeds and produces distribution artifacts <!-- id: 8 -->
- [x] Write `handoff.md` and report back to parent agent <!-- id: 9 -->
