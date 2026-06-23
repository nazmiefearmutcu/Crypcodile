from __future__ import annotations

import logging
import numpy as np
import polars as pl

# Use Agg backend for headless environments to prevent display-related errors.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from scipy.interpolate import griddata
try:
    from scipy.spatial import QhullError
except ImportError:
    class QhullError(Exception):  # type: ignore
        pass

log = logging.getLogger(__name__)


def plot_volsurface_3d(
    surface_df: pl.DataFrame,
    use_fitted: bool = True,
    at_ns: int | None = None,
    title: str = "Implied Volatility Surface",
    save_path: str | None = None,
    show: bool = False,
) -> Figure:
    """Generate a 3D visualization of the implied volatility surface.

    Args:
        surface_df: Polars DataFrame output from `iv_surface`.
        use_fitted: If True, plot `fitted_iv`. Otherwise, plot `iv`.
        at_ns: Reference timestamp in nanoseconds UTC. If provided, expiries are
               computed as days from this timestamp.
        title: Title of the plot.
        save_path: Path to save the generated plot image.
        show: If True, call plt.show() (display the window).

    Returns:
        The matplotlib Figure object.
    """
    if len(surface_df) == 0:
        raise ValueError("Surface DataFrame is empty")

    vol_col = "fitted_iv" if use_fitted else "iv"
    
    # Filter out rows with null/nan values in key fields
    df = surface_df.filter(
        pl.col("expiry").is_not_null() &
        pl.col("strike").is_not_null() &
        pl.col(vol_col).is_not_null() &
        pl.col(vol_col).is_finite()
    )

    if len(df) < 3:
        raise ValueError(f"Insufficient valid data points to construct a surface (need at least 3, got {len(df)})")

    # Compute days to expiry
    if at_ns is not None:
        expiries_days = (df["expiry"] - at_ns) / (1e9 * 86400.0)
    else:
        min_exp = df["expiry"].min()
        if min_exp is None:
            min_exp = 0
        expiries_days = (df["expiry"] - min_exp) / (1e9 * 86400.0)

    strikes = df["strike"].to_numpy()
    vols = df[vol_col].to_numpy()

    # Create a grid for interpolation
    num_grid_points = 50
    xi = np.linspace(expiries_days.min(), expiries_days.max(), num_grid_points)
    yi = np.linspace(strikes.min(), strikes.max(), num_grid_points)
    X, Y = np.meshgrid(xi, yi)

    # Interpolate the scattered data onto the grid
    points = np.column_stack((expiries_days.to_numpy(), strikes))
    try:
        Z = griddata(points, vols, (X, Y), method="linear")

        # Fallback to nearest neighbor interpolation if linear has NaNs at boundaries
        nan_mask = np.isnan(Z)
        if np.any(nan_mask):
            Z_nearest = griddata(points, vols, (X, Y), method="nearest")
            Z[nan_mask] = Z_nearest[nan_mask]
    except (ValueError, QhullError, Exception):
        raise ValueError(
            "Insufficient non-collinear unique data points to construct the 3D surface."
        )

    # Create 3D plot
    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection="3d")

    surf = ax.plot_surface(
        X, Y, Z,
        cmap="viridis",
        edgecolor="none",
        alpha=0.8,
    )

    # Plot the actual data points as scatter for reference
    ax.scatter(
        expiries_days.to_numpy(),
        strikes,
        vols,
        color="red",
        marker="o",
        s=15,
        label="Market Points",
    )

    ax.set_xlabel("Time to Expiry (Days)")
    ax.set_ylabel("Strike Price")
    ax.set_zlabel("Implied Volatility")
    ax.set_title(title)
    fig.colorbar(surf, ax=ax, shrink=0.5, aspect=5, label="Implied Volatility")

    plt.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=300)
    if show:
        plt.show()

    return fig
