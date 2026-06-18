## 2026-06-18T17:56:20Z
Please implement the UI/UX visual enhancements and SSE error handling / simulation fixes for Crypcodile.

Target Files:
1. `src/crypcodile/api_portal/public/index.html`
2. `src/crypcodile/api_portal/public/css/style.css`
3. `src/crypcodile/api_portal/public/js/app.js`
4. `src/crypcodile/api_portal/server.js`

Specific Instructions:
R1. UI/UX Visual Enhancement:
- Enhance the visual style of `public/index.html` and `public/css/style.css` to look extremely modern, beautiful, and premium.
- Use curated harmonious color palettes (e.g., dark indigo, slate, cyan, emerald), glowing backdrops, and subtle hover animations on cards/buttons.
- Ensure standard elements (e.g. `bg-slate-950`, `text-slate-100`, `<main class="flex-1`, `h-full flex flex-col font-sans` in HTML, and `Crypcodile` copyright token in `style.css`) are preserved to avoid breaking E2E tests. Do not delete them.

R2. SSE Error Handling & Fallback:
- In `app.js`'s `connectSSE`, if the SSE stream fails to connect or times out:
  1. Hide the `chart-loading-overlay` (add the `hidden` class to it, instead of keeping it visible/blocking).
  2. Set sseStatusText.textContent to indicate the error (e.g. `Disconnected: ${err.message}`).
  3. Ensure the local client-side pricing simulation fallback (already active in app.js via `startPriceChartSimulation()`) is allowed to update the chart and live price metric text when offline, without layout block.
- Wire up a click listener for the reconnect button (`dom.sseReconnectBtn` or `#sse-reconnect-btn`) to call `connectSSE()` to gracefully retry connecting.

R2. Block Confirmation checks:
- In `server.js`'s `app.get('/api/gated-data')` route, ensure the verification SSE event broadcast payload for `block_confirmation` includes the correct `payment_id`:
  `data: { confirmations: 12, payment_id: paymentIdHeader }`
- Also, for consistency and correctness, ensure the `sender_matching` verification event broadcast payload includes `payment_id`:
  `data: { recovered: recoveredAddress, expected: paymentSenderHeader, payment_id: paymentIdHeader }`
- In `app.js`'s `onmessage` event handler:
  In the `case 'payment':` block, check `if (payload.stage === 'payment_received')` FIRST and call `setStepStatus('unlocked', 'success', ...)` BEFORE checking `if (payload.data.txHash)`. This prevents `syncDebuggerState` from overriding `confirmation` back to `pending` and resetting the debugger steps.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Verification:
- In `src/crypcodile/api_portal`, run all tests via `npm test` or `node tests/e2e.test.js` to verify they all pass.
- Write down what you changed and test execution results. Let me know when you are done.
