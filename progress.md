# Progress Log

- Last visited: 2026-07-14
- Branch: `ralph/continuous-dev`
- Status: Continuous loop Waves 1–51 COMPLETE (since `66b44af`). Version `0.1.044`.

## Ralph continuous-dev (0.1.044)

On `ralph/continuous-dev` at **0.1.044**, continuous-dev rotation continues. Wave 51: REST **`GET /api/v1/catalog/search`** accepts optional **`channel`** and **`exchange`** query filters (empty/whitespace → no filter; values stripped) and forwards them to `client.search_symbols` — parity with CLI `search` / `Catalog.search_symbols`. Remaining candidates: CLI catalog-dates, MCP search exchange filter, Bybit book resync, portal/payment polish, more indicator CLI modes.

See `docs/ralph/LOOP_STATE.md` for full wave tables and commits.
