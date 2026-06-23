from __future__ import annotations

from typing import Any


class SmartMoneyTracker:
    """Monitors capital flows and volumes for selected profitable/MEV addresses."""

    def __init__(self, smart_addresses: list[str] | set[str]) -> None:
        self.smart_addresses = {addr.lower() for addr in smart_addresses}
        # Key: address (lowercase) -> state dict
        self.flows: dict[str, dict[str, Any]] = {}

    def _get_or_create_address_state(self, address: str) -> dict[str, Any]:
        addr_lower = address.lower()
        if addr_lower not in self.flows:
            self.flows[addr_lower] = {
                "address": address,
                "net_flow_usd": 0.0,
                "total_volume_usd": 0.0,
                "tx_count": 0,
                "last_active_ts": 0,
            }
        return self.flows[addr_lower]

    def process_transfer(self, transfer: dict[str, Any]) -> None:
        """Process a transfer or trade event to update capital flow metrics.

        Expected event format:
        {
            "from": str (sender address),
            "to": str (recipient address),
            "usd_value": float (flow value in USD),
            "timestamp": int (nanosecond timestamp or similar)
        }
        """
        from_addr = transfer.get("from")
        to_addr = transfer.get("to")
        usd_value = float(
            transfer.get(
                "usd_value", transfer.get("amount", transfer.get("value", 0.0))
            )
        )
        ts = int(transfer.get("timestamp", transfer.get("local_ts", 0)))

        # Update sender (outgoing flow: negative net flow)
        if from_addr and from_addr.lower() in self.smart_addresses:
            state = self._get_or_create_address_state(from_addr)
            state["net_flow_usd"] -= usd_value
            state["total_volume_usd"] += usd_value
            state["tx_count"] += 1
            state["last_active_ts"] = max(state["last_active_ts"], ts)

        # Update recipient (incoming flow: positive net flow)
        if to_addr and to_addr.lower() in self.smart_addresses:
            state = self._get_or_create_address_state(to_addr)
            state["net_flow_usd"] += usd_value
            state["total_volume_usd"] += usd_value
            state["tx_count"] += 1
            state["last_active_ts"] = max(state["last_active_ts"], ts)

    def get_address_state(self, address: str) -> dict[str, Any] | None:
        """Get the current flow state metrics for a specific address."""
        return self.flows.get(address.lower())
