## 2026-06-14T15:48:32Z
You are the E2E Testing Explorer.
Your working directory is /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra.
Your task is to explore the Crypcodile codebase and design the Mock RPC Server and the E2E Test Harness architecture.
Specifically:
1. Examine the connector, normalizer, api_server, and mcp_server code to find all RPC/Web3 calls. What methods do they call? What parameters? What exact logs, receipts, and contract outputs are requested?
2. Design a Mock RPC Server using standard Python libraries (like aiohttp, fastapi, or a simple HTTP server running in a thread/process) that will intercept and handle these JSON-RPC requests.
3. Recommend how to structure tests/e2e/ and how to write test cases for Tiers 1-4.
4. Output your analysis and design recommendations in a detailed report at /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra/analysis.md.
5. Notify the orchestrator when your analysis is ready.
