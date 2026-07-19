"""Universe layer tests — filtering (pure) and volume ranking (fake ccxt).

The ccxt path is exercised without network by monkeypatching the module's
``list_ccxt_exchanges`` (so the venue is "supported") and ``_make_ccxt`` (so a
:class:`FakeExchange` is returned instead of a live exchange object).
"""

from __future__ import annotations

from typing import Any

from crypcodile.instruments import universe
from crypcodile.instruments.registry import Instrument, Kind


def _inst(symbol_raw: str, kind: Kind, base: str, quote: str) -> Instrument:
    return Instrument(
        canonical=f"fake:{symbol_raw}",
        exchange="fake",
        symbol_raw=symbol_raw,
        kind=kind,
        base=base,
        quote=quote,
    )


# --------------------------------------------------------------------------- #
# filter_instruments (pure)
# --------------------------------------------------------------------------- #

def test_filter_by_kind():
    insts = [
        _inst("BTC/USDT", Kind.SPOT, "BTC", "USDT"),
        _inst("BTC/USDT:USDT", Kind.PERPETUAL, "BTC", "USDT"),
    ]
    out = universe.filter_instruments(insts, kinds={Kind.PERPETUAL})
    assert [i.symbol_raw for i in out] == ["BTC/USDT:USDT"]


def test_filter_by_quote_and_base_case_insensitive():
    insts = [
        _inst("BTC/USDT", Kind.SPOT, "BTC", "USDT"),
        _inst("ETH/USD", Kind.SPOT, "ETH", "USD"),
        _inst("BTC/USD", Kind.SPOT, "BTC", "USD"),
    ]
    assert [i.symbol_raw for i in universe.filter_instruments(insts, quote="usd")] == [
        "ETH/USD",
        "BTC/USD",
    ]
    assert [i.symbol_raw for i in universe.filter_instruments(insts, base="btc")] == [
        "BTC/USDT",
        "BTC/USD",
    ]


# --------------------------------------------------------------------------- #
# fake ccxt for the ranking path
# --------------------------------------------------------------------------- #

def _spot(symbol: str, base: str, quote: str) -> dict[str, Any]:
    return {"symbol": symbol, "base": base, "quote": quote, "spot": True, "type": "spot"}


def _swap(symbol: str, base: str, quote: str) -> dict[str, Any]:
    return {
        "symbol": symbol, "base": base, "quote": quote,
        "swap": True, "contract": True, "type": "swap",
    }


class FakeExchange:
    def __init__(self) -> None:
        self.markets = {
            "BTC/USDT": _spot("BTC/USDT", "BTC", "USDT"),
            "ETH/USDT": _spot("ETH/USDT", "ETH", "USDT"),
            "DOGE/USDT": _spot("DOGE/USDT", "DOGE", "USDT"),
            "BTC/USD": _spot("BTC/USD", "BTC", "USD"),
            "BTC/USDT:USDT": _swap("BTC/USDT:USDT", "BTC", "USDT"),
        }
        self.has = {"fetchTickers": True}
        self.closed = False

    async def load_markets(self) -> dict[str, Any]:
        return self.markets

    async def fetch_tickers(self) -> dict[str, Any]:
        return {
            "BTC/USDT": {"quoteVolume": 1_000_000},
            "ETH/USDT": {"quoteVolume": 500_000},
            "DOGE/USDT": {"quoteVolume": 50_000},
            "BTC/USD": {"quoteVolume": 9_000_000},        # different quote
            "BTC/USDT:USDT": {"quoteVolume": 8_000_000},  # perpetual
        }

    async def close(self) -> None:
        self.closed = True


def _patch_ccxt(monkeypatch, ex):
    monkeypatch.setattr(universe, "list_ccxt_exchanges", lambda: ["fake"])
    monkeypatch.setattr(universe, "_make_ccxt", lambda exchange, cfg: ex)


async def test_top_symbols_ranked_by_volume_and_quote(monkeypatch):
    ex = FakeExchange()
    _patch_ccxt(monkeypatch, ex)
    top = await universe.top_symbols_by_volume("fake", 2, quote="USDT", kinds={Kind.SPOT})
    # USDT spot only: BTC(1M) > ETH(500k) > DOGE(50k); BTC/USD (different quote) and
    # the perpetual are excluded.
    assert top == ["BTC/USDT", "ETH/USDT"]
    assert ex.closed is True


async def test_top_symbols_kind_filter_selects_perpetual(monkeypatch):
    ex = FakeExchange()
    _patch_ccxt(monkeypatch, ex)
    top = await universe.top_symbols_by_volume("fake", 5, quote="USDT", kinds={Kind.PERPETUAL})
    assert top == ["BTC/USDT:USDT"]


async def test_top_symbols_any_quote(monkeypatch):
    ex = FakeExchange()
    _patch_ccxt(monkeypatch, ex)
    top = await universe.top_symbols_by_volume("fake", 3, quote=None, kinds={Kind.SPOT})
    # Highest-volume spot regardless of quote: BTC/USD(9M) > BTC/USDT(1M) > ETH/USDT(500k)
    assert top == ["BTC/USD", "BTC/USDT", "ETH/USDT"]


async def test_exchange_instruments_maps_and_registers(monkeypatch):
    ex = FakeExchange()
    _patch_ccxt(monkeypatch, ex)
    from crypcodile.instruments.registry import InstrumentRegistry

    reg = InstrumentRegistry()
    insts = await universe.exchange_instruments("fake", registry=reg)
    assert len(insts) == 5
    assert reg.get_raw("fake", "BTC/USDT") is not None
