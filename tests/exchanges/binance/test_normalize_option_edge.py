"""Targeted tests for uncovered branches in crocodile.exchanges.binance.normalize.

Covers:
- _parse_eapi_option_symbol: wrong dash-part count, non-numeric strike,
  unknown opt_type, and bad/short YYMMDD expiry (expiry_ns → None).
- _normalize_eapi_option_markprice: registry-backed path (inst is not None),
  unparseable strike/opt_type (skip/continue → no record yielded),
  missing "E" key (exchange_ts falls back to local_ts).
- normalize_message: "@depth" stream dispatch to normalize_depth,
  "@optionMarkPrice" whose data is a dict-wrapped shape.
"""

import json
import pathlib

import pytest

from crocodile.exchanges.binance.normalize import (
    _normalize_eapi_option_markprice,
    _parse_eapi_option_symbol,
    normalize_message,
)
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.schema.enums import OptType
from crocodile.schema.records import BookDelta, OptionsChain

P = pathlib.Path(__file__).parent / "fixtures"

LOCAL_TS = 9_000_000_000


# ---------------------------------------------------------------------------
# _parse_eapi_option_symbol - error branches
# ---------------------------------------------------------------------------


def test_parse_wrong_dash_count_returns_sym_none_tuple() -> None:
    """Fewer or more than 4 dash-parts → (sym, None, None, None)."""
    # 3 parts
    result = _parse_eapi_option_symbol("BTC-231215-50000")
    assert result == ("BTC-231215-50000", None, None, None)

    # 5 parts
    result2 = _parse_eapi_option_symbol("BTC-231215-50000-C-EXTRA")
    assert result2 == ("BTC-231215-50000-C-EXTRA", None, None, None)


def test_parse_non_numeric_strike_returns_underlying_none() -> None:
    """Non-numeric strike → (underlying, None, None, None)."""
    result = _parse_eapi_option_symbol("BTC-231215-NOTANUM-C")
    assert result[0] == "BTC"
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None


def test_parse_invalid_opt_type_returns_underlying_none() -> None:
    """Opt-type that is not C or P → (underlying, None, None, None)."""
    result = _parse_eapi_option_symbol("BTC-231215-50000-X")
    assert result[0] == "BTC"
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None


def test_parse_bad_expiry_returns_expiry_ns_none() -> None:
    """A 6-char expiry string that produces invalid date → expiry_ns is None."""
    # Month 99 is invalid → timegm will raise; expiry_ns must be None
    result = _parse_eapi_option_symbol("BTC-239999-50000-C")
    assert result[0] == "BTC"
    assert result[1] == 50000.0
    assert result[2] == OptType.CALL
    assert result[3] is None  # bad YYMMDD → expiry_ns None


def test_parse_short_expiry_returns_expiry_ns_none() -> None:
    """Expiry string shorter than 6 chars → expiry_ns is None (not 6-digit branch skipped)."""
    result = _parse_eapi_option_symbol("BTC-2312-50000-C")
    assert result[0] == "BTC"
    assert result[1] == 50000.0
    assert result[2] == OptType.CALL
    assert result[3] is None


# ---------------------------------------------------------------------------
# _normalize_eapi_option_markprice - registry-backed path (lines 99-104)
# ---------------------------------------------------------------------------


def _make_registry(
    venue: str,
    sym_raw: str,
    *,
    canonical: str = "binance-eapi:BTC-231215-50000-C",
    strike: float = 50000.0,
    opt_type: str = "C",
    expiry: int = 1_702_598_400_000_000_000,
) -> InstrumentRegistry:
    registry = InstrumentRegistry()
    inst = Instrument(
        canonical=canonical,
        exchange=venue,
        symbol_raw=sym_raw,
        kind=Kind.OPTION,
        base="BTC",
        quote="USDT",
        strike=strike,
        expiry=expiry,
        opt_type=opt_type,
    )
    registry.add(inst)
    return registry


def test_registry_backed_instrument_path() -> None:
    """When the registry has an entry for the symbol, inst metadata is used."""
    venue = "binance-eapi"
    sym = "BTC-231215-50000-C"
    registry = _make_registry(venue, sym)

    entry = {
        "s": sym,
        "mp": "0.05",
        "d": "0.5",
        "g": "0.001",
        "t": "-3.0",
        "v": "12.0",
        "b": "64.0",
        "a": "66.0",
        "vo": "65.0",
        "oi": "10.0",
        "E": 1_700_000_000_000,
    }
    out = list(
        _normalize_eapi_option_markprice("BTC@optionMarkPrice", [entry], LOCAL_TS, venue, registry)
    )
    assert len(out) == 1
    rec = out[0]
    assert isinstance(rec, OptionsChain)
    # canonical from registry
    assert rec.symbol == "binance-eapi:BTC-231215-50000-C"
    # underlying from inst.base
    assert rec.underlying == "BTC"
    assert rec.strike == 50000.0
    assert rec.opt_type == OptType.CALL
    assert rec.expiry == 1_702_598_400_000_000_000


# ---------------------------------------------------------------------------
# _normalize_eapi_option_markprice - unparseable symbol, no record yielded (lines 109-113)
# ---------------------------------------------------------------------------


def test_unparseable_symbol_yields_no_record(caplog: pytest.LogCaptureFixture) -> None:
    """Symbol that cannot be parsed (no registry, wrong format) → iterator yields nothing."""
    entry = {
        "s": "BAD_SYMBOL_NO_DASHES",
        "mp": "0.05",
        "E": 1_700_000_000_000,
    }
    out = list(
        _normalize_eapi_option_markprice(
            "BTC@optionMarkPrice", [entry], LOCAL_TS, "binance-eapi", None
        )
    )
    assert out == []


def test_unparseable_symbol_with_none_registry_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A symbol with valid dashes but non-numeric strike (so strike=None) emits a warning."""
    entry = {
        "s": "BTC-231215-NOTANUM-C",
        "mp": "0.05",
        "E": 1_700_000_000_000,
    }
    import logging

    with caplog.at_level(logging.WARNING, logger="crocodile.exchanges.binance.normalize"):
        out = list(
            _normalize_eapi_option_markprice(
                "BTC@optionMarkPrice", [entry], LOCAL_TS, "binance-eapi", None
            )
        )
    assert out == []
    assert any("cannot parse" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _normalize_eapi_option_markprice - missing "E" key, exchange_ts falls back to local_ts (118-123)
# ---------------------------------------------------------------------------


def test_missing_exchange_ts_falls_back_to_local_ts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Entry without 'E' key → exchange_ts equals local_ts passed in."""
    entry = {
        "s": "BTC-231215-50000-C",
        "mp": "0.05",
        "d": "0.5",
        "g": "0.001",
        "t": "-3.0",
        "v": "12.0",
        "b": "64.0",
        "a": "66.0",
        "vo": "65.0",
        "oi": "10.0",
        # "E" intentionally absent
    }
    import logging

    with caplog.at_level(logging.WARNING, logger="crocodile.exchanges.binance.normalize"):
        out = list(
            _normalize_eapi_option_markprice(
                "BTC@optionMarkPrice", [entry], LOCAL_TS, "binance-eapi", None
            )
        )
    assert len(out) == 1
    rec = out[0]
    assert rec.exchange_ts == LOCAL_TS
    assert any("local_ts" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# normalize_message - "@depth" stream dispatches to normalize_depth (line 265)
# ---------------------------------------------------------------------------


def test_depth_stream_dispatches_to_normalize_depth() -> None:
    """@depth in stream → yields BookDelta records (delegate to normalize_depth)."""
    msg = json.loads((P / "usdm_depth.json").read_text())
    # Confirm the fixture uses @depth
    assert "@depth" in msg["stream"]
    out = list(normalize_message(msg, local_ts=LOCAL_TS, venue="binance-usdm"))
    assert len(out) > 0
    assert all(isinstance(r, BookDelta) for r in out)


# ---------------------------------------------------------------------------
# normalize_message - "@optionMarkPrice" with dict-wrapped data (lines 274-278)
# ---------------------------------------------------------------------------


def test_option_markprice_dict_wrapped_data() -> None:
    """When data is a dict with inner 'data' list, the inner list is processed."""
    sym = "BTC-231215-50000-C"
    inner_entry = {
        "s": sym,
        "mp": "0.05",
        "d": "0.5",
        "g": "0.001",
        "t": "-3.0",
        "v": "12.0",
        "b": "64.0",
        "a": "66.0",
        "vo": "65.0",
        "oi": "10.0",
        "E": 1_700_000_000_000,
    }
    msg = {
        "stream": "BTC@optionMarkPrice",
        "data": {
            "data": [inner_entry],
        },
    }
    out = list(normalize_message(msg, local_ts=LOCAL_TS, venue="binance-eapi"))
    assert len(out) == 1
    rec = out[0]
    assert isinstance(rec, OptionsChain)
    assert rec.symbol_raw == sym
    assert rec.strike == 50000.0
    assert rec.opt_type == OptType.CALL


# ---------------------------------------------------------------------------
# _parse_eapi_option_symbol - PUT branch (line 49)
# ---------------------------------------------------------------------------


def test_parse_put_option_returns_put_opt_type() -> None:
    """Opt-type 'P' → OptType.PUT in the returned tuple."""
    result = _parse_eapi_option_symbol("ETH-231215-2000-P")
    assert result[0] == "ETH"
    assert result[1] == 2000.0
    assert result[2] == OptType.PUT
    assert result[3] is not None  # valid expiry


# ---------------------------------------------------------------------------
# normalize_message - "@optionMarkPrice" with list data (line 271)
# ---------------------------------------------------------------------------


def test_option_markprice_list_data_via_normalize_message() -> None:
    """When msg['data'] is already a list, normalize_message yields OptionsChain records."""
    sym = "BTC-231215-50000-C"
    entry = {
        "s": sym,
        "mp": "0.05",
        "d": "0.5",
        "g": "0.001",
        "t": "-3.0",
        "v": "12.0",
        "b": "64.0",
        "a": "66.0",
        "vo": "65.0",
        "oi": "10.0",
        "E": 1_700_000_000_000,
    }
    msg = {
        "stream": "BTC@optionMarkPrice",
        "data": [entry],
    }
    out = list(normalize_message(msg, local_ts=LOCAL_TS, venue="binance-eapi"))
    assert len(out) == 1
    assert isinstance(out[0], OptionsChain)
    assert out[0].symbol_raw == sym


def test_option_markprice_dict_without_inner_data_uses_raw_dict_as_list() -> None:
    """When dict-wrapped data has no 'data' key, [raw_data] is used as the list."""
    sym = "BTC-231215-50000-C"
    raw_entry: dict[str, object] = {
        "s": sym,
        "mp": "0.05",
        "d": "0.5",
        "g": "0.001",
        "t": "-3.0",
        "v": "12.0",
        "b": "64.0",
        "a": "66.0",
        "vo": "65.0",
        "oi": "10.0",
        "E": 1_700_000_000_000,
    }
    msg = {
        "stream": "BTC@optionMarkPrice",
        "data": raw_entry,  # dict with no nested "data" key
    }
    out = list(normalize_message(msg, local_ts=LOCAL_TS, venue="binance-eapi"))
    assert len(out) == 1
    rec = out[0]
    assert isinstance(rec, OptionsChain)
    assert rec.strike == 50000.0
