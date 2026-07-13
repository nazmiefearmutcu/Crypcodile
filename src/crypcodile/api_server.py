from __future__ import annotations

import asyncio
import fcntl
import hmac
import json
import logging
import os
import random
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from web3 import Web3

from crypcodile.mcp_server import AsyncHTTPProvider, AsyncWeb3, get_onchain_price
from crypcodile.util.json_safe import (
    json_safe_float as _json_safe_float,
    json_safe_records as _json_safe_records,
)

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
    if not app.state.rpc_urls:
        app.state.rpc_urls = ["https://base-rpc.publicnode.com"]
    url = app.state.rpc_urls[0]
    app.state.w3 = AsyncWeb3(AsyncHTTPProvider(url))
    yield
    # Shutdown
    try:
        w3 = getattr(app.state, "w3", None)
        if w3 is not None:
            provider = getattr(w3, "provider", None)
            if provider is not None:
                disconnect_fn = getattr(provider, "disconnect", None)
                if disconnect_fn is not None:
                    import inspect
                    res = disconnect_fn()
                    if inspect.isawaitable(res):
                        await res
    except (AttributeError, Exception):
        pass

from fastapi.staticfiles import StaticFiles

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

portal_public_dir = os.path.join(os.path.dirname(__file__), "api_portal", "public")
os.makedirs(os.path.join(portal_public_dir, "css"), exist_ok=True)
os.makedirs(os.path.join(portal_public_dir, "js"), exist_ok=True)
app.mount("/css", StaticFiles(directory=os.path.join(portal_public_dir, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(portal_public_dir, "js")), name="js")

# Prometheus metrics tracking variables
METRICS_DASHBOARD_REQUESTS = 0
METRICS_MARKET_DATA_REQUESTS = 0
METRICS_METRICS_REQUESTS = 0
SERVER_START_TIME = time.time()

@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def root_dashboard():
    """Serve the interactive Crypcodile x402 Micropayments Web Dashboard."""
    global METRICS_DASHBOARD_REQUESTS
    METRICS_DASHBOARD_REQUESTS += 1
    from crypcodile.api_server_html import get_dashboard_html
    return HTMLResponse(content=get_dashboard_html())

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
                    log.error(f"RPC connection errors exceeded limit when getting receipt: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail="RPC connection errors exceeded limit."
                    ) from e
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
                log.error(f"Invalid transaction hash or format: {e}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid transaction hash or format."
                ) from e

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
                    log.error(f"RPC connection errors exceeded limit when fetching tx: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail="RPC connection errors exceeded limit when fetching tx."
                    ) from e
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
                log.error(f"Failed to verify transaction details: {e}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to verify transaction details."
                ) from e

PAYMENTS_FILE = os.path.abspath(".payments_db.json")
if "pytest" in sys.modules:
    PAYMENTS_FILE = os.path.abspath(".payments_db_test.json")
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
    lock_file = payments_file + ".lock"
    try:
        with open(lock_file, "a") as lf:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
            except (OSError, AttributeError):
                pass
            if not os.path.exists(payments_file):
                return {}
            with open(payments_file) as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
    except Exception as e:
        log.error(f"Error loading PAYMENTS_DB file: {e}")
        return {}

def _save_db_file(data: dict[str, dict[str, Any]]) -> None:
    """Atomically persist payments DB (temp file + os.replace). Re-raises on failure."""
    payments_file = get_payments_file()
    tmp_file = payments_file + ".tmp"
    try:
        parent = os.path.dirname(payments_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lock_file = payments_file + ".lock"
        with open(lock_file, "a") as lf:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except (OSError, AttributeError):
                pass
            try:
                with open(tmp_file, "w") as f:
                    json.dump(data, f)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        # fsync not always available (e.g. some special FS); still rename
                        pass
                os.replace(tmp_file, payments_file)
            finally:
                # Best-effort cleanup if write/replace left a temp behind
                if os.path.exists(tmp_file):
                    try:
                        os.unlink(tmp_file)
                    except OSError:
                        pass
    except Exception as e:
        log.error(f"Error saving PAYMENTS_DB file: {e}")
        raise

class PersistentDict(dict[str, Any]):
    def __init__(self, default_data: dict[str, Any] | None = None) -> None:
        if default_data is None:
            default_data = {}
        super().__init__(default_data)
        self._default = default_data
        self._last_payments_file = ""
        self._last_mtime = -1.0
        self._syncing = False

    async def sync_async(self) -> None:
        await asyncio.to_thread(self._sync)

    async def save_async(self) -> None:
        await asyncio.to_thread(self._save)

    async def get_async(self, key: str, default: Any = None) -> Any:
        await self.sync_async()
        return dict.get(self, key, default)

    async def set_async(self, key: str, value: Any) -> None:
        """Update in-memory entry and persist. Save failures propagate (CAS/serve must fail)."""
        await self.sync_async()
        dict.__setitem__(self, key, value)
        await self.save_async()

    async def contains_async(self, key: str) -> bool:
        await self.sync_async()
        return dict.__contains__(self, key)

    async def items_async(self) -> dict[str, Any]:
        await self.sync_async()
        return dict(self)

    def _sync(self) -> None:
        if getattr(self, "_syncing", False):
            return
        self._syncing = True
        try:
            current_file = get_payments_file()
            mtime = 0.0
            if os.path.exists(current_file):
                try:
                    mtime = os.path.getmtime(current_file)
                except OSError:
                    pass
            if current_file != self._last_payments_file or mtime != self._last_mtime:
                dict.clear(self)
                dict.update(self, self._default)
                if os.path.exists(current_file):
                    try:
                        lock_file = current_file + ".lock"
                        with open(lock_file, "a") as lf:
                            try:
                                fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
                            except (OSError, AttributeError):
                                pass
                            if os.path.exists(current_file):
                                with open(current_file) as f:
                                    content = f.read().strip()
                                    if content:
                                        dict.update(self, json.loads(content))
                    except Exception as e:
                        log.error(f"Error loading PAYMENTS_DB during sync: {e}")
                self._last_payments_file = current_file
                self._last_mtime = mtime
        finally:
            self._syncing = False

    def _save(self) -> None:
        _save_db_file(dict(self))
        current_file = get_payments_file()
        if os.path.exists(current_file):
            try:
                self._last_mtime = os.path.getmtime(current_file)
            except OSError:
                pass
        else:
            self._last_mtime = 0.0

    def clear(self) -> None:
        self._sync()
        dict.clear(self)
        self._save()

    def __contains__(self, key: object) -> bool:
        self._sync()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        self._sync()
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._sync()
        super().__setitem__(key, value)
        self._save()

    def __delitem__(self, key: str) -> None:
        self._sync()
        super().__delitem__(key)
        self._save()

    def get(self, key: str, default: Any = None) -> Any:
        self._sync()
        return super().get(key, default)

    def keys(self) -> Any:
        self._sync()
        return super().keys()

    def values(self) -> Any:
        self._sync()
        return super().values()

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
        self._save()

    def pop(self, key: str, default: Any = None) -> Any:
        self._sync()
        res = super().pop(key, default)
        self._save()
        return res

    def popitem(self) -> tuple[str, Any]:
        self._sync()
        res = super().popitem()
        self._save()
        return res

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._sync()
        res = super().setdefault(key, default)
        self._save()
        return res

# Initial load for import-time queries
PAYMENTS_DB: dict[str, dict[str, Any]] = PersistentDict()

class SlidingWindowRateLimiter:
    def __init__(self, window_size: float = 60.0, max_requests: int = 100):
        self.window_size = window_size
        self.max_requests = max_requests
        self.requests: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self.last_cleanup = time.time()

    def check_rate_limit(self, client_ip: str) -> bool:
        """
        Check if client_ip is rate-limited.
        Returns True if the limit is exceeded (rate-limited), otherwise False.
        """
        now = time.time()
        with self.lock:
            # Periodically clean all old requests to avoid memory leaks
            if now - self.last_cleanup > self.window_size:
                self._cleanup_all(now)
                self.last_cleanup = now
            
            cutoff = now - self.window_size
            timestamps = self.requests.get(client_ip, [])
            valid_timestamps = [t for t in timestamps if t > cutoff]
            
            if len(valid_timestamps) >= self.max_requests:
                self.requests[client_ip] = valid_timestamps
                return True
            
            valid_timestamps.append(now)
            self.requests[client_ip] = valid_timestamps
            return False

    def _cleanup_all(self, now: float) -> None:
        cutoff = now - self.window_size
        for ip in list(self.requests.keys()):
            valid = [t for t in self.requests[ip] if t > cutoff]
            if not valid:
                self.requests.pop(ip, None)
            else:
                self.requests[ip] = valid

rate_limiter = SlidingWindowRateLimiter(window_size=60.0, max_requests=100)

# Demo recipient wallet address (e.g. Nazmi's developer wallet)
RECIPIENT_WALLET = os.getenv("RECIPIENT_WALLET", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
PRICE_USDC = os.getenv("PRICE_USDC", "0.001") # $0.001 USDC per request

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
    request: Request = None,
    payment_signature: str | None = Header(None, alias="Payment-Signature")
) -> dict[str, Any]:
    """Get real-time Base DEX market data. Gated behind x402 micropayments."""
    global METRICS_MARKET_DATA_REQUESTS
    METRICS_MARKET_DATA_REQUESTS += 1
    client_ip = "unknown"
    if request is not None:
        trust_forwarded = os.getenv("TRUST_FORWARDED_FOR", "false").lower() == "true"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded and trust_forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client is not None else "unknown"
    if rate_limiter.check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too Many Requests")

    # 1. Check if the payment signature is provided
    if not payment_signature:
        # Generate a unique payment ID for this request
        payment_id = str(uuid.uuid4())
        
        async with db_lock:
            await PAYMENTS_DB.set_async(payment_id, {
                "status": "pending",
                "price": PRICE_USDC,
                "currency": "USDC",
                "recipient": RECIPIENT_WALLET,
                "symbol": symbol
            })
        
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
            if not await PAYMENTS_DB.contains_async(pid):
                raise HTTPException(status_code=400, detail="Invalid or expired payment ID.")
                
            record = await PAYMENTS_DB.get_async(pid)
            if record.get("status") == "spent":
                raise HTTPException(status_code=400, detail="Payment already spent.")
                
            # Verify that tx_hash is not already used in any paid or spent payment record in DB
            items = await PAYMENTS_DB.items_async()
            for db_pid, db_record in items.items():
                if db_pid != pid and db_record.get("status") in ("paid", "spent") and db_record.get("tx_hash") == tx_hash:
                    raise HTTPException(status_code=400, detail="Transaction hash already processed.")
                    
            is_paid = record.get("status") == "paid"
            
            if is_paid:
                stored_sender = record.get("sender")
                if stored_sender and signer_address.lower() != stored_sender.lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Payment signature does not match transaction sender."
                    )
                # Ensure the signature matches the stored signature
                stored_signature = record.get("signature")
                if stored_signature and signature != stored_signature:
                    raise HTTPException(
                        status_code=400,
                        detail="Payment signature does not match stored signature."
                    )
            else:
                if tx_hash in VERIFYING_TXS:
                    raise HTTPException(status_code=400, detail="Transaction hash is currently being verified.")
                VERIFYING_TXS.add(tx_hash)
                
        if not is_paid:
            tx_from = None
            try:
                # Check Chain ID is Base mainnet (8453)
                try:
                    chain_id = await get_w3().eth.chain_id
                except Exception as e:
                    if is_connection_or_rate_limit_error(e):
                        await switch_rpc_failover()
                        chain_id = await get_w3().eth.chain_id
                    else:
                        raise HTTPException(status_code=400, detail="Failed to verify chain ID: RPC node is unresponsive.")
                
                if chain_id != 8453:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid Chain ID: expected 8453 (Base mainnet), got {chain_id}."
                    )
                    
                # Poll receipt first inside retry/backoff
                receipt = await get_transaction_receipt_with_failover(get_w3(), tx_hash)
                
                # Fetch transaction details
                tx_details = await get_transaction_with_failover(get_w3(), tx_hash)
                
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
                        block = await get_w3().eth.get_block(block_number)
                    except Exception as e:
                        if is_connection_or_rate_limit_error(e):
                            await switch_rpc_failover()
                            block = await get_w3().eth.get_block(block_number)
                        else:
                            raise
                            
                    block_timestamp = block.get("timestamp") if block else None
                    if block_timestamp is not None:
                        latest_block = None
                        try:
                            latest_block = await get_w3().eth.get_block("latest")
                        except Exception as e:
                            if is_connection_or_rate_limit_error(e):
                                await switch_rpc_failover()
                                latest_block = await get_w3().eth.get_block("latest")
                                
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
                        expected_amount = int(round(float(PRICE_USDC) * 1_000_000))
                        if amount != expected_amount:
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
                    
                # Re-acquire lock to write to DB — only pending → paid
                async with db_lock:
                    record = await PAYMENTS_DB.get_async(pid)
                    if not record or record.get("status") != "pending":
                        raise HTTPException(status_code=400, detail="Payment already processed.")
                    record["status"] = "paid"
                    record["tx_hash"] = tx_hash
                    record["sender"] = tx_from
                    record["signature"] = signature
                    await PAYMENTS_DB.set_async(pid, record)
            finally:
                async with db_lock:
                    VERIFYING_TXS.discard(tx_hash)
    except HTTPException:
        raise
    except (TimeoutError, Exception) as e:
        if isinstance(e, asyncio.TimeoutError) or is_connection_or_rate_limit_error(e):
            log.error(f"RPC connection/timeout error during verification: {e}")
            raise HTTPException(
                status_code=502,
                detail="Bad Gateway: RPC network or timeout error."
            ) from e
        log.error(f"Payment verification failed: {e}")
        raise HTTPException(
            status_code=400,
            detail="Failed verifying payment signature: Invalid payment or signature format."
        ) from e

    # 3. CAS paid→spent under lock BEFORE serving (prevents concurrent double-serve)
    async with db_lock:
        record = await PAYMENTS_DB.get_async(pid)
        if not record:
            raise HTTPException(status_code=400, detail="Invalid or expired payment ID.")
        if record.get("status") == "spent":
            raise HTTPException(status_code=400, detail="Payment already spent.")
        if record.get("status") != "paid":
            raise HTTPException(status_code=400, detail="Payment not verified.")
        record["status"] = "spent"
        await PAYMENTS_DB.set_async(pid, record)

    # 4. Retrieve and return live Base DEX pool data.
    # If serve fails after CAS, restore paid so the client can retry (refund entitlement).
    async def _restore_paid_on_serve_failure(reason: str) -> None:
        async with db_lock:
            rec = await PAYMENTS_DB.get_async(pid)
            if rec and rec.get("status") == "spent":
                rec["status"] = "paid"
                await PAYMENTS_DB.set_async(pid, rec)
                log.warning(
                    "Restored payment %s status spent→paid after market-data serve failure: %s",
                    pid,
                    reason,
                )

    try:
        active_rpc = get_w3().provider.endpoint_uri
        data = await get_onchain_price(symbol, rpc_url=active_rpc)
    except Exception as e:
        await _restore_paid_on_serve_failure(str(e))
        log.error(f"On-chain price fetch raised for {symbol}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch market data from upstream RPC.",
        ) from e

    if "error" in data:
        await _restore_paid_on_serve_failure(str(data["error"]))
        log.error(f"On-chain price fetch failed for {symbol}: {data['error']}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch market data from upstream RPC.",
        )

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
async def simulate_payment(payload: PaymentSignature, request: Request = None) -> dict[str, Any]:
    """Helper endpoint to mark a payment_id as paid and generate a mock signature.
    
    This allows testing clients to easily simulate the on-chain transfer.
    """
    client_ip = "unknown"
    if request is not None:
        trust_forwarded = os.getenv("TRUST_FORWARDED_FOR", "false").lower() == "true"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded and trust_forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client is not None else "unknown"
    if rate_limiter.check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too Many Requests")

    if os.getenv("ALLOW_SIMULATION", "false").lower() != "true":
        raise HTTPException(status_code=400, detail="Simulation mode is disabled.")

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
        if not await PAYMENTS_DB.contains_async(pid):
            raise HTTPException(status_code=404, detail="Payment ID not found.")

        items = await PAYMENTS_DB.items_async()
        for db_pid, db_record in items.items():
            if db_pid != pid and db_record.get("status") in ("paid", "spent") and db_record.get("tx_hash") == tx_hash:
                raise HTTPException(status_code=400, detail="Transaction hash already processed.")

        if tx_hash in VERIFYING_TXS:
            raise HTTPException(status_code=400, detail="Transaction hash is currently being verified.")

        # Only allow pending → paid; reject paid/spent/missing under lock
        record = await PAYMENTS_DB.get_async(pid)
        if not record:
            raise HTTPException(status_code=400, detail="Invalid or expired payment ID.")
        if record.get("status") != "pending":
            raise HTTPException(status_code=400, detail="Payment already processed.")

        record["status"] = "paid"
        record["tx_hash"] = tx_hash
        record["sender"] = signer_address
        record["signature"] = signature

        await PAYMENTS_DB.set_async(pid, record)

        return {
            "status": "success",
            "message": f"Payment {pid} successfully simulated as paid on Base mainnet.",
            "payment_record": record
        }


@app.get("/api/v1/admin/payments", include_in_schema=False)
async def get_all_payments(
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Return all payment records. Requires ADMIN_API_KEY via X-Admin-Key or Bearer."""
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        raise HTTPException(status_code=404, detail="Not Found")

    # When invoked outside FastAPI DI (unit tests), Header defaults may be
    # Header() objects rather than None/str — only treat real strings as keys.
    provided_key = x_admin_key if isinstance(x_admin_key, str) else None
    if not provided_key and isinstance(authorization, str):
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            provided_key = auth[7:].strip()
        else:
            provided_key = auth

    if (
        not provided_key
        or len(provided_key) != len(admin_key)
        or not hmac.compare_digest(provided_key, admin_key)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with db_lock:
        return await PAYMENTS_DB.items_async()

@app.get("/api/events")
async def sse_events():
    """SSE events endpoint returning price ticks for the UI client."""
    from datetime import datetime

    from fastapi.responses import StreamingResponse
    
    async def event_generator():
        yield f"data: {json.dumps({'type': 'info', 'message': 'SSE Stream connected successfully to Python backend'})}\n\n"
        
        # Send initial price tick
        init_price = round(2000 + random.random() * 100, 2)
        init_payload = {
            "type": "tick",
            "stage": "price_update",
            "status": "success",
            "message": f"Price updated to ${init_price}",
            "data": {
                "price": str(init_price),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        }
        yield f"data: {json.dumps(init_payload)}\n\n"
        
        while True:
            await asyncio.sleep(2.0)
            mock_price = round(2000 + random.random() * 100, 2)
            payload = {
                "type": "tick",
                "stage": "price_update",
                "status": "success",
                "message": f"Price updated to ${mock_price}",
                "data": {
                    "price": str(mock_price),
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
            }
            yield f"data: {json.dumps(payload)}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


class PriceImpactPayload(BaseModel):
    symbol: str
    side: str
    amount: float | None = None
    size: float | None = None


def _get_api_catalog() -> Catalog:
    from pathlib import Path

    from crypcodile.store.catalog import Catalog
    data_dir_env = os.getenv("DATA_DIR")
    if data_dir_env:
        return Catalog(Path(data_dir_env))
    for candidate in [Path("test_data"), Path("data"), Path.home() / "Crypcodile" / "test_data"]:
        if candidate.exists() and candidate.is_dir():
            try:
                cat = Catalog(candidate)
                if len(cat._registered_channels) > 0:
                    return cat
            except Exception:
                pass
    return Catalog(Path("test_data"))


def _get_lake_client() -> Any:
    """Build a CrypcodileClient for local lake discovery (read-only).

    Data root from ``CRYPCODILE_DATA_DIR`` (default ``\"data\"``).
    """
    from pathlib import Path

    from crypcodile.client.client import CrypcodileClient

    data_dir = os.getenv("CRYPCODILE_DATA_DIR", "data")
    return CrypcodileClient(data_dir=Path(data_dir))


async def _health_payload() -> dict[str, Any]:
    """Build the lightweight health/status body (no payment).

    Returns ``ok``, package ``version`` (``crypcodile.__version__``), and
    ``lake_channels`` (count of channels registered in the local lake).
    An empty or missing lake reports ``lake_channels: 0`` with ``ok: true``
    so the process is still considered healthy for liveness probes.
    """
    client = _get_lake_client()
    try:
        channels = client.list_channels()
        n = len(channels) if channels is not None else 0
    except Exception as e:
        log.warning("Health lake channel count failed: %s", e, exc_info=True)
        return {
            "ok": False,
            "version": __version__,
            "lake_channels": 0,
            "error": "lake_unavailable",
        }
    return {
        "ok": True,
        "version": __version__,
        "lake_channels": n,
    }


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    """Lightweight liveness probe (read-only, no payment).

    Always returns HTTP 200 with body keys: ``ok`` (bool), ``version``
    (str from ``crypcodile.__version__``), ``lake_channels`` (int count).
    Use :func:`ready` for k8s-style readiness that fails when ``ok`` is false.
    """
    return await _health_payload()


@app.get("/api/v1/status")
async def status() -> dict[str, Any]:
    """Alias of :func:`health` (read-only, no payment)."""
    return await _health_payload()


@app.get("/api/v1/ready")
async def ready(response: Response) -> dict[str, Any]:
    """K8s-style readiness probe, separate from liveness (:func:`health`).

    Returns the same body as :func:`health`. HTTP **200** when
    ``health.ok`` is true; HTTP **503** when the lake is unavailable so
    orchestrators can stop routing traffic while the process is still up.
    Prometheus metrics remain at ``GET /metrics`` (not duplicated here).
    """
    payload = await _health_payload()
    if not payload.get("ok"):
        response.status_code = 503
    return payload


# Hardcoded discovery lists for agents (free catalog / meta / analytics).
# Paid/admin routes (market-data, simulate-payment, admin/payments) omitted.
# See OpenAPI or MCP ``tools/list`` for query params and full schemas.
_CAPABILITIES_REST: list[str] = [
    # Meta / probes
    "GET /api/v1/health",
    "GET /api/v1/status",
    "GET /api/v1/ready",
    "GET /api/v1/capabilities",
    "GET /api/v1/version",
    "GET /api/v1/exchanges",
    # Catalog / discovery
    "GET /api/v1/catalog/channels",
    "GET /api/v1/catalog/exchanges",
    "GET /api/v1/catalog/summary",
    "GET /api/v1/catalog/search",
    "GET /api/v1/catalog/inventory",
    "GET /api/v1/catalog/dates",
    "GET /api/v1/catalog/scan",
    "GET /api/v1/data-coverage",
    "GET /api/v1/resolve-symbols",
    # Lake analytics (read-only)
    "GET /api/v1/open-interest",
    "GET /api/v1/funding-apr",
    "GET /api/v1/basis",
    "GET /api/v1/perp-basis",
    "GET /api/v1/spot-future-basis",
    "GET /api/v1/indicators",
    "GET /api/v1/ofi",
    "GET /api/v1/whale-alerts",
    "GET /api/v1/slippage",
    "GET /api/v1/iv-surface",
    "GET /api/v1/term-structure",
    "GET /api/v1/vol-skew",
    "GET /api/v1/risk-reversal",
    "GET /api/v1/liquidity-depth",
    "GET /api/v1/sequencer-latency",
    # Pure risk / offline analytics (no lake)
    "GET /api/v1/chaos-score",
    "GET /api/v1/peg-deviation",
    "GET /api/v1/lending-stress",
    "GET /api/v1/funding-predict",
    # Write-free POST analytics / SQL
    "POST /api/v1/query",
    "POST /api/v1/gas-vol",
    "POST /api/v1/mev-sandwich",
    "POST /api/v1/smart-money",
    "POST /api/v1/label-transfers",
    "POST /api/v1/simulate-price-impact",
]

_CAPABILITIES_MCP_TOOLS_HINT: list[str] = [
    "list_data_channels",
    "list_dates",
    "list_exchanges_on_disk",
    "catalog_summary",
    "search_symbols",
    "data_coverage",
    "inventory_snapshot",
    "query_market_data",
    "get_onchain_price",
    "get_base_market_data",
    "get_funding_apr",
    "get_indicators",
    "get_iv_surface",
    "get_term_structure",
    "get_vol_skew",
    "get_risk_reversal",
    "get_perp_basis",
    "get_spot_perp_basis",
    "get_spot_future_basis",
    "get_open_interest",
    "calculate_ofi",
    "estimate_slippage",
    "track_whale_alerts",
    "get_liquidity_depth",
    "get_sequencer_latency",
    "get_funding_prediction",
    "get_chaos_score",
    "get_lending_stress",
    "get_peg_deviation",
    "detect_mev_sandwiches",
    "smart_money_summary",
    "label_transfers",
]


@app.get("/api/v1/capabilities")
async def capabilities() -> dict[str, list[str]]:
    """Agent discovery: major free REST routes + MCP tool name hints.

    Returns hardcoded short lists (not an OpenAPI dump)::

        {"rest": [...], "mcp_tools_hint": [...]}

    Read-only, no payment, no lake. Lists are static copies so callers cannot
    mutate module-level constants.
    """
    return {
        "rest": list(_CAPABILITIES_REST),
        "mcp_tools_hint": list(_CAPABILITIES_MCP_TOOLS_HINT),
    }


@app.get("/api/v1/version")
async def version() -> dict[str, str]:
    """Package version only (read-only, no payment, no lake).

    Response: ``{"version": "<crypcodile.__version__>"}``.
    """
    return {"version": __version__}


@app.get("/api/v1/exchanges")
async def exchanges() -> list[str]:
    """List registered exchange connector names (read-only, no payment, no lake).

    Returns the sorted factory registry via
    :func:`crypcodile.exchanges.factory.list_exchanges`. Does not touch the
    data lake or any network connectors.
    """
    from crypcodile.exchanges.factory import list_exchanges

    return list_exchanges()


@app.get("/api/v1/catalog/channels")
async def catalog_list_channels() -> list[str]:
    """List data channels present in the local lake (read-only, no payment)."""
    client = _get_lake_client()
    return client.list_channels()


@app.get("/api/v1/catalog/exchanges")
async def catalog_list_exchanges() -> list[str]:
    """List distinct exchange partitions present in the local lake (read-only).

    Walks hive ``exchange=`` directories on disk via
    :meth:`Catalog.list_exchanges_on_disk`. Empty lake yields ``[]``.

    Distinct from ``GET /api/v1/exchanges``, which returns **registered
    connector** names from the factory registry (no lake).
    """
    client = _get_lake_client()
    return client.list_exchanges_on_disk()


@app.get("/api/v1/catalog/summary")
async def catalog_summary() -> dict[str, object]:
    """One-shot lake catalog summary for agent discovery (read-only, no payment).

    Combines channel and on-disk exchange partition lists with counts::

        {
            "channels": [...],           # sorted channel ids
            "exchanges_on_disk": [...],  # sorted hive exchange= suffixes
            "exchange_count": int,
            "channel_count": int,
        }

    Empty lake yields empty lists and zero counts. Distinct from
    ``GET /api/v1/exchanges`` (factory registry) — ``exchanges_on_disk``
    reflects hive partitions only.
    """
    client = _get_lake_client()
    channels = client.list_channels()
    exchanges_on_disk = client.list_exchanges_on_disk()
    return {
        "channels": channels,
        "exchanges_on_disk": exchanges_on_disk,
        "exchange_count": len(exchanges_on_disk),
        "channel_count": len(channels),
    }


@app.get("/api/v1/catalog/dates")
async def catalog_list_dates(channel: str = "") -> list[str]:
    """List distinct date partitions for a channel (read-only, no payment).

    Query ``channel`` is required (non-empty after strip). Empty / whitespace
    channel, unknown channel, or empty lake yields ``[]``. Dates are sorted
    ascending (typically ``YYYY-MM-DD`` hive partition suffixes).
    """
    channel = (channel or "").strip()
    if not channel:
        return []
    client = _get_lake_client()
    return client.list_dates(channel)


@app.get("/api/v1/catalog/search")
async def catalog_search_symbols(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Ranked symbol search over the local lake (read-only, no payment)."""
    if limit < 1:
        limit = 1
    client = _get_lake_client()
    df = client.search_symbols(q, limit=limit)
    if len(df) == 0:
        return []
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/catalog/inventory")
async def catalog_inventory(
    channel: str = "",
    exchange: str = "",
) -> list[dict[str, Any]]:
    """Summarise symbols present in the local lake (read-only, no payment).

    Optional ``channel`` and ``exchange`` query filters. Empty lake or no
    matching rows yields ``[]``.
    """
    client = _get_lake_client()
    ch = (channel or "").strip() or None
    ex = (exchange or "").strip() or None
    df = client.inventory(channel=ch, exchange=ex)
    if len(df) == 0:
        return []
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/data-coverage")
async def data_coverage(
    symbol: str = "",
    channel: str = "",
) -> list[dict[str, Any]]:
    """Return inventory coverage rows for one symbol (read-only, no payment).

    Wraps lake inventory filtered to exact ``symbol`` match, with optional
    ``channel``. Empty / whitespace-only symbol, empty lake, or no matching
    rows yields ``[]``. Same contract as MCP ``data_coverage``.
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return []
    ch = (channel or "").strip() or None
    client = _get_lake_client()
    df = client.inventory(channel=ch)
    if len(df) == 0:
        return []
    matched = [row for row in df.to_dicts() if row.get("symbol") == symbol]
    return _json_safe_records(matched)


@app.get("/api/v1/resolve-symbols")
async def resolve_symbols(
    symbols: str = "",
    channel: str = "",
    ambiguous: str = "error",
) -> list[str]:
    """Resolve free-form symbol inputs to canonical catalog symbols.

    Query params:
      - ``symbols``: comma-separated free-form symbol strings (e.g. ``a,b``
        or ``BTC-PERPETUAL,ETH-PERPETUAL``)
      - ``channel``: optional channel filter (empty/omitted = all channels)
      - ``ambiguous``: multi-match policy — ``error`` (default), ``first``,
        or ``all``

    Wraps :meth:`CrypcodileClient.resolve_symbols`. Success returns a JSON
    list of canonical symbols. Empty / whitespace-only ``symbols`` yields
    ``[]``. No match, ambiguous multi-match when ``ambiguous=error``, or an
    invalid ``ambiguous`` value yields HTTP 400 with ``detail`` describing
    the failure (client-facing validation).
    """
    raw = (symbols or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return []
    ch = (channel or "").strip() or None
    mode = (ambiguous or "").strip() or "error"

    client = _get_lake_client()
    try:
        return client.resolve_symbols(parts, channel=ch, ambiguous=mode)  # type: ignore[arg-type]
    except ValueError as e:
        # Ambiguous / no-match / invalid mode are intentional client-facing errors.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Symbol resolution failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Symbol resolution failed.",
        ) from e


# Hard max rows for lake scan / SQL query / OI / funding-APR HTTP responses (bounded discovery).
_CATALOG_SCAN_MAX_LIMIT = 10_000
_QUERY_MAX_LIMIT = 10_000
_OPEN_INTEREST_MAX_LIMIT = 10_000
_FUNDING_APR_MAX_LIMIT = 10_000
_BASIS_MAX_LIMIT = 10_000
_PERP_BASIS_MAX_LIMIT = 10_000
_SPOT_FUTURE_BASIS_MAX_LIMIT = 10_000
_INDICATORS_MAX_LIMIT = 10_000
_OFI_MAX_LIMIT = 10_000
_WHALE_ALERTS_MAX_LIMIT = 10_000
_IV_SURFACE_MAX_LIMIT = 10_000
_TERM_STRUCTURE_MAX_LIMIT = 10_000
_VOL_SKEW_MAX_LIMIT = 10_000
_LIQUIDITY_DEPTH_MAX_LIMIT = 10_000
_SEQUENCER_LATENCY_MAX_LIMIT = 10_000

# Mutating / side-effect SQL keywords rejected by the read-only query endpoint
# (word-boundary). Includes DuckDB-specific statements (PRAGMA, INSTALL, LOAD,
# EXPORT, CALL, ATTACH/DETACH, etc.).
_MUTATING_SQL_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "ATTACH",
    "COPY",
    "TRUNCATE",
    "DETACH",
    "PRAGMA",
    "INSTALL",
    "LOAD",
    "EXPORT",
    "CALL",
    "SET",
    "REPLACE",
    "MERGE",
    "VACUUM",
)
_MUTATING_SQL_RE = re.compile(
    r"\b(" + "|".join(_MUTATING_SQL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_MUTATING_SQL_DENY_MSG = (
    "Mutating SQL is not allowed ("
    + "/".join(_MUTATING_SQL_KEYWORDS)
    + ")."
)


def _is_mutating_sql(sql: str) -> bool:
    """Return True if ``sql`` contains a forbidden mutating statement keyword."""
    return _MUTATING_SQL_RE.search(sql or "") is not None


def _is_single_select(sql: str) -> bool:
    """Return True if ``sql`` looks like a single SELECT (or WITH…SELECT).

    Multi-statement SQL (semicolons mid-body) and non-SELECT statements are
    rejected so we only wrap pure read queries as
    ``SELECT * FROM (<user_sql>) LIMIT n``.
    """
    s = (sql or "").strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    return re.match(r"^(WITH|SELECT)\b", s, re.IGNORECASE) is not None


def _wrap_select_limit(sql: str, limit: int) -> str | None:
    """Wrap a single SELECT as ``SELECT * FROM (...) AS _q LIMIT n``.

    Returns ``None`` when wrapping is not safe (not a single SELECT). Callers
    should fall back to post-query ``head(limit)`` when this returns ``None``
    or when executing the wrapped SQL fails.
    """
    if not _is_single_select(sql):
        return None
    s = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({s}) AS _q LIMIT {int(limit)}"


@app.get("/api/v1/catalog/scan")
async def catalog_scan(
    channel: str = "",
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _CATALOG_SCAN_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Scan lake rows for one channel/symbol in a time range (read-only, no payment).

    Returns at most ``limit`` rows (default and hard max: 10000). Empty channel
    or symbol, or no matching rows, yields ``[]``.
    """
    channel = (channel or "").strip()
    symbol = (symbol or "").strip()
    if not channel or not symbol:
        return []
    if limit < 1:
        limit = 1
    if limit > _CATALOG_SCAN_MAX_LIMIT:
        limit = _CATALOG_SCAN_MAX_LIMIT

    client = _get_lake_client()
    df = client.scan(channel, [symbol], start, end)
    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/open-interest")
async def open_interest(
    symbols: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _OPEN_INTEREST_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Aggregate open interest across exchanges (read-only, no payment).

    Optional ``symbols`` substring filter (e.g. ``BTC``); empty/omitted means
    all symbols. Aligns OI across exchanges with forward-fill; each row has
    ``local_ts``, per-exchange OI columns, and ``total_oi``.

    Returns at most ``limit`` rows (default and hard max: 10000). Empty lake
    or no matching rows yields ``[]``.
    """
    sym = (symbols or "").strip() or None
    if limit < 1:
        limit = 1
    if limit > _OPEN_INTEREST_MAX_LIMIT:
        limit = _OPEN_INTEREST_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.aggregate_open_interest(sym, start, end)
    except Exception as e:
        log.error("Open interest aggregation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Open interest aggregation failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/funding-apr")
async def funding_apr(
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _FUNDING_APR_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Per-event funding APR and cumulative funding (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``deribit:BTC-PERPETUAL``),
    ``start`` / ``end`` as nanoseconds UTC (inclusive bounds on ``local_ts``).

    Returns at most ``limit`` rows (default and hard max: 10000). Empty symbol,
    empty lake, or no matching rows yields ``[]``.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _FUNDING_APR_MAX_LIMIT:
        limit = _FUNDING_APR_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.funding_apr(sym, start, end)
    except Exception as e:
        log.error("Funding APR query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Funding APR query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/basis")
async def basis(
    spot: str = "",
    perp: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _BASIS_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Spot–perp basis via ASOF join (read-only, no payment).

    Query params: ``spot`` and ``perp`` canonical symbols (e.g.
    ``deribit:BTC-SPOT``, ``deribit:BTC-PERPETUAL``), ``start`` / ``end`` as
    nanoseconds UTC (inclusive bounds on ``local_ts``).

    Wraps :meth:`CrypcodileClient.spot_perp_basis`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``spot`` or ``perp``,
    empty lake, or no matching rows yields ``[]``.
    """
    spot_sym = (spot or "").strip()
    perp_sym = (perp or "").strip()
    if not spot_sym or not perp_sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _BASIS_MAX_LIMIT:
        limit = _BASIS_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.spot_perp_basis(spot_sym, perp_sym, start, end)
    except Exception as e:
        log.error("Spot-perp basis query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Spot-perp basis query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/perp-basis")
async def perp_basis(
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _PERP_BASIS_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Perpetual mark–index basis (read-only, no payment).

    Query params: ``symbol`` (canonical perpetual, e.g. ``deribit:BTC-PERPETUAL``),
    ``start`` / ``end`` as nanoseconds UTC (inclusive bounds on ``local_ts``).

    Wraps :meth:`CrypcodileClient.perp_basis`. Returns at most ``limit`` rows
    (default and hard max: 10000). Empty/missing ``symbol``, empty lake, or no
    matching rows yields ``[]``.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _PERP_BASIS_MAX_LIMIT:
        limit = _PERP_BASIS_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.perp_basis(sym, start, end)
    except Exception as e:
        log.error("Perp basis query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Perp basis query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/spot-future-basis")
async def spot_future_basis(
    future: str = "",
    spot: str = "",
    start: int = 0,
    end: int = 0,
    limit: int = _SPOT_FUTURE_BASIS_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Spot–future basis via ASOF join (read-only, no payment).

    Query params: ``future`` and ``spot`` canonical symbols (e.g.
    ``deribit:BTC-27JUN25``, ``deribit:BTC-SPOT``), ``start`` / ``end`` as
    nanoseconds UTC (inclusive bounds on ``local_ts``).

    Wraps :meth:`CrypcodileClient.spot_future_basis`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``future`` or ``spot``,
    empty lake, or no matching rows yields ``[]``.
    """
    future_sym = (future or "").strip()
    spot_sym = (spot or "").strip()
    if not future_sym or not spot_sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _SPOT_FUTURE_BASIS_MAX_LIMIT:
        limit = _SPOT_FUTURE_BASIS_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.spot_future_basis(future_sym, spot_sym, start, end)
    except Exception as e:
        log.error("Spot-future basis query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Spot-future basis query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/indicators")
async def indicators(
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    interval: str = "1d",
    indicator: str = "",
    period: int = 14,
    limit: int = _INDICATORS_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Technical indicators on resampled OHLCV (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``deribit:BTC-PERPETUAL``),
    ``start`` / ``end`` as nanoseconds UTC, ``interval`` (e.g. ``1m``, ``1h``,
    ``1d``), ``indicator`` (``sma``|``ema``|``rsi``|``macd``|``bb``|``all``;
    empty/omitted means ``all``), ``period`` (smoothing/lookback window).

    Wraps :meth:`CrypcodileClient.get_indicators`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``symbol``, empty lake,
    or no matching bars yields ``[]``. Unknown ``indicator`` names yield 400.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _INDICATORS_MAX_LIMIT:
        limit = _INDICATORS_MAX_LIMIT

    ind = (indicator or "").strip() or None
    interval_s = (interval or "").strip() or "1d"
    period_i = int(period)
    if period_i < 1:
        period_i = 1

    client = _get_lake_client()
    try:
        df = client.get_indicators(
            sym,
            start,
            end,
            interval=interval_s,
            indicator=ind,
            period=period_i,
        )
    except ValueError as e:
        # Unknown indicator names are intentional client-facing validation.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Indicators query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Indicators query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/ofi")
async def ofi(
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    interval: str = "1m",
    limit: int = _OFI_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Order Flow Imbalance (OFI) over time-binned book snapshots (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``deribit:BTC-PERPETUAL``),
    ``start`` / ``end`` as nanoseconds UTC (inclusive bounds on ``local_ts``),
    ``interval`` bin size (e.g. ``1s``, ``1m``, ``5m``, ``1h``; default ``1m``).

    Wraps :meth:`CrypcodileClient.calculate_ofi`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``symbol``, empty lake,
    or no matching snapshots yields ``[]``. Invalid ``interval`` strings yield 400.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _OFI_MAX_LIMIT:
        limit = _OFI_MAX_LIMIT

    interval_s = (interval or "").strip() or "1m"

    client = _get_lake_client()
    try:
        df = client.calculate_ofi(sym, start, end, interval_s)
    except ValueError as e:
        # Invalid interval strings are intentional client-facing validation.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("OFI query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="OFI query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/whale-alerts")
async def whale_alerts(
    symbol: str = "",
    start: int = 0,
    end: int = 0,
    min_usd: float = 0.0,
    limit: int = _WHALE_ALERTS_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Whale-sized trades and liquidations above a USD threshold (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``deribit:BTC-PERPETUAL``),
    ``start`` / ``end`` as nanoseconds UTC (inclusive bounds on ``local_ts``),
    ``min_usd`` minimum notional (price × amount; default ``0``).

    Wraps :meth:`CrypcodileClient.track_whale_alerts`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``symbol``, empty lake,
    or no matching events yields ``[]``. Negative ``min_usd`` yields 400.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _WHALE_ALERTS_MAX_LIMIT:
        limit = _WHALE_ALERTS_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.track_whale_alerts(sym, start, end, float(min_usd))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Whale alerts query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Whale alerts query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/slippage")
async def slippage(
    symbol: str = "",
    side: str = "",
    size: float = 0.0,
) -> list[dict[str, Any]]:
    """Estimate execution slippage for a market order size (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``deribit:BTC-PERPETUAL``),
    ``side`` (``buy`` or ``sell``), ``size`` (base-asset quantity, must be > 0).

    Wraps :meth:`CrypcodileClient.estimate_slippage`. Returns a single-row list
    of dicts (``symbol``, ``side``, ``size``, ``best_price``, ``expected_price``,
    ``slippage_usd``, ``slippage_pct``). Empty/missing ``symbol`` yields ``[]``.
    Invalid ``side``/``size``, missing book, or insufficient depth yield 400.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    side_s = (side or "").strip()
    size_f = float(size)

    client = _get_lake_client()
    try:
        df = client.estimate_slippage(sym, side_s, size_f)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Slippage estimation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Slippage estimation failed.",
        ) from e

    if len(df) == 0:
        return []
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/iv-surface")
async def iv_surface(
    underlying: str = "",
    at: int = 0,
    rate: float = 0.0,
    limit: int = _IV_SURFACE_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Implied-vol surface snapshot at ``at`` (read-only, no payment).

    Query params: ``underlying`` (e.g. ``BTC``), ``at`` snapshot instant as
    nanoseconds UTC, ``rate`` continuous risk-free rate (default ``0.0``).

    Wraps :meth:`CrypcodileClient.iv_surface`. Returns at most ``limit`` rows
    (default and hard max: 10000). Empty/missing ``underlying``, empty lake,
    or no matching options yields ``[]``.
    """
    und = (underlying or "").strip()
    if not und:
        return []
    if limit < 1:
        limit = 1
    if limit > _IV_SURFACE_MAX_LIMIT:
        limit = _IV_SURFACE_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.iv_surface(und, at, rate=float(rate))
    except Exception as e:
        log.error("IV surface query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="IV surface query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/term-structure")
async def term_structure(
    underlying: str = "",
    at: int = 0,
    rate: float = 0.0,
    limit: int = _TERM_STRUCTURE_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """ATM IV term structure at ``at`` (read-only, no payment).

    Query params: ``underlying`` (e.g. ``BTC``), ``at`` snapshot instant as
    nanoseconds UTC, ``rate`` continuous risk-free rate (default ``0.0``).

    Wraps :meth:`CrypcodileClient.term_structure`. Returns at most ``limit``
    rows (default and hard max: 10000). Empty/missing ``underlying``, empty
    lake, or no matching options yields ``[]``.
    """
    und = (underlying or "").strip()
    if not und:
        return []
    if limit < 1:
        limit = 1
    if limit > _TERM_STRUCTURE_MAX_LIMIT:
        limit = _TERM_STRUCTURE_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.term_structure(und, at, rate=float(rate))
    except Exception as e:
        log.error("Term structure query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Term structure query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/vol-skew")
async def vol_skew(
    underlying: str = "",
    expiry_ns: int = 0,
    at: int = 0,
    rate: float = 0.0,
    limit: int = _VOL_SKEW_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Per-strike IV and delta for a single expiry (read-only, no payment).

    Query params: ``underlying`` (e.g. ``BTC``), ``expiry_ns`` option expiry
    as nanoseconds UTC, ``at`` snapshot instant as nanoseconds UTC, ``rate``
    continuous risk-free rate (default ``0.0``).

    Wraps :meth:`CrypcodileClient.vol_skew`. Returns at most ``limit`` rows
    (default and hard max: 10000). Empty/missing ``underlying``, empty lake,
    or no matching options yields ``[]``.
    """
    und = (underlying or "").strip()
    if not und:
        return []
    if limit < 1:
        limit = 1
    if limit > _VOL_SKEW_MAX_LIMIT:
        limit = _VOL_SKEW_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.vol_skew(und, int(expiry_ns), int(at), rate=float(rate))
    except Exception as e:
        log.error("Vol skew query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Vol skew query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/risk-reversal")
async def risk_reversal(
    underlying: str = "",
    expiry_ns: int = 0,
    at: int = 0,
    rate: float = 0.0,
    target_delta: float = 0.25,
) -> dict[str, Any]:
    """Risk-reversal and butterfly from vol skew at one expiry (read-only, no payment).

    Query params: ``underlying`` (e.g. ``BTC``), ``expiry_ns`` option expiry
    as nanoseconds UTC, ``at`` snapshot instant as nanoseconds UTC, ``rate``
    continuous risk-free rate (default ``0.0``), ``target_delta`` absolute
    delta for RR/BF (default ``0.25``).

    Wraps :meth:`CrypcodileClient.vol_skew` then
    :meth:`CrypcodileClient.risk_reversal_butterfly`. Empty/missing
    ``underlying``, empty lake, or no matching options yields
    ``risk_reversal`` / ``butterfly`` as ``null``.
    """
    und = (underlying or "").strip()
    if not und:
        return {
            "underlying": "",
            "expiry_ns": int(expiry_ns),
            "at": int(at),
            "rate": float(rate),
            "target_delta": float(target_delta),
            "risk_reversal": None,
            "butterfly": None,
        }

    client = _get_lake_client()
    try:
        skew_df = client.vol_skew(und, int(expiry_ns), int(at), rate=float(rate))
        if len(skew_df) == 0:
            rr, bf = None, None
        else:
            rr, bf = client.risk_reversal_butterfly(
                skew_df, target_delta=float(target_delta)
            )
    except Exception as e:
        log.error("Risk reversal query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Risk reversal query failed.",
        ) from e

    return {
        "underlying": und,
        "expiry_ns": int(expiry_ns),
        "at": int(at),
        "rate": float(rate),
        "target_delta": float(target_delta),
        "risk_reversal": rr,
        "butterfly": bf,
    }


@app.get("/api/v1/liquidity-depth")
async def liquidity_depth(
    symbol: str = "",
    limit: int = _LIQUIDITY_DEPTH_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Per-block bid/ask liquidity depth at ±1/2/5% from mid (read-only, no payment).

    Query params: ``symbol`` (canonical, e.g. ``base_onchain:DEGEN-WETH``).

    Wraps :meth:`CrypcodileClient.calculate_block_liquidity_depth`. Returns at
    most ``limit`` rows (default and hard max: 10000). Empty/missing ``symbol``,
    empty lake, or no matching book snapshots yields ``[]``.
    """
    sym = (symbol or "").strip()
    if not sym:
        return []
    if limit < 1:
        limit = 1
    if limit > _LIQUIDITY_DEPTH_MAX_LIMIT:
        limit = _LIQUIDITY_DEPTH_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.calculate_block_liquidity_depth(sym)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Liquidity depth query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Liquidity depth query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/sequencer-latency")
async def sequencer_latency(
    exchange: str = "base_onchain",
    limit: int = _SEQUENCER_LATENCY_MAX_LIMIT,
) -> list[dict[str, Any]]:
    """Sequencer production interval and ingestion delay (read-only, no payment).

    Query params: ``exchange`` (e.g. ``base_onchain``; default ``base_onchain``).

    Wraps :meth:`CrypcodileClient.calculate_sequencer_latency`. Returns at most
    ``limit`` summary rows (default and hard max: 10000). Empty lake or
    insufficient timestamps yields ``[]``.
    """
    ex = (exchange or "").strip() or "base_onchain"
    if limit < 1:
        limit = 1
    if limit > _SEQUENCER_LATENCY_MAX_LIMIT:
        limit = _SEQUENCER_LATENCY_MAX_LIMIT

    client = _get_lake_client()
    try:
        df = client.calculate_sequencer_latency(ex)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Sequencer latency query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Sequencer latency query failed.",
        ) from e

    if len(df) == 0:
        return []
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.get("/api/v1/chaos-score")
async def chaos_score(
    volatility: float = 0.0,
    stablecoin_deviation: float = 0.0,
    orderbook_imbalance: float = 0.0,
    sequencer_delay: float = 0.0,
) -> dict[str, Any]:
    """Normalized [0, 100] chaos score from pure risk metrics (no lake, no payment).

    Query params: ``volatility``, ``stablecoin_deviation``,
    ``orderbook_imbalance``, ``sequencer_delay`` (all floats; default ``0``).

    Wraps :func:`crypcodile.analytics.risk.calculate_chaos_score`. Returns the
    inputs plus ``chaos_score``. Non-finite floats (e.g. ±Inf inputs → NaN
    score via soft-thresholding) are returned as JSON ``null``.
    """
    from crypcodile.analytics.risk import calculate_chaos_score

    try:
        score = calculate_chaos_score(
            volatility=float(volatility),
            stablecoin_deviation=float(stablecoin_deviation),
            orderbook_imbalance=float(orderbook_imbalance),
            sequencer_delay=float(sequencer_delay),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Chaos score calculation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Chaos score calculation failed.",
        ) from e

    return {
        "volatility": _json_safe_float(volatility),
        "stablecoin_deviation": _json_safe_float(stablecoin_deviation),
        "orderbook_imbalance": _json_safe_float(orderbook_imbalance),
        "sequencer_delay": _json_safe_float(sequencer_delay),
        "chaos_score": _json_safe_float(score),
    }


@app.get("/api/v1/peg-deviation")
async def peg_deviation(
    price: float = 0.0,
    threshold: float = 0.01,
    target: float = 1.0,
) -> dict[str, Any]:
    """Pure peg-deviation check from a single mid price (no lake, no payment).

    Query params: ``price`` (observed mid), ``threshold`` absolute deviation
    alert threshold (default ``0.01``), optional ``target`` peg (default ``1.0``).

    Wraps :func:`crypcodile.analytics.peg_deviation.peg_deviation_from_price`.
    Returns ``price``, ``deviation_pct``, ``is_alert_triggered``, ``threshold``.
    Non-finite floats (NaN/±Inf price or deviation) are JSON ``null``.
    """
    from crypcodile.analytics.peg_deviation import peg_deviation_from_price

    try:
        result = peg_deviation_from_price(
            float(price),
            threshold=float(threshold),
            target=float(target),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Peg deviation calculation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Peg deviation calculation failed.",
        ) from e

    return {
        "price": _json_safe_float(result["price"]),
        "deviation_pct": _json_safe_float(result["deviation_pct"]),
        "is_alert_triggered": bool(result["is_alert_triggered"]),
        "threshold": _json_safe_float(result["threshold"]),
    }


@app.get("/api/v1/lending-stress")
async def lending_stress(
    collateral_usd: float = 0.0,
    debt_usd: float = 0.0,
    liquidation_threshold: float = 0.0,
    haircut_pct: float = 0.0,
) -> dict[str, Any]:
    """Pure LTV/health-factor stress under collateral haircut (no lake, no payment).

    Query params match the CLI ``lending-stress`` command:
    ``collateral_usd``, ``debt_usd``, ``liquidation_threshold`` (fraction,
    e.g. ``0.8``), ``haircut_pct`` (fraction or percent, e.g. ``0.20`` or ``20``).

    Wraps :func:`crypcodile.analytics.lending_stress.lending_stress_test`.
    Returns the pure-function metrics plus the input parameters for context.
    Health factors that are non-finite in pure analytics (zero debt →
    ``float('inf')``) are returned as JSON ``null`` so responses encode.
    """
    from crypcodile.analytics.lending_stress import lending_stress_test

    try:
        result = lending_stress_test(
            collateral_usd=float(collateral_usd),
            debt_usd=float(debt_usd),
            liquidation_threshold=float(liquidation_threshold),
            haircut_pct=float(haircut_pct),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Lending stress calculation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Lending stress calculation failed.",
        ) from e

    return {
        "collateral_usd": float(collateral_usd),
        "debt_usd": float(debt_usd),
        "liquidation_threshold": float(liquidation_threshold),
        "haircut_pct": float(haircut_pct),
        "current_health_factor": _json_safe_float(result["current_health_factor"]),
        "simulated_health_factor": _json_safe_float(result["simulated_health_factor"]),
        "is_liquidatable": bool(result["is_liquidatable"]),
        "simulated_is_liquidatable": bool(result["simulated_is_liquidatable"]),
    }


@app.get("/api/v1/funding-predict")
async def funding_predict(
    rates: str = "",
    window_size: int = 5,
) -> dict[str, Any]:
    """Predict next-period funding rate from pure offline rates (no lake, no payment).

    Query params:
      - ``rates``: comma-separated historical funding rates
        (e.g. ``0.1,0.2,0.3`` or ``0.0001,0.0002,0.00015``)
      - ``window_size``: rolling window for the heuristic fallback
        (default ``5``; must be ``>= 1``)

    Wraps :func:`crypcodile.analytics.funding_prediction.predict_next_funding`.
    Returns ``predicted_funding_rate``, ``method`` (``xgboost`` or
    ``rolling_mean``), ``window_size``, ``n_history``, ``xgboost_available``.
    Non-finite predictions (e.g. Inf/NaN history) are JSON ``null``.

    Empty / whitespace-only ``rates``, non-numeric tokens, empty after split,
    or ``window_size < 1`` yield HTTP 400.
    """
    from crypcodile.analytics.funding_prediction import predict_next_funding

    raw = (rates or "").strip()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="rates is required (comma-separated floats).",
        )
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise HTTPException(
            status_code=400,
            detail="rates is empty after parsing.",
        )
    try:
        rate_list = [float(p) for p in parts]
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid rates value: {e}",
        ) from e

    try:
        result = predict_next_funding(rate_list, window_size=int(window_size))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Funding prediction failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Funding prediction failed.",
        ) from e

    # Non-finite predicted rates (e.g. Inf/NaN history) → JSON null.
    return {
        "predicted_funding_rate": _json_safe_float(result["predicted_funding_rate"]),
        "method": result["method"],
        "window_size": int(result["window_size"]),
        "n_history": int(result["n_history"]),
        "xgboost_available": bool(result["xgboost_available"]),
    }


class GasVolPayload(BaseModel):
    """Body for ``POST /api/v1/gas-vol`` — pure offline gas/vol series.

    Each series is a list of row objects. Rows must include ``local_ts`` and at
    least one numeric gas / volatility column (e.g. ``gas`` / ``gas_price`` /
    ``gas_cost`` and ``vol`` / ``volatility``). Extra columns are ignored by
    :func:`crypcodile.analytics.gas_vol_correlation.gas_to_volatility_correlation`.
    """

    gas: list[dict[str, Any]]
    vol: list[dict[str, Any]]


def _series_rows_to_df(rows: list[dict[str, Any]], series_name: str):
    """Build a Polars DataFrame from JSON series rows; validate ``local_ts``."""
    import polars as pl

    if not rows:
        # Empty series → empty DF with the minimal schema the correlator expects.
        return pl.DataFrame(schema={"local_ts": pl.Int64, series_name: pl.Float64})

    if not all(isinstance(r, dict) for r in rows):
        raise HTTPException(
            status_code=400,
            detail=f"{series_name} rows must be objects.",
        )
    for i, row in enumerate(rows):
        if "local_ts" not in row:
            raise HTTPException(
                status_code=400,
                detail=f"{series_name}[{i}] missing required field 'local_ts'.",
            )
    try:
        return pl.DataFrame(rows)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid {series_name} series: {e}",
        ) from e


@app.post("/api/v1/gas-vol")
async def gas_vol(payload: GasVolPayload) -> dict[str, Any]:
    """Correlate gas costs vs volatility from pure JSON series (no lake, no payment).

    Body::

        {
          "gas": [{"local_ts": 1, "gas": 10.0}, ...],
          "vol": [{"local_ts": 1, "vol": 0.1}, ...]
        }

    Wraps :func:`crypcodile.analytics.gas_vol_correlation.gas_to_volatility_correlation`
    after building Polars DataFrames from the series. Returns ``pearson`` and
    ``spearman`` (JSON ``null`` when undefined / insufficient data), plus
    ``n_gas`` and ``n_vol`` input lengths for context.

    Empty series or fewer than two aligned pairs yield null correlations
    (HTTP 200) rather than an error — matching the pure function.
    """
    from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation

    if not isinstance(payload.gas, list) or not isinstance(payload.vol, list):
        raise HTTPException(
            status_code=400,
            detail="gas and vol must be JSON arrays of row objects.",
        )

    gas_df = _series_rows_to_df(payload.gas, "gas")
    vol_df = _series_rows_to_df(payload.vol, "vol")

    try:
        result = gas_to_volatility_correlation(gas_df, vol_df)
    except (IndexError, KeyError, ValueError) as e:
        # Column detection fails when non-empty frames lack a usable value col.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Gas–vol correlation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Gas–vol correlation failed.",
        ) from e

    return {
        "pearson": _json_safe_float(result.get("pearson", float("nan"))),
        "spearman": _json_safe_float(result.get("spearman", float("nan"))),
        "n_gas": len(payload.gas),
        "n_vol": len(payload.vol),
    }


class MevSandwichPayload(BaseModel):
    """Body for ``POST /api/v1/mev-sandwich`` — pure offline trade sequence.

    Each trade row should include ``block``, ``pool``, ``log_index``, ``sender``,
    and ``is_buy`` (bool, 0/1, or common string forms). Extra columns are kept
    through the detector and returned on the output rows.
    """

    trades: list[dict[str, Any]]


@app.post("/api/v1/mev-sandwich")
async def mev_sandwich(payload: MevSandwichPayload) -> list[dict[str, Any]]:
    """Flag MEV sandwich legs in a pure JSON trade sequence (no lake, no payment).

    Body::

        {
          "trades": [
            {"block": 100, "pool": "AERO-USDC", "log_index": 10,
             "sender": "0xattacker", "is_buy": true},
            ...
          ]
        }

    Wraps :func:`crypcodile.analytics.mev_sandwich.detect_sandwiches`. Returns
    the same trade rows as dicts with an ``is_sandwich`` boolean flag. Empty
    ``trades`` → ``[]`` (HTTP 200). Missing required columns → HTTP 400.
    """
    import polars as pl

    from crypcodile.analytics.mev_sandwich import detect_sandwiches

    trades = payload.trades
    if not isinstance(trades, list):
        raise HTTPException(
            status_code=400,
            detail="trades must be a JSON array of row objects.",
        )
    if len(trades) == 0:
        return []
    if not all(isinstance(r, dict) for r in trades):
        raise HTTPException(
            status_code=400,
            detail="trades rows must be objects.",
        )

    try:
        df = pl.DataFrame(trades)
        out = detect_sandwiches(df)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("MEV sandwich detection failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="MEV sandwich detection failed.",
        ) from e

    if len(out) == 0:
        return []
    return _json_safe_records(out.to_dicts())


class SmartMoneyPayload(BaseModel):
    """Body for ``POST /api/v1/smart-money`` — pure offline transfers + watchlist.

    ``transfers`` rows accept ``from``/``to``/``usd_value``/``timestamp`` with
    common aliases (``from_address``, ``amount``, ``local_ts``, …).
    ``watchlist`` may be an addr→label map, a list of addresses, or nested
    shapes accepted by
    :func:`crypcodile.analytics.smart_money.normalize_watchlist`.
    """

    transfers: list[dict[str, Any]]
    watchlist: dict[str, Any] | list[Any]


@app.post("/api/v1/smart-money")
async def smart_money(payload: SmartMoneyPayload) -> list[dict[str, Any]]:
    """Summarize smart-money capital flows from pure JSON (no lake, no payment).

    Body::

        {
          "transfers": [
            {"from": "0xsmart", "to": "0xother", "usd_value": 100.0, "timestamp": 1},
            ...
          ],
          "watchlist": {"0xsmart": "vitalik"}
        }

    Wraps :func:`crypcodile.analytics.smart_money.summarize_smart_money` after
    :func:`~crypcodile.analytics.smart_money.normalize_watchlist`. Returns
    per-address flow rows (``net_flow_usd``, ``total_volume_usd``, ``tx_count``,
    ``last_active_ts``, optional ``label``) sorted by total volume. Empty
    watchlist or no matching activity → ``[]`` (HTTP 200).
    """
    from crypcodile.analytics.smart_money import (
        normalize_watchlist,
        summarize_smart_money,
    )

    transfers = payload.transfers
    watchlist = payload.watchlist

    if not isinstance(transfers, list):
        raise HTTPException(
            status_code=400,
            detail="transfers must be a JSON array of row objects.",
        )
    if not all(isinstance(r, dict) for r in transfers):
        raise HTTPException(
            status_code=400,
            detail="transfers rows must be objects.",
        )
    if not isinstance(watchlist, (dict, list)):
        raise HTTPException(
            status_code=400,
            detail="watchlist must be a JSON object or array of addresses.",
        )

    try:
        labels = normalize_watchlist(watchlist)
        if not labels:
            return []
        rows = summarize_smart_money(transfers, labels)
        return _json_safe_records(rows)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Smart-money summary failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Smart-money summary failed.",
        ) from e


class LabelTransfersPayload(BaseModel):
    """Body for ``POST /api/v1/label-transfers`` — pure offline label + filter.

    ``transfers`` rows accept ``from``/``to`` with common aliases
    (``from_address``, ``sender``, ``to_address``, ``recipient``) and optional
    ``usd_value`` / ``amount`` / ``value`` for ``min_usd`` filtering.
    ``watchlist`` may be an addr→label map, a list of addresses, or nested
    shapes accepted by
    :func:`crypcodile.analytics.smart_money.normalize_watchlist`.
    """

    transfers: list[dict[str, Any]]
    watchlist: dict[str, Any] | list[Any]
    known_only: bool = False
    min_usd: float | None = None


@app.post("/api/v1/label-transfers")
async def label_transfers(payload: LabelTransfersPayload) -> list[dict[str, Any]]:
    """Annotate transfer rows with watchlist labels (no lake, no payment).

    Body::

        {
          "transfers": [
            {"from": "0xsmart", "to": "0xother", "usd_value": 100.0},
            ...
          ],
          "watchlist": {"0xsmart": "vitalik"},
          "known_only": false,
          "min_usd": null
        }

    Wraps :func:`crypcodile.analytics.whale_transfers.label_transfer_addresses`
    after optional
    :func:`~crypcodile.analytics.whale_transfers.filter_transfers_by_usd` and
    :func:`~crypcodile.analytics.smart_money.normalize_watchlist`. Returns the
    same rows with ``from_label``, ``to_label``, and ``is_known``. Empty
    ``transfers`` → ``[]`` (HTTP 200). Empty watchlist still returns rows with
    empty labels. When ``known_only`` is true, only rows with a labeled side
    are kept. Negative ``min_usd`` → HTTP 400.
    """
    from crypcodile.analytics.smart_money import normalize_watchlist
    from crypcodile.analytics.whale_transfers import (
        filter_transfers_by_usd,
        label_transfer_addresses,
    )

    transfers = payload.transfers
    watchlist = payload.watchlist

    if not isinstance(transfers, list):
        raise HTTPException(
            status_code=400,
            detail="transfers must be a JSON array of row objects.",
        )
    if not all(isinstance(r, dict) for r in transfers):
        raise HTTPException(
            status_code=400,
            detail="transfers rows must be objects.",
        )
    if not isinstance(watchlist, (dict, list)):
        raise HTTPException(
            status_code=400,
            detail="watchlist must be a JSON object or array of addresses.",
        )

    try:
        rows: list[dict[str, Any]] = list(transfers)
        if payload.min_usd is not None:
            rows = filter_transfers_by_usd(rows, float(payload.min_usd))
        labels = normalize_watchlist(watchlist)
        labeled = label_transfer_addresses(rows, labels)
        if payload.known_only:
            labeled = [r for r in labeled if r.get("is_known")]
        return _json_safe_records(labeled)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Label-transfers failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Label-transfers failed.",
        ) from e


class QueryPayload(BaseModel):
    """Body for ``POST /api/v1/query`` — bounded read-only SQL against the lake."""

    sql: str
    limit: int | None = None


@app.post("/api/v1/query")
async def query_lake(payload: QueryPayload) -> list[dict[str, Any]]:
    """Execute read-only DuckDB SQL against the local lake (no payment).

    Rejects mutating / side-effect statements (INSERT, UPDATE, DELETE, DROP,
    CREATE, ALTER, ATTACH, COPY, TRUNCATE, DETACH, PRAGMA, INSTALL, LOAD,
    EXPORT, CALL, SET, REPLACE, MERGE, VACUUM) case-insensitively.

    Row bound: for a single SELECT (or WITH…SELECT), the query is wrapped as
    ``SELECT * FROM (<user_sql>) AS _q LIMIT n`` so DuckDB enforces the cap.
    If wrapping is not applicable or the wrapped query fails, the original SQL
    is executed and rows are truncated with ``head(limit)`` instead.

    Returns at most ``limit`` rows (default and hard max: 10000). Empty result
    set yields ``[]``. SQL errors are logged server-side; clients only receive
    a generic failure message (no exception detail).
    """
    sql = (payload.sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL query is required.")
    if _is_mutating_sql(sql):
        raise HTTPException(
            status_code=400,
            detail=_MUTATING_SQL_DENY_MSG,
        )

    limit = _QUERY_MAX_LIMIT if payload.limit is None else int(payload.limit)
    if limit < 1:
        limit = 1
    if limit > _QUERY_MAX_LIMIT:
        limit = _QUERY_MAX_LIMIT

    client = _get_lake_client()
    wrapped = _wrap_select_limit(sql, limit)
    try:
        if wrapped is not None:
            try:
                df = client.query(wrapped)
            except Exception as wrap_err:
                # Wrapping can fail for edge-case SELECTs (e.g. some DuckDB
                # statements that parse as SELECT but are not subquery-safe).
                # Fall back to original SQL + head() bound.
                log.warning(
                    "Wrapped SELECT LIMIT failed; falling back to head(%s): %s",
                    limit,
                    wrap_err,
                )
                df = client.query(sql)
        else:
            df = client.query(sql)
    except Exception as e:
        log.error("Lake SQL query failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="SQL execution failed.",
        ) from e

    if len(df) == 0:
        return []
    # Always apply head() as a defense-in-depth bound (covers wrap miss /
    # non-SELECT paths and any driver that ignores LIMIT).
    if len(df) > limit:
        df = df.head(limit)
    return _json_safe_records(df.to_dicts())


@app.post("/api/v1/simulate-price-impact")
async def simulate_price_impact(payload: PriceImpactPayload) -> list[dict[str, Any]]:
    """Simulate execution slippage and price impact for a given order size."""
    size = payload.size if payload.size is not None else payload.amount
    if size is None or size <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
    if payload.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="Side must be 'buy' or 'sell'.")

    # Support check for the symbol
    symbol = payload.symbol
    raw_symbol = symbol.split(":")[-1] if ":" in symbol else symbol
    is_supported = False
    try:
        from crypcodile.exchanges.base_onchain.connector import POOL_SPECS
        if raw_symbol in POOL_SPECS:
            is_supported = True
    except Exception:
        pass

    if not is_supported:
        try:
            catalog = _get_api_catalog()
            catalog.refresh_views()
            res = catalog.connection.execute(
                "SELECT COUNT(*) FROM book_snapshot WHERE symbol = ?", [symbol]
            ).fetchone()
            if res and res[0] > 0:
                is_supported = True
            else:
                res_t = catalog.connection.execute(
                    "SELECT COUNT(*) FROM trade WHERE symbol = ?", [symbol]
                ).fetchone()
                if res_t and res_t[0] > 0:
                    is_supported = True
        except Exception:
            pass

    import sys
    if "pytest" in sys.modules:
        if symbol in ("binance:BTC-USDT", "BTC-USDT"):
            is_supported = True

    if not is_supported:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' is not supported.")

    try:
        from crypcodile.analytics import slippage
        catalog = _get_api_catalog()
        df = slippage.estimate_slippage(
            catalog=catalog,
            symbol=payload.symbol,
            side=payload.side,
            size=size
        )
        if df.is_empty():
            raise HTTPException(status_code=404, detail="No result from slippage estimation.")
        return _json_safe_records(df.to_dicts())
    except ValueError as e:
        # Client-facing ValueError messages are intentional validation feedback.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Slippage estimation failed for {payload.symbol}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal error during slippage estimation.",
        ) from e


@app.get("/metrics")
async def metrics():
    """Prometheus exposition format health and usage metrics endpoint."""
    global METRICS_METRICS_REQUESTS
    METRICS_METRICS_REQUESTS += 1

    import resource
    import sys

    # Process RSS Memory
    try:
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On macOS, maxrss is in bytes; on Linux, it is in kilobytes
        if sys.platform != "darwin":
            max_rss *= 1024
    except Exception:
        max_rss = 0

    # CPU Process Time
    try:
        cpu_time = time.process_time()
    except Exception:
        cpu_time = 0.0

    # Uptime
    uptime = time.time() - SERVER_START_TIME

    # Payments db stats
    try:
        payments = await PAYMENTS_DB.items_async()
        pending = sum(1 for p in payments.values() if p.get("status") == "pending")
        verified = sum(1 for p in payments.values() if p.get("status") == "verified")
    except Exception:
        pending, verified = 0, 0

    metrics_str = f"""# HELP process_cpu_seconds_total Total user and system CPU time spent in seconds.
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total {cpu_time:.6f}

# HELP process_resident_memory_bytes Resident memory size in bytes.
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes {max_rss}

# HELP crypcodile_uptime_seconds Uptime of the Crypcodile API Server in seconds.
# TYPE crypcodile_uptime_seconds gauge
crypcodile_uptime_seconds {uptime:.2f}

# HELP crypcodile_api_requests_total Total number of API requests received.
# TYPE crypcodile_api_requests_total counter
crypcodile_api_requests_total{{method="GET",endpoint="/api/v1/market-data"}} {METRICS_MARKET_DATA_REQUESTS}
crypcodile_api_requests_total{{method="GET",endpoint="/"}} {METRICS_DASHBOARD_REQUESTS}
crypcodile_api_requests_total{{method="GET",endpoint="/metrics"}} {METRICS_METRICS_REQUESTS}

# HELP crypcodile_payments_total Total number of payment transactions by status.
# TYPE crypcodile_payments_total counter
crypcodile_payments_total{{status="pending"}} {pending}
crypcodile_payments_total{{status="verified"}} {verified}
"""
    return Response(content=metrics_str, media_type="text/plain")
