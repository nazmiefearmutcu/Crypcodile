from __future__ import annotations

from typing import Any
import eth_abi
from web3 import AsyncWeb3


class WhaleTransferTracker:
    """Tracks large on-chain ERC-20 token transfers exceeding a USD threshold."""

    def __init__(
        self,
        w3: AsyncWeb3,
        token_address: str,
        token_price_usd: float,
        usd_threshold: float = 100000.0,
        decimals: int = 18,
    ) -> None:
        self.w3 = w3
        self.token_address = w3.to_checksum_address(token_address)
        self.token_price_usd = token_price_usd
        self.usd_threshold = usd_threshold
        self.decimals = decimals
        # keccak("Transfer(address,address,uint256)")
        self.transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        )

    async def get_whale_transfers(
        self, start_block: int, end_block: int
    ) -> list[dict[str, Any]]:
        """Fetch and filter Transfer logs within block range for whale movements."""
        try:
            logs = await self.w3.eth.get_logs(
                {
                    "address": self.token_address,
                    "topics": [self.transfer_topic],
                    "fromBlock": start_block,
                    "toBlock": end_block,
                }
            )
        except Exception:
            return []

        whales = []
        for log in logs:
            topics = log.get("topics", [])
            data = log.get("data", "")

            # Ensure we have a valid Transfer log: 3 topics [TransferTopic, from, to]
            if len(topics) < 3:
                continue

            try:
                # Decode addresses from topics
                from_addr = "0x" + topics[1].hex()[-40:]
                to_addr = "0x" + topics[2].hex()[-40:]
                from_addr_checksum = self.w3.to_checksum_address(from_addr)
                to_addr_checksum = self.w3.to_checksum_address(to_addr)

                # Decode amount from data
                data_hex = data.hex() if isinstance(data, bytes) else data
                data_hex = (
                    data_hex[2:] if data_hex.startswith("0x") else data_hex
                )
                data_bytes = bytes.fromhex(data_hex)

                raw_value = eth_abi.decode(["uint256"], data_bytes)[0]
                amount = float(raw_value) / (10**self.decimals)
                usd_value = amount * self.token_price_usd

                if usd_value >= self.usd_threshold:
                    whales.append({
                        "transaction_hash": log.get("transactionHash").hex()
                        if isinstance(log.get("transactionHash"), bytes)
                        else str(log.get("transactionHash", "")),
                        "block_number": int(log.get("blockNumber", 0)),
                        "from": from_addr_checksum,
                        "to": to_addr_checksum,
                        "amount": amount,
                        "usd_value": usd_value,
                    })
            except Exception:
                continue

        return whales
