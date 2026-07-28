"""The US Treasury's daily par yield curve, keyless.

The risk-free leg an equity carry is measured net of. See
:mod:`crocodile.equity.providers.treasury.client` for why this is a client rather than a
:class:`~crocodile.equity.providers.base.Provider`.
"""

from crocodile.equity.providers.treasury.client import (
    PAR_YIELD_CURVE_URL,
    Tenor,
    TreasuryYieldClient,
    parse_par_yield_csv,
    parse_tenor,
    tenor_days,
)

__all__ = [
    "PAR_YIELD_CURVE_URL",
    "Tenor",
    "TreasuryYieldClient",
    "parse_par_yield_csv",
    "parse_tenor",
    "tenor_days",
]
