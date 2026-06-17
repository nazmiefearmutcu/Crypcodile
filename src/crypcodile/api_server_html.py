def get_dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypcodile x402 Micropayments Gated API - Dashboard</title>
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <!-- FontAwesome for Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <!-- Ethers.js for client-side signing simulation -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/ethers/6.7.0/ethers.umd.min.js"></script>
    <style>
        :root {
            --bg-base: #070a13;
            --bg-surface: #0f1524;
            --bg-card: #151d30;
            --border-color: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(16, 185, 129, 0.3);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-green: #10b981;
            --accent-green-glow: rgba(16, 185, 129, 0.15);
            --accent-blue: #3b82f6;
            --accent-blue-glow: rgba(59, 130, 246, 0.15);
            --accent-amber: #f59e0b;
            --accent-amber-glow: rgba(245, 158, 11, 0.15);
            --accent-red: #ef4444;
            --accent-red-glow: rgba(239, 68, 68, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.5;
            padding: 2rem 1rem;
            min-height: 100vh;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        /* Header section */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 2rem;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .brand i {
            font-size: 2.25rem;
            color: var(--accent-green);
            text-shadow: 0 0 15px var(--accent-green-glow);
        }

        .brand-text h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 1.75rem;
            font-weight: 800;
            letter-spacing: -0.025em;
            background: linear-gradient(135deg, #f8fafc 30%, #10b981 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand-text p {
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        .header-links {
            display: flex;
            gap: 1rem;
            align-items: center;
        }

        .btn-link {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background-color: var(--bg-surface);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.5rem 1rem;
            border-radius: 0.375rem;
            text-decoration: none;
            font-size: 0.875rem;
            font-weight: 500;
            transition: all 0.2s ease;
        }

        .btn-link:hover {
            border-color: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green-glow);
        }

        /* Responsive Columns */
        .dashboard-grid {
            display: grid;
            grid-template-columns: 1fr 1.5fr;
            gap: 2rem;
            margin-bottom: 2rem;
        }

        @media (max-width: 900px) {
            .dashboard-grid {
                grid-template-columns: 1fr;
            }
        }

        /* Glassmorphic Panel Cards */
        .panel {
            background-color: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 1.5rem;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
            position: relative;
            overflow: hidden;
        }

        .panel-title {
            font-family: 'Outfit', sans-serif;
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.75rem;
        }

        .panel-title i {
            color: var(--accent-green);
        }

        /* Metadata & Status indicators */
        .meta-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .meta-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.925rem;
        }

        .meta-label {
            color: var(--text-secondary);
        }

        .meta-value {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 500;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.375rem;
            color: var(--accent-green);
            font-weight: 600;
        }

        .status-pulse {
            width: 8px;
            height: 8px;
            background-color: var(--accent-green);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--accent-green);
            animation: pulse-ring 1.5s infinite;
        }

        @keyframes pulse-ring {
            0% { transform: scale(0.95); opacity: 1; }
            50% { transform: scale(1.3); opacity: 0.5; }
            100% { transform: scale(0.95); opacity: 1; }
        }

        /* Stepper Flow Cards */
        .stepper {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            position: relative;
            margin-top: 1rem;
        }

        .stepper::before {
            content: '';
            position: absolute;
            left: 17px;
            top: 10px;
            bottom: 10px;
            width: 2px;
            background-color: var(--border-color);
            z-index: 1;
        }

        .step-info {
            display: flex;
            gap: 1rem;
            position: relative;
            z-index: 2;
        }

        .step-icon {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background-color: var(--bg-card);
            border: 2px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
            font-weight: 700;
            color: var(--text-secondary);
            flex-shrink: 0;
            transition: all 0.3s ease;
        }

        .step-info.active .step-icon {
            border-color: var(--accent-green);
            color: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green-glow);
            background-color: var(--bg-surface);
        }

        .step-desc h4 {
            font-family: 'Outfit', sans-serif;
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 0.25rem;
        }

        .step-desc p {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        /* Interactive Simulator Styles */
        .sim-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            padding: 1.25rem;
            margin-bottom: 1.5rem;
            transition: all 0.3s ease;
        }

        .sim-card.disabled {
            opacity: 0.4;
            pointer-events: none;
            filter: grayscale(100%);
        }

        .sim-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }

        .sim-title {
            font-family: 'Outfit', sans-serif;
            font-size: 1.1rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .sim-title i {
            color: var(--accent-blue);
        }

        .sim-step-badge {
            background-color: var(--border-color);
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-secondary);
        }

        .sim-card.active-step {
            border-color: var(--accent-blue);
            box-shadow: 0 0 15px rgba(59, 130, 246, 0.1);
        }

        .sim-card.active-step .sim-step-badge {
            background-color: var(--accent-blue);
            color: #fff;
        }

        .sim-card.success-step {
            border-color: var(--accent-green);
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.1);
        }

        .sim-card.success-step .sim-step-badge {
            background-color: var(--accent-green);
            color: #fff;
        }

        /* Inputs & Controls */
        .form-group {
            margin-bottom: 1rem;
        }

        .form-label {
            display: block;
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 0.375rem;
        }

        .select-input {
            width: 100%;
            background-color: var(--bg-surface);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.6rem;
            border-radius: 0.375rem;
            font-family: inherit;
            font-size: 0.925rem;
            outline: none;
            transition: all 0.2s ease;
        }

        .select-input:focus {
            border-color: var(--accent-blue);
        }

        .btn {
            width: 100%;
            background-color: var(--accent-blue);
            color: #fff;
            border: none;
            padding: 0.65rem 1.25rem;
            border-radius: 0.375rem;
            font-weight: 600;
            font-size: 0.925rem;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }

        .btn:hover {
            opacity: 0.95;
            transform: translateY(-1px);
        }

        .btn:active {
            transform: translateY(0);
        }

        .btn-green {
            background-color: var(--accent-green);
        }

        .btn-amber {
            background-color: var(--accent-amber);
        }

        /* Console/Code blocks outputs */
        .output-box {
            background-color: #060912;
            border: 1px solid var(--border-color);
            border-radius: 0.375rem;
            padding: 0.75rem;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            max-height: 180px;
            overflow-y: auto;
            margin-top: 0.75rem;
            color: #a7f3d0;
            white-space: pre-wrap;
        }

        .output-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 0.75rem;
            margin-bottom: 0.25rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }

        .status-pill {
            padding: 0.125rem 0.5rem;
            border-radius: 0.25rem;
            font-weight: 600;
            font-size: 0.75rem;
        }

        .status-pill.red {
            background-color: var(--accent-red-glow);
            color: var(--accent-red);
            border: 1px solid rgba(239, 68, 68, 0.2);
        }

        .status-pill.green {
            background-color: var(--accent-green-glow);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }

        /* Data display grid/table */
        .market-data-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 0.75rem;
            font-size: 0.85rem;
        }

        .market-data-table th,
        .market-data-table td {
            padding: 0.5rem 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        .market-data-table th {
            color: var(--text-secondary);
            font-weight: 500;
        }

        .market-data-table td {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Payments DB ledger list */
        .payments-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
            margin-top: 1rem;
        }

        .payments-table th,
        .payments-table td {
            padding: 0.75rem 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        .payments-table th {
            color: var(--text-secondary);
            font-weight: 600;
        }

        .payments-table td {
            font-family: 'JetBrains Mono', monospace;
        }

        .payments-table tr:hover {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .badge-status {
            padding: 0.2rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }

        .badge-status.paid {
            background-color: var(--accent-green-glow);
            color: var(--accent-green);
        }

        .badge-status.pending {
            background-color: var(--accent-amber-glow);
            color: var(--accent-amber);
        }

        /* Help tip alert box */
        .help-alert {
            background-color: rgba(59, 130, 246, 0.05);
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-radius: 0.5rem;
            padding: 1rem;
            display: flex;
            gap: 0.75rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
            margin-bottom: 1.5rem;
        }

        .help-alert i {
            color: var(--accent-blue);
            font-size: 1.1rem;
            margin-top: 0.1rem;
        }

        .help-alert strong {
            color: var(--text-primary);
        }

    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="brand">
                <i class="fa-solid fa-dragon"></i>
                <div class="brand-text">
                    <h1>Crypcodile Gated API Portal</h1>
                    <p>Interactive Sandbox & Playground</p>
                </div>
            </div>
            <div class="header-links">
                <a href="/docs" class="btn-link" target="_blank">
                    <i class="fa-solid fa-book-open"></i> API Docs (Swagger)
                </a>
                <a href="https://github.com/nazmiefearmutcu/Crypcodile" class="btn-link" target="_blank">
                    <i class="fa-brands fa-github"></i> GitHub
                </a>
            </div>
        </header>

        <div class="help-alert">
            <i class="fa-solid fa-circle-info"></i>
            <div>
                <strong>x402 Micropayment Gated Protocol:</strong> This playground demonstrates how the Crypcodile API automatically requires and verifies decentralized micropayments on the <strong>Base Network</strong>. Walk through the 3 steps in the playground to trigger a <code>402 Payment Required</code>, simulate a cryptographic signature/transaction, and serve the final market data.
            </div>
        </div>

        <!-- Two column layout -->
        <div class="dashboard-grid">
            <!-- Left Column: Specs & Walkthrough -->
            <div style="display: flex; flex-direction: column; gap: 2rem;">
                <!-- Status specs -->
                <div class="panel">
                    <h3 class="panel-title">
                        <i class="fa-solid fa-server"></i> Server Metadata & Specs
                    </h3>
                    <ul class="meta-list">
                        <li class="meta-item">
                            <span class="meta-label">API Server Status</span>
                            <span class="meta-value">
                                <span class="status-badge">
                                    <span class="status-pulse"></span> Active / Online
                                </span>
                            </span>
                        </li>
                        <li class="meta-item">
                            <span class="meta-label">Protocol Mode</span>
                            <span class="meta-value" style="color: var(--accent-blue);">x402 Gated Agent API</span>
                        </li>
                        <li class="meta-item">
                            <span class="meta-label">Gated Price</span>
                            <span class="meta-value" style="color: var(--accent-green);">0.001 USDC / query</span>
                        </li>
                        <li class="meta-item">
                            <span class="meta-label">Settlement Network</span>
                            <span class="meta-value">Base Mainnet (Layer-2)</span>
                        </li>
                        <li class="meta-item">
                            <span class="meta-label">Recipient Wallet</span>
                            <span class="meta-value" style="font-size: 0.75rem;" id="spec-recipient">0x70997970C51812dc3A010C7d01b50e0d17dc79C8</span>
                        </li>
                        <li class="meta-item">
                            <span class="meta-label">Database File</span>
                            <span class="meta-value">.payments_db.json</span>
                        </li>
                    </ul>
                </div>

                <!-- Workflow diagram -->
                <div class="panel">
                    <h3 class="panel-title">
                        <i class="fa-solid fa-diagram-project"></i> Cryptographic Flow
                    </h3>
                    <div class="stepper">
                        <div class="step-info" id="flow-step-1">
                            <div class="step-icon">1</div>
                            <div class="step-desc">
                                <h4>Request & Reject (402)</h4>
                                <p>Client makes a query. Server rejects it with <code>402 Payment Required</code> and supplies a unique <code>payment_id</code>.</p>
                            </div>
                        </div>
                        <div class="step-info" id="flow-step-2">
                            <div class="step-icon">2</div>
                            <div class="step-desc">
                                <h4>On-Chain Transfer & Sign</h4>
                                <p>Client pays 0.001 USDC on Base mainnet. Client cryptographically signs the <code>payment_id</code> to prove wallet ownership.</p>
                            </div>
                        </div>
                        <div class="step-info" id="flow-step-3">
                            <div class="step-icon">3</div>
                            <div class="step-desc">
                                <h4>Verify & Deliver</h4>
                                <p>Client resubmits request with <code>Payment-Signature</code>. Server validates the signature, verifies the transaction, and returns data.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Column: Interactive Playground -->
            <div class="panel" style="padding-bottom: 2rem;">
                <h3 class="panel-title">
                    <i class="fa-solid fa-gamepad"></i> Interactive Playground Sandbox
                </h3>

                <!-- Step 1 Card -->
                <div class="sim-card active-step" id="card-step-1">
                    <div class="sim-header">
                        <span class="sim-title">
                            <i class="fa-solid fa-cloud-arrow-down"></i> Step 1: Initial Request (Unpaid)
                        </span>
                        <span class="sim-step-badge">STEP 1</span>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="symbol-select">Select Asset Symbol (Base Mainnet DEX)</label>
                        <select class="select-input" id="symbol-select">
                            <option value="cbBTC-USDC">cbBTC-USDC (Coinbase Wrapped BTC)</option>
                            <option value="AERO-USDC">AERO-USDC (Aerodrome Finance)</option>
                            <option value="WETH-USDC">WETH-USDC (Wrapped Ethereum)</option>
                            <option value="DEGEN-WETH">DEGEN-WETH (Degen Token)</option>
                            <option value="WELL-WETH">WELL-WETH (Moonwell Token)</option>
                        </select>
                    </div>
                    <button class="btn" id="btn-step-1" onclick="executeStep1()">
                        <i class="fa-solid fa-play"></i> Fetch Gated Market Data
                    </button>
                    <div id="step-1-details" style="display: none; margin-top: 1rem;">
                        <div class="output-header">
                            <span>HTTP Response Details</span>
                            <span class="status-pill red" id="step-1-status">402 Payment Required</span>
                        </div>
                        <div class="output-box" id="step-1-output"></div>
                    </div>
                </div>

                <!-- Step 2 Card -->
                <div class="sim-card disabled" id="card-step-2">
                    <div class="sim-header">
                        <span class="sim-title">
                            <i class="fa-solid fa-key"></i> Step 2: Payment Simulation & Signing
                        </span>
                        <span class="sim-step-badge">STEP 2</span>
                    </div>
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1rem;">
                        The client generates a temporary Ethereum wallet to cryptographically sign the <code>payment_id</code> and registers the payment.
                    </p>
                    <div style="background-color: var(--bg-base); padding: 0.75rem; border-radius: 0.375rem; font-size: 0.85rem; margin-bottom: 1rem; border: 1px solid var(--border-color);">
                        <div style="margin-bottom: 0.375rem;">
                            <span style="color: var(--text-secondary);">Simulated Wallet Address:</span>
                            <span id="wallet-address" style="font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--accent-blue); display: block; overflow-wrap: break-word;">Not generated</span>
                        </div>
                        <div>
                            <span style="color: var(--text-secondary);">Generated Mock Tx Hash:</span>
                            <span id="tx-hash" style="font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--text-primary); display: block; overflow-wrap: break-word;">Not generated</span>
                        </div>
                    </div>
                    <button class="btn btn-amber" id="btn-step-2" onclick="executeStep2()">
                        <i class="fa-solid fa-signature"></i> Simulate Payment & Sign Message
                    </button>
                    <div id="step-2-details" style="display: none; margin-top: 1rem;">
                        <div class="output-header">
                            <span>Simulation API Response</span>
                            <span class="status-pill green" id="step-2-status">200 OK</span>
                        </div>
                        <div class="output-box" id="step-2-output"></div>
                    </div>
                </div>

                <!-- Step 3 Card -->
                <div class="sim-card disabled" id="card-step-3">
                    <div class="sim-header">
                        <span class="sim-title">
                            <i class="fa-solid fa-unlock-keyhole"></i> Step 3: Access Gated Data
                        </span>
                        <span class="sim-step-badge">STEP 3</span>
                    </div>
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1rem;">
                        Resubmit request with the <code>Payment-Signature</code> header. The server verifies and serves the data.
                    </p>
                    <button class="btn btn-green" id="btn-step-3" onclick="executeStep3()">
                        <i class="fa-solid fa-circle-check"></i> Retrieve Gated Market Data
                    </button>
                    <div id="step-3-details" style="display: none; margin-top: 1rem;">
                        <div class="output-header">
                            <span>Market Data Result (200 OK)</span>
                            <span class="status-pill green">Success</span>
                        </div>
                        <div id="step-3-rendered" style="margin-top: 0.5rem; background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 0.375rem; padding: 0.5rem;">
                            <!-- Rendered Table -->
                        </div>
                        <div class="output-header" style="margin-top: 1rem;">
                            <span>Raw JSON Payload</span>
                        </div>
                        <div class="output-box" id="step-3-output" style="color: #60a5fa;"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Ledger logs bottom panel -->
        <div class="panel">
            <h3 class="panel-title">
                <i class="fa-solid fa-list-check"></i> Recent Simulated Payments Ledger
            </h3>
            <div style="overflow-x: auto;">
                <table class="payments-table">
                    <thead>
                        <tr>
                            <th>Payment ID</th>
                            <th>Status</th>
                            <th>Symbol</th>
                            <th>Amount</th>
                            <th>Sender Address</th>
                            <th>Transaction Hash</th>
                        </tr>
                    </thead>
                    <tbody id="payments-tbody">
                        <tr>
                            <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 2rem;">Loading payments...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- JavaScript logic -->
    <script>
        let currentPaymentId = "";
        let currentRecipient = "";
        let currentPrice = "";
        let currentSignature = "";
        let currentTxHash = "";
        let currentWallet = null;

        // Load payments on start
        window.addEventListener('DOMContentLoaded', () => {
            refreshPaymentsLedger();
        });

        async function executeStep1() {
            const symbol = document.getElementById('symbol-select').value;
            const outputBox = document.getElementById('step-1-output');
            const detailsDiv = document.getElementById('step-1-details');
            
            outputBox.innerText = "Fetching...";
            detailsDiv.style.display = "block";

            try {
                const response = await fetch(`/api/v1/market-data?symbol=${encodeURIComponent(symbol)}`);
                const status = response.status;
                const data = await response.json();
                
                outputBox.innerText = JSON.stringify(data, null, 2);
                
                if (status === 402 && data.payment_required) {
                    currentPaymentId = data.payment_required.payment_id;
                    currentRecipient = data.payment_required.recipient;
                    currentPrice = data.payment_required.price;
                    
                    document.getElementById('spec-recipient').innerText = currentRecipient;
                    
                    // Mark step 1 as success-step (colored/amber or green because it produced required info)
                    document.getElementById('card-step-1').className = "sim-card success-step";
                    document.getElementById('flow-step-1').className = "step-info active";
                    
                    // Unlock step 2
                    document.getElementById('card-step-2').className = "sim-card active-step";
                    
                    // Generate simulated wallet and tx hash
                    currentWallet = ethers.Wallet.createRandom();
                    currentTxHash = "0x" + Array.from({length: 64}, () => Math.floor(Math.random()*16).toString(16)).join("");
                    
                    document.getElementById('wallet-address').innerText = currentWallet.address;
                    document.getElementById('tx-hash').innerText = currentTxHash;
                } else if (status === 200) {
                    // Already paid or unlocked
                    document.getElementById('card-step-1').className = "sim-card success-step";
                    document.getElementById('flow-step-1').className = "step-info active";
                    outputBox.innerText += "\\n\\n(Note: Market data returned directly - symbol already unlocked!)";
                }
            } catch (err) {
                outputBox.innerText = "Error: " + err.message;
            }
        }

        async function executeStep2() {
            if (!currentPaymentId || !currentWallet) return;
            
            const outputBox = document.getElementById('step-2-output');
            const detailsDiv = document.getElementById('step-2-details');
            outputBox.innerText = "Signing and simulating payment on-chain...";
            detailsDiv.style.display = "block";

            try {
                // Cryptographically sign the payment_id in-browser
                currentSignature = await currentWallet.signMessage(currentPaymentId);
                
                const payload = {
                    payment_id: currentPaymentId,
                    tx_hash: currentTxHash,
                    signature: currentSignature
                };

                const response = await fetch('/api/v1/simulate-payment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                const data = await response.json();
                outputBox.innerText = JSON.stringify(data, null, 2);
                
                if (response.status === 200 && data.status === "success") {
                    document.getElementById('card-step-2').className = "sim-card success-step";
                    document.getElementById('flow-step-2').className = "step-info active";
                    
                    // Unlock step 3
                    document.getElementById('card-step-3').className = "sim-card active-step";
                    refreshPaymentsLedger();
                }
            } catch (err) {
                outputBox.innerText = "Error: " + err.message;
            }
        }

        async function executeStep3() {
            if (!currentPaymentId || !currentSignature || !currentTxHash) return;
            
            const outputBox = document.getElementById('step-3-output');
            const detailsDiv = document.getElementById('step-3-details');
            const renderedDiv = document.getElementById('step-3-rendered');
            const symbol = document.getElementById('symbol-select').value;
            
            outputBox.innerText = "Retrieving gated data with signature...";
            detailsDiv.style.display = "block";

            try {
                const headerPayload = {
                    payment_id: currentPaymentId,
                    tx_hash: currentTxHash,
                    signature: currentSignature
                };

                const response = await fetch(`/api/v1/market-data?symbol=${encodeURIComponent(symbol)}`, {
                    headers: {
                        'Payment-Signature': JSON.stringify(headerPayload)
                    }
                });

                const data = await response.json();
                outputBox.innerText = JSON.stringify(data, null, 2);
                
                if (response.status === 200 && data.status === "success") {
                    document.getElementById('card-step-3').className = "sim-card success-step";
                    document.getElementById('flow-step-3').className = "step-info active";
                    
                    // Render the market data in a beautiful table
                    const mdata = data.data;
                    let tableHtml = `<table class="market-data-table">
                        <thead>
                            <tr>
                                <th>Metric / Field</th>
                                <th>Value</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr><td>Asset Symbol</td><td style="color: var(--accent-green); font-weight:bold;">${mdata.symbol || symbol}</td></tr>
                            <tr><td>DEX Pool Address</td><td>${mdata.pool_address || 'N/A'}</td></tr>
                            <tr><td>Token 0 (Base)</td><td style="font-size:0.75rem;">${mdata.token0 || 'N/A'}</td></tr>
                            <tr><td>Token 1 (USDC)</td><td style="font-size:0.75rem;">${mdata.token1 || 'N/A'}</td></tr>
                            <tr><td>Current Spot Price</td><td style="color: var(--accent-blue); font-weight:bold;">${mdata.price_usdc || mdata.price || 'N/A'} USDC</td></tr>
                            <tr><td>Current Tick</td><td>${mdata.tick || '0'}</td></tr>
                            <tr><td>Current Liquidity</td><td>${mdata.liquidity || 'N/A'}</td></tr>
                        </tbody>
                    </table>`;
                    renderedDiv.innerHTML = tableHtml;
                    refreshPaymentsLedger();
                }
            } catch (err) {
                outputBox.innerText = "Error: " + err.message;
            }
        }

        async function refreshPaymentsLedger() {
            const tbody = document.getElementById('payments-tbody');
            try {
                const response = await fetch('/api/v1/admin/payments');
                const data = await response.json();
                
                const keys = Object.keys(data);
                if (keys.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 2rem;">No payments registered in database yet.</td></tr>`;
                    return;
                }
                
                // Sort keys (latest first)
                keys.reverse();
                
                let html = "";
                for (let pid of keys) {
                    const rec = data[pid];
                    const statusClass = rec.status === "paid" ? "badge-status paid" : "badge-status pending";
                    const shortPid = pid.substring(0, 8) + "...";
                    const shortSender = rec.sender ? (rec.sender.substring(0, 6) + "..." + rec.sender.substring(rec.sender.length - 4)) : "N/A";
                    const shortTx = rec.tx_hash ? (rec.tx_hash.substring(0, 10) + "...") : "N/A";
                    
                    html += `<tr>
                        <td title="${pid}">${shortPid}</td>
                        <td><span class="${statusClass}">${rec.status}</span></td>
                        <td>${rec.symbol || 'N/A'}</td>
                        <td style="color: var(--accent-green);">${rec.price || '0.001'} ${rec.currency || 'USDC'}</td>
                        <td title="${rec.sender || ''}">${shortSender}</td>
                        <td title="${rec.tx_hash || ''}">${shortTx}</td>
                    </tr>`;
                }
                tbody.innerHTML = html;
            } catch (err) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--accent-red); padding: 2rem;">Failed loading ledger: ${err.message}</td></tr>`;
            }
        }
    </script>
</body>
</html>
"""
