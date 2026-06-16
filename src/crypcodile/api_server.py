from __future__ import annotations

import json
import logging
import os
import sys
import uuid
import random
import time
import asyncio
import fcntl
from typing import Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from web3 import Web3

from crypcodile.mcp_server import get_onchain_price, serve_stdio, AsyncWeb3, AsyncHTTPProvider

log = logging.getLogger(__name__)

VERIFYING_TXS: set[str] = set()

def _get_rpc_urls() -> list[str]:
    urls_str = os.getenv("BASE_RPC_URLS", "")
    if urls_str:
        return [u.strip() for u in urls_str.split(",") if u.strip()]
    fallback = os.getenv("BASE_RPC_URL", "https://base-rpc.publicnode.com")
    return [fallback]

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.rpc_urls = _get_rpc_urls()
    app.state.current_rpc_index = 0
    url = app.state.rpc_urls[0]
    app.state.w3 = AsyncWeb3(AsyncHTTPProvider(url))
    yield
    # Shutdown
    disconnect_fn = getattr(app.state.w3.provider, "disconnect", None)
    if disconnect_fn is not None:
        import inspect
        try:
            res = disconnect_fn()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass

from crypcodile import __version__

app = FastAPI(
    title="Crypcodile x402 Gated Market Data API",
    description=(
        "A demo API gating Base mainnet market data behind the x402 AI Agent "
        "payment protocol."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None
)

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root to /docs."""
    return RedirectResponse(url="/docs")

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    from fastapi.openapi.docs import get_swagger_ui_html
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )
    
    # Custom premium dark green theme CSS for Swagger UI (no filter: invert hack)
    custom_css = """
    <style>
        html, body {
            background-color: #0b0f19 !important;
            color: #f1f5f9 !important;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
            margin: 0;
            padding: 0;
        }
        .swagger-ui {
            color: #e2e8f0 !important;
            background-color: #0b0f19 !important;
        }
        .swagger-ui .topbar {
            background-color: #0f172a !important;
            border-bottom: 2px solid #059669 !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
        }
        .swagger-ui .topbar a {
            color: #f1f5f9 !important;
            font-weight: bold;
        }
        .swagger-ui .topbar .download-url-wrapper input[type=text] {
            border: 1px solid #334155 !important;
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border-radius: 4px;
        }
        .swagger-ui .topbar .download-url-wrapper .download-url-button {
            background: #059669 !important;
            color: #fff !important;
            border-radius: 4px;
        }
        .swagger-ui .info {
            margin: 30px 0 !important;
        }
        .swagger-ui .info .title {
            color: #059669 !important;
            font-size: 2.2rem !important;
            font-weight: 800 !important;
            letter-spacing: -0.025em;
        }
        .swagger-ui .info p, .swagger-ui .info li, .swagger-ui .info td, .swagger-ui label {
            color: #94a3b8 !important;
            font-size: 0.95rem !important;
            line-height: 1.6;
        }
        .swagger-ui .info a {
            color: #10b981 !important;
            text-decoration: none;
        }
        .swagger-ui .info a:hover {
            color: #34d399 !important;
            text-decoration: underline;
        }
        .swagger-ui .opblock-tag {
            color: #f1f5f9 !important;
            border-bottom: 1px solid #1e293b !important;
            font-size: 1.4rem !important;
            font-weight: 700 !important;
        }
        .swagger-ui .scheme-container {
            background-color: #0f172a !important;
            box-shadow: none !important;
            border: 1px solid #1e293b !important;
            border-radius: 8px !important;
            padding: 20px !important;
            margin-bottom: 25px !important;
        }
        .swagger-ui select {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            color: #f1f5f9 !important;
            border-radius: 6px !important;
            padding: 6px 10px !important;
        }
        .swagger-ui .opblock {
            background-color: #0f172a !important;
            border: 1px solid #1e293b !important;
            border-radius: 8px !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1) !important;
            overflow: hidden;
            margin-bottom: 15px !important;
        }
        .swagger-ui .opblock .opblock-summary {
            padding: 12px 20px !important;
            border-bottom: 1px solid #1e293b !important;
        }
        .swagger-ui .opblock.opblock-get {
            border-color: rgba(16, 185, 129, 0.4) !important;
            background-color: rgba(16, 185, 129, 0.03) !important;
        }
        .swagger-ui .opblock.opblock-get .opblock-summary-method {
            background-color: #059669 !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            border-radius: 4px;
            padding: 6px 12px !important;
        }
        .swagger-ui .opblock.opblock-get .opblock-summary {
            border-color: rgba(16, 185, 129, 0.2) !important;
        }
        .swagger-ui .opblock.opblock-post {
            border-color: rgba(59, 130, 246, 0.4) !important;
            background-color: rgba(59, 130, 246, 0.03) !important;
        }
        .swagger-ui .opblock.opblock-post .opblock-summary-method {
            background-color: #2563eb !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            border-radius: 4px;
            padding: 6px 12px !important;
        }
        .swagger-ui .opblock.opblock-post .opblock-summary {
            border-color: rgba(59, 130, 246, 0.2) !important;
        }
        .swagger-ui .opblock .opblock-summary-path,
        .swagger-ui .opblock .opblock-summary-path a {
            color: #f1f5f9 !important;
            font-weight: 600 !important;
            font-size: 0.95rem !important;
        }
        .swagger-ui .opblock .opblock-summary-description {
            color: #94a3b8 !important;
        }
        .swagger-ui .btn {
            border-color: #334155 !important;
            color: #f1f5f9 !important;
            background-color: #1e293b !important;
            border-radius: 6px !important;
            font-weight: 600 !important;
            transition: all 0.2s ease;
        }
        .swagger-ui .btn:hover {
            background-color: #334155 !important;
            color: #ffffff !important;
        }
        .swagger-ui .btn.execute {
            background-color: #059669 !important;
            border-color: #059669 !important;
            color: #ffffff !important;
        }
        .swagger-ui .btn.execute:hover {
            background-color: #10b981 !important;
            border-color: #10b981 !important;
        }
        .swagger-ui table thead tr td,
        .swagger-ui table thead tr th {
            color: #f1f5f9 !important;
            font-weight: 600 !important;
            border-bottom: 2px solid #1e293b !important;
        }
        .swagger-ui .parameters-col_name {
            color: #34d399 !important;
            font-weight: 600 !important;
        }
        .swagger-ui .parameter__name.required {
            color: #f87171 !important;
        }
        .swagger-ui .parameter__type {
            color: #60a5fa !important;
        }
        .swagger-ui .parameter__in {
            color: #94a3b8 !important;
        }
        .swagger-ui input[type=text] {
            background-color: #0b0f19 !important;
            border: 1px solid #334155 !important;
            color: #f8fafc !important;
            border-radius: 6px !important;
            padding: 8px 12px !important;
        }
        .swagger-ui input[type=text]:focus {
            border-color: #10b981 !important;
        }
        .swagger-ui .response-col_status {
            color: #34d399 !important;
            font-weight: 700 !important;
        }
        .swagger-ui .response-col_description {
            color: #e2e8f0 !important;
        }
        .swagger-ui .opblock-body pre.microlight {
            background-color: #080c14 !important;
            border: 1px solid #1e293b !important;
            color: #34d399 !important;
            border-radius: 8px !important;
            padding: 14px !important;
            font-family: monospace !important;
            font-size: 0.85rem !important;
        }
        .swagger-ui .model-box {
            background-color: #0f172a !important;
            border: 1px solid #1e293b !important;
            border-radius: 6px !important;
            padding: 12px !important;
        }
        .swagger-ui .model {
            color: #e2e8f0 !important;
        }
        .swagger-ui .model-title {
            color: #f8fafc !important;
        }
        .swagger-ui .prop-name {
            color: #94a3b8 !important;
        }
        .swagger-ui .prop-type {
            color: #60a5fa !important;
        }
        .swagger-ui section.models {
            border: 1px solid #1e293b !important;
            border-radius: 8px !important;
            background-color: #0f172a !important;
            margin-top: 35px !important;
        }
        .swagger-ui section.models h4 {
            color: #059669 !important;
            border-bottom: 1px solid #1e293b !important;
            padding: 15px 20px !important;
            font-size: 1.25rem !important;
            font-weight: 700 !important;
        }
        .swagger-ui section.models .model-container {
            background-color: #0b0f19 !important;
            border: 1px solid #1e293b !important;
            margin: 15px 20px !important;
            border-radius: 6px !important;
        }
        .swagger-ui table.headers td {
            color: #94a3b8 !important;
        }
        .swagger-ui .tabli.active button {
            color: #10b981 !important;
        }
    </style>
    """
    html_content = response.body.decode("utf-8")
    html_content = html_content.replace("</head>", f"{custom_css}</head>")
    return Response(content=html_content, media_type="text/html")


def get_w3() -> AsyncWeb3:
    if hasattr(app.state, "w3") and app.state.w3 is not None:
        return app.state.w3
    # Fallback/lazy init (for tests that call handlers directly)
    urls = _get_rpc_urls()
    app.state.rpc_urls = urls
    app.state.current_rpc_index = 0
    app.state.w3 = AsyncWeb3(AsyncHTTPProvider(urls[0]))
    return app.state.w3

async def switch_rpc_failover():
    if not hasattr(app.state, "rpc_urls") or not app.state.rpc_urls:
        app.state.rpc_urls = _get_rpc_urls()
    if not hasattr(app.state, "current_rpc_index"):
        app.state.current_rpc_index = 0
        
    num_urls = len(app.state.rpc_urls)
    if num_urls <= 1:
        return
        
    w3 = get_w3()
    disconnect_fn = getattr(w3.provider, "disconnect", None)
    if disconnect_fn is not None:
        import inspect
        try:
            res = disconnect_fn()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass
            
    app.state.current_rpc_index = (app.state.current_rpc_index + 1) % num_urls
    new_url = app.state.rpc_urls[app.state.current_rpc_index]
    log.warning(f"RPC Failover: switching to next RPC URL: {new_url}")
    w3.provider = AsyncHTTPProvider(new_url)

def is_connection_or_rate_limit_error(e: Exception) -> bool:
    err_str = str(e).lower()
    if "429" in err_str or "rate limit" in err_str:
        return True
    connection_keywords = [
        "connection", "timeout", "connect", "refused", "disconnected",
        "502", "503", "504", "http status", "http error", "status code 429"
    ]
    if any(kw in err_str for kw in connection_keywords):
        return True
    return False

async def get_transaction_receipt_with_failover(w3: AsyncWeb3, tx_hash: str) -> Any:
    attempt = 0
    max_attempts = 5
    base_delay = 1.0
    max_delay = 10.0
    
    while True:
        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                return receipt
            raise ValueError("Receipt is None")
        except Exception as e:
            if is_connection_or_rate_limit_error(e):
                log.warning(f"Connection/rate limit error when getting receipt: {e}. Switching RPC...")
                await switch_rpc_failover()
                w3 = get_w3()
                attempt += 1
                if attempt >= max_attempts:
                    raise HTTPException(
                        status_code=500,
                        detail=f"RPC connection errors exceeded limit: {e}"
                    )
                await asyncio.sleep(0.5)
                continue
                
            from web3.exceptions import TransactionNotFound
            is_not_found = isinstance(e, TransactionNotFound) or "not found" in str(e).lower()
            
            if is_not_found:
                attempt += 1
                if attempt >= max_attempts:
                    raise HTTPException(
                        status_code=400,
                        detail="Transaction receipt not found on-chain."
                    ) from e
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay = delay * random.uniform(0.5, 1.0)
                log.warning(f"Transaction receipt not found yet. Retrying in {delay:.4f}s...")
                await asyncio.sleep(delay)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transaction hash or format: {e}"
                )

async def get_transaction_with_failover(w3: AsyncWeb3, tx_hash: str) -> Any:
    attempt = 0
    max_attempts = 5
    base_delay = 1.0
    max_delay = 10.0
    
    while True:
        try:
            tx = await w3.eth.get_transaction(tx_hash)
            if tx is not None:
                return tx
            raise ValueError("Transaction details are None")
        except Exception as e:
            if is_connection_or_rate_limit_error(e):
                log.warning(f"Connection/rate limit error when getting transaction: {e}. Switching RPC...")
                await switch_rpc_failover()
                w3 = get_w3()
                attempt += 1
                if attempt >= max_attempts:
                    raise HTTPException(
                        status_code=500,
                        detail=f"RPC connection errors exceeded limit when fetching tx: {e}"
                    )
                await asyncio.sleep(0.5)
                continue
                
            from web3.exceptions import TransactionNotFound
            is_not_found = isinstance(e, TransactionNotFound) or "not found" in str(e).lower()
            
            if is_not_found:
                attempt += 1
                if attempt >= max_attempts:
                    raise HTTPException(
                        status_code=400,
                        detail="Transaction details not found on-chain."
                    ) from e
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay = delay * random.uniform(0.5, 1.0)
                log.warning(f"Transaction details not found yet. Retrying in {delay:.4f}s...")
                await asyncio.sleep(delay)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to verify transaction details: {e}"
                )

PAYMENTS_FILE = "/Users/nazmi/Crypcodile/.payments_db.json"
if "pytest" in sys.modules:
    PAYMENTS_FILE = "/Users/nazmi/Crypcodile/.payments_db_test.json"
    try:
        if os.path.exists(PAYMENTS_FILE):
            os.remove(PAYMENTS_FILE)
    except Exception:
        pass

# Persistent DB path helper
def get_payments_file() -> str:
    return os.getenv("PAYMENTS_FILE", PAYMENTS_FILE)

db_lock = asyncio.Lock()

def _load_db_file() -> dict[str, dict[str, Any]]:
    payments_file = get_payments_file()
    if not os.path.exists(payments_file):
        return {}
    try:
        with open(payments_file, "r") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except OSError:
                pass
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception as e:
        log.error(f"Error loading PAYMENTS_DB file: {e}")
        return {}

def _save_db_file(data: dict[str, dict[str, Any]]) -> None:
    payments_file = get_payments_file()
    temp_file = payments_file + f".{uuid.uuid4().hex}.tmp"
    try:
        os.makedirs(os.path.dirname(payments_file), exist_ok=True)
        with open(temp_file, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, payments_file)
    except Exception as e:
        log.error(f"Error saving PAYMENTS_DB file: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass

class PersistentDict(dict[str, Any]):
    def __init__(self, default_data: dict[str, Any] | None = None) -> None:
        if default_data is None:
            default_data = {}
        super().__init__(default_data)
        self._default = default_data
        self._last_payments_file = ""

    def _sync(self) -> None:
        current_file = get_payments_file()
        if current_file != self._last_payments_file:
            dict.clear(self)
            dict.update(self, self._default)
            try:
                if os.path.exists(current_file):
                    with open(current_file, "r") as f:
                        content = f.read().strip()
                        if content:
                            dict.update(self, json.loads(content))
            except Exception:
                pass
            self._last_payments_file = current_file

    def clear(self) -> None:
        self._sync()
        dict.clear(self)
        _save_db_file({})

    def __contains__(self, key: object) -> bool:
        self._sync()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        self._sync()
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._sync()
        super().__setitem__(key, value)
        _save_db_file(dict(self))

    def __delitem__(self, key: str) -> None:
        self._sync()
        super().__delitem__(key)
        _save_db_file(dict(self))

    def get(self, key: str, default: Any = None) -> Any:
        self._sync()
        return super().get(key, default)

    def keys(self) -> Any:
        self._sync()
        return super().keys()

    def values(self) -> Any:
        return self._load().values() if hasattr(self, '_load') else super().values()

    def items(self) -> Any:
        self._sync()
        return super().items()

    def __len__(self) -> int:
        self._sync()
        return super().__len__()

    def __iter__(self) -> Any:
        self._sync()
        return super().__iter__()

    def __repr__(self) -> str:
        self._sync()
        return super().__repr__()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._sync()
        super().update(*args, **kwargs)
        _save_db_file(dict(self))

# Initial load for import-time queries
PAYMENTS_DB: dict[str, dict[str, Any]] = PersistentDict()

# Demo recipient wallet address (e.g. Nazmi's developer wallet)
RECIPIENT_WALLET = os.getenv("RECIPIENT_WALLET", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
PRICE_USDC = "0.001" # $0.001 USDC per request

class PaymentSignature(BaseModel):
    payment_id: str
    tx_hash: str
    signature: str

async def load_payments_db() -> dict[str, dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _load_db_file)

async def save_payments_db(db: dict[str, dict[str, Any]]) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save_db_file, db)

@app.get("/api/v1/market-data")
async def get_market_data(
    symbol: str,
    response: Response,
    payment_signature: str | None = Header(None, alias="Payment-Signature")
) -> dict[str, Any]:
    """Get real-time Base DEX market data. Gated behind x402 micropayments."""
    # 1. Check if the payment signature is provided
    if not payment_signature:
        # Generate a unique payment ID for this request
        payment_id = str(uuid.uuid4())
        
        async with db_lock:
            db = await load_payments_db()
            db[payment_id] = {
                "status": "pending",
                "price": PRICE_USDC,
                "currency": "USDC",
                "recipient": RECIPIENT_WALLET,
                "symbol": symbol
            }
            await save_payments_db(db)
            
            dict.clear(PAYMENTS_DB)
            PAYMENTS_DB.update(db)
        
        payment_required_payload = {
            "price": PRICE_USDC,
            "currency": "USDC",
            "recipient": RECIPIENT_WALLET,
            "network": "base-mainnet",
            "payment_id": payment_id,
            "message": "Payment required to access market data."
        }
        
        # Set x402 headers
        response.status_code = 402
        response.headers["Payment-Required"] = Web3.to_json(payment_required_payload)
        return {
            "status": "payment_required",
            "message": (
                "Please pay 0.001 USDC on Base mainnet. Resubmit the request "
                "with the 'Payment-Signature' header."
            ),
            "payment_id": payment_id,
            "payment_required": payment_required_payload
        }
        
    # 2. Parse and verify the payment signature
    try:
        try:
            sig_data = (
                Web3.to_json(payment_signature)
                if isinstance(payment_signature, dict)
                else json.loads(payment_signature)
            )
        except Exception as e:
            log.error(f"Malformed signature JSON: {e}")
            raise HTTPException(
                status_code=400,
                detail="Failed verifying payment signature: Malformed signature JSON string."
            )
            
        pid = sig_data.get("payment_id")
        tx_hash = sig_data.get("tx_hash")
        signature = sig_data.get("signature")
        
        if not pid or not tx_hash or not signature:
            raise HTTPException(
                status_code=400,
                detail="Missing payment_id, tx_hash, or signature."
            )
            
        from eth_account import Account
        from eth_account.messages import encode_defunct
        
        # Strictly enforce signature format and recover signer
        if not signature or not isinstance(signature, str):
            raise HTTPException(status_code=400, detail="Missing or invalid signature format.")
            
        try:
            clean_sig = signature[2:] if signature.startswith("0x") else signature
            bytes.fromhex(clean_sig)
            if len(clean_sig) not in (128, 130):
                raise ValueError("Invalid signature length.")
        except Exception as e:
            log.error(f"Signature format error: {e}")
            raise HTTPException(status_code=400, detail="Malformed signature: Invalid signature format or length.")
            
        try:
            message = encode_defunct(text=pid)
            signer_address = Account.recover_message(message, signature=signature)
        except Exception as e:
            log.error(f"Signature recovery failed: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid cryptographic signature: Recovery failed."
            )
            
        if not signer_address:
            raise HTTPException(
                status_code=400,
                detail="Cryptographic recovery failed."
            )
            
        async with db_lock:
            db = await load_payments_db()
            if pid not in db:
                raise HTTPException(status_code=400, detail="Invalid or expired payment ID.")
                
            # Verify that tx_hash is not already used in any paid payment record in DB
            for db_pid, db_record in db.items():
                if db_pid != pid and db_record.get("status") == "paid" and db_record.get("tx_hash") == tx_hash:
                    raise HTTPException(status_code=400, detail="Transaction hash already processed.")
                    
            record = db[pid]
            is_paid = record.get("status") == "paid"
            
            if is_paid:
                stored_sender = record.get("sender")
                if stored_sender and signer_address.lower() != stored_sender.lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Payment signature does not match transaction sender."
                    )
            else:
                if tx_hash in VERIFYING_TXS:
                    raise HTTPException(status_code=400, detail="Transaction hash is currently being verified.")
                VERIFYING_TXS.add(tx_hash)
                
        if not is_paid:
            tx_from = None
            try:
                w3 = get_w3()
                
                # Check Chain ID is Base mainnet (8453)
                try:
                    chain_id = await w3.eth.chain_id
                except Exception as e:
                    if is_connection_or_rate_limit_error(e):
                        await switch_rpc_failover()
                        w3 = get_w3()
                        chain_id = await w3.eth.chain_id
                    else:
                        raise HTTPException(status_code=400, detail="Failed to verify chain ID: RPC node is unresponsive.")
                
                if chain_id != 8453:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid Chain ID: expected 8453 (Base mainnet), got {chain_id}."
                    )
                    
                # Poll receipt first inside retry/backoff
                receipt = await get_transaction_receipt_with_failover(w3, tx_hash)
                
                # Fetch transaction details
                tx_details = await get_transaction_with_failover(w3, tx_hash)
                
                # Verify sender
                tx_from = tx_details.get("from")
                if not tx_from or tx_from.lower() != signer_address.lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Payment signature does not match transaction sender."
                    )
                    
                # Verify transaction chainId if present
                tx_chain_id = tx_details.get("chainId")
                if tx_chain_id is not None:
                    if isinstance(tx_chain_id, str):
                        try:
                            tx_chain_id_int = int(tx_chain_id, 16) if tx_chain_id.startswith("0x") else int(tx_chain_id)
                        except ValueError:
                            tx_chain_id_int = None
                    else:
                        tx_chain_id_int = int(tx_chain_id)
                    
                    if tx_chain_id_int is not None and tx_chain_id_int != 8453:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Transaction Chain ID mismatch: expected 8453, got {tx_chain_id_int}."
                        )
                        
                # Verify block timestamp is recent (within 1 hour)
                block_number = receipt.get("blockNumber")
                if block_number is not None:
                    block = None
                    try:
                        block = await w3.eth.get_block(block_number)
                    except Exception as e:
                        if is_connection_or_rate_limit_error(e):
                            await switch_rpc_failover()
                            w3 = get_w3()
                            block = await w3.eth.get_block(block_number)
                        else:
                            raise
                            
                    block_timestamp = block.get("timestamp") if block else None
                    if block_timestamp is not None:
                        latest_block = None
                        try:
                            latest_block = await w3.eth.get_block("latest")
                        except Exception as e:
                            if is_connection_or_rate_limit_error(e):
                                await switch_rpc_failover()
                                w3 = get_w3()
                                latest_block = await w3.eth.get_block("latest")
                                
                        latest_timestamp = latest_block.get("timestamp") if latest_block else None
                        if latest_timestamp is None:
                            latest_timestamp = int(time.time())
                            
                        if abs(latest_timestamp - block_timestamp) > 3600:
                            raise HTTPException(
                                status_code=400,
                                detail="Transaction is too old (mined more than 1 hour ago)."
                            )
                            
                status = receipt.get("status")
                if status not in (1, "0x1", 0x1, "1"):
                    raise HTTPException(
                        status_code=400,
                        detail="Transaction status is unsuccessful."
                    )
                    
                # Safe Log Parsing
                official_usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913".lower()
                transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                
                def clean_hex(val: Any) -> str:
                    if isinstance(val, bytes):
                        return val.hex().lower()
                    s = str(val).lower()
                    if s.startswith("0x"):
                        return s[2:]
                    return s
                    
                valid_transfer = False
                for log_entry in receipt.get("logs", []):
                    try:
                        log_addr = log_entry.get("address", "")
                        if not log_addr:
                            continue
                        if clean_hex(log_addr) != clean_hex(official_usdc_contract):
                            continue
                            
                        topics = log_entry.get("topics", [])
                        if len(topics) < 3:
                            continue
                            
                        t0 = topics[0].hex().lower() if isinstance(topics[0], bytes) else str(topics[0]).lower()
                        if not t0.startswith("0x"):
                            t0 = "0x" + t0
                        if t0 != transfer_topic:
                            continue
                            
                        t2 = clean_hex(topics[2])
                        if len(t2) < 40:
                            continue
                        recipient = "0x" + t2[-40:]
                        if clean_hex(recipient) != clean_hex(RECIPIENT_WALLET):
                            continue
                            
                        data_val = log_entry.get("data")
                        if not data_val:
                            continue
                        amount = int(clean_hex(data_val), 16)
                        if amount != 1000:
                            continue
                            
                        valid_transfer = True
                        break
                    except Exception as e:
                        log.warning(f"Error parsing log entry: {e}")
                        continue
                        
                if not valid_transfer:
                    raise HTTPException(
                        status_code=400,
                        detail="USDC payment validation failed."
                    )
                    
                # Re-acquire lock to write to DB
                async with db_lock:
                    db = await load_payments_db()
                    record = db.get(pid)
                    if record:
                        record["status"] = "paid"
                        record["tx_hash"] = tx_hash
                        record["sender"] = tx_from
                        record["signature"] = signature
                        await save_payments_db(db)
                        
                        dict.clear(PAYMENTS_DB)
                        PAYMENTS_DB.update(db)
            finally:
                async with db_lock:
                    VERIFYING_TXS.discard(tx_hash)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Payment verification failed: {e}")
        raise HTTPException(
            status_code=400,
            detail="Failed verifying payment signature: Invalid payment or signature format."
        ) from e

    # 3. Retrieve and return live Base DEX pool data
    data = await get_onchain_price(symbol)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
        
    # Set x402 success headers
    response.headers["Payment-Response"] = Web3.to_json({
        "status": "success",
        "payment_id": pid,
        "tx_hash": tx_hash
    })
    
    return {
        "status": "success",
        "payment_id": pid,
        "tx_hash": tx_hash,
        "data": data
    }

@app.post("/api/v1/simulate-payment")
async def simulate_payment(payload: PaymentSignature) -> dict[str, Any]:
    """Helper endpoint to mark a payment_id as paid and generate a mock signature.
    
    This allows testing clients to easily simulate the on-chain transfer.
    """
    pid = payload.payment_id
    tx_hash = payload.tx_hash
    signature = payload.signature
    
    from eth_account import Account
    from eth_account.messages import encode_defunct
    
    # Strictly enforce signature format and recover signer
    if not signature or not isinstance(signature, str):
        raise HTTPException(status_code=400, detail="Missing or invalid signature format.")
        
    try:
        clean_sig = signature[2:] if signature.startswith("0x") else signature
        bytes.fromhex(clean_sig)
        if len(clean_sig) not in (128, 130):
            raise ValueError("Invalid signature length.")
    except Exception as e:
        log.error(f"Simulation malformed signature: {e}")
        raise HTTPException(status_code=400, detail="Malformed signature: Invalid signature format or length.")
        
    try:
        message = encode_defunct(text=pid)
        signer_address = Account.recover_message(message, signature=signature)
    except Exception as e:
        log.error(f"Simulation signature recovery failed: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid cryptographic signature: Recovery failed."
        )
        
    if not signer_address:
        raise HTTPException(
            status_code=400,
            detail="Cryptographic recovery failed."
        )
        
    async with db_lock:
        db = await load_payments_db()
        if pid not in db:
            raise HTTPException(status_code=404, detail="Payment ID not found.")
            
        for db_pid, db_record in db.items():
            if db_pid != pid and db_record.get("status") == "paid" and db_record.get("tx_hash") == tx_hash:
                raise HTTPException(status_code=400, detail="Transaction hash already processed.")
                
        if tx_hash in VERIFYING_TXS:
            raise HTTPException(status_code=400, detail="Transaction hash is currently being verified.")
            
        db[pid]["status"] = "paid"
        db[pid]["tx_hash"] = tx_hash
        db[pid]["sender"] = signer_address
        db[pid]["signature"] = signature
        
        await save_payments_db(db)
        
        dict.clear(PAYMENTS_DB)
        PAYMENTS_DB.update(db)
        
        return {
            "status": "success",
            "message": f"Payment {pid} successfully simulated as paid on Base mainnet.",
            "payment_record": db[pid]
        }
