"""Tests for the whole-market census: aggregation (faked network) + rendering."""

from __future__ import annotations

import re
from typing import Any

from crypcodile import census


def _fixture() -> dict[str, Any]:
    return {
        "generated_ns": 1_700_000_000_000_000_000,
        "connectors": {"native": ["binance"], "native_count": 10, "ccxt_count": 104,
                       "total_reachable": 108},
        "venues": {
            "enumerated": 2,
            "total_markets": 4200,
            "by_kind": {"spot": 2500, "perpetual": 1200, "future": 100, "option": 400},
            "rows": [
                {"exchange": "binance", "markets": 2500, "spot": 1500, "perpetual": 800,
                 "future": 100, "option": 100},
                {"exchange": "okx", "markets": 1700, "spot": 1000, "perpetual": 400,
                 "future": 0, "option": 300},
            ],
        },
        "coins": {
            "active": 17657, "tracked_markets": 1500,
            "total_mcap_usd": 2.29e12, "total_vol_24h_usd": 40.2e9,
            "btc_dominance": 56.5, "eth_dominance": 9.9, "sampled": 250,
            "top": [{"symbol": "BTC", "name": "Bitcoin", "price": 64547.0, "mcap": 1.29e12,
                     "rank": 1, "change_24h": 0.7}],
            "top_gainers": [{"symbol": "PEPE", "name": "Pepe", "price": 0.00001,
                             "rank": 30, "change_24h": 18.4}],
            "top_losers": [{"symbol": "XYZ", "name": "Xyz", "price": 1.2, "rank": 90,
                            "change_24h": -12.1}],
        },
        "defi": {"chains": 458, "total_tvl_usd": 75.8e9,
                 "top_chains": [{"name": "Ethereum", "tvl": 50e9}]},
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def test_render_html_fills_every_placeholder():
    out = census.render_html(_fixture(), generated_iso="2026-07-19 12:00 UTC")
    # No unresolved {token} placeholders remain (CSS single braces are fine; a
    # leftover placeholder would look like {word}).
    leftovers = re.findall(r"\{[a-z_]+\}", out)
    assert leftovers == [], f"unfilled placeholders: {leftovers}"


def test_render_html_has_real_numbers_and_both_themes():
    out = census.render_html(_fixture(), generated_iso="2026-07-19 12:00 UTC")
    assert "108" in out                      # reachable venues
    assert "4,200" in out                    # total markets
    assert "17,657" in out                   # active coins
    assert "$2.29T" in out                   # market cap
    assert "56.5%" in out                    # btc dominance
    assert "BTC" in out and "PEPE" in out    # tables rendered
    # theme-aware: both token sets present
    assert '[data-theme="dark"]' in out and '[data-theme="light"]' in out
    assert "prefers-color-scheme: light" in out
    # self-contained: no external resource links
    assert "http://" not in out
    assert "src=" not in out and "@import" not in out


def test_render_html_movers_colored_by_direction():
    out = census.render_html(_fixture())
    assert 'class="chg up"' in out    # gainer
    assert 'class="chg down"' in out  # loser


def test_render_terminal_summary():
    out = census.render_terminal(_fixture())
    assert "Reachable venues: 108" in out
    assert "BTC dom 56.5%" in out
    assert "DeFi TVL: $75.80B across 458 chains" in out


def test_fmt_usd_scales():
    assert census._fmt_usd(2.29e12) == "$2.29T"
    assert census._fmt_usd(40.2e9) == "$40.20B"
    assert census._fmt_usd(500) == "$500"


# --------------------------------------------------------------------------- #
# aggregation (network faked)
# --------------------------------------------------------------------------- #

async def test_market_census_aggregates(monkeypatch):
    async def fake_venue(exchange, sem):
        data = {"binance": {"spot": 100, "perpetual": 50, "future": 5, "option": 2},
                "okx": {"spot": 80, "perpetual": 40, "future": 0, "option": 10}}
        c = data[exchange]
        return {"exchange": exchange, "markets": sum(c.values()), **c}

    async def fake_global(session):
        return {"active_cryptocurrencies": 17000, "markets": 1500,
                "total_market_cap": {"usd": 2.3e12}, "total_volume": {"usd": 41e9},
                "market_cap_percentage": {"btc": 56.0, "eth": 10.0}}

    async def fake_markets(session, **kw):
        return [
            {"id": "a", "symbol": "a", "current_price": 1, "market_cap": 9,
             "market_cap_rank": 1, "price_change_percentage_24h": 5.0},
            {"id": "b", "symbol": "b", "current_price": 2, "market_cap": 8,
             "market_cap_rank": 2, "price_change_percentage_24h": -3.0},
        ]

    async def fake_defi(session):
        return {"chains": 400, "total_tvl_usd": 70e9,
                "top_chains": [{"name": "Ethereum", "tvl": 50e9}]}

    monkeypatch.setattr(census, "_venue_census", fake_venue)
    monkeypatch.setattr(census, "fetch_global", fake_global)
    monkeypatch.setattr(census, "fetch_markets", fake_markets)
    monkeypatch.setattr(census, "_fetch_defi", fake_defi)

    snap = await census.market_census(
        generated_ns=123, venues=["binance", "okx"], coin_pages=1
    )
    assert snap["venues"]["total_markets"] == 157 + 130
    assert snap["venues"]["by_kind"]["spot"] == 180
    # rows sorted by markets desc
    assert snap["venues"]["rows"][0]["exchange"] == "binance"
    assert snap["coins"]["active"] == 17000
    assert snap["coins"]["btc_dominance"] == 56.0
    # gainers/losers sorted correctly
    assert snap["coins"]["top_gainers"][0]["symbol"] == "A"
    assert snap["coins"]["top_losers"][0]["symbol"] == "B"
    assert snap["defi"]["chains"] == 400
    assert snap["connectors"]["total_reachable"] >= snap["connectors"]["native_count"]
