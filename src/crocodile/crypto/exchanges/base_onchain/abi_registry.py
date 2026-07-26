import os
import json
import logging
from typing import Any
import aiohttp
from web3 import AsyncWeb3

log = logging.getLogger(__name__)

class ABIRegistry:
    def __init__(self, cache_dir: str | None = None, basescan_api_key: str | None = None) -> None:
        self.cache_dir = cache_dir or os.path.expanduser("~/.crypcodile/abi_cache")
        self.api_key = basescan_api_key
        os.makedirs(self.cache_dir, exist_ok=True)

    async def get_abi(self, address: str) -> list[dict[str, Any]]:
        checksum_address = AsyncWeb3.to_checksum_address(address)
        cache_path = os.path.join(self.cache_dir, f"{checksum_address.lower()}.json")
        
        # 1. Local Cache Check
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Failed to read cached ABI for {checksum_address}: {e}")

        # 2. Try Basescan
        abi = await self._fetch_from_basescan(checksum_address)
        if abi:
            self._save_to_cache(checksum_address, abi)
            return abi

        # 3. Fallback to Sourcify
        abi = await self._fetch_from_sourcify(checksum_address)
        if abi:
            self._save_to_cache(checksum_address, abi)
            return abi

        raise ValueError(f"Could not retrieve ABI for {checksum_address} from Basescan or Sourcify")

    async def _fetch_from_basescan(self, address: str) -> list[dict[str, Any]] | None:
        url = "https://api.basescan.org/api"
        params = {
            "module": "contract",
            "action": "getabi",
            "address": address,
        }
        if self.api_key:
            params["apikey"] = self.api_key
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == "1":
                            result = data.get("result")
                            if isinstance(result, str):
                                return json.loads(result)
                            elif isinstance(result, list):
                                return result
                        else:
                            log.info(f"Basescan returned error for {address}: {data.get('result')}")
        except Exception as e:
            log.warning(f"Failed to fetch from Basescan for {address}: {e}")
        return None

    async def _fetch_from_sourcify(self, address: str) -> list[dict[str, Any]] | None:
        async with aiohttp.ClientSession() as session:
            for match_type in ["full_match", "partial_match"]:
                url = f"https://repo.sourcify.dev/contracts/{match_type}/8453/{address}/metadata.json"
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            abi = data.get("output", {}).get("abi")
                            if abi:
                                return abi
                except Exception as e:
                    log.warning(f"Failed to fetch from Sourcify ({match_type}) for {address}: {e}")
        return None

    def _save_to_cache(self, address: str, abi: list[dict[str, Any]]) -> None:
        cache_path = os.path.join(self.cache_dir, f"{address.lower()}.json")
        try:
            with open(cache_path, "w") as f:
                json.dump(abi, f)
        except Exception as e:
            log.warning(f"Failed to cache ABI for {address}: {e}")
