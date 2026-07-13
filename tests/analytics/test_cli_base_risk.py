"""CLI exposure tests for Base risk analytics (open-interest, peg-deviation, chaos-score, lending-stress, gas-vol)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from crypcodile.analytics.peg_deviation import peg_deviation_from_price
from crypcodile.cli import app
from crypcodile.client.client import CrypcodileClient
from crypcodile.schema.records import BookTicker, OpenInterest
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000
_RUNNER = CliRunner()


def _make_oi(ts: int, exchange: str, symbol: str, oi: float) -> OpenInterest:
    return OpenInterest(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol.split(":")[-1],
        exchange_ts=ts,
        local_ts=ts,
        open_interest=oi,
    )


async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()


@pytest.fixture
def oi_lake(tmp_path: Path) -> Path:
    records = [
        _make_oi(_BASE_TS, "binance", "binance:BTCUSDT", 100.0),
        _make_oi(_BASE_TS, "okx", "okx:BTC-USDT-SWAP", 50.0),
        _make_oi(_BASE_TS + 1_000, "binance", "binance:BTCUSDT", 110.0),
        _make_oi(_BASE_TS + 1_000, "okx", "okx:BTC-USDT-SWAP", 60.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    return tmp_path


@pytest.fixture
def peg_lake(tmp_path: Path) -> Path:
    async def _setup() -> None:
        sink = ParquetSink(tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
        await sink.put(
            BookTicker(
                exchange="base_onchain",
                symbol="base_onchain:USDC-USDbC",
                symbol_raw="USDC-USDbC",
                exchange_ts=_BASE_TS,
                local_ts=_BASE_TS,
                bid_px=0.999,
                bid_sz=1.0,
                ask_px=1.001,
                ask_sz=1.0,
            )
        )
        await sink.put(
            BookTicker(
                exchange="base_onchain",
                symbol="base_onchain:USDC-USDbC",
                symbol_raw="USDC-USDbC",
                exchange_ts=_BASE_TS + 1_000_000_000,
                local_ts=_BASE_TS + 1_000_000_000,
                bid_px=0.979,
                bid_sz=1.0,
                ask_px=0.981,
                ask_sz=1.0,
            )
        )
        await sink.flush()

    asyncio.run(_setup())
    return tmp_path


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_peg_deviation_from_price_alert() -> None:
    res = peg_deviation_from_price(0.98, threshold=0.01)
    assert res["deviation_pct"] == pytest.approx(0.02)
    assert res["is_alert_triggered"] is True


def test_peg_deviation_from_price_ok() -> None:
    res = peg_deviation_from_price(1.0, threshold=0.01)
    assert res["deviation_pct"] == pytest.approx(0.0)
    assert res["is_alert_triggered"] is False


# ---------------------------------------------------------------------------
# Client wrappers
# ---------------------------------------------------------------------------


def test_client_aggregate_open_interest(oi_lake: Path) -> None:
    client = CrypcodileClient(oi_lake)
    df = client.aggregate_open_interest("BTC", _BASE_TS, _BASE_TS + 1_000)
    assert len(df) == 2
    assert "total_oi" in df.columns
    assert df["total_oi"][0] == pytest.approx(150.0)


def test_client_calculate_peg_deviation(peg_lake: Path) -> None:
    client = CrypcodileClient(peg_lake)
    df = client.calculate_peg_deviation("base_onchain:USDC-USDbC", threshold=0.01)
    assert len(df) == 2
    assert df["is_alert_triggered"][1] is True


# ---------------------------------------------------------------------------
# CLI: open-interest
# ---------------------------------------------------------------------------


def test_cli_open_interest_exits_0(oi_lake: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "open-interest",
            "--symbol",
            "BTC",
            "--start",
            str(_BASE_TS),
            "--end",
            str(_BASE_TS + 1_000),
            "--data-dir",
            str(oi_lake),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "total_oi" in result.output


def test_cli_open_interest_empty_exits_0(tmp_path: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "open-interest",
            "--symbol",
            "BTC",
            "--start",
            "0",
            "--end",
            "1",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No open interest" in result.output


# ---------------------------------------------------------------------------
# CLI: peg-deviation
# ---------------------------------------------------------------------------


def test_cli_peg_deviation_pure_price() -> None:
    result = _RUNNER.invoke(
        app,
        ["peg-deviation", "--price", "0.98", "--threshold", "0.01"],
    )
    assert result.exit_code == 0, result.output
    assert "is_alert_triggered: True" in result.output
    assert "deviation_pct: 0.02" in result.output


def test_cli_peg_deviation_pure_bid_ask() -> None:
    result = _RUNNER.invoke(
        app,
        ["peg-deviation", "--bid", "0.97", "--ask", "0.97", "--threshold", "0.01"],
    )
    assert result.exit_code == 0, result.output
    assert "is_alert_triggered: True" in result.output


def test_cli_peg_deviation_lake(peg_lake: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "peg-deviation",
            "--symbol",
            "base_onchain:USDC-USDbC",
            "--threshold",
            "0.01",
            "--data-dir",
            str(peg_lake),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "deviation_pct" in result.output or "True" in result.output


def test_cli_peg_deviation_missing_args_exits_1() -> None:
    result = _RUNNER.invoke(app, ["peg-deviation"])
    assert result.exit_code == 1
    assert "Error" in result.output


# ---------------------------------------------------------------------------
# CLI: chaos-score (pure numeric kwargs)
# ---------------------------------------------------------------------------


def test_cli_chaos_score_zeros() -> None:
    result = _RUNNER.invoke(app, ["chaos-score"])
    assert result.exit_code == 0, result.output
    assert "chaos_score: 0.0" in result.output


def test_cli_chaos_score_metrics() -> None:
    from crypcodile.analytics.risk import calculate_chaos_score

    vol, dev, imb, delay = 0.05, 0.002, 0.1, 1.0
    expected = calculate_chaos_score(vol, dev, imb, delay)
    result = _RUNNER.invoke(
        app,
        [
            "chaos-score",
            "--volatility",
            str(vol),
            "--stablecoin-deviation",
            str(dev),
            "--orderbook-imbalance",
            str(imb),
            "--sequencer-delay",
            str(delay),
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"chaos_score: {expected}" in result.output
    assert "volatility: 0.05" in result.output
    assert "stablecoin_deviation: 0.002" in result.output
    assert "orderbook_imbalance: 0.1" in result.output
    assert "sequencer_delay: 1.0" in result.output


def test_cli_chaos_score_high_risk_near_100() -> None:
    result = _RUNNER.invoke(
        app,
        [
            "chaos-score",
            "--volatility",
            "1000",
            "--stablecoin-deviation",
            "1000",
            "--orderbook-imbalance",
            "1",
            "--sequencer-delay",
            "1000",
        ],
    )
    assert result.exit_code == 0, result.output
    # Parse chaos_score line
    score_line = next(
        line for line in result.output.splitlines() if line.startswith("chaos_score:")
    )
    score = float(score_line.split(":", 1)[1].strip())
    assert 0.0 <= score <= 100.0
    assert score > 90.0


# ---------------------------------------------------------------------------
# CLI: lending-stress (pure numeric kwargs)
# ---------------------------------------------------------------------------


def test_cli_lending_stress_healthy() -> None:
    from crypcodile.analytics.lending_stress import lending_stress_test

    expected = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=5000.0,
        liquidation_threshold=0.8,
        haircut_pct=0.20,
    )
    result = _RUNNER.invoke(
        app,
        [
            "lending-stress",
            "--collateral-usd",
            "10000",
            "--debt-usd",
            "5000",
            "--liquidation-threshold",
            "0.8",
            "--haircut-pct",
            "0.20",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "collateral_usd: 10000.0" in result.output
    assert "debt_usd: 5000.0" in result.output
    assert f"current_health_factor: {expected['current_health_factor']}" in result.output
    assert f"simulated_health_factor: {expected['simulated_health_factor']}" in result.output
    assert "is_liquidatable: False" in result.output
    assert "simulated_is_liquidatable: False" in result.output


def test_cli_lending_stress_liquidation() -> None:
    result = _RUNNER.invoke(
        app,
        [
            "lending-stress",
            "--collateral-usd",
            "10000",
            "--debt-usd",
            "9000",
            "--liquidation-threshold",
            "0.8",
            "--haircut-pct",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "is_liquidatable: True" in result.output
    assert "simulated_is_liquidatable: True" in result.output


def test_cli_lending_stress_zero_debt_inf() -> None:
    result = _RUNNER.invoke(
        app,
        [
            "lending-stress",
            "--collateral-usd",
            "10000",
            "--debt-usd",
            "0",
            "--liquidation-threshold",
            "0.8",
            "--haircut-pct",
            "20",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "current_health_factor: inf" in result.output
    assert "simulated_health_factor: inf" in result.output
    assert "is_liquidatable: False" in result.output
    assert "simulated_is_liquidatable: False" in result.output


def test_cli_lending_stress_missing_args_exits_2() -> None:
    # Typer/Click exits 2 when required options are missing.
    result = _RUNNER.invoke(app, ["lending-stress"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# CLI: gas-vol
# ---------------------------------------------------------------------------


def test_cli_gas_vol_csv(tmp_path: Path) -> None:
    gas_path = tmp_path / "gas.csv"
    vol_path = tmp_path / "vol.csv"
    pl.DataFrame(
        {"local_ts": [1, 2, 3, 4, 5], "gas_price": [10.0, 20.0, 30.0, 40.0, 50.0]}
    ).write_csv(gas_path)
    pl.DataFrame(
        {"local_ts": [1, 2, 3, 4, 5], "volatility": [0.1, 0.2, 0.3, 0.4, 0.5]}
    ).write_csv(vol_path)

    result = _RUNNER.invoke(
        app,
        ["gas-vol", "--gas-file", str(gas_path), "--vol-file", str(vol_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert abs(payload["pearson"] - 1.0) < 1e-6
    assert abs(payload["spearman"] - 1.0) < 1e-6


def test_cli_gas_vol_json(tmp_path: Path) -> None:
    gas_path = tmp_path / "gas.json"
    vol_path = tmp_path / "vol.json"
    pl.DataFrame(
        {"local_ts": [1, 2, 3], "gas_cost": [1.0, 2.0, 3.0]}
    ).write_json(gas_path)
    pl.DataFrame(
        {"local_ts": [1, 2, 3], "vol": [0.1, 0.2, 0.3]}
    ).write_json(vol_path)

    result = _RUNNER.invoke(
        app,
        ["gas-vol", "--gas-file", str(gas_path), "--vol-file", str(vol_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert abs(payload["pearson"] - 1.0) < 1e-6


def test_cli_gas_vol_missing_args_exits_1() -> None:
    result = _RUNNER.invoke(app, ["gas-vol"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_cli_commands_registered() -> None:
    result = _RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "open-interest" in result.output
    assert "peg-deviation" in result.output
    assert "chaos-score" in result.output
    assert "lending-stress" in result.output
    assert "gas-vol" in result.output
    assert "mev-sandwich" in result.output
