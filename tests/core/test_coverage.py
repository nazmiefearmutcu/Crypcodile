"""The resolver, exercised on a crypto record.

The point is not that the class moved. It is that 108 venues quoting the same BTC
now have something to reconcile them, which the crypto side never had.
"""

from crocodile.core.coverage import CoverageResolver
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import Trade


def _trade(source: str, price: float) -> Trade:
    return Trade(
        source=source,
        symbol="BTC/USDT",
        symbol_raw="BTCUSDT",
        local_ts=1_700_000_000_000_000_000,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        id="1",
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def test_priority_strategy_picks_the_preferred_venue() -> None:
    resolver = CoverageResolver(priority_list=["binance", "mexc"])
    resolved = resolver.resolve_records([_trade("mexc", 2.0), _trade("binance", 1.0)], "priority")
    assert [r.price for r in resolved] == [1.0]


def test_an_unknown_source_ranks_last() -> None:
    resolver = CoverageResolver(priority_list=["binance"])
    assert resolver.get_priority_rank("some-new-venue") == 1
