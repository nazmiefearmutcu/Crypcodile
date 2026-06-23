from __future__ import annotations

import os
import time
import matplotlib.pyplot as plt
import polars as pl
import pytest

from crypcodile.analytics.volsurface_3d import plot_volsurface_3d


@pytest.fixture
def sample_surface_df() -> pl.DataFrame:
    now_ns = int(time.time() * 1e9)
    # Define a set of strikes and expiries to construct a surface
    expiries = [
        now_ns + int(7 * 86400 * 1e9),
        now_ns + int(14 * 86400 * 1e9),
        now_ns + int(30 * 86400 * 1e9),
    ]
    strikes = [55000.0, 60000.0, 65000.0]

    out_expiry = []
    out_strike = []
    out_iv = []
    out_fitted_iv = []
    out_opt_type = []
    out_moneyness = []
    out_source = []

    # Cartesian product
    for exp in expiries:
        for strike in strikes:
            out_expiry.append(exp)
            out_strike.append(strike)
            out_iv.append(0.45 - (strike - 60000.0) / 100000.0)
            out_fitted_iv.append(0.46 - (strike - 60000.0) / 100000.0)
            out_opt_type.append("C")
            out_moneyness.append(strike / 60000.0)
            out_source.append("computed")

    return pl.DataFrame(
        {
            "expiry": pl.Series(out_expiry, dtype=pl.Int64),
            "strike": pl.Series(out_strike, dtype=pl.Float64),
            "moneyness": pl.Series(out_moneyness, dtype=pl.Float64),
            "opt_type": pl.Series(out_opt_type, dtype=pl.Utf8),
            "iv": pl.Series(out_iv, dtype=pl.Float64),
            "fitted_iv": pl.Series(out_fitted_iv, dtype=pl.Float64),
            "source": pl.Series(out_source, dtype=pl.Utf8),
        }
    )


def test_plot_volsurface_3d_success(sample_surface_df, tmp_path) -> None:
    """Test generating and saving a 3D surface plot successfully."""
    # Plot using fitted_iv
    fig_fitted = plot_volsurface_3d(
        sample_surface_df,
        use_fitted=True,
        title="Fitted IV Surface",
    )
    assert isinstance(fig_fitted, plt.Figure)
    plt.close(fig_fitted)

    # Plot using raw iv
    fig_raw = plot_volsurface_3d(
        sample_surface_df,
        use_fitted=False,
        title="Raw IV Surface",
    )
    assert isinstance(fig_raw, plt.Figure)
    plt.close(fig_raw)

    # Test saving to a file
    save_file = os.path.join(tmp_path, "surface.png")
    fig_save = plot_volsurface_3d(
        sample_surface_df,
        use_fitted=True,
        save_path=save_file,
    )
    assert os.path.exists(save_file)
    assert os.path.getsize(save_file) > 0
    plt.close(fig_save)


def test_plot_volsurface_3d_insufficient_points() -> None:
    """Test that ValueError is raised if there are fewer than 3 valid points."""
    # Empty DataFrame
    empty_df = pl.DataFrame(
        {
            "expiry": pl.Series([], dtype=pl.Int64),
            "strike": pl.Series([], dtype=pl.Float64),
            "moneyness": pl.Series([], dtype=pl.Float64),
            "opt_type": pl.Series([], dtype=pl.Utf8),
            "iv": pl.Series([], dtype=pl.Float64),
            "fitted_iv": pl.Series([], dtype=pl.Float64),
            "source": pl.Series([], dtype=pl.Utf8),
        }
    )

    with pytest.raises(ValueError, match="Surface DataFrame is empty"):
        plot_volsurface_3d(empty_df)

    # DataFrame with 2 points
    insufficient_df = pl.DataFrame(
        {
            "expiry": pl.Series([1000, 2000], dtype=pl.Int64),
            "strike": pl.Series([50.0, 60.0], dtype=pl.Float64),
            "moneyness": pl.Series([0.9, 1.1], dtype=pl.Float64),
            "opt_type": pl.Series(["C", "P"], dtype=pl.Utf8),
            "iv": pl.Series([0.3, 0.4], dtype=pl.Float64),
            "fitted_iv": pl.Series([0.31, 0.41], dtype=pl.Float64),
            "source": pl.Series(["computed", "computed"], dtype=pl.Utf8),
        }
    )

    with pytest.raises(ValueError, match="Insufficient valid data points"):
        plot_volsurface_3d(insufficient_df)


def test_plot_volsurface_3d_degenerate_coordinates() -> None:
    """Test that ValueError is raised cleanly when inputs are duplicate or collinear."""
    # 1. Duplicate points (all same coordinate)
    duplicate_df = pl.DataFrame(
        {
            "expiry": pl.Series([1000, 1000, 1000], dtype=pl.Int64),
            "strike": pl.Series([50.0, 50.0, 50.0], dtype=pl.Float64),
            "moneyness": pl.Series([1.0, 1.0, 1.0], dtype=pl.Float64),
            "opt_type": pl.Series(["C", "C", "C"], dtype=pl.Utf8),
            "iv": pl.Series([0.3, 0.3, 0.3], dtype=pl.Float64),
            "fitted_iv": pl.Series([0.3, 0.3, 0.3], dtype=pl.Float64),
            "source": pl.Series(["computed", "computed", "computed"], dtype=pl.Utf8),
        }
    )

    with pytest.raises(ValueError, match="Insufficient non-collinear unique data points"):
        plot_volsurface_3d(duplicate_df)

    # 2. Collinear points (all lie on a single line)
    collinear_df = pl.DataFrame(
        {
            "expiry": pl.Series([1000, 2000, 3000], dtype=pl.Int64),
            "strike": pl.Series([50.0, 50.0, 50.0], dtype=pl.Float64),
            "moneyness": pl.Series([1.0, 1.0, 1.0], dtype=pl.Float64),
            "opt_type": pl.Series(["C", "C", "C"], dtype=pl.Utf8),
            "iv": pl.Series([0.3, 0.3, 0.3], dtype=pl.Float64),
            "fitted_iv": pl.Series([0.3, 0.3, 0.3], dtype=pl.Float64),
            "source": pl.Series(["computed", "computed", "computed"], dtype=pl.Utf8),
        }
    )

    with pytest.raises(ValueError, match="Insufficient non-collinear unique data points"):
        plot_volsurface_3d(collinear_df)
