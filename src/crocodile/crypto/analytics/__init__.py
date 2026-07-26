"""Analytics package — exports are resolved lazily (PEP 562).

Submodules pull heavy dependencies (numpy/polars/matplotlib); importing the
package — which also happens implicitly on any ``crocodile.crypto.analytics.X``
submodule import — must stay cheap so light consumers (e.g. the exchange
connectors) never pay for plotting stacks they do not use.
"""

from typing import Any

_EXPORTS: dict[str, str] = {
    "calculate_ofi": "crocodile.crypto.analytics.ofi",
    "parse_interval_to_ns": "crocodile.crypto.analytics.ofi",
    "estimate_slippage": "crocodile.crypto.analytics.slippage",
    "track_whale_alerts": "crocodile.crypto.analytics.whale",
    "plot_volsurface_3d": "crocodile.crypto.analytics.volsurface_3d",
    "calculate_bollinger_bands": "crocodile.core.analytics.indicators",
    "calculate_ema": "crocodile.core.analytics.indicators",
    "calculate_macd": "crocodile.core.analytics.indicators",
    "calculate_rsi": "crocodile.core.analytics.indicators",
    "calculate_sma": "crocodile.core.analytics.indicators",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return __all__
