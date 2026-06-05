"""Tests for Task 6.1 - Black-76 pricing, greeks, and implied-vol solver.

Acceptance criteria (from the plan, verbatim golden numbers):
  - bs_price(100, 100, 1.0, 0.2, CALL) approx 7.9656 (abs tol 1e-3); equals PUT (ATM symmetry).
  - bs_greeks(100,100,1.0,0.2,CALL).delta approx norm_cdf(0.1) approx 0.5398 (tol 1e-3);
    gamma > 0; vega approx 100*norm_pdf(0.1)*1 approx 39.69 (tol 1e-1).
  - Put-call parity: call - put == D*(forward - strike) within 1e-9 for
    (F=120,K=100,T=0.5,vol=0.3,r=0.05).
  - IV round-trip: implied_vol(bs_price(100,110,0.5,0.35,CALL), 100,110,0.5,CALL) approx
    0.35 (tol 1e-4); same for a PUT.
  - No-arb / expired guards: implied_vol(price=0.0, ...) -> None;
    bs_price(100,100,-1,0.2,CALL)==0.0; bs_greeks(...,-1,...) all zero.
  - ruff + mypy clean.
"""

from __future__ import annotations

import math

from crocodile.analytics.blackscholes import (
    Greeks,
    bs_greeks,
    bs_price,
    implied_vol,
    norm_cdf,
    norm_pdf,
)
from crocodile.schema.enums import OptType

# ---------------------------------------------------------------------------
# Helpers used in assertions (mirrors the implementation helpers)
# ---------------------------------------------------------------------------

CALL = OptType.CALL
PUT = OptType.PUT


# ---------------------------------------------------------------------------
# norm_cdf / norm_pdf sanity
# ---------------------------------------------------------------------------


def test_norm_cdf_symmetry() -> None:
    """N(0) == 0.5; N(x) + N(-x) == 1."""
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    for x in (0.1, 1.0, -1.5, 2.0):
        assert abs(norm_cdf(x) + norm_cdf(-x) - 1.0) < 1e-12, f"symmetry failed at x={x}"


def test_norm_pdf_positive_and_symmetric() -> None:
    assert norm_pdf(0.0) > 0
    assert abs(norm_pdf(0.1) - norm_pdf(-0.1)) < 1e-14
    # mode at 0
    assert norm_pdf(0.0) >= norm_pdf(1.0)


# ---------------------------------------------------------------------------
# bs_price golden values
# ---------------------------------------------------------------------------


def test_bs_price_atm_call_golden() -> None:
    """ATM call price ≈ 7.9656 (tol 1e-3)."""
    price = bs_price(100.0, 100.0, 1.0, 0.2, CALL)
    assert abs(price - 7.9656) < 1e-3, f"ATM call price={price}, expected≈7.9656"


def test_bs_price_atm_put_equals_call() -> None:
    """ATM put == ATM call (Black-76 symmetry when F==K, r==0)."""
    call = bs_price(100.0, 100.0, 1.0, 0.2, CALL)
    put = bs_price(100.0, 100.0, 1.0, 0.2, PUT)
    assert abs(call - put) < 1e-9, f"ATM symmetry broken: call={call}, put={put}"


def test_bs_price_put_call_parity() -> None:
    """call - put == D*(F - K) within 1e-9 for (F=120,K=100,T=0.5,vol=0.3,r=0.05)."""
    F, K, T, vol, r = 120.0, 100.0, 0.5, 0.3, 0.05
    call = bs_price(F, K, T, vol, CALL, rate=r)
    put = bs_price(F, K, T, vol, PUT, rate=r)
    D = math.exp(-r * T)
    parity = call - put - D * (F - K)
    assert abs(parity) < 1e-9, f"Put-call parity violated: diff={parity}"


# ---------------------------------------------------------------------------
# bs_price expired guard
# ---------------------------------------------------------------------------


def test_bs_price_expired_call_returns_intrinsic() -> None:
    """Expired (t_years <= 0): bs_price = D*max(F-K, 0) for call."""
    price = bs_price(100.0, 100.0, -1.0, 0.2, CALL)
    # D = exp(-0*(-1)) = 1; max(100-100,0)=0
    assert price == 0.0, f"expired ATM call should be 0, got {price}"


def test_bs_price_expired_itm_call() -> None:
    """Expired ITM call: D*max(F-K,0)."""
    price = bs_price(110.0, 100.0, 0.0, 0.2, CALL)
    expected = max(110.0 - 100.0, 0.0)  # rate=0 so D=1
    assert abs(price - expected) < 1e-9, f"expired ITM call price={price}, expected={expected}"


def test_bs_price_expired_put_returns_intrinsic() -> None:
    """Expired ITM put: D*max(K-F,0)."""
    price = bs_price(90.0, 100.0, -1.0, 0.2, PUT)
    expected = max(100.0 - 90.0, 0.0)
    assert abs(price - expected) < 1e-9, f"expired ITM put price={price}, expected={expected}"


# ---------------------------------------------------------------------------
# bs_greeks golden values
# ---------------------------------------------------------------------------


def test_bs_greeks_call_delta_golden() -> None:
    """delta_call ≈ norm_cdf(0.1) ≈ 0.5398 (tol 1e-3)."""
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL)
    expected_delta = norm_cdf(0.1)
    assert abs(g.delta - expected_delta) < 1e-3, f"delta={g.delta}, expected≈{expected_delta}"


def test_bs_greeks_call_gamma_positive() -> None:
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL)
    assert g.gamma > 0, f"gamma should be positive, got {g.gamma}"


def test_bs_greeks_call_vega_golden() -> None:
    """vega ≈ 100 * norm_pdf(0.1) * 1 ≈ 39.69 (tol 1e-1)."""
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL)
    expected_vega = 100.0 * norm_pdf(0.1) * 1.0
    assert abs(g.vega - expected_vega) < 1e-1, f"vega={g.vega}, expected≈{expected_vega}"


def test_bs_greeks_put_delta_negative() -> None:
    """Put delta should be negative (in range [-1, 0])."""
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, PUT)
    assert -1.0 < g.delta < 0.0, f"put delta out of range: {g.delta}"


def test_bs_greeks_call_put_delta_sum_is_D() -> None:
    """delta_call + |delta_put| = D for same strike (Black-76 property)."""
    F, K, T, vol, r = 100.0, 100.0, 1.0, 0.2, 0.05
    D = math.exp(-r * T)
    g_call = bs_greeks(F, K, T, vol, CALL, rate=r)
    g_put = bs_greeks(F, K, T, vol, PUT, rate=r)
    # delta_call - delta_put = D  (because delta_put = delta_call - D)
    assert abs(g_call.delta - g_put.delta - D) < 1e-9


def test_bs_greeks_expired_all_zero() -> None:
    """Expired: all greeks are zero."""
    g = bs_greeks(100.0, 100.0, -1.0, 0.2, CALL)
    assert isinstance(g, Greeks)
    assert g.delta == 0.0
    assert g.gamma == 0.0
    assert g.vega == 0.0
    assert g.theta == 0.0
    assert g.rho == 0.0


def test_bs_greeks_returns_named_tuple() -> None:
    """Greeks is a NamedTuple with the required fields."""
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL)
    assert hasattr(g, "delta")
    assert hasattr(g, "gamma")
    assert hasattr(g, "vega")
    assert hasattr(g, "theta")
    assert hasattr(g, "rho")


# ---------------------------------------------------------------------------
# implied_vol round-trip
# ---------------------------------------------------------------------------


def test_implied_vol_call_roundtrip() -> None:
    """implied_vol(bs_price(100,110,0.5,0.35,CALL), ...) ≈ 0.35 (tol 1e-4)."""
    target_vol = 0.35
    price = bs_price(100.0, 110.0, 0.5, target_vol, CALL)
    iv = implied_vol(price, 100.0, 110.0, 0.5, CALL)
    assert iv is not None, "implied_vol returned None for a valid price"
    assert abs(iv - target_vol) < 1e-4, f"IV round-trip: {iv} != {target_vol}"


def test_implied_vol_put_roundtrip() -> None:
    """implied_vol(bs_price(100,110,0.5,0.35,PUT), ...) ≈ 0.35 (tol 1e-4)."""
    target_vol = 0.35
    price = bs_price(100.0, 110.0, 0.5, target_vol, PUT)
    iv = implied_vol(price, 100.0, 110.0, 0.5, PUT)
    assert iv is not None, "implied_vol returned None for a valid price"
    assert abs(iv - target_vol) < 1e-4, f"IV round-trip: {iv} != {target_vol}"


def test_implied_vol_atm_roundtrip() -> None:
    """ATM round-trip with rate=0."""
    target_vol = 0.2
    price = bs_price(100.0, 100.0, 1.0, target_vol, CALL)
    iv = implied_vol(price, 100.0, 100.0, 1.0, CALL)
    assert iv is not None
    assert abs(iv - target_vol) < 1e-4


def test_implied_vol_with_rate_roundtrip() -> None:
    """Round-trip with non-zero rate."""
    F, K, T, vol, r = 120.0, 100.0, 0.5, 0.3, 0.05
    price = bs_price(F, K, T, vol, CALL, rate=r)
    iv = implied_vol(price, F, K, T, CALL, rate=r)
    assert iv is not None
    assert abs(iv - vol) < 1e-4


def test_implied_vol_zero_price_returns_none() -> None:
    """implied_vol(price=0.0, ...) → None (no-arb bounds)."""
    iv = implied_vol(0.0, 100.0, 100.0, 1.0, CALL)
    assert iv is None, f"expected None for zero price, got {iv}"


def test_implied_vol_expired_returns_none() -> None:
    """t_years <= 0 → None."""
    iv = implied_vol(5.0, 100.0, 100.0, 0.0, CALL)
    assert iv is None
    iv2 = implied_vol(5.0, 100.0, 100.0, -1.0, CALL)
    assert iv2 is None


def test_implied_vol_price_above_forward_returns_none() -> None:
    """Call price >= D*F is above theoretical max → None."""
    # D*F for rate=0, F=100 is 100; pass price=101 (above max)
    iv = implied_vol(101.0, 100.0, 100.0, 1.0, CALL)
    assert iv is None


def test_implied_vol_price_below_intrinsic_returns_none() -> None:
    """Price below discounted intrinsic (no-arb lower bound) → None."""
    # ITM call (F=200, K=100, T=1, r=0): intrinsic = D*(F-K) = 100;
    # a price of 50 is below that.
    iv = implied_vol(50.0, 200.0, 100.0, 1.0, CALL)
    assert iv is None


def test_implied_vol_various_vol_levels() -> None:
    """IV solver works across a range of vol levels."""
    for target in (0.1, 0.5, 1.0, 2.0, 3.0):
        price = bs_price(100.0, 100.0, 1.0, target, CALL)
        iv = implied_vol(price, 100.0, 100.0, 1.0, CALL)
        assert iv is not None, f"IV returned None for vol={target}"
        assert abs(iv - target) < 1e-4, f"IV={iv} != target={target}"


# ---------------------------------------------------------------------------
# Edge / robustness
# ---------------------------------------------------------------------------


def test_bs_price_deep_itm_call() -> None:
    """Deep ITM call price should approach D*(F-K)."""
    F, K, T, vol = 200.0, 100.0, 1.0, 0.2
    price = bs_price(F, K, T, vol, CALL)
    intrinsic = F - K  # D=1 (r=0)
    assert price >= intrinsic - 1e-6, "call price below intrinsic"


def test_bs_price_deep_otm_call_near_zero() -> None:
    """Deep OTM call price should be close to zero."""
    price = bs_price(10.0, 10000.0, 1.0, 0.2, CALL)
    assert price < 1e-10, f"deep OTM call price too large: {price}"


def test_bs_greeks_theta_negative_for_call() -> None:
    """Theta should be negative for a standard long call (time decay)."""
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL)
    assert g.theta < 0, f"theta should be negative, got {g.theta}"


def test_bs_greeks_theta_nonzero_rate_golden() -> None:
    """Theta golden pin at r=0.05: ATM call theta ≈ -3.397 (tol 1e-2).

    Finite-difference confirmation (central, eps=1e-5):
        d(price)/d(T) ≈ -3.3971

    The correct Black-76 formula is:
        theta = -D*F*n(d1)*vol/(2*sqrt_t) + rate*price
    where the rate term is *added* (not subtracted).  Subtracting it
    over-counts the rate drag and gives ≈ -4.155 instead of ≈ -3.397.
    """
    g = bs_greeks(100.0, 100.0, 1.0, 0.2, CALL, rate=0.05)
    assert abs(g.theta - (-3.397)) < 0.01, (
        f"theta_call(r=0.05) = {g.theta:.4f}, expected ≈ -3.397"
    )


def test_implied_vol_bisection_fallback() -> None:
    """Bisection branch is exercised when Newton steps outside [_IV_MIN, _IV_MAX].

    F=100, K=130, T=0.1, vol=4.0 (high-vol OTM call): the first Newton step
    from the seed vol=0.5 overshoots to ≈ 11.56, which is above _IV_MAX=10.0.
    The solver falls back to bisection and must still recover vol ≈ 4.0.

    Verification that Newton indeed steps OOB (checked analytically):
        At seed=0.5, vega ≈ tiny → step = 0.5 - (p_seed - target)/vega ≈ 11.56 > 10.
    """
    target_vol = 4.0
    F, K, T = 100.0, 130.0, 0.1
    price = bs_price(F, K, T, target_vol, CALL)
    iv = implied_vol(price, F, K, T, CALL)
    assert iv is not None, "implied_vol returned None for a valid high-vol price"
    assert abs(iv - target_vol) < 1e-4, (
        f"bisection fallback: IV={iv:.6f} != target={target_vol}"
    )


def test_bs_greeks_rho_call() -> None:
    """Black-76 rho: rho_call = -t_years * call_price."""
    F, K, T, vol = 100.0, 100.0, 1.0, 0.2
    call_price = bs_price(F, K, T, vol, CALL)
    g = bs_greeks(F, K, T, vol, CALL)
    expected_rho = -T * call_price
    assert abs(g.rho - expected_rho) < 1e-9, f"rho_call={g.rho}, expected={expected_rho}"


def test_bs_greeks_rho_put() -> None:
    """Black-76 rho: rho_put = -t_years * put_price."""
    F, K, T, vol = 100.0, 100.0, 1.0, 0.2
    put_price = bs_price(F, K, T, vol, PUT)
    g = bs_greeks(F, K, T, vol, PUT)
    expected_rho = -T * put_price
    assert abs(g.rho - expected_rho) < 1e-9, f"rho_put={g.rho}, expected={expected_rho}"
