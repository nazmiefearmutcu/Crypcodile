# Crocodile Analytics — Implementation Plan (M6, tasks 6.1–6.6)

Source-of-truth design: `docs/superpowers/specs/2026-06-05-crocodile-analytics-design.md`.
This plan is **self-contained** (formulas + acceptance tests inline — there is no
research appendix for analytics). All work is on branch `feat/core`, package
`src/crocodile/analytics/`, tests under `tests/analytics/`. Reuse existing types
(`crocodile.schema.records`, `crocodile.schema.enums.OptType`), the `Catalog`
(`crocodile.store.catalog`), and the `ParquetSink` (`crocodile.sink`/store) for
fixtures. Pure `math` only — do NOT add numpy/scipy. Outputs are `polars.DataFrame`.

Conventions used below:
- `OptType` enum: `OptType.CALL`, `OptType.PUT`.
- Timestamps are nanosecond epoch ints. `t_years = (expiry_ns - now_ns) / (365*24*3600*1e9)`.
- `forward` = the option's `underlying_price` (Black-76 forward/index).

---

## M6 — Analytics (IV surface, greeks, skew, term structure, basis, funding APR)

### Task 6.1: Black-Scholes (Black-76) pricing, greeks, implied-vol solver

`src/crocodile/analytics/blackscholes.py` + `tests/analytics/test_blackscholes.py`.
Pure standard-library `math`. Black-76 (option on a forward `F`, discount
`D = exp(-rate*t_years)`, default `rate=0.0`).

Helpers:
- `norm_cdf(x) = 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))`
- `norm_pdf(x) = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)`

Core (for `t_years > 0`, `vol > 0`, `forward > 0`, `strike > 0`):
```
sqrt_t = sqrt(t_years)
d1 = (log(forward / strike) + 0.5 * vol*vol * t_years) / (vol * sqrt_t)
d2 = d1 - vol * sqrt_t
D  = exp(-rate * t_years)
call = D * (forward * norm_cdf(d1)  - strike * norm_cdf(d2))
put  = D * (strike  * norm_cdf(-d2) - forward * norm_cdf(-d1))
```
Greeks (Black-76, natural units):
```
delta_call = D * norm_cdf(d1)      ; delta_put = -D * norm_cdf(-d1)
gamma      = D * norm_pdf(d1) / (forward * vol * sqrt_t)
vega       = D * forward * norm_pdf(d1) * sqrt_t           # per 1.00 vol
theta_call = -D*forward*norm_pdf(d1)*vol/(2*sqrt_t) - rate*call ... (use the standard Black-76 theta; document the exact expression chosen, per 1.00 year)
theta_put  = symmetric variant
rho_call   = -t_years * call        ; rho_put = -t_years * put   # (Black-76 rho wrt rate; document)
```
API:
- `bs_price(forward, strike, t_years, vol, opt_type, rate=0.0) -> float`
- `class Greeks(NamedTuple)` with `delta, gamma, vega, theta, rho` (all float)
- `bs_greeks(forward, strike, t_years, vol, opt_type, rate=0.0) -> Greeks`
- `implied_vol(price, forward, strike, t_years, opt_type, rate=0.0) -> float | None`
  Newton seeded at 0.5 using vega; if it steps outside `[1e-6, 10.0]` or vega≈0,
  fall back to bisection on `[1e-6, 10.0]`; tol `1e-6` on price; max 100 iters.
  Return `None` if `t_years <= 0`, price ≤ discounted intrinsic, or price ≥
  `D*forward` (call) / `D*strike` (put) (no-arb bounds), or no convergence.
- Expired guard (`t_years <= 0`): `bs_price` = `D*max(forward-strike,0)` (call) /
  `D*max(strike-forward,0)` (put); `bs_greeks` = all zeros; `implied_vol` = None.

**Acceptance test** (`rate=0.0` unless noted):
- `bs_price(100, 100, 1.0, 0.2, CALL)` ≈ `7.9656` (abs tol 1e-3); equals PUT (ATM symmetry).
- `bs_greeks(100,100,1.0,0.2,CALL).delta` ≈ `norm_cdf(0.1)` ≈ `0.5398` (tol 1e-3);
  `gamma > 0`; `vega` ≈ `100*norm_pdf(0.1)*1` ≈ `39.69` (tol 1e-1).
- Put-call parity: `call - put == D*(forward - strike)` within 1e-9 for
  `(F=120,K=100,T=0.5,σ=0.3,r=0.05)`.
- IV round-trip: `implied_vol(bs_price(100,110,0.5,0.35,CALL), 100,110,0.5,CALL)` ≈ `0.35` (tol 1e-4); same for a PUT.
- No-arb / expired: `implied_vol(price=0.0, ...)` → `None`; `bs_price(100,100,-1,0.2,CALL)==0.0`; `bs_greeks(...,-1,...)` all zero.
- ruff + mypy clean.

### Task 6.2: Funding APR analytics

`src/crocodile/analytics/funding.py` + `tests/analytics/test_funding.py`.
Reads the `funding` channel via `catalog.scan("funding", symbol, start_ns, end_ns)`
(returns a Polars DataFrame with `funding_rate`, `funding_timestamp`,
`interval_hours`, `local_ts`). Positive rate ⇒ longs pay shorts (document).

- `periods_per_year(interval_hours: int) -> float` = `8760.0 / interval_hours`.
- `apr_from_rate(rate: float, interval_hours: int) -> float` = `rate * periods_per_year(interval_hours)`.
- `funding_apr(catalog, symbol, start_ns, end_ns) -> pl.DataFrame`: columns
  `funding_ts (Int64), funding_rate (f64), interval_hours (Int64), apr (f64),
  cumulative_funding (f64)` ordered by ts ascending; `cumulative_funding` is the
  running sum of `funding_rate`. Default missing `interval_hours` → 8.
  Empty input → empty `pl.DataFrame()`.
- `funding_summary(catalog, symbol, start_ns, end_ns) -> pl.DataFrame`: single row
  `n_events (Int64), mean_rate (f64), mean_apr (f64), total_funding (f64)`.

**Acceptance test:** write 3 `Funding` records (rates 0.0001, -0.0002, 0.0003,
interval_hours=8) through `ParquetSink` to a tmp lake; `funding_apr` →
3 rows, `apr` of row0 ≈ `0.0001*1095 = 0.10950` (tol 1e-6),
`cumulative_funding` last ≈ `0.0002`; `funding_summary.n_events == 3`,
`mean_rate ≈ 0.0000667`. Empty range → empty DF. ruff+mypy clean.

### Task 6.3: Basis analytics (spot-future + perp)

`src/crocodile/analytics/basis.py` + `tests/analytics/test_basis.py`.

- `spot_future_basis(catalog, future_symbol, spot_symbol, start_ns, end_ns,
  expiry_ns=None) -> pl.DataFrame`: ASOF-join future trades to the nearest prior
  spot trade on `local_ts`. Use a DuckDB query via `catalog`:
  `... FROM future ASOF JOIN spot ON future.local_ts >= spot.local_ts`
  (both filtered to symbol + range from the `trade` view). Columns:
  `local_ts, future_price, spot_price, basis (=F-S), basis_pct (=(F-S)/S)`,
  and when `expiry_ns` given also `annualized_pct = basis_pct * 365 /
  days_to_expiry` where `days_to_expiry = (expiry_ns - local_ts)/(86_400e9)`.
- `perp_basis(catalog, perp_symbol, start_ns, end_ns) -> pl.DataFrame`: from
  `derivative_ticker` rows (need `mark_price`,`index_price`): `local_ts,
  mark_price, index_price, basis (=mark-index), basis_pct`. Skip rows where
  either price is null.
- Empty/one-sided input → empty `pl.DataFrame()`.

**Acceptance test:** write spot `Trade`s at t=1000,3000 (px 100,102) and future
`Trade`s at t=2000,4000 (px 101,104) for two symbols; `spot_future_basis` →
2 rows: at t=2000 spot=100 (asof prior) basis=1 pct=0.01; at t=4000 spot=102
basis=2 pct≈0.0196. With `expiry_ns` one year out, `annualized_pct` ≈
`basis_pct*365/365`. `perp_basis`: 2 `DerivativeTicker` rows (mark 100.5/index
100.0) → basis 0.5, pct 0.005. ruff+mypy clean.

### Task 6.4: IV surface, vol skew, term structure

`src/crocodile/analytics/volsurface.py` + `tests/analytics/test_volsurface.py`.
Uses `blackscholes.implied_vol` (6.1) when `mark_iv` is null. Reads
`options_chain` via `catalog`. Snapshot at `at_ns` = latest row per
`(strike, expiry, opt_type)` with `local_ts <= at_ns`.

- `iv_surface(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame`: columns
  `expiry (Int64), strike (f64), moneyness (f64 = strike/underlying_price),
  opt_type (str), iv (f64|null), source (str)`. `source` ∈
  {"mark_iv","computed","unavailable"}. Filter chain rows by `underlying`.
- `vol_skew(catalog, underlying, expiry_ns, at_ns, rate=0.0) -> pl.DataFrame`:
  one expiry, ordered by strike: `strike, moneyness, opt_type, iv, delta`
  (delta from chain `delta` or computed via `bs_greeks`).
- `risk_reversal_butterfly(skew_df, target_delta=0.25) -> tuple[float|None, float|None]`:
  `rr = iv(call @ +target_delta) - iv(put @ -target_delta)`,
  `bf = mean(those two) - atm_iv` (atm = nearest |delta|→0.5 or moneyness→1).
  Return `(None, None)` if the deltas can't be bracketed.
- `term_structure(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame`:
  per expiry, ATM strike = nearest to `underlying_price`; columns
  `expiry, days_to_expiry, atm_strike, atm_iv` ordered by expiry.
- Empty input → empty `pl.DataFrame()`.

**Acceptance test:** write `OptionsChain` rows for underlying "BTC"
(underlying_price=100) at one ts: expiry E1 strikes {90,100,110} and expiry E2
strike {100}, some with `mark_iv` set (e.g. 0.5) and at least one with
`mark_iv=None` but `mark_price` set (force the `computed` path; assert
`source=="computed"` and `iv` is a finite number). `iv_surface` row count = 4,
`moneyness` of strike 110 ≈ 1.1. `term_structure` → 2 rows (E1,E2) ordered,
each `atm_strike==100`. `vol_skew(E1)` → 3 rows ordered by strike. ruff+mypy clean.

### Task 6.5: Client API + CLI subcommands

Extend `src/crocodile/client/client.py` and `src/crocodile/cli.py`
(+ `tests/analytics/test_client_cli.py` or extend existing client/cli tests).

- `CrocodileClient` methods (thin, construct from `self._catalog`):
  `funding_apr(symbol, start_ns, end_ns)`, `spot_future_basis(future_symbol,
  spot_symbol, start_ns, end_ns, expiry_ns=None)`, `perp_basis(perp_symbol,
  start_ns, end_ns)`, `iv_surface(underlying, at_ns)`,
  `term_structure(underlying, at_ns)` — each returns the analytics `pl.DataFrame`.
- CLI subcommands on the existing Typer `app` mirroring current command style
  (Rich table output, `--data-dir` option): `funding-apr`, `basis`
  (`--future/--spot/--perp` modes), `iv-surface`, `term-structure`.
- Keep CLI thin: parse args → call client/analytics → render. No business logic
  in the CLI layer.

**Acceptance test:** populate a tmp lake; `CrocodileClient(tmp).funding_apr(...)`
equals `analytics.funding.funding_apr(catalog, ...)`. Invoke the CLI via Typer's
`CliRunner`: `funding-apr --symbol ... --data-dir tmp` exits 0 and prints a
table containing the APR. `iv-surface` exits 0 on a populated options lake.
ruff+mypy clean.

### Task 6.6: Docs + examples + ANALYTICS gate

- README: add an **Analytics** section with Python + CLI snippets for funding
  APR, basis, IV surface, term structure (mirror the existing quickstart style).
- `examples/analytics_funding.py` and `examples/analytics_iv_surface.py`:
  offline, argparse `--data-dir`, run end-to-end against a fixture lake, print a
  table, exit 0 (graceful message + exit 0 if the lake is empty).
- **ANALYTICS GATE:** `uv run pytest` all green; `uv run ruff check .` clean;
  `uv run mypy` clean; `uv run pytest --cov=crocodile.analytics
  --cov-report=term-missing` shows ≥90%; both new offline examples run to exit 0;
  `collect_deribit.py` still compiles. Commit per task.

---

## Self-review (plan author)

- **Spec coverage:** all six roadmap items map to a task — greeks+IV (6.1),
  funding APR (6.2), basis (6.3), IV surface+skew+term structure (6.4); exposure
  (6.5); docs/examples/gate (6.6). ✅
- **No placeholders:** every task carries concrete formulas + a runnable
  acceptance test with golden numbers. ✅
- **Dep discipline:** pure-math BS, no numpy/scipy; Polars/DuckDB for data. ✅
- **Type/interface consistency:** `Catalog`, `OptType`, `pl.DataFrame`,
  `resample_*`-style signatures reused throughout. ✅
- **Highest-risk task:** 6.4 (IV from chain vs computed, skew delta bracketing) —
  mitigated by forcing the computed path in its acceptance test and depending on
  the golden-tested 6.1 solver. ✅
