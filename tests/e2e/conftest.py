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
    # Start Mock RPC server on dynamic port (passing 0 allows the OS to select a free port atomically)
    runner, actual_port = await start_mock_server(host="127.0.0.1", port=0)
    rpc_url = f"http://127.0.0.1:{actual_port}"
    
    yield rpc_url, actual_port
    
    await runner.cleanup()

@pytest.fixture(scope="function")
def api_server(mock_rpc, tmp_path) -> Generator[str, None, None]:
    rpc_url, _ = mock_rpc
    
    max_attempts = 5
    for attempt in range(max_attempts):
        port = get_free_port()
        
        # Isolate the payment DB file for each test function
        payments_file = tmp_path / f"payments_db_{attempt}.json"
        
        # Run API server subprocess overriding BASE_RPC_URL and setting PYTHONPATH
        env = os.environ.copy()
        env["BASE_RPC_URL"] = rpc_url
        env["PYTHONPATH"] = os.path.abspath("src")
        env["PAYMENTS_FILE"] = str(payments_file)
        env["ALLOW_SIMULATION"] = "true"
        
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "crypcodile.api_server:app", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for FastAPI to start
        start_time = time.time()
        api_url = f"http://127.0.0.1:{port}"
        success = False
        
        while time.time() - start_time < 5.0:
            if proc.poll() is not None:
                # Server crashed (e.g. port collision), break to try next port
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    success = True
                    break
            except OSError:
                time.sleep(0.1)
        
        if success:
            yield api_url
            proc.terminate()
            proc.wait()
            return
        else:
            try:
                proc.terminate()
                proc.wait()
            except Exception:
                pass
    else:
        raise RuntimeError("API server failed to start on any ports after multiple retries.")

@pytest.fixture(scope="function")
def mcp_server_client(mock_rpc) -> Generator[subprocess.Popen, None, None]:
    rpc_url, _ = mock_rpc
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")
    
    # Run MCP server subprocess (over stdin/stdout) using the cli entrypoint
    proc = subprocess.Popen(
        [sys.executable, "-m", "crypcodile.cli", "mcp"],
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


def is_localhost_blocked() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.listen(1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(0.1)
                client.connect(("127.0.0.1", port))
            return False
    except Exception:
        return True


def pytest_runtest_setup(item):
    test_path = getattr(item, "path", None) or getattr(item, "fspath", None)
    if test_path and "tests/e2e" in str(test_path):
        if is_localhost_blocked():
            pytest.skip("Localhost port binding is blocked.")

