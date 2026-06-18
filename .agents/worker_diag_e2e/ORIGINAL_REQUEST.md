## 2026-06-14T16:12:06Z
<USER_REQUEST>
You are a worker tasked with investigating the failure of the integration test `test_smoke_e2e.py::test_api_server_payment_flow`.
Your working directory is `/Users/nazmi/Crypcodile/.agents/worker_diag_e2e`.
Please run the failing test using `uv run pytest -s tests/e2e/test_smoke_e2e.py` and inspect the output.
If the API server output contains details about the HTTP 500 error, capture them.
If needed, write a small Python script to invoke `get_onchain_price` directly with the mock RPC server URL during a simulated test run, printing any exception stack trace.
Report the root cause and suggestions/remedies in a handoff report at `/Users/nazmi/Crypcodile/.agents/worker_diag_e2e/handoff.md`.
Then send a message back to your parent.
</USER_REQUEST>
