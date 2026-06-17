/**
 * Crypcodile Portal Shared Utility Functions
 * public/js/utils.js
 */

/**
 * Returns time string formatted as "hh:mm:ss A" using a locked GMT+3 time representation 
 * to prevent server-client timezone drifts.
 */
function getSyncedTime(dateInput = null) {
    const date = dateInput ? new Date(dateInput) : new Date();
    // Convert current client time to GMT+3 (Istanbul Timezone equivalent)
    const utc = date.getTime() + (date.getTimezoneOffset() * 60000);
    const targetOffset = 3 * 3600000; // +3 hours
    const targetDate = new Date(utc + targetOffset);

    let hours = targetDate.getHours();
    const minutes = String(targetDate.getMinutes()).padStart(2, '0');
    const seconds = String(targetDate.getSeconds()).padStart(2, '0');
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12; // '0' becomes '12'
    const hoursStr = String(hours).padStart(2, '0');
    
    return `${hoursStr}:${minutes}:${seconds} ${ampm}`;
}

/**
 * Generates a mock signature of exactly 130 characters (64 bytes of hex + 0x prefix)
 * using a deterministic generator based on wallet address and payment ID.
 */
function generateMockSignature(address = "", paymentId = "") {
    const addr = address || "0x7a97970C51812dc3A010C7d01b50e0d17dc79C8";
    const pid = paymentId || "a3b04c8f-2879-4d8e-9d22-132d7b5f6390";
    const seedStr = `${addr.toLowerCase()}-${pid.toLowerCase()}`;
    
    let hash1 = 0;
    let hash2 = 0;
    for (let i = 0; i < seedStr.length; i++) {
        const char = seedStr.charCodeAt(i);
        hash1 = (hash1 * 31 + char) & 0xFFFFFFFF;
        hash2 = (hash2 * 37 + char) & 0xFFFFFFFF;
    }

    let result = "";
    let s1 = Math.abs(hash1 || 0x12345678);
    let s2 = Math.abs(hash2 || 0x87654321);

    // Generate exactly 128 random hex characters
    for (let i = 0; i < 128; i++) {
        s1 = (s1 * 1664525 + 1013904223) & 0xFFFFFFFF;
        s2 = (s2 * 1566083941 + 2531011) & 0xFFFFFFFF;
        const val = (s1 ^ s2) % 16;
        result += val.toString(16);
    }
    return "0x" + result;
}

/**
 * Generates an array of 20 time-series data entries where each step has a maximum
 * deviation of 0.05% of the current price, preventing crash-to-zero and overflow errors.
 */
function generateTimeSeriesData(basePrice, count = 20) {
    const data = [];
    let currentPrice = basePrice;
    const now = Date.now();
    const intervalMs = 2000;

    for (let i = count - 1; i >= 0; i--) {
        const timeVal = new Date(now - i * intervalMs);
        const maxDeviation = currentPrice * 0.0005; // max %0.05 deviation per step
        const change = (Math.random() - 0.5) * 2 * maxDeviation;
        currentPrice = currentPrice + change;
        
        // Safety bounds
        if (currentPrice < basePrice * 0.8) currentPrice = basePrice * 0.8;
        if (currentPrice > basePrice * 1.2) currentPrice = basePrice * 1.2;
        
        data.push({
            time: timeVal,
            price: parseFloat(currentPrice.toFixed(2))
        });
    }
    return data;
}

// Export functions for browser / standard script usage
window.getSyncedTime = getSyncedTime;
window.generateMockSignature = generateMockSignature;
window.generateTimeSeriesData = generateTimeSeriesData;
