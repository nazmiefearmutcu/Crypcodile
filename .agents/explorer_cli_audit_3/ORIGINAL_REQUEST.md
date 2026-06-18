## 2026-06-18T17:45:39Z
Your working directory is /Users/nazmi/Crypcodile/.agents/explorer_cli_audit_3.
Your role: teamwork_preview_explorer.
Your task:
1. Scan and audit all CLI terminal commands defined in src/crypcodile/cli.py (query, catalog, export, replay, collect, funding-apr, basis, iv-surface, term-structure, mcp, update, shell).
2. Check for:
   - Structural bugs, syntax errors, or unhandled TODOs.
   - Input validation errors (e.g., missing options, invalid formats).
   - Unhandled exceptions (e.g., when local data lake is empty, file not found, schema mismatches, network/provider issues).
   - Interactive prompt safety (e.g. prompt_symbol, time ranges, custom autocompletes, and handling when stdin is non-interactive or input is invalid).
3. Read the existing CLI tests in tests/test_cli.py and other test files to see how CLI commands are invoked and validated.
4. Document all findings in handoff.md in your working directory. For each finding, list the command name, description of the issue, file and line number reference, and recommendation for fix.
5. Message the parent agent when complete.
