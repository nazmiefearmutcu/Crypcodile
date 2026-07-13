# Progress Log

- Last visited: 2026-07-14
- Branch: `ralph/continuous-dev`
- Status: Continuous loop Waves 1–52 COMPLETE (since `66b44af`). Version `0.1.044`.

## Ralph continuous-dev (0.1.044)

On `ralph/continuous-dev` at **0.1.044**, continuous-dev rotation continues. Wave 52: MCP **`search_symbols`** optional **`exchange`** filter (parity with REST/CLI/Catalog; channel already present); verified MCP **`inventory_snapshot`** already has channel+exchange filters; REST **`GET /api/v1/catalog/symbols?channel=&exchange=`** returns sorted distinct inventory symbols (lighter than full inventory; capabilities entry). Remaining candidates: CLI catalog-dates, Bybit book resync, portal/payment polish, more indicator CLI modes.

See `docs/ralph/LOOP_STATE.md` for full wave tables and commits.
