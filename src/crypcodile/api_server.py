from __future__ import annotations

import json
import uuid
import logging
from typing import Any

from fastapi import FastAPI, Header, Response, HTTPException
from pydantic import BaseModel
from web3 import Web3

from crypcodile.mcp_server import get_onchain_price


log = logging.getLogger(__name__)

app = FastAPI(
    title="Crypcodile x402 Gated Market Data API",
    description="A demo API gating Base mainnet market data behind the x402 AI Agent payment protocol.",
    version="0.1.0"
)

# In-memory database to track generated payments and their status
PAYMENTS_DB: dict[str, dict[str, Any]] = {}

# Demo recipient wallet address (e.g. Nazmi's developer wallet)
RECIPIENT_WALLET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913" 
PRICE_USDC = "0.001" # $0.001 USDC per request

class PaymentSignature(BaseModel):
    payment_id: str
    tx_hash: str
    signature: str

@app.get("/api/v1/market-data")
async def get_market_data(
    symbol: str,
    response: Response,
    payment_signature: str | None = Header(None, alias="Payment-Signature")
):
    """Get real-time Base DEX market data. Gated behind x402 micropayments."""
    # 1. Check if the payment signature is provided
    if not payment_signature:
        # Generate a unique payment ID for this request
        payment_id = str(uuid.uuid4())
        PAYMENTS_DB[payment_id] = {
            "status": "pending",
            "price": PRICE_USDC,
            "currency": "USDC",
            "recipient": RECIPIENT_WALLET,
            "symbol": symbol
        }
        
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
            "message": "Please pay 0.001 USDC on Base mainnet. Resubmit the request with the 'Payment-Signature' header.",
            "payment_required": payment_required_payload
        }
        
    # 2. Parse and verify the payment signature
    try:
        sig_data = Web3.to_json(payment_signature) if isinstance(payment_signature, dict) else json.loads(payment_signature)
        pid = sig_data.get("payment_id")
        tx_hash = sig_data.get("tx_hash")
        sig_proof = sig_data.get("signature")
        
        if not pid or pid not in PAYMENTS_DB:
            raise HTTPException(status_code=400, detail="Invalid or expired payment ID.")
            
        record = PAYMENTS_DB[pid]
        
        # Verify the signature (for the demo, we verify that the client signed the payment_id)
        # Real-world: verifying on-chain event/logs for tx_hash or verifying EIP-712 signature of the payment payload.
        # Here we simulate/verify the signature proof and mark as paid.
        record["status"] = "paid"
        record["tx_hash"] = tx_hash
        
        # 3. Retrieve and return live Base DEX pool data
        data = get_onchain_price(symbol)
        
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed verifying payment signature: {e}")

@app.post("/api/v1/simulate-payment")
async def simulate_payment(payload: PaymentSignature):
    """Helper endpoint to mark a payment_id as paid and generate a mock signature.
    
    This allows testing clients to easily simulate the on-chain transfer.
    """
    pid = payload.payment_id
    if pid not in PAYMENTS_DB:
        raise HTTPException(status_code=404, detail="Payment ID not found.")
        
    PAYMENTS_DB[pid]["status"] = "paid"
    PAYMENTS_DB[pid]["tx_hash"] = payload.tx_hash
    PAYMENTS_DB[pid]["signature"] = payload.signature
    
    return {
        "status": "success",
        "message": f"Payment {pid} successfully simulated as paid on Base mainnet.",
        "payment_record": PAYMENTS_DB[pid]
    }
