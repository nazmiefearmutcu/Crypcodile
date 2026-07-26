from __future__ import annotations

from typing import Any


class PerpPositionTracker:
    """A position tracker for perpetual contracts (e.g., GMX and Synthetix)."""

    def __init__(self) -> None:
        # Key: symbol (str) -> position state dict
        self.positions: dict[str, dict[str, Any]] = {}

    def _get_or_create_position(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.positions:
            self.positions[symbol] = {
                "symbol": symbol,
                "size_usd": 0.0,
                "collateral_usd": 0.0,
                "leverage": 0.0,
                "entry_price": 0.0,
                "realized_pnl": 0.0,
                "margin": 0.0,
                "liquidations": 0,
                "funding_fee": 0.0,
            }
        return self.positions[symbol]

    def process_event(self, event: dict[str, Any]) -> None:
        """Process a position event.

        Supported event types:
        - PositionIncrease / IncreasePosition
        - PositionDecrease / DecreasePosition
        - LiquidatePosition / Liquidation
        - ClosePosition
        """
        event_type = event.get("event") or event.get("event_name")
        if not event_type:
            return

        symbol = event.get("symbol")
        if not symbol:
            return

        pos = self._get_or_create_position(symbol)

        event_lower = event_type.lower()
        if "increase" in event_lower:
            size_delta = float(event.get("size_delta_usd", event.get("size_usd", 0.0)))
            collateral_delta = float(event.get("collateral_delta_usd", event.get("collateral_usd", 0.0)))
            price = float(event.get("price", event.get("entry_price", 0.0)))
            funding_fee_delta = float(event.get("funding_fee_delta", event.get("funding_fee", 0.0)))

            # Update size and collateral
            old_size = pos["size_usd"]
            pos["size_usd"] += size_delta
            pos["collateral_usd"] += collateral_delta
            pos["funding_fee"] += funding_fee_delta

            # Update entry price if size increased and price is provided
            if size_delta > 0 and price > 0:
                if old_size == 0:
                    pos["entry_price"] = price
                else:
                    pos["entry_price"] = (old_size * pos["entry_price"] + size_delta * price) / (old_size + size_delta)

        elif "decrease" in event_lower:
            size_delta = float(event.get("size_delta_usd", event.get("size_usd", 0.0)))
            collateral_delta = float(event.get("collateral_delta_usd", event.get("collateral_usd", 0.0)))
            realized_pnl_delta = float(event.get("realized_pnl_delta", event.get("realized_pnl", 0.0)))
            funding_fee_delta = float(event.get("funding_fee_delta", event.get("funding_fee", 0.0)))

            pos["size_usd"] = max(0.0, pos["size_usd"] - size_delta)
            pos["collateral_usd"] = max(0.0, pos["collateral_usd"] - collateral_delta)
            pos["realized_pnl"] += realized_pnl_delta
            pos["funding_fee"] += funding_fee_delta

            if pos["size_usd"] == 0:
                pos["entry_price"] = 0.0

        elif "liquidate" in event_lower or "liquidation" in event_lower:
            realized_pnl_delta = float(event.get("realized_pnl_delta", event.get("realized_pnl", 0.0)))
            pos["liquidations"] += 1
            if realized_pnl_delta != 0.0:
                pos["realized_pnl"] += realized_pnl_delta
            else:
                # If no explicit loss delta is provided, assume full collateral loss
                pos["realized_pnl"] -= pos["collateral_usd"]
            pos["size_usd"] = 0.0
            pos["collateral_usd"] = 0.0
            pos["entry_price"] = 0.0

        elif "close" in event_lower:
            realized_pnl_delta = float(event.get("realized_pnl_delta", event.get("realized_pnl", 0.0)))
            pos["realized_pnl"] += realized_pnl_delta
            pos["size_usd"] = 0.0
            pos["collateral_usd"] = 0.0
            pos["entry_price"] = 0.0

        # Update margin (can be explicitly provided or fall back to collateral_usd)
        if "margin" in event:
            pos["margin"] = float(event["margin"])
        else:
            pos["margin"] = pos["collateral_usd"]

        # Calculate leverage: size_usd / margin (or size_usd / collateral_usd)
        margin_val = pos["margin"]
        if margin_val > 0:
            pos["leverage"] = pos["size_usd"] / margin_val
        else:
            pos["leverage"] = 0.0

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get the current position state for a symbol."""
        return self.positions.get(symbol)


class GMXPositionTracker(PerpPositionTracker):
    """GMX-specific position tracker."""
    pass


class SynthetixPositionTracker(PerpPositionTracker):
    """Synthetix-specific position tracker."""
    pass
