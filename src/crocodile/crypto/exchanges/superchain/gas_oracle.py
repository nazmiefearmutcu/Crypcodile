import logging
from typing import Any
from web3 import Web3

log = logging.getLogger(__name__)

# Standard OP Stack precompile contract address for L1 gas price oracle
GAS_ORACLE_ADDRESS = "0x420000000000000000000000000000000000000F"

# ABI for L1 Gas Price Oracle precompile
GAS_ORACLE_ABI = [
    {
        "inputs": [],
        "name": "l1BaseFee",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "overhead",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "scalar",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "data", "type": "bytes"}],
        "name": "getL1Fee",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

class SuperchainGasOracle:
    """Standard interface to query the OP Stack L1 Gas Price Oracle precompile."""

    def __init__(self) -> None:
        self.address = Web3.to_checksum_address(GAS_ORACLE_ADDRESS)

    async def get_l1_base_fee(self, w3: Any) -> int:
        """Query the L1 base fee (in wei) from the precompile."""
        try:
            contract = w3.eth.contract(address=self.address, abi=GAS_ORACLE_ABI)
            return await contract.functions.l1BaseFee().call()
        except Exception as e:
            log.debug(f"Failed to query l1BaseFee: {e}")
            return 0

    async def get_l1_overhead(self, w3: Any) -> int:
        """Query the L1 overhead from the precompile."""
        try:
            contract = w3.eth.contract(address=self.address, abi=GAS_ORACLE_ABI)
            return await contract.functions.overhead().call()
        except Exception as e:
            log.debug(f"Failed to query overhead: {e}")
            return 0

    async def get_l1_scalar(self, w3: Any) -> int:
        """Query the L1 scalar from the precompile."""
        try:
            contract = w3.eth.contract(address=self.address, abi=GAS_ORACLE_ABI)
            return await contract.functions.scalar().call()
        except Exception as e:
            log.debug(f"Failed to query scalar: {e}")
            return 0

    async def get_l1_fee_for_calldata(self, w3: Any, data: bytes) -> int:
        """Query the estimated L1 fee (in wei) for the given transaction calldata."""
        try:
            contract = w3.eth.contract(address=self.address, abi=GAS_ORACLE_ABI)
            return await contract.functions.getL1Fee(data).call()
        except Exception as e:
            log.debug(f"Failed to query getL1Fee: {e}")
            return 0
