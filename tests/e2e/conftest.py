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
# Fully qualified, not a sys.path insert plus a bare name: the equity e2e tier
# has its own, DIFFERENT mock_rpc_server.py. Under a bare import the first one
# into sys.modules wins and the other tier silently runs against the wrong
# mock — green, and testing something else.
from tests.e2e.mock_rpc_server import start_mock_server

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

        # The lake the server reads. `build_server` resolves it through Settings, whose
        # data_dir defaults to a relative "data" that may not exist; point it at a real
        # empty directory so /api/v1/health and /api/v1/ready answer instead of reporting
        # lake_unavailable.
        lake_dir = tmp_path / f"lake_{attempt}"
        lake_dir.mkdir(parents=True, exist_ok=True)

        # Run API server subprocess overriding BASE_RPC_URL and setting PYTHONPATH
        env = os.environ.copy()
        env["BASE_RPC_URL"] = rpc_url
        env["PYTHONPATH"] = os.path.abspath("src")
        env["PAYMENTS_FILE"] = str(payments_file)
        env["ALLOW_SIMULATION"] = "true"
        env["CROCODILE_DATA_DIR"] = str(lake_dir)

        # `build_server` is a factory, not a module-level app, so uvicorn needs --factory.
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "crocodile.surfaces.server:build_server", "--factory", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for FastAPI to start
        start_time = time.time()
        api_url = f"http://127.0.0.1:{port}"
        success = False
        
        while time.time() - start_time < 60.0:
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
                stdout_data, stderr_data = proc.communicate(timeout=1.0)
                print(f"\nAPI server startup failed on port {port}!\nSTDOUT:\n{stdout_data}\nSTDERR:\n{stderr_data}\n", file=sys.stderr)
            except Exception as e:
                print(f"Failed to read subprocess output: {e}", file=sys.stderr)
            try:
                proc.terminate()
                proc.wait()
            except Exception:
                pass
    else:
        raise RuntimeError("API server failed to start on any ports after multiple retries.")

@pytest.fixture(scope="function")
def mcp_server_client(mock_rpc, tmp_path) -> Generator[subprocess.Popen, None, None]:
    rpc_url, _ = mock_rpc
    lake_dir = tmp_path / "mcp_lake"
    lake_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")
    # Tools resolve their lake through Settings; give the subprocess a real empty one so a
    # tools/call answers with rows instead of failing to open a relative "data" directory.
    env["CROCODILE_DATA_DIR"] = str(lake_dir)

    # Run MCP server subprocess (over stdin/stdout) using the console-script entrypoint
    proc = subprocess.Popen(
        [sys.executable, "-m", "crocodile.surfaces.entrypoint", "mcp"],
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

    # Reset BaseOnchainTransport POOL_SPECS and TOKENS to defaults
    try:
        from crocodile.crypto.exchanges.base_onchain.connector import reset_to_defaults
        reset_to_defaults()
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

