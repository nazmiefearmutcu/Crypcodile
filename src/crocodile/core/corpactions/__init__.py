"""CRSP-style cumulative adjustment factors, for either asset class.

A token redenomination and a stock split rebase a price series the same way and obey the
same arithmetic, so one calculator serves both. See ``CorpActionType`` in
:mod:`crocodile.core.schema.enums`.
"""

from crocodile.core.corpactions.calculator import (
    adjust_bars,
    adjust_dataframe,
    calculate_cumulative_factors,
    calculate_total_returns,
)

__all__ = [
    "adjust_bars",
    "adjust_dataframe",
    "calculate_cumulative_factors",
    "calculate_total_returns",
]
