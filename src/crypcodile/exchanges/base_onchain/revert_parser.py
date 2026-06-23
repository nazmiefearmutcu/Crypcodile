import logging
from typing import Any
from web3 import AsyncWeb3
from web3.exceptions import ContractLogicError
import eth_abi
import eth_utils

log = logging.getLogger(__name__)

class RevertReasonParser:
    def __init__(self, w3: AsyncWeb3, abi_registry: Any) -> None:
        self.w3 = w3
        self.abi_registry = abi_registry

    async def parse_revert_reason(self, tx_hash: str, block_number: int) -> str:
        try:
            tx = await self.w3.eth.get_transaction(tx_hash)
        except Exception as e:
            return f"Unknown: Failed to fetch transaction details: {e}"
            
        if not tx:
            return "Unknown: Transaction not found"
            
        call_params = {
            "from": tx.get("from"),
            "to": tx.get("to"),
            "data": tx.get("input") or tx.get("data") or "0x",
            "value": tx.get("value", 0),
            "gas": tx.get("gas", 21000),
        }
        
        sim_block = max(0, block_number - 1)
        
        try:
            await self.w3.eth.call(call_params, block_identifier=sim_block)
            return "Transaction succeeded in simulation"
        except ContractLogicError as cle:
            raw_data = getattr(cle, "data", None)
            if not raw_data:
                msg = str(cle)
                if "execution reverted" in msg.lower():
                    return msg
                return f"Revert: {msg}"
            return await self._decode_revert_data(raw_data, tx.get("to"))
        except Exception as e:
            err_data = None
            if hasattr(e, "args") and len(e.args) > 0 and isinstance(e.args[0], dict):
                err_data = e.args[0].get("data")
            elif hasattr(e, "data"):
                err_data = getattr(e, "data")
                
            if isinstance(err_data, dict):
                err_data = err_data.get("data") or err_data.get("message")
                
            if isinstance(err_data, str):
                return await self._decode_revert_data(err_data, tx.get("to"))
            
            msg = str(e)
            if "revert" in msg.lower():
                return msg
            return f"Simulation error: {msg}"

    async def _decode_revert_data(self, data_hex: str, contract_address: str | None) -> str:
        if not data_hex:
            return "Revert: No data returned"
            
        if data_hex.startswith("0x"):
            data_hex = data_hex[2:]
            
        try:
            data_bytes = bytes.fromhex(data_hex)
        except ValueError:
            return f"Revert: {data_hex}"
            
        if len(data_bytes) < 4:
            return f"Revert: {data_hex}"
            
        selector = data_bytes[:4]
        payload = data_bytes[4:]
        
        # Standard Error(string)
        if selector == b"\x08\xc3\x79\xa0":
            try:
                msg = eth_abi.decode(["string"], payload)[0]
                return f"Revert: {msg}"
            except Exception:
                pass
                
        # Standard Panic(uint256)
        if selector == b"\x4e\x48\x7b\x71":
            try:
                code = eth_abi.decode(["uint256"], payload)[0]
                panic_codes = {
                    0x01: "Assertion error",
                    0x11: "Arithmetic overflow/underflow",
                    0x12: "Divide by zero",
                    0x21: "Invalid enum value",
                    0x22: "Storage byte array length error",
                    0x31: "Pop empty array",
                    0x32: "Array index out of bounds",
                    0x41: "Allocation error (out of memory)",
                    0x51: "Zero-initialized variable of internal function type",
                }
                desc = panic_codes.get(code, f"Panic code {hex(code)}")
                return f"Panic: {desc}"
            except Exception:
                pass
                
        # Custom Error decoding via ABI registry
        if contract_address and self.abi_registry:
            try:
                abi = await self.abi_registry.get_abi(contract_address)
                for item in abi:
                    if item.get("type") == "error":
                        name = item.get("name", "")
                        inputs = item.get("inputs", [])
                        input_types = [inp.get("type", "") for inp in inputs]
                        sig = f"{name}({','.join(input_types)})"
                        sig_hash = eth_utils.keccak(text=sig)[:4]
                        if sig_hash == selector:
                            decoded = eth_abi.decode(input_types, payload)
                            decoded_str = ", ".join(f"{inputs[i].get('name')}={val}" for i, val in enumerate(decoded))
                            return f"CustomError: {name}({decoded_str})"
            except Exception as e:
                log.debug(f"Failed to decode custom error via ABI: {e}")
                
        return f"CustomError({selector.hex()}): {payload.hex()}"
