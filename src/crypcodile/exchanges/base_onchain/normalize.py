from __future__ import annotations

import logging
from collections.abc import Iterable
from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, BookTicker, Trade, Record

log = logging.getLogger(__name__)

EXCHANGE = "base_onchain"

def normalize_onchain_update(msg: dict, local_ts: int) -> Iterable[Record]:
    """Normalize on-chain pool updates.
    
    The input msg has the structure:
    {
        "type": "onchain_update",
        "block": int,
        "pool": str, (e.g. "cbBTC-USDC")
        "pool_type": "uniswap_v3" | "aerodrome_v2",
        "timestamp": int,
        "state": { ... },
        "swaps": [ ... ]
    }
    """
    pool_name = msg["pool"]
    pool_type = msg["pool_type"]
    state = msg["state"]
    swaps = msg.get("swaps", [])
    block = msg["block"]
    
    # 1. Parse swaps into Trade records
    for sw in swaps:
        yield Trade(
            exchange=EXCHANGE,
            symbol=f"{EXCHANGE}:{pool_name}",
            symbol_raw=pool_name,
            exchange_ts=sw["timestamp"] * 1_000_000_000, # convert sec to ns
            local_ts=local_ts,
            id=f"{sw['tx_hash']}-{sw['log_index']}",
            price=sw["price"],
            amount=sw["amount"],
            side=Side.BUY if sw["is_buy"] else Side.SELL,
        )
        
    # 2. Parse state into BookSnapshot and BookTicker
    price = state["price"]
    if price <= 0:
        return
        
    # Construct a synthetic orderbook around the current pool price
    # using virtual/actual reserves to show depth.
    # Spread of 5 basis points (0.05%)
    bid_px = price * 0.9995
    ask_px = price * 1.0005
    
    # Virtual reserves or real reserves for size
    reserve_token0 = state.get("reserve0", 0.0)
    reserve_token1 = state.get("reserve1", 0.0)
    
    # Bid size is the amount of token0 one can buy with the token1 reserve
    bid_sz = reserve_token1 / price if price > 0 else 0.0
    # Ask size is the amount of token0 available in reserves
    ask_sz = reserve_token0
    
    # Enforce minimum sizes to keep it realistic
    bid_sz = max(bid_sz, 0.0001)
    ask_sz = max(ask_sz, 0.0001)
    
    yield BookTicker(
        exchange=EXCHANGE,
        symbol=f"{EXCHANGE}:{pool_name}",
        symbol_raw=pool_name,
        exchange_ts=msg["timestamp"] * 1_000_000_000,
        local_ts=local_ts,
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
        update_id=block
    )
    
    yield BookSnapshot(
        exchange=EXCHANGE,
        symbol=f"{EXCHANGE}:{pool_name}",
        symbol_raw=pool_name,
        exchange_ts=msg["timestamp"] * 1_000_000_000,
        local_ts=local_ts,
        bids=[(bid_px, bid_sz)],
        asks=[(ask_px, ask_sz)],
        depth=1,
        sequence_id=block,
        is_snapshot=True
    )
