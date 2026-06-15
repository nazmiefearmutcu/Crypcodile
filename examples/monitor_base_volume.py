"""Example: Monitor Base Volume (WETH/USDC).

Connects to the Base network, pulls the last 100 trades for WETH/USDC on Uniswap V3,
and prints a neat data table of the trades.
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from web3 import Web3

# Setup python path to import crypcodile
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade

def main():
    rpc_url = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
    print(f"Connecting to Base network RPC: {rpc_url}...")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print("Failed to connect to Base network.")
        return

    # Uniswap V3 Factory and token addresses
    factory_address = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
    usdc = w3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913")
    weth = w3.to_checksum_address("0x4200000000000000000000000000000000000006")
    
    factory_abi = [{
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"type": "address"}],
        "stateMutability": "view", "type": "function"
    }]
    
    factory = w3.eth.contract(address=w3.to_checksum_address(factory_address), abi=factory_abi)
    # 0.05% fee pool (500)
    pool_address = factory.functions.getPool(usdc, weth, 500).call()
    print(f"Resolved WETH/USDC pool: {pool_address}")

    latest_block = w3.eth.block_number
    # V3 swap topic: Swap(address,address,int256,int256,uint160,uint128,int24)
    swap_topic = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

    print("Fetching last 100 trades from Uniswap V3 (searching backward)...")
    logs = []
    chunk_size = 500
    current_block = latest_block
    
    while len(logs) < 100 and current_block > latest_block - 10000:
        from_block = max(0, current_block - chunk_size)
        try:
            chunk = w3.eth.get_logs({
                "address": pool_address,
                "topics": [swap_topic],
                "fromBlock": from_block,
                "toBlock": current_block
            })
            # logs are ordered oldest to newest in get_logs, so reverse the chunk to get newest first
            logs.extend(reversed(chunk))
            current_block = from_block - 1
        except Exception as e:
            print(f"Error fetching logs: {e}")
            break

    trades = []
    # Process only the first 100 logs
    for lg in logs[:100]:
        data = lg["data"]
        # Uniswap V3 Swap event fields:
        # data has:
        # amount0 (int256) - USDC
        # amount1 (int256) - WETH
        # sqrtPriceX96 (uint160)
        # liquidity (uint128)
        # tick (int24)
        amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
        amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
        
        # USDC has 6 decimals, WETH has 18
        # In the Uniswap V3 USDC/WETH pool contract, WETH is token0 and USDC is token1.
        # WETH (base) is amount0, USDC (quote) is amount1.
        # If amount0 < 0, WETH is output (bought by user).
        is_buy = amount0 < 0
        
        abs_base = abs(amount0) / 10**18
        abs_quote = abs(amount1) / 10**6
        price = abs_quote / abs_base if abs_base > 0 else 0.0
        
        # Get block timestamp (cached or fetched)
        try:
            block = w3.eth.get_block(lg["blockNumber"])
            ts = block["timestamp"]
        except Exception:
            ts = int(datetime.utcnow().timestamp())
            
        trade = Trade(
            exchange="base_onchain",
            symbol="base_onchain:WETH-USDC",
            symbol_raw="WETH-USDC",
            exchange_ts=ts * 1_000_000_000,
            local_ts=int(datetime.utcnow().timestamp() * 1_000_000_000),
            id=f"{lg['transactionHash'].hex()}-{lg['logIndex']}",
            price=price,
            amount=abs_base,
            side=Side.BUY if is_buy else Side.SELL
        )
        trades.append(trade)

    print("\n" + "="*70)
    print(f"| {'Timestamp':<19} | {'Side':<4} | {'Price (USDC)':<14} | {'Amount (WETH)':<14} |")
    print("="*70)
    for t in trades:
        dt_str = datetime.fromtimestamp(t.exchange_ts / 1_000_000_000).strftime('%Y-%m-%d %H:%M:%S')
        side_str = "BUY" if t.side == Side.BUY else "SELL"
        print(f"| {dt_str:<19} | {side_str:<4} | {t.price:<14.2f} | {t.amount:<14.4f} |")
    print("="*70)
    print(f"Total trades listed: {len(trades)}")

if __name__ == "__main__":
    main()
