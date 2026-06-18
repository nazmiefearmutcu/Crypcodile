# BRIEFING — 2026-06-14T17:11:40+03:00

## Mission
Fix bugs in the base_onchain exchange connector, MCP server, create unit tests, a showcase script, and prepare the package for PyPI release.

## 🔒 My Identity
- Archetype: Connector Developer
- Roles: implementer, qa, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m1_m4_1
- Original parent: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Milestone: m1_m4_1

## 🔒 Key Constraints
- CODE_ONLY network mode: no access to external websites or services, do not use curl/wget/lynx.
- Do not cheat, do not hardcode test results, no dummy implementations.

## Current Parent
- Conversation ID: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Updated: 2026-06-14T17:11:40+03:00

## Task Summary
- **What to build**: Fix pricing/reserves and log parsing for flipped pools (Uniswap V3 and Aerodrome V2) in base_onchain connector and MCP server, fix polling loop liveness queue hang on close, cache block timestamps, create unit tests covering standard/flipped pools (using mock Web3/contracts, 4+ tests), create examples/collect_base_onchain.py, update README.md, bump version in pyproject.toml to 0.1.0, verify `uv run pytest` and `uv build`.
- **Success criteria**: Tests pass, build succeeds, code correctness verified.
- **Interface contracts**: src/crypcodile/exchanges/base_onchain/connector.py, src/crypcodile/mcp_server.py
- **Code layout**: Source in `src/crypcodile/`, tests in `tests/exchanges/base_onchain/`.

## Key Decisions Made
- Use unittest/pytest mock framework to mock web3 contracts and avoid network/RPC calls.

## Change Tracker
- **Files modified**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`: Fixed pricing/reserves and log parsing for flipped pools (Uniswap V3 and Aerodrome V2), fixed WELL-WETH check, resolved queue hang on close using sentinel, cached block timestamps.
  - `src/crypcodile/mcp_server.py`: Fixed matching pricing/reserves and slot0/liquidity query logic.
  - `src/crypcodile/api_server.py`: Wrapped long lines and updated exception raising to keep ruff check clean.
  - `tests/exchanges/base_onchain/test_connector.py`: Created 6 new unit tests targeting normal and flipped Uniswap V3/Aerodrome V2 pools, normalizer, and liveness queue sentinel.
  - `examples/collect_base_onchain.py`: Created showcase script with offline `--dry-run` and live modes.
  - `pyproject.toml`: Bumped package version to 0.1.0.
  - `README.md`: Added Base On-Chain DEX collection section.
- **Build status**: PASS
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS (608 passed)
- **Lint status**: PASS (ruff check clean, mypy strict clean)
- **Tests added/modified**: `tests/exchanges/base_onchain/test_connector.py` (6 new unit tests)

## Loaded Skills
- **Source**: none loaded (no skills required for this connector development task)
- **Local copy**: N/A
- **Core methodology**: N/A

## Artifact Index
- `src/crypcodile/exchanges/base_onchain/connector.py` — Exchange connector code.
- `src/crypcodile/mcp_server.py` — MCP server implementation.
- `tests/exchanges/base_onchain/test_connector.py` — Connector unit tests.
- `examples/collect_base_onchain.py` — Showcase script.
- `pyproject.toml` — Package configuration and version.
- `README.md` — User documentation.
