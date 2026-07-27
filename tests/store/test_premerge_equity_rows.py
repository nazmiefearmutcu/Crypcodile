"""What the canonical reader does with a pre-migration *equity* row, measured.

``StockodileClient.replay`` reads with ``crocodile.core.store.rows.from_row``, and that
reader now dispatches on record family: a row whose populated origin marker is
``provider`` is read in the equity fork's dialect and returned as a canonical record.

This file used to be the inventory of what that reader *could not* do. Five channels
raised — the safe half — and two came back with a column silently dropped, which is the
half that mattered: a reader that raises is a reader someone fixes; a reader that returns
a bar with a null ``num_trades`` is a lake that quietly forgot how many prints were in it.

Every one of those eleven channels now reconstructs, so the file is the inventory of the
dialect instead: one case per spelling the equity fork used and the canonical union does
not. The two "records the loss" assertions are gone because the loss is.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from crocodile.core.schema.enums import AssetClass, OptType, Side
from crocodile.core.schema.provenance import Provenance
from crocodile.core.store.rows import from_row

_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _legacy_equity_row(channel: str, **body: Any) -> dict[str, Any]:
    """A row as ``polars.to_dicts()`` yields it from a pre-migration equity file.

    ``exchange`` is present and null on every one of them: the equity file schema declares
    it for compatibility and nothing populates it, which is why the record family is
    decided by a marker's *value* rather than its presence.
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


def test_a_legacy_trade_names_its_quantity_size_and_reads_back_as_amount() -> None:
    """``Trade.amount`` is required and not optional; the fork spelled it ``size``.

    Before the family dispatch this raised ``KeyError('amount')`` on a bare legacy file,
    and returned ``Trade(amount=None)`` on a file sitting beside canonical parts, because
    ``union_by_name`` supplies the canonical column as a null.
    """
    rec = from_row(_legacy_equity_row("trade", id="1", price=150.0, size=10.0, amount=None))

    assert rec.price == 150.0  # type: ignore[union-attr]
    assert rec.amount == 10.0  # type: ignore[union-attr]


def test_a_legacy_trade_reads_back_as_an_unclassified_aggressor_not_a_null_one() -> None:
    """The legacy equity ``Trade`` had no ``side`` field at all.

    ``Side.UNKNOWN`` is the value the canonical record reserves for a tape that does not
    classify the aggressor, and it is what every equity adapter in the tree writes for the
    same prints. ``None`` is not that value: ``rec.side is Side.UNKNOWN`` reads False and
    ``rec.side.value`` raises ``AttributeError`` frames away from the reader.
    """
    rec = from_row(_legacy_equity_row("trade", id="1", price=150.0, size=10.0))

    assert rec.side is Side.UNKNOWN  # type: ignore[union-attr]
    assert rec.side.value == "unknown"  # type: ignore[union-attr]


@pytest.mark.parametrize("channel", ["book_snapshot", "book_delta"])
def test_a_legacy_book_level_is_price_size_and_reads_back_as_price_amount(channel: str) -> None:
    rec = from_row(
        _legacy_equity_row(
            channel,
            bids=[{"price": 1.0, "size": 2.0}],
            asks=[{"price": 3.0, "size": 0.0}],
            depth=1,
        )
    )

    assert rec.bids == [(1.0, 2.0)]  # type: ignore[union-attr]
    assert rec.asks == [(3.0, 0.0)], "0.0 is a level removal, not a missing size"  # type: ignore[union-attr]


def test_a_legacy_depth_ladder_keeps_the_method_it_recorded() -> None:
    """The equity fork's ``basis``/``is_synthetic`` pair is the prototype the tail generalised.

    Defaulting these rows to NATIVE was the reader claiming a modelled volume-at-price
    ladder had been published by a venue — and doing it with a confidence of 1.0, which
    would have outranked every measured profile beside it.
    """
    rec = from_row(
        _legacy_equity_row(
            "depth",
            bids=[{"price": 1.0, "size": 2.0}],
            asks=[],
            reference_price=1.0,
            basis="yahoo_1m_vap",
            is_synthetic=True,
            depth=1,
        )
    )

    assert rec.prov is Provenance.SYNTHETIC
    assert rec.prov_basis == "yahoo_1m_vap"
    assert rec.prov_confidence == 0.0, "the fork recorded no sampling measurement"
    assert rec.is_synthetic is True  # type: ignore[union-attr]


def test_a_legacy_row_claiming_synthetic_with_no_method_is_refused() -> None:
    """The surfaces owe a ``describe(basis)`` warning for every non-native record."""
    with pytest.raises(ValueError, match="records no 'basis'"):
        from_row(
            _legacy_equity_row(
                "depth",
                bids=[],
                asks=[],
                reference_price=1.0,
                basis=None,
                is_synthetic=True,
                depth=0,
            )
        )


def test_a_legacy_bar_decodes_under_the_tag_that_absorbed_it_and_keeps_its_count() -> None:
    """``channel=bar`` used to raise ``Unknown channel tag: 'bar'``.

    Equity's ``Bar`` and equity's own ``OHLCV`` were field-for-field identical and both
    collapsed into the canonical ``ohlcv``; the print count was a rename from
    ``trade_count`` to ``num_trades``. Reading only the new name turned a recorded 77 into
    ``None``, which is the encoding reserved for "the source did not publish one" — the
    loss written in the vocabulary of a different, plausible fact.
    """
    for channel in ("bar", "ohlcv"):
        rec = from_row(
            _legacy_equity_row(
                channel,
                interval="1d",
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=9.0,
                vwap=1.2,
                trade_count=77,
            )
        )
        assert rec.asset_class is AssetClass.EQUITY
        assert rec.volume == 9.0  # type: ignore[union-attr]
        assert rec.num_trades == 77, f"channel={channel}"  # type: ignore[union-attr]


def test_a_legacy_option_quote_decodes_into_the_options_chain_it_merged_with() -> None:
    """Four renames and one unit change, all of which used to be ``Unknown channel tag``."""
    rec = from_row(
        _legacy_equity_row(
            "option_quote",
            underlying="AAPL",
            expiry="2026-06-18",
            strike=200.0,
            type="C",
            bid=1.5,
            ask=1.7,
            last=1.6,
            implied_volatility=0.42,
            volume=12.0,
            open_interest=340.0,
        )
    )

    assert rec.underlying == "AAPL"  # type: ignore[union-attr]
    assert rec.opt_type is OptType.CALL  # type: ignore[union-attr]
    assert rec.bid_px == 1.5  # type: ignore[union-attr]
    assert rec.ask_px == 1.7  # type: ignore[union-attr]
    assert rec.last_price == 1.6  # type: ignore[union-attr]
    assert rec.mark_iv == 0.42  # type: ignore[union-attr]
    assert rec.underlying_price is None, "the fork published none, and the field says so"  # type: ignore[union-attr]
    expected = int(
        datetime.datetime(2026, 6, 18, tzinfo=datetime.UTC).timestamp() * 1_000_000_000
    )
    assert rec.expiry == expected  # type: ignore[union-attr]


def test_a_legacy_instrument_keeps_its_listing_venue() -> None:
    """Legacy equity lakes hold the listing venue under ``exchange_name``.

    The fork moved it aside because a plain ``exchange`` collided with the old
    ``exchange=`` partition key. That key is ``source=`` now, so the canonical struct uses
    the real name and the reader has to accept both.
    """
    rec = from_row(_legacy_equity_row("instrument", name="Apple Inc.", exchange_name="NASDAQ"))

    assert rec.asset_class is AssetClass.EQUITY
    assert rec.name == "Apple Inc."  # type: ignore[union-attr]
    assert rec.exchange == "NASDAQ"  # type: ignore[union-attr]


def test_a_row_naming_its_origin_under_no_marker_is_refused() -> None:
    """The dialect decides how every other column is read, so it is never guessed."""
    row = _legacy_equity_row("trade", id="1", price=1.0, size=1.0)
    row["provider"] = None
    row["source"] = None
    with pytest.raises(KeyError, match="names its origin under none of"):
        from_row(row)
