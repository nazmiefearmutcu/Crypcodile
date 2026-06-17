/**
 * Crypcodile x402 Web3 Gated Portal Redesign
 * public/js/app.js Implementation Plan
 */

document.addEventListener('DOMContentLoaded', () => {
    // ----------------------------------------------------
    // 1. Application Global State
    // ----------------------------------------------------
    let eventSource = null;
    let priceChart = null;
    let walletAddress = null;
    let walletSigner = null;
    
    // Active simulation details
    let activePaymentId = null;
    let activeFee = "0.10";
    let activeRecipient = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
    let activeCurrency = "USDC";

    // Gated Content cache
    let activeSession = null; // { path, sender, signature, paymentId, verified }

    // Ledger filters
    let ledgerSearchQuery = '';
    let ledgerStatus = '';
    let ledgerLimit = 10;
    let ledgerOffset = 0;
    let ledgerSortOrder = 'desc'; // 'asc' or 'desc' for timestamps
    let ledgerAmountSort = null;  // null, 'asc' or 'desc' (handled client side)

    // Chart tick tracker
    const maxChartTicks = 20;
    const chartLabels = [];
    const chartPrices = [];

    // ----------------------------------------------------
    // 2. DOM Node Accessors
    // ----------------------------------------------------
    const dom = {
        // Navigation Buttons
        connectWalletBtn: document.getElementById('connect-wallet-btn'),
        disconnectWalletBtn: document.getElementById('disconnect-wallet-btn'),
        walletAddressSpan: document.getElementById('wallet-address'),
        walletProviderAlert: document.getElementById('wallet-provider-alert'),
        walletSignatureStatus: document.getElementById('wallet-signature-status'),
        oneClickSimBtn: document.getElementById('one-click-sim-btn'),
        
        // Metrics Displays
        metricsLivePrice: document.getElementById('metrics-live-price'),
        metricsTotalFees: document.getElementById('metrics-total-fees'),
        metricsVerifiedCount: document.getElementById('metrics-verified-count'),
        metricsPendingCount: document.getElementById('metrics-pending-count'),
        
        // Settings Panel
        settingsRpcInput: document.getElementById('settings-rpc-input'),
        settingsContractInput: document.getElementById('settings-contract-input'),
        settingsFeeInput: document.getElementById('settings-fee-input'),
        settingsSaveBtn: document.getElementById('settings-save-btn'),
        
        // API Request Builder
        apiMethodSelect: document.getElementById('api-method-select'),
        apiPathInput: document.getElementById('api-path-input'),
        apiParamsContainer: document.getElementById('api-params-container'),
        apiAddParamBtn: document.getElementById('api-add-param-btn'),
        apiSendBtn: document.getElementById('api-send-btn'),
        apiHeadersPreview: document.getElementById('api-headers-preview'),
        apiResponseConsole: document.getElementById('api-response-console'),
        apiStatusBadge: document.getElementById('api-status-badge'),
        
        // Debugger Stepper Components
        stepHandshake: document.getElementById('debugger-step-handshake'),
        stepRecovery: document.getElementById('debugger-step-recovery'),
        stepMatching: document.getElementById('debugger-step-matching'),
        stepConfirmation: document.getElementById('debugger-step-confirmation'),
        stepUnlocked: document.getElementById('debugger-step-unlocked'),
        debuggerMessage: document.getElementById('debugger-message'),
        
        // Payments Ledger Elements
        ledgerSearchInput: document.getElementById('ledger-search-input'),
        ledgerStatusFilter: document.getElementById('ledger-status-filter'),
        ledgerTableBody: document.getElementById('ledger-table-body'),
        ledgerSortTimestamp: document.getElementById('ledger-sort-timestamp'),
        ledgerSortAmount: document.getElementById('ledger-sort-amount'),
        ledgerPrevBtn: document.getElementById('ledger-prev-btn'),
        ledgerNextBtn: document.getElementById('ledger-next-btn'),
        ledgerPageInfo: document.getElementById('ledger-page-info'),
        ledgerExportJson: document.getElementById('ledger-export-json'),
        ledgerExportCsv: document.getElementById('ledger-export-csv'),
        
        // SSE Event Stream Logger Elements
        sseStatusDot: document.getElementById('sse-status-dot'),
        sseStatusText: document.getElementById('sse-status-text'),
        sseReconnectBtn: document.getElementById('sse-reconnect-btn'),
        sseClearBtn: document.getElementById('sse-clear-btn'),
        sseAutoscrollChk: document.getElementById('sse-autoscroll-chk'),
        sseLogConsole: document.getElementById('sse-log-console'),
    };

    // ----------------------------------------------------
    // 3. UI Helpers: Logs, Stepper & Badges
    // ----------------------------------------------------
    
    // Add log to Event Stream log console
    function logConsole(type, message, status = '') {
        const timeStr = new Date().toLocaleTimeString();
        let colorClass = 'text-slate-400';
        
        if (type === 'info') colorClass = 'text-sky-400';
        else if (type === 'tick') colorClass = 'text-slate-500';
        else if (type === 'payment') {
            colorClass = (status === 'success') ? 'text-emerald-400 font-semibold' : 'text-amber-400';
        } else if (type === 'verification') {
            if (status === 'success') colorClass = 'text-cyan-400';
            else if (status === 'failed') colorClass = 'text-rose-500 font-bold';
            else colorClass = 'text-purple-400';
        }

        const logLine = document.createElement('div');
        logLine.className = `${colorClass} py-0.5 border-b border-slate-900/40`;
        logLine.innerHTML = `[${timeStr}] [${type.toUpperCase()}] ${message}`;
        
        dom.sseLogConsole.appendChild(logLine);
        
        if (dom.sseAutoscrollChk.checked) {
            dom.sseLogConsole.scrollTop = dom.sseLogConsole.scrollHeight;
        }
    }

    // Set styling and messages for verification steps
    function setStepStatus(stepKey, status, message) {
        let node;
        switch (stepKey) {
            case 'handshake': node = dom.stepHandshake; break;
            case 'recovery': node = dom.stepRecovery; break;
            case 'matching': node = dom.stepMatching; break;
            case 'confirmation': node = dom.stepConfirmation; break;
            case 'unlocked': node = dom.stepUnlocked; break;
        }
        if (!node) return;

        // Reset classes
        node.className = "absolute -left-6 w-6 h-6 rounded-full border-4 border-slate-950 flex items-center justify-center text-[10px] font-bold transition-all";
        
        if (status === 'idle') {
            node.classList.add('bg-slate-800', 'text-slate-400');
        } else if (status === 'pending') {
            node.classList.add('bg-amber-500', 'text-slate-950', 'animate-pulse');
        } else if (status === 'success') {
            node.classList.add('bg-emerald-500', 'text-slate-950');
            node.innerHTML = '✓';
        } else if (status === 'failed') {
            node.classList.add('bg-rose-500', 'text-white');
            node.innerHTML = '✗';
        }

        if (message) {
            dom.debuggerMessage.textContent = `[${stepKey.toUpperCase()}] ${message}`;
        }
    }

    // Reset verification stepper back to idle state
    function resetDebugger() {
        const steps = ['handshake', 'recovery', 'matching', 'confirmation', 'unlocked'];
        steps.forEach((step, idx) => {
            setStepStatus(step, 'idle');
            let node;
            switch (step) {
                case 'handshake': node = dom.stepHandshake; break;
                case 'recovery': node = dom.stepRecovery; break;
                case 'matching': node = dom.stepMatching; break;
                case 'confirmation': node = dom.stepConfirmation; break;
                case 'unlocked': node = dom.stepUnlocked; break;
            }
            if (node) {
                node.innerHTML = (idx + 1).toString();
            }
        });
        dom.debuggerMessage.textContent = "Debugger initialized. Ready to record request steps.";
    }

    // Update Request builder HTTP badge
    function updateStatusBadge(statusCode, statusText) {
        dom.apiStatusBadge.classList.remove('hidden', 'bg-emerald-500/10', 'text-emerald-400', 'border-emerald-500/20', 'bg-amber-500/10', 'text-amber-400', 'border-amber-500/20', 'bg-rose-500/10', 'text-rose-400', 'border-rose-500/20');
        dom.apiStatusBadge.classList.add('inline-flex', 'border', 'px-2', 'py-0.5', 'rounded', 'font-mono', 'text-xs');
        
        dom.apiStatusBadge.textContent = `${statusCode} ${statusText}`;

        if (statusCode >= 200 && statusCode < 300) {
            dom.apiStatusBadge.classList.add('bg-emerald-500/10', 'text-emerald-400', 'border-emerald-500/20');
        } else if (statusCode === 402) {
            dom.apiStatusBadge.classList.add('bg-amber-500/10', 'text-amber-400', 'border-amber-500/20');
        } else {
            dom.apiStatusBadge.classList.add('bg-rose-500/10', 'text-rose-400', 'border-rose-500/20');
        }
    }

    function updateHeadersPreview(headers) {
        dom.apiHeadersPreview.textContent = JSON.stringify(headers, null, 2);
    }

    function updateResponseConsole(payload) {
        dom.apiResponseConsole.textContent = JSON.stringify(payload, null, 2);
    }

    // ----------------------------------------------------
    // 4. Initializing Chart.js
    // ----------------------------------------------------
    function initChart() {
        const ctx = dom.priceChartCanvas = document.getElementById('price-chart-canvas').getContext('2d');
        
        priceChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: chartLabels,
                datasets: [{
                    label: 'Mock Token Price (USDC)',
                    data: chartPrices,
                    borderColor: 'rgba(34, 211, 238, 1)', // cyan-400
                    backgroundColor: 'rgba(34, 211, 238, 0.05)',
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true,
                    pointRadius: 2,
                    pointHoverRadius: 6,
                    pointHoverBackgroundColor: 'rgba(34, 211, 238, 1)',
                    pointHoverBorderColor: '#020617',
                    pointHoverBorderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: '#0f172a',
                        titleColor: '#94a3b8',
                        bodyColor: '#34d399',
                        borderColor: '#334155',
                        borderWidth: 1,
                        bodyFont: { family: 'monospace' }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#64748b', font: { size: 9, family: 'monospace' } }
                    },
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#64748b', font: { size: 9, family: 'monospace' } }
                    }
                }
            }
        });
    }

    // ----------------------------------------------------
    // 5. Settings Panel Actions
    // ----------------------------------------------------
    function loadSettings() {
        const savedRpc = localStorage.getItem('x402_rpc_endpoint');
        const savedContract = localStorage.getItem('x402_contract_address');
        const savedFee = localStorage.getItem('x402_gated_fee');

        if (savedRpc) dom.settingsRpcInput.value = savedRpc;
        if (savedContract) dom.settingsContractInput.value = savedContract;
        if (savedFee) dom.settingsFeeInput.value = savedFee;
    }

    dom.settingsSaveBtn.addEventListener('click', () => {
        localStorage.setItem('x402_rpc_endpoint', dom.settingsRpcInput.value);
        localStorage.setItem('x402_contract_address', dom.settingsContractInput.value);
        localStorage.setItem('x402_gated_fee', dom.settingsFeeInput.value);
        logConsole('info', 'Portal configuration parameters successfully updated in localStorage.');
        alert('Configurations saved successfully!');
    });

    // ----------------------------------------------------
    // 6. Query Parameters Dynamic Form Builder
    // ----------------------------------------------------
    dom.apiAddParamBtn.addEventListener('click', () => {
        const row = document.createElement('div');
        row.className = 'flex items-center space-x-2 param-row';
        row.innerHTML = `
            <input type="text" placeholder="Key" class="w-1/3 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500">
            <input type="text" placeholder="Value" class="w-1/2 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500">
            <button class="remove-param-btn text-rose-500 hover:text-rose-400 text-xs px-2 py-1">Remove</button>
        `;
        
        row.querySelector('.remove-param-btn').addEventListener('click', () => {
            row.remove();
        });
        dom.apiParamsContainer.appendChild(row);
    });

    function getQueryParameters() {
        const rows = dom.apiParamsContainer.querySelectorAll('.param-row');
        const urlParams = new URLSearchParams();
        rows.forEach(row => {
            const inputs = row.querySelectorAll('input');
            const key = inputs[0].value.trim();
            const val = inputs[1].value.trim();
            if (key) {
                urlParams.append(key, val);
            }
        });
        return urlParams.toString();
    }

    // ----------------------------------------------------
    // 7. Server-Sent Events (SSE) Client
    // ----------------------------------------------------
    function connectSSE() {
        if (eventSource) {
            eventSource.close();
        }

        dom.sseStatusDot.className = "w-2.5 h-2.5 rounded-full bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.6)] animate-pulse transition-all";
        dom.sseStatusText.textContent = "Connecting...";
        
        eventSource = new EventSource('/api/events');
        
        eventSource.onopen = () => {
            dom.sseStatusDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] transition-all";
            dom.sseStatusText.textContent = "Connected";
            logConsole('info', 'SSE connection to /api/events successfully established.');
        };

        eventSource.onerror = (err) => {
            dom.sseStatusDot.className = "w-2.5 h-2.5 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.8)] transition-all";
            dom.sseStatusText.textContent = "Disconnected";
            logConsole('verification', 'SSE channel error. Server connection lost.', 'failed');
        };

        eventSource.onmessage = (event) => {
            let payload;
            try {
                payload = JSON.parse(event.data);
            } catch (e) {
                console.error("Malformed SSE data", e);
                return;
            }

            // Print output to terminal console
            logConsole(payload.type, payload.message, payload.status);

            // Handle event logic based on event type
            switch (payload.type) {
                case 'tick':
                    if (payload.data && payload.data.price) {
                        const price = parseFloat(payload.data.price);
                        dom.metricsLivePrice.textContent = `$${price.toFixed(2)}`;
                        
                        // Push price to Chart data array
                        const timeString = new Date(payload.data.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                        chartLabels.push(timeString);
                        chartPrices.push(price);

                        if (chartLabels.length > maxChartTicks) chartLabels.shift();
                        if (chartPrices.length > maxChartTicks) chartPrices.shift();

                        priceChart.update('none'); // Update without full redraw animation
                    }
                    break;

                case 'payment':
                    // Refresh ledger data
                    fetchLedger();
                    
                    // If this broadcast matches our current active operation
                    if (payload.data && payload.data.payment_id === activePaymentId) {
                        if (payload.stage === 'pending') {
                            setStepStatus('handshake', 'success', 'Gated handshake completed.');
                        } else if (payload.stage === 'payment_received') {
                            setStepStatus('unlocked', 'success', 'Payment settled. Gated content ready.');
                        }
                    }
                    break;

                case 'verification':
                    if (payload.data && payload.data.payment_id === activePaymentId) {
                        const stepKey = mapStageToStep(payload.stage);
                        if (stepKey) {
                            setStepStatus(stepKey, payload.status, payload.message);
                        }
                    }
                    break;
            }
        };
    }

    function mapStageToStep(stage) {
        switch (stage) {
            case 'signature_recovery': return 'recovery';
            case 'sender_matching': return 'matching';
            case 'block_confirmation': return 'confirmation';
            default: return null;
        }
    }

    // Manual Reconnect Actions
    dom.sseReconnectBtn.addEventListener('click', () => {
        logConsole('info', 'Manually trigger SSE channel reconnection...');
        connectSSE();
    });

    // Clear logs action
    dom.sseClearBtn.addEventListener('click', () => {
        dom.sseLogConsole.innerHTML = '<div class="text-slate-500">[System] Event log buffer cleared.</div>';
    });

    // ----------------------------------------------------
    // 8. Web3 Connect Wallet Functionality
    // ----------------------------------------------------
    dom.connectWalletBtn.addEventListener('click', async () => {
        if (typeof window.ethereum === 'undefined') {
            alert('Ethereum browser wallet not detected. Install MetaMask or click ⚡ One-Click Simulation to use an ephemeral client identity.');
            logConsole('verification', 'Connector error: window.ethereum is undefined.', 'failed');
            return;
        }

        try {
            logConsole('info', 'Connecting Web3 wallet provider...');
            dom.connectWalletBtn.disabled = true;
            dom.connectWalletBtn.textContent = 'Connecting...';

            const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
            walletAddress = accounts[0];

            // Setup ethers v6 Provider/Signer
            const provider = new ethers.BrowserProvider(window.ethereum);
            walletSigner = await provider.getSigner();

            // Set UI updates - Hide connect wallet, show disconnect wallet, populate address
            dom.connectWalletBtn.classList.add('hidden');
            if (dom.disconnectWalletBtn) {
                dom.disconnectWalletBtn.classList.remove('hidden');
            }
            if (dom.walletAddressSpan) {
                dom.walletAddressSpan.classList.remove('hidden');
                dom.walletAddressSpan.textContent = walletAddress;
            }
            if (dom.walletProviderAlert) {
                dom.walletProviderAlert.classList.remove('hidden');
                dom.walletProviderAlert.textContent = "Provider Status: Connected";
            }

            logConsole('info', `Successfully linked wallet address: ${walletAddress}`);
        } catch (err) {
            console.error(err);
            logConsole('verification', `Connection rejected: ${err.message}`, 'failed');
            dom.connectWalletBtn.textContent = '🔗 Connect Wallet';
            alert(`Wallet link failed: ${err.message}`);
        } finally {
            dom.connectWalletBtn.disabled = false;
        }
    });

    if (dom.disconnectWalletBtn) {
        dom.disconnectWalletBtn.addEventListener('click', () => {
            walletAddress = null;
            walletSigner = null;

            // Hide disconnect wallet button and wallet address span
            dom.disconnectWalletBtn.classList.add('hidden');
            if (dom.walletAddressSpan) {
                dom.walletAddressSpan.classList.add('hidden');
                dom.walletAddressSpan.textContent = '';
            }
            if (dom.walletProviderAlert) {
                dom.walletProviderAlert.classList.add('hidden');
                dom.walletProviderAlert.textContent = '';
            }
            if (dom.walletSignatureStatus) {
                dom.walletSignatureStatus.classList.add('hidden');
                dom.walletSignatureStatus.textContent = '';
            }

            // Show connect wallet button
            dom.connectWalletBtn.classList.remove('hidden');
            dom.connectWalletBtn.textContent = '🔗 Connect Wallet';

            logConsole('info', 'Wallet disconnected successfully.');
        });
    }

    // ----------------------------------------------------
    // 9. Interactive API Send & Verification Protocol
    // ----------------------------------------------------
    dom.apiSendBtn.addEventListener('click', async () => {
        const method = dom.apiMethodSelect.value;
        const path = dom.apiPathInput.value;
        const params = getQueryParameters();
        
        let url = path;
        if (params) {
            url += `?${params}`;
        }

        dom.apiSendBtn.disabled = true;
        dom.apiSendBtn.textContent = 'Sending...';
        dom.apiResponseConsole.textContent = 'Executing network query...';
        dom.apiStatusBadge.className = 'hidden';

        try {
            // Compute Headers
            const headers = {};
            if (activeSession && activeSession.path === path && activeSession.verified) {
                headers['Payment-Sender'] = activeSession.sender;
                headers['Payment-Signature'] = activeSession.signature;
                headers['Payment-Id'] = activeSession.paymentId;
            }

            updateHeadersPreview(headers);

            const res = await fetch(url, { method, headers });
            
            updateStatusBadge(res.status, res.statusText);
            const body = await res.json();
            updateResponseConsole(body);

            if (res.status === 402) {
                // Handshake protocol initialized by 402
                activePaymentId = body.payment_id;
                activeFee = body.fee;
                activeRecipient = body.recipient;
                activeCurrency = body.currency;

                resetDebugger();
                setStepStatus('handshake', 'success', `Initial 402 Handshake generated payment_id: ${activePaymentId}`);
                logConsole('payment', `Gated Resource returned 402. Payment is required: ${activeFee} ${activeCurrency} to ${activeRecipient}`, 'pending');

                // Determine whether to pay using linked browser wallet or alert user to simulation
                if (walletSigner && walletAddress) {
                    const pay = confirm(`Request requires payment of ${activeFee} ${activeCurrency}.\nSign payment ID: ${activePaymentId} using connected wallet?`);
                    if (pay) {
                        await executeWeb3PaymentFlow();
                    }
                } else {
                    const sim = confirm(`No Web3 wallet is connected.\nWould you like to auto-trigger the ⚡ One-Click Simulation flow instead?`);
                    if (sim) {
                        await executeSimulationFlow(activePaymentId, activeFee, activeRecipient, activeCurrency);
                    }
                }
            } else if (res.status === 200) {
                logConsole('payment', 'Micropayments payload successfully resolved. Content unlocked.', 'success');
                if (activePaymentId) {
                    setStepStatus('unlocked', 'success', 'Authorized access verified!');
                }
            } else {
                logConsole('verification', `HTTP request finished with status code: ${res.status}`, 'failed');
            }
        } catch (err) {
            console.error(err);
            updateResponseConsole({ error: err.message });
            logConsole('verification', `Request failed: ${err.message}`, 'failed');
        } finally {
            dom.apiSendBtn.disabled = false;
            dom.apiSendBtn.textContent = 'Send Request';
        }
    });

    // Web3 Wallet Signature & Settlement Sequence
    async function executeWeb3PaymentFlow() {
        try {
            setStepStatus('recovery', 'pending', 'Awaiting wallet cryptographic signature...');
            logConsole('verification', 'Requesting message signature from connected wallet...', 'pending');

            // Sign the exact payment ID uuid string
            const signature = await walletSigner.signMessage(activePaymentId);

            setStepStatus('recovery', 'success', 'Cryptographic signature captured.');
            logConsole('verification', `Signature: ${signature.slice(0, 20)}...`, 'success');

            // Emulate USDC transfer settlement to server payments ledger
            setStepStatus('matching', 'pending', 'Broadcasting mock USDC transfer on-chain...');
            logConsole('payment', 'Simulating payment transaction receipt to ledger...', 'pending');

            const mockTxHash = '0x' + Array.from({length: 64}, () => Math.floor(Math.random()*16).toString(16)).join('');
            
            const postRes = await fetch('/api/payments', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    payment_id: activePaymentId,
                    status: 'verified',
                    sender: walletAddress,
                    recipient: activeRecipient,
                    amount: activeFee,
                    currency: activeCurrency,
                    txHash: mockTxHash,
                    signature: signature
                })
            });

            if (!postRes.ok) {
                throw new Error('On-chain simulation rejected by payments ledger.');
            }

            const paymentObj = await postRes.json();
            logConsole('payment', `Transaction confirmed on-chain. Hash: ${mockTxHash.slice(0, 20)}...`, 'success');

            // Save active session for subsequent requests
            activeSession = {
                path: dom.apiPathInput.value,
                sender: walletAddress,
                signature: signature,
                paymentId: activePaymentId,
                verified: true
            };

            // Re-trigger Request builder with valid headers
            setStepStatus('confirmation', 'pending', 'Sending authorized query to gated API...');
            logConsole('verification', 'Re-executing gated API endpoint with security headers...', 'pending');
            
            setTimeout(async () => {
                // Clicking send will now pull the credentials from activeSession
                dom.apiSendBtn.click();
            }, 1200);

        } catch (err) {
            console.error(err);
            setStepStatus('recovery', 'failed', err.message);
            logConsole('verification', `Cryptographic signing failed: ${err.message}`, 'failed');
        }
    }

    // ----------------------------------------------------
    // 10. One-Click Simulation Flow
    // ----------------------------------------------------
    dom.oneClickSimBtn.addEventListener('click', async () => {
        // Trigger simulation starting from a new handshake
        await executeSimulationFlow();
    });

    async function executeSimulationFlow(paymentId = null, fee = "0.10", recipient = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", currency = "USDC") {
        logConsole('info', '⚡ Initializing automated One-Click Gated API simulation...');
        resetDebugger();

        try {
            // Step 1: Handshake
            if (!paymentId) {
                setStepStatus('handshake', 'pending', 'Calling Gated resource to retrieve payment challenge...');
                logConsole('payment', 'Querying gated resource /api/gated-data...', 'pending');

                const initRes = await fetch('/api/gated-data');
                if (initRes.status !== 402) {
                    throw new Error(`Expected handshake response 402, got: ${initRes.status}`);
                }
                const challenge = await initRes.json();
                paymentId = challenge.payment_id;
                fee = challenge.fee;
                recipient = challenge.recipient;
                currency = challenge.currency;
                
                activePaymentId = paymentId;
                updateResponseConsole(challenge);
                updateStatusBadge(402, 'Payment Required');
            }

            setStepStatus('handshake', 'success', `UUID Handshake completed: ${paymentId}`);
            logConsole('payment', `Received Payment ID Challenge: ${paymentId}`, 'success');

            // Step 2: Create Mock Client Wallet
            setStepStatus('recovery', 'pending', 'Instantiating ephemeral client identity...');
            logConsole('verification', 'Generating secure client mock keypair using ethers.Wallet...', 'pending');
            
            const mockWallet = ethers.Wallet.createRandom();
            const mockAddress = mockWallet.address;
            logConsole('info', `Simulated client address: ${mockAddress}`);

            // Step 3: Sign Payment ID
            const signature = await mockWallet.signMessage(paymentId);
            setStepStatus('recovery', 'success', 'Mock message signed successfully.');
            logConsole('verification', `Recoverable signature generated: ${signature.slice(0, 16)}...`, 'success');

            // Step 4: Register simulated payment transaction
            setStepStatus('matching', 'pending', 'Posting ledger verification status...');
            logConsole('payment', 'Submitting transaction data to /api/payments...', 'pending');
            
            const mockTxHash = '0x' + Array.from({length: 64}, () => Math.floor(Math.random()*16).toString(16)).join('');
            
            const postRes = await fetch('/api/payments', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    payment_id: paymentId,
                    status: 'verified',
                    sender: mockAddress,
                    recipient: recipient,
                    amount: fee,
                    currency: currency,
                    txHash: mockTxHash,
                    signature: signature
                })
            });

            if (!postRes.ok) {
                throw new Error('Server rejected simulated payment registration.');
            }

            const paymentData = await postRes.json();
            logConsole('payment', `Simulated ledger payment marked verified. Hash: ${mockTxHash.slice(0, 16)}...`, 'success');

            // Step 5: Fetch Gated API with correct credentials
            setStepStatus('confirmation', 'pending', 'Awaiting block validation...');
            logConsole('verification', 'Hitting GET /api/gated-data with custom authentication headers...', 'pending');

            const headers = {
                'Payment-Sender': mockAddress,
                'Payment-Signature': signature,
                'Payment-Id': paymentId
            };
            updateHeadersPreview(headers);

            const finalRes = await fetch('/api/gated-data', {
                method: 'GET',
                headers: headers
            });

            updateStatusBadge(finalRes.status, finalRes.statusText);
            const finalBody = await finalRes.json();
            updateResponseConsole(finalBody);

            if (finalRes.status === 200) {
                setStepStatus('unlocked', 'success', 'Access granted! Content successfully unlocked.');
                logConsole('payment', 'Simulation completed! Premium dark theme gated dataset unlocked.', 'success');

                // Cache credentials to local session state
                activeSession = {
                    path: '/api/gated-data',
                    sender: mockAddress,
                    signature: signature,
                    paymentId: paymentId,
                    verified: true
                };
            } else {
                throw new Error(`Server returned error ${finalRes.status}: ${finalBody.error}`);
            }

        } catch (err) {
            console.error(err);
            setStepStatus('unlocked', 'failed', err.message);
            logConsole('verification', `Simulation failure: ${err.message}`, 'failed');
            alert(`Simulation failed: ${err.message}`);
        }
    }

    // ----------------------------------------------------
    // 11. Payments Ledger Table Actions (Search, Sort, Pagination)
    // ----------------------------------------------------
    async function fetchLedger() {
        // Build API URL query params
        const urlParams = new URLSearchParams();
        if (ledgerSearchQuery) urlParams.append('search', ledgerSearchQuery);
        if (ledgerStatus) urlParams.append('status', ledgerStatus);
        urlParams.append('limit', ledgerLimit);
        urlParams.append('offset', ledgerOffset);
        
        // Backend handles sorting of timestamp (asc/desc)
        urlParams.append('sort', ledgerSortOrder);

        try {
            const res = await fetch(`/api/payments?${urlParams.toString()}`);
            if (!res.ok) throw new Error('Failed to retrieve ledger data');
            const data = await res.json();

            renderLedgerTable(data.payments, data.total);
            updateMetrics(data.payments);
        } catch (err) {
            console.error(err);
            dom.ledgerTableBody.innerHTML = `<tr><td colspan="6" class="py-4 text-center text-rose-500">Error loading ledger records.</td></tr>`;
        }
    }

    function renderLedgerTable(payments, totalCount) {
        dom.ledgerTableBody.innerHTML = '';
        
        // If sorting by amount is active, perform client-side sorting since backend does not support it
        if (ledgerAmountSort) {
            payments.sort((a, b) => {
                const amtA = parseFloat(a.amount);
                const amtB = parseFloat(b.amount);
                return (ledgerAmountSort === 'asc') ? amtA - amtB : amtB - amtA;
            });
        }

        if (payments.length === 0) {
            dom.ledgerTableBody.innerHTML = `<tr><td colspan="6" class="py-4 text-center text-slate-500">No payment records found.</td></tr>`;
            dom.ledgerPageInfo.textContent = 'Page 1 of 1';
            dom.ledgerPrevBtn.disabled = true;
            dom.ledgerNextBtn.disabled = true;
            return;
        }

        payments.forEach(p => {
            const tr = document.createElement('tr');
            tr.className = 'hover:bg-slate-950/40 border-b border-slate-900/60 transition-colors';
            
            const badgeClass = p.status === 'verified' 
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                : 'bg-amber-500/10 text-amber-500 border border-amber-500/20';

            const senderText = p.sender ? `${p.sender.slice(0, 6)}...${p.sender.slice(-4)}` : 'N/A';
            const txHashText = p.txHash ? `${p.txHash.slice(0, 8)}...${p.txHash.slice(-6)}` : 'N/A';

            tr.innerHTML = `
                <td class="py-3 px-2 flex items-center space-x-1">
                    <span class="cursor-pointer hover:underline text-cyan-400" title="${p.payment_id}" onclick="navigator.clipboard.writeText('${p.payment_id}'); alert('Payment ID copied!');">
                        ${p.payment_id.slice(0, 8)}...
                    </span>
                </td>
                <td class="py-3 px-2 text-slate-400">${new Date(p.timestamp).toLocaleString()}</td>
                <td class="py-3 px-2 text-slate-400" title="${p.sender || ''}">${senderText}</td>
                <td class="py-3 px-2 font-bold text-slate-200">${p.amount} ${p.currency}</td>
                <td class="py-3 px-2 text-slate-400" title="${p.txHash || ''}">${txHashText}</td>
                <td class="py-3 px-2">
                    <span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold ${badgeClass}">
                        ${p.status}
                    </span>
                </td>
            `;
            dom.ledgerTableBody.appendChild(tr);
        });

        // Update pagination buttons
        const totalPages = Math.ceil(totalCount / ledgerLimit) || 1;
        const currentPage = Math.floor(ledgerOffset / ledgerLimit) + 1;

        dom.ledgerPageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
        dom.ledgerPrevBtn.disabled = ledgerOffset === 0;
        dom.ledgerNextBtn.disabled = (ledgerOffset + ledgerLimit) >= totalCount;
    }

    // Recalculate summary metrics from current loaded list
    function updateMetrics(payments) {
        let totalFees = 0;
        let verifiedCount = 0;
        let pendingCount = 0;

        // Note: For true global metrics, we loop through all records. Since limit is 10, we do local loop.
        // In a real app we'd fetch aggregate stats from an endpoint. Here we compute from loaded data.
        payments.forEach(p => {
            if (p.status === 'verified') {
                totalFees += parseFloat(p.amount);
                verifiedCount++;
            } else {
                pendingCount++;
            }
        });

        dom.metricsTotalFees.textContent = `${totalFees.toFixed(2)} USDC`;
        dom.metricsVerifiedCount.textContent = verifiedCount;
        dom.metricsPendingCount.textContent = pendingCount;
    }

    // Debounce helper for Search input
    let searchTimeout;
    dom.ledgerSearchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            ledgerSearchQuery = e.target.value.trim();
            ledgerOffset = 0;
            fetchLedger();
        }, 300);
    });

    // Status filter selector change
    dom.ledgerStatusFilter.addEventListener('change', (e) => {
        ledgerStatus = e.target.value;
        ledgerOffset = 0;
        fetchLedger();
    });

    // Toggle Sorting on Table Headers
    dom.ledgerSortTimestamp.addEventListener('click', () => {
        // Toggle sort order
        ledgerSortOrder = (ledgerSortOrder === 'desc') ? 'asc' : 'desc';
        ledgerAmountSort = null; // deactivate amount sort
        fetchLedger();
    });

    dom.ledgerSortAmount.addEventListener('click', () => {
        // Toggle client-side amount sort
        ledgerAmountSort = (ledgerAmountSort === 'asc') ? 'desc' : 'asc';
        fetchLedger();
    });

    // Pagination triggers
    dom.ledgerPrevBtn.addEventListener('click', () => {
        if (ledgerOffset >= ledgerLimit) {
            ledgerOffset -= ledgerLimit;
            fetchLedger();
        }
    });

    dom.ledgerNextBtn.addEventListener('click', () => {
        ledgerOffset += ledgerLimit;
        fetchLedger();
    });

    // ----------------------------------------------------
    // 12. JSON / CSV Data Export Action Handlers
    // ----------------------------------------------------
    async function fetchAllFilteredLedger() {
        const urlParams = new URLSearchParams();
        if (ledgerSearchQuery) urlParams.append('search', ledgerSearchQuery);
        if (ledgerStatus) urlParams.append('status', ledgerStatus);
        urlParams.append('limit', 100); // Retrieve up to 100 records for bulk exports
        urlParams.append('offset', 0);
        urlParams.append('sort', ledgerSortOrder);

        const res = await fetch(`/api/payments?${urlParams.toString()}`);
        const data = await res.json();
        return data.payments || [];
    }

    dom.ledgerExportJson.addEventListener('click', async () => {
        try {
            const data = await fetchAllFilteredLedger();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            triggerDownload(blob, `payments_ledger_${Date.now()}.json`);
            logConsole('info', 'Payments ledger exported to JSON file format successfully.');
        } catch (e) {
            alert('Export failed.');
        }
    });

    dom.ledgerExportCsv.addEventListener('click', async () => {
        try {
            const data = await fetchAllFilteredLedger();
            const headers = ['Payment ID', 'Status', 'Sender', 'Recipient', 'Amount', 'Currency', 'Tx Hash', 'Timestamp'];
            const csvRows = [headers.join(',')];
            
            data.forEach(p => {
                const row = [
                    p.payment_id,
                    p.status,
                    p.sender || '',
                    p.recipient,
                    p.amount,
                    p.currency,
                    p.txHash || '',
                    p.timestamp
                ].map(val => `"${val.replace(/"/g, '""')}"`);
                csvRows.push(row.join(','));
            });

            const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
            triggerDownload(blob, `payments_ledger_${Date.now()}.csv`);
            logConsole('info', 'Payments ledger exported to CSV file format successfully.');
        } catch (e) {
            alert('Export failed.');
        }
    });

    function triggerDownload(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // ----------------------------------------------------
    // 13. Application Initialization Entry Point
    // ----------------------------------------------------
    function init() {
        loadSettings();
        initChart();
        connectSSE();
        fetchLedger();
        resetDebugger();
        logConsole('info', 'Crypcodile Web3 gated dashboard fully loaded.');
    }

    init();
});
