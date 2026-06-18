## 2026-06-14T14:11:57Z
You are a teamwork_preview_auditor.
Your role: Forensic Auditor
Your working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_auditor_1

Please perform the following tasks:
1. Initialize your progress.md under your working directory.
2. Perform integrity forensics on the repository. Check for:
   - Hardcoded test results, expected outputs, or verification strings in source code.
   - Dummy or facade implementations that produce correct-looking outputs without genuine logic.
   - Circumvention of the intended task.
3. Review `src/crypcodile/exchanges/base_onchain/connector.py` and `normalize.py` and the newly implemented tests.
4. Run checks to verify if there are any integrity violations.
5. Document all findings in an audit report (audit.md) and handoff.md under your working directory.
6. State your final verdict: CLEAN or VIOLATION.
