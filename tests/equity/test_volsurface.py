"""The equity vol surface: the mid it inverts, the vols it refuses, and the shape it keeps.

Every test here writes real :class:`OptionsChain` records through ``ParquetSink`` and reads
them back through a ``Catalog``, because the whole of this module's work happens against
the ``options_chain`` DuckDB view — a stubbed catalog would exercise the model functions
and none of the reading. Nothing reaches the network: Yahoo's *shape* is what is being
tested, and a chain is a chain once it is in the lake.

The crypto half's own suite (``tests/analytics/test_volsurface.py``) is the regression gate
on the lifted core, so what is asserted here is the part that is genuinely different: which
price gets inverted, what happens when there is not one, and that the two halves produce
the same columns.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import polars as pl
import pytest

from crocodile.core.analytics.volsurface import ChainPrices
from crocodile.core.schema.enums import AssetClass, OptType
from crocodile.core.schema.records import OptionsChain
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.volsurface import iv_surface as crypto_iv_surface
from crocodile.equity.analytics.options import bsm_price
from crocodile.equity.analytics.volsurface import (
    iv_surface,
    mid_price,
    term_structure,
    vol_skew,
)

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_YEAR_NS = 365 * 86_400 * 1_000_000_000
_E1 = _BASE_NS + _YEAR_NS
_E2 = _BASE_NS + 2 * _YEAR_NS

_SPOT = 100.0
_UNDERLYING = "AAPL"

# The mid of a bid/ask straddling the Black-Scholes call price at exactly 40 % vol, one
# year out, at the money and at a zero rate. Solving it back has to return 0.40, which is
# what makes the "computed" branch checkable rather than merely non-null.
_TRUE_VOL = 0.40
_ATM_CALL = bsm_price(_SPOT, _SPOT, 1.0, 0.0, _TRUE_VOL, 0.0, OptType.CALL)


def _contract(
    strike: float,
    expiry: int,
    opt_type: OptType,
    *,
    mark_iv: float | None = None,
    bid_px: float | None = None,
    ask_px: float | None = None,
    last_price: float | None = None,
    underlying_price: float | None = _SPOT,
    open_interest: float | None = None,
    ts: int = _BASE_NS,
    source: str = "yahoo",
) -> OptionsChain:
    """One Yahoo-shaped contract: no ``mark_price``, because Yahoo publishes none."""
    symbol = f"{source}:{_UNDERLYING}-{expiry}-{int(strike)}-{opt_type.value}"
    return OptionsChain(
        source=source,
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        underlying=_UNDERLYING,
        underlying_price=underlying_price,
        strike=strike,
        expiry=expiry,
        opt_type=opt_type,
        mark_iv=mark_iv,
        bid_px=bid_px,
        ask_px=ask_px,
        last_price=last_price,
        open_interest=open_interest,
    )


async def _write(data_dir: Path, records: list[OptionsChain]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for record in records:
        await sink.put(record)
    await sink.flush()


def _lake(tmp_path: Path, records: list[OptionsChain]) -> Catalog:
    asyncio.run(_write(tmp_path, records))
    return Catalog(tmp_path)


# ---------------------------------------------------------------------------
# mid_price — what counts as a mark when the feed publishes none
# ---------------------------------------------------------------------------


def _prices(bid: float | None, ask: float | None, last: float | None = None) -> ChainPrices:
    return ChainPrices(mark_iv=None, mark_price=None, bid_px=bid, ask_px=ask, last_price=last)


def test_a_two_sided_quote_has_a_mid() -> None:
    assert mid_price(_prices(4.8, 5.2)) == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("bid", "ask"),
    [
        (None, 5.2),
        (4.8, None),
        (None, None),
        (0.0, 5.2),
        (4.8, 0.0),
        (-1.0, 5.2),
        (float("nan"), 5.2),
    ],
)
def test_a_one_sided_or_zero_quote_has_no_mid(bid: float | None, ask: float | None) -> None:
    """Half a market is not a market, and ``(0 + ask) / 2`` is not the price of anything.

    Yahoo really does publish ``bid: 0.0`` on a contract nobody is bidding on. Averaging
    that with the offer implies roughly half the vol the offer alone would, so the failure
    is a plausible wrong number rather than a visible hole — which is why it is refused
    here rather than filtered downstream.
    """
    assert mid_price(_prices(bid, ask)) is None


# ---------------------------------------------------------------------------
# iv_surface — the three sources, and which price each one used
# ---------------------------------------------------------------------------


def test_a_published_implied_volatility_is_used_as_it_stands(tmp_path: Path) -> None:
    """Yahoo's own ``impliedVolatility`` lands on the row as ``mark_iv`` and wins outright.

    The quote below is deliberately inconsistent with the published vol — a 5.0 mid on an
    ATM year would imply about 0.125, not 0.55 — so a surface that solved anyway would
    report a visibly different number under the same ``source``.
    """
    catalog = _lake(
        tmp_path, [_contract(100.0, _E1, OptType.CALL, mark_iv=0.55, bid_px=4.8, ask_px=5.2)]
    )
    surface = iv_surface(catalog, _UNDERLYING, _BASE_NS)
    row = surface.row(0, named=True)
    assert row["source"] == "mark_iv"
    assert row["iv"] == pytest.approx(0.55)


def test_an_absent_implied_volatility_is_solved_off_the_mid(tmp_path: Path) -> None:
    """M1's sentence, checked against a mid whose vol is known exactly.

    The bid and ask straddle the Black-Scholes price at 40 % vol by a cent either side, so
    their mid *is* that price and the recovered vol has to be 0.40 rather than merely
    finite. A solver inverting the wrong price — the bid, the ask, the last trade — lands
    somewhere else, and a solver handed the spot as though it were a forward would too.
    """
    catalog = _lake(
        tmp_path,
        [
            _contract(
                100.0,
                _E1,
                OptType.CALL,
                mark_iv=None,
                bid_px=_ATM_CALL - 0.01,
                ask_px=_ATM_CALL + 0.01,
            )
        ],
    )
    row = iv_surface(catalog, _UNDERLYING, _BASE_NS).row(0, named=True)
    assert row["source"] == "computed"
    assert row["iv"] == pytest.approx(_TRUE_VOL, abs=1e-3)


def test_a_last_trade_alone_is_not_a_mark_and_the_row_says_unavailable(tmp_path: Path) -> None:
    """``lastPrice`` is a traded price at an unstated instant, so it is refused.

    The whole premise of the surface is that every row is the snapshot at ``at_ns``. A vol
    implied from a trade that may be days old is a measurement of a different moment, and
    filing it in this column under ``source="computed"`` would make the two
    indistinguishable.
    """
    catalog = _lake(
        tmp_path, [_contract(100.0, _E1, OptType.CALL, mark_iv=None, last_price=_ATM_CALL)]
    )
    row = iv_surface(catalog, _UNDERLYING, _BASE_NS).row(0, named=True)
    assert row["source"] == "unavailable"
    assert row["iv"] is None


def test_a_chain_with_no_underlying_price_reports_holes_rather_than_numbers(
    tmp_path: Path,
) -> None:
    """Yahoo's payload does not always carry a spot, and everything downstream needs one.

    Moneyness becomes ``NaN`` — it is a ratio one side of which was not published — and the
    mid cannot be inverted without a spot to invert it against, so the row is
    ``unavailable``. Both are visible holes; the failure worth preventing is a moneyness of
    zero or a vol solved against a spot somebody assumed.
    """
    catalog = _lake(
        tmp_path,
        [
            _contract(
                110.0,
                _E1,
                OptType.CALL,
                mark_iv=None,
                bid_px=_ATM_CALL - 0.01,
                ask_px=_ATM_CALL + 0.01,
                underlying_price=None,
            )
        ],
    )
    row = iv_surface(catalog, _UNDERLYING, _BASE_NS).row(0, named=True)
    assert math.isnan(row["moneyness"])
    assert row["source"] == "unavailable"


def test_the_surface_reports_moneyness_against_the_published_spot(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, [_contract(110.0, _E1, OptType.CALL, mark_iv=0.5)])
    assert iv_surface(catalog, _UNDERLYING, _BASE_NS)["moneyness"][0] == pytest.approx(1.1)


def test_the_snapshot_keeps_the_latest_quote_per_contract(tmp_path: Path) -> None:
    """One contract polled twice is one row, at the later poll — not two."""
    catalog = _lake(
        tmp_path,
        [
            _contract(100.0, _E1, OptType.CALL, mark_iv=0.30, ts=_BASE_NS),
            _contract(100.0, _E1, OptType.CALL, mark_iv=0.42, ts=_BASE_NS + 1),
        ],
    )
    surface = iv_surface(catalog, _UNDERLYING, _BASE_NS + 2)
    assert surface.height == 1
    assert surface["iv"][0] == pytest.approx(0.42)


def test_the_two_halves_return_the_same_columns(tmp_path: Path) -> None:
    """One core surface read through two models, so the frames cannot drift apart.

    Asserted rather than assumed because it is the property the lift exists to buy: a
    consumer that selects ``fitted_iv`` or filters on ``source`` must not have to know
    which market answered.
    """
    equity = _lake(tmp_path / "equity", [_contract(100.0, _E1, OptType.CALL, mark_iv=0.5)])
    crypto_records = [
        OptionsChain(
            source="deribit",
            symbol="deribit:BTC-1-C",
            symbol_raw="BTC-1-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying="BTC",
            underlying_price=_SPOT,
            strike=100.0,
            expiry=_E1,
            opt_type=OptType.CALL,
            mark_price=10.0,
            mark_iv=0.5,
        )
    ]
    crypto = _lake(tmp_path / "crypto", crypto_records)

    from_equity = iv_surface(equity, _UNDERLYING, _BASE_NS)
    from_crypto = crypto_iv_surface(crypto, "BTC", _BASE_NS)
    assert from_equity.columns == from_crypto.columns
    assert from_equity.dtypes == from_crypto.dtypes


def test_an_empty_lake_returns_an_empty_frame(tmp_path: Path) -> None:
    assert iv_surface(Catalog(tmp_path), _UNDERLYING, _BASE_NS).is_empty()


# ---------------------------------------------------------------------------
# vol_skew and term_structure — iv_surface read two more ways
# ---------------------------------------------------------------------------


def _three_strike_chain() -> list[OptionsChain]:
    return [
        _contract(strike, _E1, opt_type, mark_iv=iv, open_interest=oi)
        for strike, oi in ((90.0, 10.0), (100.0, 20.0), (110.0, 30.0))
        for opt_type, iv in ((OptType.CALL, 0.50), (OptType.PUT, 0.70))
    ] + [_contract(100.0, _E2, OptType.CALL, mark_iv=0.45, open_interest=5.0)]


def test_the_skew_prices_a_black_scholes_delta_against_the_spot(tmp_path: Path) -> None:
    """The delta column is what ``risk-reversal`` searches, so it has to be priced.

    A call struck at the money one year out at 50 % vol has a delta near 0.6 under
    Black-Scholes with no carry; the assertion is loose because the point is the sign, the
    magnitude and the fact that a spot model was used at all — a Black-76 delta on a
    forward misread as a spot would sit at a different place on the same skew.
    """
    catalog = _lake(tmp_path, _three_strike_chain())
    skew = vol_skew(catalog, _UNDERLYING, _E1, _BASE_NS)
    assert skew.height == 6
    assert skew["strike"].to_list() == sorted(skew["strike"].to_list())
    assert all(delta is not None for delta in skew["delta"].to_list())

    atm_call = skew.filter((pl.col("strike") == 100.0) & (pl.col("opt_type") == "C"))
    assert 0.5 < atm_call["delta"][0] < 0.75
    atm_put = skew.filter((pl.col("strike") == 100.0) & (pl.col("opt_type") == "P"))
    assert -0.6 < atm_put["delta"][0] < -0.2


def test_the_term_structure_is_one_atm_row_per_expiry(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, _three_strike_chain())
    rows = term_structure(catalog, _UNDERLYING, _BASE_NS)
    assert rows["expiry"].to_list() == [_E1, _E2]
    assert rows["atm_strike"].to_list() == [100.0, 100.0]
    assert rows["days_to_expiry"][0] == pytest.approx(365.0)


def test_an_expiry_nobody_quoted_yields_an_empty_skew(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, _three_strike_chain())
    assert vol_skew(catalog, _UNDERLYING, _E1 + 1, _BASE_NS).is_empty()
