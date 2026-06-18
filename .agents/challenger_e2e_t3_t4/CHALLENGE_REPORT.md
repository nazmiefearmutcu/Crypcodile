# E2E Tier 3 & Tier 4 E2E Challenge Report

## Challenge Summary
* **Overall risk assessment**: LOW

All 11 target test cases have been verified to execute and pass cleanly under local mocking of Base mainnet JSON-RPC node calls, REST APIs, and MCP server endpoints. One minor bug in the example script dry-run mock definitions was identified and patched. One assertion discrepancy regarding decimal scaling has been updated to reflect correct mathematics.

---

## Challenges

### [Low] Challenge 1: example dry-run mock AsyncWeb3 incompatibility
* **Assumption challenged**: That the mock objects representing `AsyncWeb3` in dry run scripts can return plain non-awaitable integer literals.
* **Attack scenario/Failure mode**: Evaluating `await w3.eth.block_number` raises `TypeError: object int can't be used in 'await' expression`.
* **Blast radius**: The dry run command (`uv run python examples/collect_base_onchain.py --dry-run`) is completely broken and crashes.
* **Mitigation**: Introduce a custom `AwaitableInt` class that inherits from `int` and defines a standard `__await__` method yielding the integer value asynchronously.

### [Low] Challenge 2: Asset Price Decimal Scaling Assertion Mismatch
* **Assumption challenged**: That checking prices via MCP for cbBTC-USDC will return `1.0` when the `sqrtPriceX96` is set to `2**96`.
* **Attack scenario/Failure mode**: cbBTC has 8 decimals, while USDC has 6. The normalizer scales token ratios by $10^{\text{decimals0} - \text{decimals1}}$. Thus, a base price ratio of `1.0` is scaled by $10^{8-6} = 100$. The returned price is `100.0`. The test assertion `assert price == 1.0` fails.
* **Blast radius**: The test suite reports spurious failures despite correct underlying calculations.
* **Mitigation**: Updated E2E test assertion to match scaled price `100.0`.

---

## Stress Test Results

* **Scenario 1**: Log range pagination under intermittent 429 rate limit errors.
  * *Expected Behavior*: Transport divides queries into <= 500 block chunks, sleeps exponentially during 429s, and resumes.
  * *Actual Behavior*: PASS (6 getLogs calls verified with intermittent errors resolved successfully).
  
* **Scenario 2**: x402 Micropayments under fast block production.
  * *Expected Behavior*: API server fetches transaction receipt and allows access when valid payment signatures are provided even across progressing block heights.
  * *Actual Behavior*: PASS.
  
* **Scenario 3**: Multi-pool concurrent ingestion under stress.
  * *Expected Behavior*: 4 distinct DEX pools query log and state updates concurrently without blocking or data race issues.
  * *Actual Behavior*: PASS.

---

## Unchallenged Areas
* **Mainnet RPC nodes**: Mainnet RPC node endpoints are not called directly in these tests due to code-only/offline environment constraints.
