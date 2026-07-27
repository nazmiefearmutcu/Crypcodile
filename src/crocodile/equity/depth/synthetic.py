from __future__ import annotations

import time
from typing import Any

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import DepthProfile
from crocodile.equity.depth.vap import (
    n_volume_bars,
    reference_price,
    split_ladder,
    volume_at_price,
)
from crocodile.equity.providers.yahoo import YahooClient


class SyntheticYahooDepthSource:
    """Keyless synthetic depth: Yahoo 1m bars -> volume-at-price ladder.

    RELATIVE liquidity (where volume concentrated), NOT real resting orders.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        bins: int = 40,
        top_n: int = 10,
        method: str = "uniform",
    ) -> None:
        self._client = client or YahooClient()
        self._bins = bins
        self._top_n = top_n
        self._method = method

    async def snapshot(self, symbol: str) -> DepthProfile:
        bars = await self._client.fetch_intraday_bars(symbol, "1m")
        if not bars:
            raise ValueError(f"no Yahoo 1m bars for {symbol!r}")
        ref = reference_price(bars)
        profile = volume_at_price(bars, bins=self._bins, method=self._method)
        bids, asks = split_ladder(profile, ref, top_n=self._top_n)
        # The tail is built rather than stated: `prov`, `prov_basis` and `prov_inputs` come
        # from the registration and `prov_confidence` from the registered formula, so the
        # number cannot drift from the sample the ladder was actually binned out of.
        tail = provenance_fields("yahoo_1m_vap", {"n_volume_bars": n_volume_bars(bars)})
        return DepthProfile(
            source="synth", symbol=f"synth:{symbol.upper()}", symbol_raw=symbol.upper(),
            local_ts=time.time_ns(), asset_class=AssetClass.EQUITY,
            bids=bids, asks=asks, reference_price=ref,
            depth=len(bids) + len(asks), source_ts=bars[-1].local_ts,
            prov=tail.prov, prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence, prov_inputs=tail.prov_inputs,
        )
