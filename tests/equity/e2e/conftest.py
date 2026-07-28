"""Fixtures for the equity tier tests: a mock Base node, the API server, the MCP server.

The two server fixtures used to spawn ``crocodile.equity.legacy.api_server`` and
``crocodile.equity.legacy.mcp_server``. Both were forks of the crypto pair and both are
deleted; there is one API server (:func:`crocodile.surfaces.server.build_server`) and one
MCP server (``crocodile mcp``, i.e. :func:`crocodile.surfaces.stdio.serve_stdio`) now, and
these launch those. ``tests/e2e/conftest.py`` launches the same two the same way.

``BASE_RPC_URL`` is still handed to both subprocesses even though neither server opens an
RPC socket on its own account: the ``onchain-price`` and ``base-market-data`` capabilities
do, and pointing them at the mock node is what keeps a tools/call off Base mainnet.
"""

import os
import sys
import tempfile
from pathlib import Path

# Prevent flock deadlocks on the default shared IPC file. Written to the system
# temp dir rather than next to this file: the session-scoped cleanup below only
# removes the path this process set, so every subprocess-created variant was
# left behind in the repository, where the next commit would have swept it in.
os.environ["CUSTOM_POOLS_IPC_FILE"] = str(
    Path(tempfile.gettempdir()) / f".crocodile_test_custom_pools_ipc_{os.getpid()}.json"
)

import socket
import subprocess
import time
from collections.abc import AsyncGenerator, Generator

import aiohttp
import pytest

# Fully qualified — see the note in tests/e2e/conftest.py. This tier's mock
# server differs from the crypto tier's, and a bare import resolved to
# whichever was loaded first.
from tests.equity.e2e.mock_rpc_server import start_mock_server

_SRC = Path(__file__).resolve().parents[3] / "src"
"""This checkout's ``src``, for the subprocesses' ``PYTHONPATH``.

Derived from this file rather than from ``os.path.abspath("src")``: the environment's
editable install resolves ``crocodile`` to a *different* checkout, so a subprocess that
inherits the wrong path silently tests somebody else's tree, and the old spelling only
happened to be right because pytest was being run from the repository root.
"""


def _subprocess_env(rpc_url: str, lake: Path) -> dict[str, str]:
    """Environment for a server subprocess: this tree, a real lake, the mock node."""
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + os.environ.get("PYTHONPATH", "")
    # `Settings.data_dir` defaults to a relative "data" that need not exist, and
    # /api/v1/health reports `lake_unavailable` when it cannot be opened. Point it at a
    # real empty directory so the probes answer for the reason they are meant to.
    env["CROCODILE_DATA_DIR"] = str(lake)
    return env


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="function")
async def mock_rpc() -> AsyncGenerator[tuple[str, int], None]:
    # Start Mock RPC server on dynamic port (passing 0 allows OS to select a free port atomically)
    runner, actual_port = await start_mock_server(host="127.0.0.1", port=0)
    rpc_url = f"http://127.0.0.1:{actual_port}"

    yield rpc_url, actual_port

    await runner.cleanup()


@pytest.fixture(scope="function")
def api_server(mock_rpc: tuple[str, int], tmp_path: Path) -> Generator[str, None, None]:
    rpc_url, _ = mock_rpc

    max_attempts = 5
    for attempt in range(max_attempts):
        port = get_free_port()

        # Isolate the payment DB file for each test function
        payments_file = tmp_path / f"payments_db_{attempt}.json"
        lake_dir = tmp_path / f"lake_{attempt}"
        lake_dir.mkdir(parents=True, exist_ok=True)

        env = _subprocess_env(rpc_url, lake_dir)
        env["PAYMENTS_FILE"] = str(payments_file)
        # `payments_path()` only falls back to a temp file when pytest is imported, which it
        # is not in a subprocess; and `_allow_simulation()` reads this and nothing else.
        env["ALLOW_SIMULATION"] = "true"

        # The fork spawned `/Users/nazmi/Desktop/Stockodile/.venv/bin/stockodile_runner.py`,
        # an absolute path into a checkout that no longer exists — so every test
        # behind this fixture could only ever have passed on one machine, in one
        # directory. `build_server` is a factory rather than a module-level app, so
        # uvicorn is told so rather than the app being constructed in a `-c` string.
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "crocodile.surfaces.server:build_server",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for FastAPI to start
        start_time = time.time()
        api_url = f"http://127.0.0.1:{port}"
        success = False

        while time.time() - start_time < 45.0:
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
def mcp_server_client(
    mock_rpc: tuple[str, int], tmp_path: Path
) -> Generator[subprocess.Popen[str], None, None]:
    rpc_url, _ = mock_rpc
    lake_dir = tmp_path / "mcp_lake"
    lake_dir.mkdir(parents=True, exist_ok=True)
    env = _subprocess_env(rpc_url, lake_dir)

    # Run the MCP server over stdin/stdout through the console-script entry point, which is
    # how an agent client starts it. `crocodile mcp` is `surfaces.operate.mcp`, which is
    # `asyncio.run(serve_stdio())` plus the banner it writes to stderr.
    proc = subprocess.Popen(
        [sys.executable, "-m", "crocodile.surfaces.entrypoint", "mcp"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    # Verify it doesn't crash immediately
    time.sleep(0.5)
    if proc.poll() is not None:
        _stdout, stderr = proc.communicate()
        raise RuntimeError(f"MCP server failed to start. Stderr: {stderr}")

    yield proc

    proc.terminate()
    proc.wait()


@pytest.fixture(autouse=True)
async def clear_mock_rpc_state(mock_rpc: tuple[str, int]) -> None:
    rpc_url, _ = mock_rpc
    # Clear and reset state of mock RPC server between tests
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{rpc_url}/control/reset") as resp:
                await resp.text()
        except Exception:
            pass


def is_localhost_blocked() -> bool:
    return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    test_path = getattr(item, "path", None) or getattr(item, "fspath", None)
    if test_path and "tests/e2e" in str(test_path):
        if is_localhost_blocked():
            pytest.skip("Localhost port binding is blocked.")


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_ipc_file() -> Generator[None, None, None]:
    yield
    ipc_file = os.environ.get("CUSTOM_POOLS_IPC_FILE")
    if ipc_file:
        for path_str in [ipc_file, ipc_file + ".lock", ipc_file + ".tmp"]:
            try:
                os.remove(path_str)
            except Exception:
                pass
