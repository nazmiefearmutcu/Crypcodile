## 2026-06-14T14:06:04Z
You are a teamwork_preview_explorer.
Your role: Codebase Researcher
Your working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1
Please perform the following tasks:
1. Initialize your progress.md under your working directory.
2. Run the existing test suite (using `uv run pytest`) and document the output in your handoff.
3. Investigate the current implementation of `base_onchain` connector in `src/crypcodile/exchanges/base_onchain/connector.py` and `normalize.py`. Identify any missing parts or bugs.
4. Inspect other exchange connectors (such as binance, coinbase, or deribit) and their tests to see how they mock RPC or network calls and what structure they follow.
5. Search for existing test fixtures or files that we can use for mocking or verify what data we need to mock.
6. Check pyproject.toml and README.md structure.
7. Write an exploration report (analysis.md) and handoff.md in your working directory.
8. Report back to the parent orchestrator with your findings.
