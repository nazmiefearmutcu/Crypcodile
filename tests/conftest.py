import os
import pytest

@pytest.fixture(autouse=True, scope="function")
def configure_payments_env(tmp_path):
    temp_db = tmp_path / "payments_db.json"
    os.environ["PAYMENTS_FILE"] = str(temp_db)
    temp_ipc = tmp_path / "custom_pools_ipc.json"
    os.environ["CUSTOM_POOLS_IPC_FILE"] = str(temp_ipc)
    yield
    if "PAYMENTS_FILE" in os.environ:
        del os.environ["PAYMENTS_FILE"]
    if "CUSTOM_POOLS_IPC_FILE" in os.environ:
        del os.environ["CUSTOM_POOLS_IPC_FILE"]
