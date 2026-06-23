from __future__ import annotations

import math
import pytest

from crypcodile.analytics.blackscholes import (
    PureMathGreeksSolverAdapter,
    SciPyGreeksSolverAdapter,
)
from crypcodile.schema.enums import OptType


@pytest.fixture
def pure_math_solver():
    return PureMathGreeksSolverAdapter()


@pytest.fixture
def scipy_solver():
    return SciPyGreeksSolverAdapter()


def test_adapters_pricing_equivalence(pure_math_solver, scipy_solver) -> None:
    """Test that pricing matches between pure math and SciPy solvers."""
    scenarios = [
        # (forward, strike, t_years, vol, opt_type, rate)
        (100.0, 100.0, 1.0, 0.2, OptType.CALL, 0.0),
        (100.0, 100.0, 1.0, 0.2, OptType.PUT, 0.0),
        (120.0, 100.0, 0.5, 0.3, OptType.CALL, 0.05),
        (120.0, 100.0, 0.5, 0.3, OptType.PUT, 0.05),
        (80.0, 100.0, 2.0, 0.15, OptType.CALL, 0.02),
        (80.0, 100.0, 2.0, 0.15, OptType.PUT, 0.02),
    ]

    for f, k, t, v, opt_type, r in scenarios:
        price_pure = pure_math_solver.price(f, k, t, v, opt_type, rate=r)
        price_scipy = scipy_solver.price(f, k, t, v, opt_type, rate=r)
        assert abs(price_pure - price_scipy) < 1e-9, f"Mismatch at pricing {f, k, t, v, opt_type, r}: {price_pure} vs {price_scipy}"


def test_adapters_greeks_equivalence(pure_math_solver, scipy_solver) -> None:
    """Test that calculated Greeks match between pure math and SciPy solvers."""
    scenarios = [
        (100.0, 100.0, 1.0, 0.2, OptType.CALL, 0.0),
        (100.0, 100.0, 1.0, 0.2, OptType.PUT, 0.0),
        (120.0, 100.0, 0.5, 0.3, OptType.CALL, 0.05),
        (120.0, 100.0, 0.5, 0.3, OptType.PUT, 0.05),
        (80.0, 100.0, 2.0, 0.15, OptType.CALL, 0.02),
        (80.0, 100.0, 2.0, 0.15, OptType.PUT, 0.02),
    ]

    for f, k, t, v, opt_type, r in scenarios:
        g_pure = pure_math_solver.greeks(f, k, t, v, opt_type, rate=r)
        g_scipy = scipy_solver.greeks(f, k, t, v, opt_type, rate=r)

        assert abs(g_pure.delta - g_scipy.delta) < 1e-9, f"Delta mismatch: {g_pure.delta} vs {g_scipy.delta}"
        assert abs(g_pure.gamma - g_scipy.gamma) < 1e-9, f"Gamma mismatch: {g_pure.gamma} vs {g_scipy.gamma}"
        assert abs(g_pure.vega - g_scipy.vega) < 1e-9, f"Vega mismatch: {g_pure.vega} vs {g_scipy.vega}"
        assert abs(g_pure.theta - g_scipy.theta) < 1e-9, f"Theta mismatch: {g_pure.theta} vs {g_scipy.theta}"
        assert abs(g_pure.rho - g_scipy.rho) < 1e-9, f"Rho mismatch: {g_pure.rho} vs {g_scipy.rho}"


def test_adapters_implied_vol_equivalence(pure_math_solver, scipy_solver) -> None:
    """Test that implied volatility calculations match."""
    scenarios = [
        (100.0, 100.0, 1.0, 0.2, OptType.CALL, 0.0),
        (100.0, 110.0, 0.5, 0.35, OptType.CALL, 0.0),
        (100.0, 110.0, 0.5, 0.35, OptType.PUT, 0.0),
        (120.0, 100.0, 0.5, 0.3, OptType.CALL, 0.05),
    ]

    for f, k, t, target_vol, opt_type, r in scenarios:
        price = pure_math_solver.price(f, k, t, target_vol, opt_type, rate=r)

        iv_pure = pure_math_solver.implied_vol(price, f, k, t, opt_type, rate=r)
        iv_scipy = scipy_solver.implied_vol(price, f, k, t, opt_type, rate=r)

        assert iv_pure is not None
        assert iv_scipy is not None
        assert abs(iv_pure - iv_scipy) < 1e-5, f"IV Mismatch: {iv_pure} vs {iv_scipy}"


def test_adapters_edge_cases(pure_math_solver, scipy_solver) -> None:
    """Test boundary conditions, expired options, and invalid inputs."""
    # 1. vol = 0
    price_p = pure_math_solver.price(100.0, 100.0, 1.0, 0.0, OptType.CALL)
    price_s = scipy_solver.price(100.0, 100.0, 1.0, 0.0, OptType.CALL)
    assert price_p == price_s == 0.0

    g_p = pure_math_solver.greeks(100.0, 100.0, 1.0, 0.0, OptType.CALL)
    g_s = scipy_solver.greeks(100.0, 100.0, 1.0, 0.0, OptType.CALL)
    assert g_p == g_s == (0.0, 0.0, 0.0, 0.0, 0.0)

    # 2. t_years <= 0 (expired)
    price_p_exp = pure_math_solver.price(110.0, 100.0, 0.0, 0.2, OptType.CALL)
    price_s_exp = scipy_solver.price(110.0, 100.0, 0.0, 0.2, OptType.CALL)
    assert price_p_exp == price_s_exp == 10.0

    g_p_exp = pure_math_solver.greeks(110.0, 100.0, 0.0, 0.2, OptType.CALL)
    g_s_exp = scipy_solver.greeks(110.0, 100.0, 0.0, 0.2, OptType.CALL)
    assert g_p_exp == g_s_exp == (0.0, 0.0, 0.0, 0.0, 0.0)

    iv_p_exp = pure_math_solver.implied_vol(5.0, 100.0, 100.0, 0.0, OptType.CALL)
    iv_s_exp = scipy_solver.implied_vol(5.0, 100.0, 100.0, 0.0, OptType.CALL)
    assert iv_p_exp is None
    assert iv_s_exp is None

    # 3. vol < 0 raises ValueError
    with pytest.raises(ValueError, match="vol"):
        pure_math_solver.price(100.0, 100.0, 1.0, -0.1, OptType.CALL)

    with pytest.raises(ValueError, match="vol"):
        scipy_solver.price(100.0, 100.0, 1.0, -0.1, OptType.CALL)


def test_adapters_extreme_inputs(pure_math_solver, scipy_solver) -> None:
    """Test that extreme inputs (causing overflow/underflow/division-by-zero) return None without crashing."""
    scenarios = [
        # (price, forward, strike, t_years, opt_type, rate)
        (10.0, 100.0, 100.0, 1.0, OptType.CALL, -1000.0),
        (10.0, 100.0, 100.0, 1.0, OptType.CALL, 1000.0),
        (5.0, 100.0, 100.0, 1e-20, OptType.CALL, 0.05),
        (5.0, 100.0, 100.0, 1.0, OptType.CALL, 1e20),
        (5.0, 100.0, 100.0, 1.0, OptType.CALL, -1e20),
        (10.0, 100.0, 100.0, 1.0, OptType.CALL, -1e300),
    ]

    for price, f, k, t, opt_type, r in scenarios:
        res_pure = pure_math_solver.implied_vol(price, f, k, t, opt_type, rate=r)
        res_scipy = scipy_solver.implied_vol(price, f, k, t, opt_type, rate=r)
        assert res_pure is None
        assert res_scipy is None
