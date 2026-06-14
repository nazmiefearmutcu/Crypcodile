import asyncio
import os
import subprocess
import time
import socket
import pytest
from typing import AsyncGenerator, Generator
import aiohttp
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from mock_rpc_server import start_mock_server

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

@pytest.fixture(scope="function")
async def mock_rpc() -> AsyncGenerator[tuple[str, int], None]:
    # Start Mock RPC server on dynamic port
    port = get_free_port()
    runner, actual_port = await start_mock_server(host="127.0.0.1", port=port)
    rpc_url = f"http://127.0.0.1:{actual_port}"
    
    yield rpc_url, actual_port
    
    await runner.cleanup()

@pytest.fixture(scope="function")
def api_server(mock_rpc, tmp_path) -> Generator[str, None, None]:
    rpc_url, _ = mock_rpc
    port = get_free_port()
    
    # Isolate the payment DB file for each test function
    payments_file = tmp_path / "payments_db.json"
    
    # Run API server subprocess overriding BASE_RPC_URL and setting PYTHONPATH
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")
    env["PAYMENTS_FILE"] = str(payments_file)
    
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "crypcodile.api_server:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for FastAPI to start
    start_time = time.time()
    api_url = f"http://127.0.0.1:{port}"
    
    while time.time() - start_time < 5.0:
        if proc.poll() is not None:
            # Server crashed!
            raise RuntimeError("API server failed to start.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("API server did not start in time.")
    
    yield api_url
    
    proc.terminate()
    proc.wait()

@pytest.fixture(scope="function")
def mcp_server_client(mock_rpc) -> Generator[subprocess.Popen, None, None]:
    rpc_url, _ = mock_rpc
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")
    
    # Run MCP server subprocess (over stdin/stdout) using the cli entrypoint
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "crypcodile.cli", "mcp"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    )
    
    # Verify it doesn't crash immediately
    time.sleep(0.5)
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"MCP server failed to start. Stderr: {stderr}")
        
    yield proc
    
    proc.terminate()
    proc.wait()


@pytest.fixture(autouse=True)
async def clear_mock_rpc_state(mock_rpc):
    rpc_url, _ = mock_rpc
    # Clear and reset state of mock RPC server between tests
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{rpc_url}/control/reset") as resp:
                await resp.text()
        except Exception:
            pass
