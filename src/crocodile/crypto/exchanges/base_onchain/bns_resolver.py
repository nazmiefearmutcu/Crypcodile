import json
import os
import logging
from typing import Dict, Optional, Any
from web3 import Web3

log = logging.getLogger(__name__)

# BNS Registry Contract Address on Base
BNS_REGISTRY_ADDRESS = "0x00000000000C2E074eC67A0a1467b8068Cb9bc75"

class BNSResolver:
    """Bidirectional Base Name Service (.base) resolver with a persistent disk cache."""

    def __init__(self, cache_path: str = ".bns_resolver_cache.json") -> None:
        self.cache_path = cache_path
        # Map: name -> address and address -> name
        self.cache: Dict[str, str] = {}
        self._load_cache()
        # Seed stable default mocks to ensure offline tests can resolve bidirectionally
        self._seed_default_mocks()

    def _load_cache(self) -> None:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r") as f:
                    self.cache = json.load(f)
            except Exception as e:
                log.warning(f"Failed to load BNS resolver cache: {e}")

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w") as f:
                json.dump(self.cache, f)
        except Exception as e:
            log.warning(f"Failed to save BNS resolver cache: {e}")

    def _seed_default_mocks(self) -> None:
        mocks = {
            "coinbase.base": "0x5030000000000000000000000000000000000000",
            "alice.base": "0xa11ce00000000000000000000000000000000000",
            "bob.base": "0xb0b0000000000000000000000000000000000000",
        }
        for name, addr in mocks.items():
            checksum_addr = Web3.to_checksum_address(addr)
            if name not in self.cache:
                self.cache[name] = checksum_addr
            if checksum_addr not in self.cache:
                self.cache[checksum_addr] = name

    async def resolve_name(self, w3: Any, name: str) -> Optional[str]:
        """Resolve a .base domain name to an EVM address."""
        name_lower = name.lower().strip()
        if not name_lower.endswith(".base"):
            return None

        if name_lower in self.cache:
            return self.cache[name_lower]

        if not w3:
            return None

        try:
            # Query standard ENS/BNS resolver contract
            # BNS Registry implements owner(node) or resolver(node)
            # Standard ENS registry ABI has: resolver(bytes32 node) -> address
            # We can hash the name using namehash
            node = self._namehash(name_lower)
            registry_contract = w3.eth.contract(
                address=Web3.to_checksum_address(BNS_REGISTRY_ADDRESS),
                abi=[{
                    "inputs": [{"name": "node", "type": "bytes32"}],
                    "name": "resolver",
                    "outputs": [{"type": "address"}],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )
            resolver_address = await registry_contract.functions.resolver(node).call()
            if resolver_address and resolver_address != "0x" + "0" * 40:
                resolver_contract = w3.eth.contract(
                    address=resolver_address,
                    abi=[{
                        "inputs": [{"name": "node", "type": "bytes32"}],
                        "name": "addr",
                        "outputs": [{"type": "address"}],
                        "stateMutability": "view",
                        "type": "function"
                    }]
                )
                resolved_addr = await resolver_contract.functions.addr(node).call()
                if resolved_addr and resolved_addr != "0x" + "0" * 40:
                    checksum_addr = Web3.to_checksum_address(resolved_addr)
                    self.cache[name_lower] = checksum_addr
                    self.cache[checksum_addr] = name_lower
                    self._save_cache()
                    return checksum_addr
        except Exception as e:
            log.debug(f"BNS resolve_name failed for {name}: {e}")

        return None

    async def resolve_address(self, w3: Any, address: str) -> Optional[str]:
        """Perform reverse lookup from an EVM address to a .base domain name."""
        checksum_addr = Web3.to_checksum_address(address)
        if checksum_addr in self.cache:
            return self.cache[checksum_addr]

        if not w3:
            return None

        try:
            # Reverse lookup standard node: hex(address)[2:] + ".addr.reverse"
            reverse_name = f"{checksum_addr[2:].lower()}.addr.reverse"
            node = self._namehash(reverse_name)
            registry_contract = w3.eth.contract(
                address=Web3.to_checksum_address(BNS_REGISTRY_ADDRESS),
                abi=[{
                    "inputs": [{"name": "node", "type": "bytes32"}],
                    "name": "resolver",
                    "outputs": [{"type": "address"}],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )
            resolver_address = await registry_contract.functions.resolver(node).call()
            if resolver_address and resolver_address != "0x" + "0" * 40:
                resolver_contract = w3.eth.contract(
                    address=resolver_address,
                    abi=[{
                        "inputs": [{"name": "node", "type": "bytes32"}],
                        "name": "name",
                        "outputs": [{"type": "string"}],
                        "stateMutability": "view",
                        "type": "function"
                    }]
                )
                resolved_name = await resolver_contract.functions.name(node).call()
                if resolved_name:
                    self.cache[checksum_addr] = resolved_name
                    self.cache[resolved_name.lower()] = checksum_addr
                    self._save_cache()
                    return resolved_name
        except Exception as e:
            log.debug(f"BNS resolve_address failed for {address}: {e}")

        return None

    def _namehash(self, name: str) -> bytes:
        """Standard namehash algorithm."""
        node = b'\x00' * 32
        if name:
            parts = name.split(".")
            for part in reversed(parts):
                label_hash = Web3.keccak(text=part)
                node = Web3.keccak(node + label_hash)
        return node
