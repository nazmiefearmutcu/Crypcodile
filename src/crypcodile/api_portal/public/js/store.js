/**
 * Crypcodile Portal Global State Store
 * public/js/store.js
 */

class GlobalStore {
    constructor() {
        this.state = {
            walletAddress: "0x7a97970C51812dc3A010C7d01b50e0d17dc79C8",
            activeTxHash: "0x3cd58525b6a71391c5c9f2",
            isConnected: true,
            activePaymentId: null,
            activeFee: "0.10",
            activeRecipient: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            activeCurrency: "USDC"
        };
        this.listeners = new Set();
    }

    getState() {
        return this.state;
    }

    setState(newState) {
        this.state = { ...this.state, ...newState };
        this.listeners.forEach(listener => listener(this.state));
    }

    subscribe(listener) {
        this.listeners.add(listener);
        listener(this.state);
        return () => {
            this.listeners.delete(listener);
        };
    }
}

// Attach to window for global browser availability
window.portalStore = new GlobalStore();
