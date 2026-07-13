# Progress Log

- Last visited: 2026-07-14
- Branch: `ralph/continuous-dev`
- Status: Continuous loop Waves 1–45 COMPLETE (since `66b44af`). Version `0.1.044`.

## Ralph continuous-dev (0.1.044)

On `ralph/continuous-dev` at **0.1.044**, continuous-dev rotation continues. Wave 45 added **REST `GET /api/v1/catalog/exchanges`** via `Catalog.list_exchanges_on_disk` / `CrypcodileClient.list_exchanges_on_disk` (hive `exchange=` partition discovery; distinct from factory `list_exchanges` / `GET /api/v1/exchanges`). Wave 44 added MCP `list_dates` + shared `json_safe`. Remaining candidates: Bybit book resync, portal/payment polish, more indicator CLI modes, optional MCP list_exchanges_on_disk.

See `docs/ralph/LOOP_STATE.md` for full wave tables and commits.
