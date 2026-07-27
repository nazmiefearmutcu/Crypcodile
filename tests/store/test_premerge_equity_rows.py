"""What the canonical reader does with a pre-migration *equity* row, measured.

``StockodileClient.replay`` reads with ``crocodile.core.store.rows.from_row`` now, and
that reader was rebuilt from the ``Record`` union — it covers every channel the sink can
write. It does not cover every channel that is already *written*: equity files older than
the union merge spell a trade size ``size``, a book level ``{price, size}``, a bar
``channel=bar`` and an option chain ``channel=option_quote``, and ``migrate_lake`` renames
directories rather than rewriting files, so those spellings do not age out.

Merging the two readers is a later task. This file is the inventory that task needs, and
it exists for one reason beyond documentation: **two of these channels come back with a
column dropped and nothing raised.** A reader that raises is a reader someone fixes; a
reader that returns a bar with a null ``num_trades`` is a lake that quietly forgot how
many prints were in it. Recording which is which is what stops the merge from being
declared done on the strength of the channels that happen to work.
"""

from __future__ import annotations

from typing import Any

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.store.rows import from_row

_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _legacy_equity_row(channel: str, **body: Any) -> dict[str, Any]:
    """A row as ``polars.to_dicts()`` yields it from a pre-migration equity file.

    ``exchange`` is present and null on every one of them: the equity file schema declares
    it for compatibility and nothing populates it, which is why the asset class is decided
    by a marker's *value* rather than its presence.
    """
    return {
        "channel": channel,
        "provider": "alpaca",
        "symbol": "AAPL",
        "symbol_raw": "AAPL",
        "source_ts": _TS,
        "local_ts": _TS,
        "exchange": None,
        "date": "2023-11-14",
        "bucket": 42,
        "source": "alpaca",
        **body,
    }


@pytest.mark.parametrize(
    ("channel", "body", "match"),
    [
        pytest.param(
            "trade",
            {"id": "1", "price": 150.0, "size": 10.0},
            "amount",
            id="trade names its quantity size, not amount",
        ),
        pytest.param(
            "book_snapshot",
            {"bids": [{"price": 1.0, "size": 2.0}], "asks": [], "depth": 1},
            "amount",
            id="a book level is {price, size}, not {price, amount}",
        ),
        pytest.param(
            "depth",
            {
                "bids": [{"price": 1.0, "size": 2.0}],
                "asks": [],
                "reference_price": 1.0,
                "basis": "yahoo_1m_vap",
                "is_synthetic": True,
                "depth": 1,
            },
            "amount",
            id="so is a depth ladder",
        ),
        pytest.param(
            "bar",
            {"interval": "1d", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": 9.0},
            "Unknown channel tag",
            id="the bar tag was absorbed into ohlcv",
        ),
        pytest.param(
            "option_quote",
            {"underlying": "AAPL", "expiry": "2026-06-18", "strike": 1.0, "type": "C"},
            "Unknown channel tag",
            id="and option_quote into options_chain",
        ),
    ],
)
def test_the_canonical_reader_refuses_the_channels_it_cannot_read(
    channel: str, body: dict[str, Any], match: str
) -> None:
    """Five of eleven raise, which is the half of this that is safe."""
    with pytest.raises((KeyError, TypeError, ValueError), match=match):
        from_row(_legacy_equity_row(channel, **body))


@pytest.mark.parametrize(
    ("channel", "body"),
    [
        ("quote", {"bid_px": 1.0, "bid_sz": 2.0, "ask_px": 3.0, "ask_sz": 4.0}),
        ("corp_action", {"ex_date": "2026-06-15", "type": "dividend_cash", "value": 0.5}),
        ("short_volume", {"date_val": "2026-06-18", "short_volume": 1.0, "total_volume": 2.0}),
        (
            "filing",
            {
                "accession_number": "a",
                "form": "10-K",
                "filing_date": "2026-01-30",
                "primary_document": "p",
                "document_url": "u",
            },
        ),
    ],
)
def test_the_channels_that_already_reconstruct_keep_their_market(
    channel: str, body: dict[str, Any]
) -> None:
    """A legacy equity row that does reconstruct must not come back as crypto.

    That is the bug the crypto migration shipped: ``_header`` tested a marker column for
    presence, and the equity file schema declares a null ``exchange``.
    """
    rec = from_row(_legacy_equity_row(channel, **body))
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.source == "alpaca"


def test_a_legacy_bar_loses_its_trade_count_without_saying_so() -> None:
    """The silent half, pinned so the reader merge cannot be called done without it.

    A pre-migration equity ``ohlcv`` row spells the print count ``trade_count`` and the
    canonical ``OHLCV`` calls it ``num_trades``. ``_record_body`` iterates the struct's
    fields, so the column is not read and not reported — the bar arrives whole except for
    a count that reads as "the source did not publish one".
    """
    rec = from_row(
        _legacy_equity_row(
            "ohlcv",
            interval="1d",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=9.0,
            vwap=1.2,
            trade_count=7,
        )
    )
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.volume == 9.0
    assert rec.num_trades is None, "records the loss; delete this line when the merge reads it"


def test_a_legacy_instrument_loses_its_listing_venue_without_saying_so() -> None:
    """The other silent one, and the one already documented as a null column.

    Legacy equity lakes hold the listing venue under ``exchange_name`` — the fork moved it
    aside because a plain ``exchange`` collided with the old ``exchange=`` partition key.
    ``crocodile.equity.store.rows.from_row`` reads both spellings; the canonical reader
    reads only its own.
    """
    rec = from_row(_legacy_equity_row("instrument", name="Apple Inc.", exchange_name="NASDAQ"))
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.name == "Apple Inc."  # type: ignore[union-attr]
    assert rec.exchange is None, "records the loss; delete this line when the merge reads it"  # type: ignore[union-attr]
