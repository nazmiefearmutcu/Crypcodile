# 🔵 Base Developer Quickstart: Track Base Whales in 10 Lines of Python

This quickstart guides you through using **Crypcodile** to monitor on-chain swap events on the Base L2 network and filter for large "whale" transactions (trades greater than $50,000 USD).

## Prerequisites

Ensure you have installed Crypcodile and synced the data pipeline:
```bash
pip install 'crypcodile[onchain]'
# or with uv
uv pip install 'crypcodile[onchain]'
```

The Base connector reads L2 RPC logs via `web3`, which ships in the
`onchain` extra (a bare `pip install crypcodile` gives you the core
CEX/lake engine only).

## Python Implementation

Using Crypcodile's standardized query interface powered by DuckDB, you can load on-chain transaction data directly into a Pandas DataFrame and filter for whale trades:

```python
from crypcodile.client.client import CrypcodileClient

# 1. Initialize Crypcodile client pointing to your Parquet data lake
client = CrypcodileClient(data_dir="data")

# 2. Query normalized trades from Base DEXs filtering for volume > $50k USD
whale_query = """
    SELECT symbol, price, amount, side, (amount * price) AS usd_value, local_ts 
    FROM trade 
    WHERE exchange = 'base_onchain' AND (amount * price) > 50000 
    ORDER BY local_ts DESC LIMIT 10
"""
df = client.query(whale_query).to_pandas()

# 3. Display the detected Base whale swaps
print(df)
```

## How It Works Under the Hood

1. **RPC Extraction**: The Crypcodile ingestion engine queries the Base mainnet RPC nodes, listening for swap events on Uniswap V3 and Aerodrome Finance pools.
2. **Schema Normalization**: Raw Ethereum logs are decoded and normalized into standard internal `Trade` schemas.
3. **Parquet Storage**: Normalized trades are written to partitioned `.parquet` files under the `data` directory.
4. **DuckDB Querying**: The `CrypcodileClient` spins up an in-memory DuckDB instance to query the Parquet files directly, combining the execution speed of Polars/Pandas with the expressiveness of SQL.

---

For complete production-grade examples, check the [examples/](file:///Users/nazmi/Crypcodile/examples) directory:
- [monitor_base_volume.py](file:///Users/nazmi/Crypcodile/examples/monitor_base_volume.py): Real-time on-chain logs parser.
- [base_dashboard.py](file:///Users/nazmi/Crypcodile/examples/base_dashboard.py): Interactive Streamlit visualizer for Base DEX volume.
- [farcaster_frame.py](file:///Users/nazmi/Crypcodile/examples/farcaster_frame.py): Deployable Farcaster Frame endpoint.
