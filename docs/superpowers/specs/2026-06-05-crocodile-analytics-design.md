# Crocodile Analytics — Design Spec (2026-06-05)

## Context

Crocodile core (milestones M1–M5) is complete: ingest → normalize → store
(hive Parquet + DuckDB) → retrieve (client/CLI/replay/export) → resample
(OHLCV / book / VWAP). The README roadmap names **Analytics** as the next layer:

> IV surface, greeks, skew, term structure, basis, funding APR.

This spec designs that layer. It is a **derived-analytics** layer: it reads the
already-stored canonical records through the DuckDB `Catalog` and computes
metrics. It adds **no** new ingestion, no new exchange code, and no live
streaming — exactly like `crocodile.resample`, which it sits beside.

## Goal

A new `crocodile.analytics` package of small, independently-testable functions
that take a `Catalog` (or pre-loaded records) plus a symbol/underlying and time
range/instant, and return Polars DataFrames. Plus client + CLI exposure, docs,
and runnable examples.

## Non-goals (YAGNI)

- No new data sources or connectors.
- No live/streaming analytics — operate on stored data only.
- No charting/dashboard (separate roadmap item).
- No portfolio/PnL/strategy backtesting.
- Only the six roadmap items, nothing more.

## Dependency decision

**No new heavy dependencies.** Black-Scholes pricing, greeks, and the implied-
vol solver are implemented with the standard-library `math` module only
(`math.erf` gives the normal CDF). We deliberately avoid adding `numpy`/`scipy`:
the math is closed-form and scalar, the existing stack (Polars/DuckDB) handles
all dataframe-shaped work, and a small dependency surface matters for an
open-source data engine. Outputs are `polars.DataFrame` for consistency with
`crocodile.resample`. Time alignment for basis uses DuckDB `ASOF JOIN`.

## Architecture

New package `src/crocodile/analytics/` with one module per concern:

```
analytics/
  __init__.py
  blackscholes.py   # pure-math option pricing, greeks, IV solver (foundation)
  funding.py        # funding rate APR, cumulative funding, time series
  basis.py          # spot-future basis + perp (mark-index) basis, annualized
  volsurface.py     # IV surface, vol skew, term structure (uses blackscholes)
```

Each module exposes free functions (no shared mutable state). The dataframe
functions follow the `resample_*` signature convention:
`fn(catalog, <symbol/underlying>, start_ns, end_ns | at_ns, ...) -> pl.DataFrame`.

### 1. `blackscholes.py` — pricing, greeks, IV (FOUNDATION)

Black-76 convention (European options on a forward/index — the correct model
for crypto options on Deribit/OKX/Bybit, whose `underlying_price` IS the
forward/index). All scalar, pure `math`.

- `norm_cdf(x)`, `norm_pdf(x)` — `N(x)=0.5*(1+erf(x/sqrt(2)))`, `n(x)=exp(-x*x/2)/sqrt(2*pi)`.
- `bs_price(forward, strike, t_years, vol, opt_type, rate=0.0) -> float`
- `Greeks` (NamedTuple: delta, gamma, vega, theta, rho) and
  `bs_greeks(forward, strike, t_years, vol, opt_type, rate=0.0) -> Greeks`
- `implied_vol(price, forward, strike, t_years, opt_type, rate=0.0) -> float | None`
  Newton–Raphson seeded at 0.5 with vega steps; bisection fallback on
  `[1e-6, 10.0]`; tol `1e-6`; max 100 iters; returns `None` when the price
  violates no-arbitrage bounds or the solver does not converge.
- Expiry guard: `t_years <= 0` → expired; price = discounted intrinsic, greeks
  zero (delta = ±1·D step), `implied_vol` returns `None`.

Vega/theta are reported in **natural units** (per 1.0 of vol, per 1.0 year);
docstrings state the convention so callers can scale (÷100 for per-1%, ÷365 for
per-day theta).

### 2. `funding.py` — funding APR & cumulative

Reads the `funding` channel. Sign convention documented (positive rate ⇒ longs
pay shorts).

- `periods_per_year(interval_hours)` = `8760 / interval_hours` (8h ⇒ 1095).
- `apr_from_rate(rate, interval_hours)` = `rate * periods_per_year`.
- `funding_apr(catalog, symbol, start_ns, end_ns) -> pl.DataFrame` with one row
  per funding event: `funding_ts, funding_rate, interval_hours, apr,
  cumulative_funding` (running sum of `funding_rate`).
- `funding_summary(catalog, symbol, start_ns, end_ns) -> pl.DataFrame` — one
  row: `n_events, mean_rate, mean_apr, total_funding, annualized_apr`.

### 3. `basis.py` — spot-future & perp basis

- Dated future vs spot: `basis = F - S`, `basis_pct = (F-S)/S`,
  `annualized = basis_pct * 365 / days_to_expiry` (uses the instrument expiry).
- Perp basis: `mark_price - index_price` from `derivative_ticker`,
  `basis_pct = (mark-index)/index`.
- Time alignment via DuckDB **ASOF JOIN** (`future.local_ts >= spot.local_ts`,
  nearest prior spot) so the two series need not be sampled in lockstep.
- `spot_future_basis(catalog, future_symbol, spot_symbol, start_ns, end_ns,
  expiry_ns=None) -> pl.DataFrame`: `local_ts, future_price, spot_price, basis,
  basis_pct, annualized_pct` (annualized only when `expiry_ns` given).
- `perp_basis(catalog, perp_symbol, start_ns, end_ns) -> pl.DataFrame`:
  `local_ts, mark_price, index_price, basis, basis_pct`.

### 4. `volsurface.py` — IV surface, skew, term structure

Snapshot semantics: at instant `at_ns`, take the latest `options_chain` row per
`(strike, expiry)` with `local_ts <= at_ns`. IV source priority: `mark_iv` when
present, else compute via `blackscholes.implied_vol` from `mark_price` +
`underlying_price`. Moneyness = `strike / underlying_price`.

- `iv_surface(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame`:
  `expiry, strike, moneyness, opt_type, iv, source` (source ∈ {"mark_iv","computed"}).
- `vol_skew(catalog, underlying, expiry_ns, at_ns, rate=0.0) -> pl.DataFrame`:
  per-strike IV for one expiry ordered by strike, plus a `delta` column;
  helper `risk_reversal_butterfly(skew_df, target_delta=0.25)` returns
  `(rr_25d, bf_25d)` from the 25-delta call/put IVs vs ATM.
- `term_structure(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame`:
  ATM IV (strike nearest `underlying_price`) per expiry, ordered by expiry:
  `expiry, days_to_expiry, atm_strike, atm_iv`.

### 5. Client + CLI integration

- `CrocodileClient` gains thin pass-throughs: `funding_apr`, `spot_future_basis`,
  `perp_basis`, `iv_surface`, `term_structure` (each constructs from
  `self._catalog`). No new state.
- CLI subcommands under the existing Typer app: `funding-apr`, `basis`,
  `iv-surface`, `term-structure` — print Rich tables, mirroring existing CLI
  command style.

### 6. Docs + examples + gate

- README **Analytics** section with copy-pasteable Python + CLI snippets.
- `examples/analytics_funding.py` and `examples/analytics_iv_surface.py`
  (offline, run against a fixture data lake, exit 0).
- ANALYTICS gate: full `pytest`, `ruff`, `mypy` clean; `crocodile.analytics`
  coverage ≥ 90%; example scripts runnable.

## Data flow

```
Parquet lake ─► DuckDB Catalog ─► analytics fn (SQL + Polars + pure-math) ─► pl.DataFrame ─► client/CLI/examples
```

Analytics never touches the network and never re-derives storage logic; it
composes `catalog.scan`/`catalog.query` with closed-form math.

## Error handling

- Empty input (no rows for symbol/range) → empty `pl.DataFrame` (consistent with
  `resample_ohlcv`), never an exception.
- Degenerate math (t≤0, non-finite inputs, price out of no-arb bounds) →
  documented sentinel: `implied_vol` returns `None`; greeks return zeros for
  expired options. No silent wrong numbers.
- Missing `underlying_price` / `mark_price` for an option row → that row's
  `iv` is null with `source="unavailable"`, never a crash.

## Testing strategy

- **Black-Scholes golden values** (textbook, rate=0): F=K=100, T=1, σ=0.2 ⇒
  call ≈ 7.9656, put ≈ 7.9656 (ATM symmetry), Δcall = N(d1)=N(0.1)≈0.5398.
  IV round-trip: `implied_vol(bs_price(σ=0.2)) ≈ 0.2 ± 1e-4`. No-arb rejection
  returns None.
- **Funding/basis/volsurface** against small hand-built fixtures written through
  the real `ParquetSink` so the full storage→catalog→analytics path is exercised.
- ASOF-join correctness: spot sampled sparser than future, nearest-prior used.
- ≥90% coverage on `crocodile.analytics`.

## Milestones (tasks 6.1–6.6)

| Task | Module | Summary |
|---|---|---|
| 6.1 | blackscholes.py | Pure-math Black-76 price + greeks + IV solver |
| 6.2 | funding.py | Funding APR / cumulative / summary |
| 6.3 | basis.py | Spot-future + perp basis, annualized, ASOF align |
| 6.4 | volsurface.py | IV surface + skew (25Δ RR/BF) + term structure |
| 6.5 | client.py / cli.py | Analytics methods + CLI subcommands |
| 6.6 | docs/examples | README analytics + examples + ANALYTICS gate |

6.1 is the foundation (4.x → none); 6.4 depends on 6.1; 6.5 depends on 6.2–6.4;
6.6 last. Built sequentially via subagent-driven-development with spec+quality
review per task, then the gate.
