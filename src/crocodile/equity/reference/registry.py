"""Resolve a provider's raw symbol to the identity of the security behind it.

The struct this hands out lives in :mod:`crocodile.equity.reference.identity`; it used
to be declared here under the name ``Instrument``, which the canonical record union also
uses for a different thing.
"""

from __future__ import annotations

from crocodile.equity.reference.identity import InstrumentIdentity
from crocodile.equity.reference.master import SecurityMaster


class InstrumentRegistry:
    def __init__(self, security_master: SecurityMaster | None = None) -> None:
        self._by_raw: dict[tuple[str, str], InstrumentIdentity] = {}
        self._by_symbol: dict[str, InstrumentIdentity] = {}
        self.security_master = security_master

    def add(self, inst: InstrumentIdentity) -> None:
        self._by_raw[(inst.source, inst.symbol_raw)] = inst
        self._by_symbol[inst.symbol] = inst

    def by_raw(self, source: str, symbol_raw: str) -> InstrumentIdentity:
        inst = self.get_raw(source, symbol_raw)
        if inst is None:
            raise KeyError((source, symbol_raw))
        return inst

    def by_symbol(self, symbol: str) -> InstrumentIdentity:
        inst = self._by_symbol.get(symbol)
        if inst is not None:
            return inst

        if self.security_master is not None:
            sec = self.security_master.get_by_symbol(symbol)
            if sec is not None:
                inst = InstrumentIdentity(
                    symbol=sec.symbol,
                    source="default",
                    symbol_raw=sec.ticker,
                    security_type=sec.security_type,
                    name=sec.name,
                    exchange=sec.exchange,
                    cik=sec.cik,
                    figi=sec.figi,
                    cusip=sec.cusip,
                )
                self._by_symbol[symbol] = inst
                return inst
        raise KeyError(symbol)

    def get_raw(self, source: str, symbol_raw: str) -> InstrumentIdentity | None:
        inst = self._by_raw.get((source, symbol_raw))
        if inst is not None:
            return inst

        if self.security_master is not None:
            symbol = self.security_master.resolve_ticker(symbol_raw)
            if symbol is not None:
                sec = self.security_master.get_by_symbol(symbol)
                if sec is not None:
                    inst = InstrumentIdentity(
                        symbol=sec.symbol,
                        source=source,
                        symbol_raw=symbol_raw,
                        security_type=sec.security_type,
                        name=sec.name,
                        exchange=sec.exchange,
                        cik=sec.cik,
                        figi=sec.figi,
                        cusip=sec.cusip,
                    )
                    self._by_raw[(source, symbol_raw)] = inst
                    self._by_symbol[sec.symbol] = inst
                    return inst
        return None
