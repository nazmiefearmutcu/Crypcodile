# Handoff Report - Milestone 4 Explorer Findings

## 1. Observation
- In `src/crypcodile/api_server.py`, cryptographic signature validation is handled as follows:
  ```python
  187:                     is_valid_format = False
  188:                     if isinstance(signature, str):
  189:                         clean_sig = signature[2:] if signature.startswith("0x") else signature
  190:                         if len(clean_sig) in (128, 130):
  191:                             try:
  192:                                 bytes.fromhex(clean_sig)
  193:                                 is_valid_format = True
  194:                             except ValueError:
  195:                                 pass
  196:                                 
  197:                     if not is_valid_format:
  198:                         signer_address = None
  199:                     else:
  200:                         try:
  201:                             message = encode_defunct(text=pid)
  202:                             signer_address = Account.recover_message(message, signature=signature)
  203:                         except Exception as e:
  ...
  211:                     if signer_address:
  212:                         try:
  213:                             tx_details = await w3.eth.get_transaction(tx_hash)
  214:                             tx_from = tx_details.get("from")
  215:                             if tx_from and tx_from.lower() != signer_address.lower():
  ```
- In `src/crypcodile/api_server.py`, the database lock is acquired globally:
  ```python
  162:         async with db_lock:
  163:             db = await load_payments_db()
  ```
- And the lock is held across all Web3 queries:
  ```python
  213:                             tx_details = await w3.eth.get_transaction(tx_hash)
  ...
  239:                             receipt = await w3.eth.get_transaction_receipt(tx_hash)
  ...
  278:                         block = await w3.eth.get_block(block_number)
  ```
- In `src/crypcodile/api_server.py`, a new `AsyncWeb3` instance is instantiated inside the handler:
  ```python
  181:                 w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
  ```
- Existing tests in `tests/exchanges/base_onchain/test_servers.py` bypass signature checks by pre-marking payment status as `"paid"` via `simulate_payment`. Other tests mock the receipt but provide short signatures (like `"0xsig"`, `"mock_sig"`) that trigger the length formatting mismatch.

## 2. Logic Chain
- **Observation 1 & 5**: The length check `if len(clean_sig) in (128, 130)` returns `False` if `signature` is not exactly a valid 64 or 65 byte hex string.
- **Observation 1**: When the format check fails, `signer_address` is set to `None`.
- **Observation 1**: Because `signer_address` is `None`, the `if signer_address:` conditional block (which contains `w3.eth.get_transaction(tx_hash)` and validates `tx_from == signer_address`) is skipped completely.
- **Conclusion A**: Any client can bypass the sender check by sending an invalid signature string (e.g. `"0xinvalid"`), allowing them to reuse a transaction hash signed by someone else (double-spending/replay vector).
- **Observation 2 & 3**: `async with db_lock:` is held globally across multiple async calls including `get_transaction`, `get_transaction_receipt` retry loop (up to 5 retries with backoff), and `get_block`.
- **Conclusion B**: Under concurrent API load, any single slow payment verification blocks all other clients from retrieving market data, creating a massive denial of service / lockup vulnerability.
- **Observation 4**: Creating a new connection pool (`AsyncHTTPProvider` and `httpx` client under the hood) per payment verification request incurs significant HTTP/TLS handshake latency and leaks sockets under stress.
- **Conclusion C**: The connection/lifecycle management is not production-ready and needs a shared singleton `AsyncWeb3` / client configuration.

## 3. Caveats
- No caveats. The gaps were traced directly via static code analysis of the source code and confirmed against the test behavior.

## 4. Conclusion
The current payment verification gateway has two high-severity gaps: a cryptographic signature verification bypass and a global concurrency bottleneck. Implementing production-grade USDC payment verification requires enforcing valid signature lengths, performing RPC calls outside the database lock, and sharing `AsyncWeb3` clients across requests.

## 5. Verification Method
- **Command**: Run tests using `uv run pytest tests/exchanges/base_onchain/test_servers.py`.
- **Validation**: Confirm the code in `src/crypcodile/api_server.py` matches the quoted segments. 
