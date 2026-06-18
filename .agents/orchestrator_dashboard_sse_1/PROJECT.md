# Project: Crypcodile Dashboard UI/UX and SSE Error Handling Fixes

## Architecture
The Crypcodile API Portal Dashboard is a Node.js Express server (`server.js`) serving static files from `public/`.
The frontend (`public/index.html`, `public/css/style.css`, and `public/js/app.js`) connects to `/api/events` via a Server-Sent Events (SSE) connection to receive real-time price ticks and payment verification updates.
The transaction debugger UI uses these SSE updates to transition through signature recovery, sender matching, and block confirmation steps when access is requested or simulated.

```
       [Web Browser (Frontend)] <==== (SSE Event Stream /api/events) ==== [Express Server (server.js)]
      /           |            \
     /            |             \
[index.html]  [style.css]     [app.js]
 (UI layout)  (Visual Style)  (SSE stream handler, wallet connection, pricing simulation, debugger)
```

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Setup | Explore codebase, review requirements, setup plans | None | DONE |
| 2 | UI/UX Enhancement | Enhance visual styling of index.html & style.css to be premium/beautiful, preserving key E2E tokens | M1 | DONE |
| 3 | SSE Error Fallback | Implement app.js timeout and connection failure handling, client-side pricing simulation fallback, and reconnect button | M1 | DONE |
| 4 | Block Confirmation Fix | Fix payment_id payload in server.js block_confirmation event and app.js payment_received state handler | M1 | DONE |
| 5 | E2E & Forensic Verification | Run all Node.js tests, verify simulation flow debugger steps turn green, run python tests | M2, M3, M4 | DONE |

## Interface Contracts
### server.js ===> app.js (SSE Payload)
- **Type**: `verification`
- **Stage**: `block_confirmation`
- **Data**: Must contain both `confirmations` (number) and `payment_id` (string).
  - Example: `data: { confirmations: 12, payment_id: "uuid-xxxx" }`

### app.js ===> server.js (Micropayment flow verification)
- Header `Payment-Id` must match the handshake ID.
- Header `Payment-Sender` must match the wallet address.
- Header `Payment-Signature` must contain the cryptographic signature.

## Code Layout
- `src/crypcodile/api_portal/public/index.html`: Dashboard structure and layout.
- `src/crypcodile/api_portal/public/css/style.css`: UI/UX styling.
- `src/crypcodile/api_portal/public/js/app.js`: Client-side logic, SSE connection, pricing simulation, wallet and payment flow handling.
- `src/crypcodile/api_portal/server.js`: Node.js backend. Handles the Express routes, payment mock DB, and SSE event broadcast.
- `src/crypcodile/api_portal/tests/e2e.test.js`: E2E test suite.
