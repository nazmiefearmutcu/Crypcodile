"""Analytics package — exports are resolved lazily (PEP 562).

Submodules pull heavy dependencies (numpy/polars/matplotlib); importing the
package — which also happens implicitly on any ``crypcodile.analytics.X``
submodule import — must stay cheap so light consumers (e.g. the exchange
connectors) never pay for plotting stacks they do not use.
"""

from typing import Any

_EXPORTS: dict[str, str] = {
    "calculate_ofi": "crypcodile.analytics.ofi",
    "parse_interval_to_ns": "crypcodile.analytics.ofi",
    "estimate_slippage": "crypcodile.analytics.slippage",
    "track_whale_alerts": "crypcodile.analytics.whale",
    "plot_volsurface_3d": "crypcodile.analytics.volsurface_3d",
    "calculate_bollinger_bands": "crypcodile.analytics.indicators",
    "calculate_ema": "crypcodile.analytics.indicators",
    "calculate_macd": "crypcodile.analytics.indicators",
    "calculate_rsi": "crypcodile.analytics.indicators",
    "calculate_sma": "crypcodile.analytics.indicators",
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
