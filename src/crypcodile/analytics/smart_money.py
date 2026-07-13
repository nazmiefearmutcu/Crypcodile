from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


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

    def snapshot(self) -> list[dict[str, Any]]:
        """Return all tracked address states sorted by total volume descending."""
        rows = list(self.flows.values())
        rows.sort(key=lambda r: float(r.get("total_volume_usd", 0.0)), reverse=True)
        return rows


def load_watchlist(path: str | Path) -> dict[str, str]:
    """Load a watchlist JSON mapping address -> label (case-normalized keys).

    Accepted shapes:
    - ``{"0xabc...": "label", ...}``
    - ``["0xabc...", ...]`` (label equals address)
    - ``{"addresses": ["0x...", ...]}``
    - ``{"watchlist": {"0x...": "label"}}`` or ``{"labels": {...}}``
    """
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    return normalize_watchlist(payload)


def normalize_watchlist(payload: Any) -> dict[str, str]:
    """Normalize various watchlist JSON shapes to ``{addr_lower: label}``."""
    if payload is None:
        return {}

    if isinstance(payload, Mapping):
        if "watchlist" in payload and isinstance(payload["watchlist"], Mapping):
            return {
                str(k).lower(): str(v)
                for k, v in payload["watchlist"].items()
                if k is not None
            }
        if "labels" in payload and isinstance(payload["labels"], Mapping):
            return {
                str(k).lower(): str(v)
                for k, v in payload["labels"].items()
                if k is not None
            }
        if "addresses" in payload and isinstance(payload["addresses"], Sequence):
            return {
                str(a).lower(): str(a)
                for a in payload["addresses"]
                if a is not None and str(a).strip()
            }
        # Flat address -> label map (skip non-address-looking nested containers)
        out: dict[str, str] = {}
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                continue
            if k is None:
                continue
            out[str(k).lower()] = str(v)
        return out

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return {
            str(a).lower(): str(a)
            for a in payload
            if a is not None and str(a).strip()
        }

    raise TypeError(
        "watchlist must be a JSON object (addr->label) or list of addresses"
    )


def transfers_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Coerce tabular transfer rows into SmartMoneyTracker event dicts."""
    out: list[dict[str, Any]] = []
    for row in rows:
        from_addr = row.get("from") or row.get("from_address") or row.get("sender")
        to_addr = row.get("to") or row.get("to_address") or row.get("recipient")
        if from_addr is None and to_addr is None:
            continue
        usd_raw = row.get("usd_value", row.get("amount", row.get("value", 0.0)))
        try:
            usd_value = float(usd_raw if usd_raw is not None else 0.0)
        except (TypeError, ValueError):
            usd_value = 0.0
        ts_raw = row.get("timestamp", row.get("local_ts", 0))
        try:
            ts = int(ts_raw if ts_raw is not None else 0)
        except (TypeError, ValueError):
            ts = 0
        out.append(
            {
                "from": str(from_addr) if from_addr is not None else None,
                "to": str(to_addr) if to_addr is not None else None,
                "usd_value": usd_value,
                "timestamp": ts,
            }
        )
    return out


def summarize_smart_money(
    transfers: Iterable[Mapping[str, Any]],
    smart_addresses: Sequence[str] | set[str] | Mapping[str, str],
) -> list[dict[str, Any]]:
    """Process transfers and return per-address flow summary rows.

    ``smart_addresses`` may be a list/set of addresses or an address->label map.
    When labels are available they are attached as a ``label`` field.
    """
    if isinstance(smart_addresses, Mapping):
        labels = {str(k).lower(): str(v) for k, v in smart_addresses.items()}
        addresses: set[str] = set(labels.keys())
    else:
        labels = {}
        addresses = {str(a).lower() for a in smart_addresses}

    tracker = SmartMoneyTracker(addresses)
    for transfer in transfers_from_rows(transfers):
        tracker.process_transfer(transfer)

    rows = tracker.snapshot()
    if labels:
        for row in rows:
            addr_key = str(row.get("address", "")).lower()
            if addr_key in labels:
                row["label"] = labels[addr_key]
    return rows
