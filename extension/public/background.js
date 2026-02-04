// Background Service Worker for Adora Extension
// Handles API calls and badge updates

// Import config
importScripts('config.js');
const API_BASE = self.ADORA_CONFIG?.API_BASE || 'http://localhost:8000';
const RISK_THRESHOLD = self.ADORA_CONFIG?.RISK_THRESHOLD || 0.6;

// Cache for results (domain -> result)
const cache = new Map();

// Listen for messages from content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'CHECK_URL') {
        checkUrl(message.url, sender.tab.id).then(sendResponse);
        return true; // Keep channel open for async response
    }
});

// Check a URL against the API
async function checkUrl(url, tabId) {
    try {
        const domain = new URL(url).hostname.replace('www.', '');

        // Check cache first
        if (cache.has(domain)) {
            const cached = cache.get(domain);
            updateBadge(tabId, cached);
            return cached;
        }

        // Call /check endpoint (lightweight DB lookup)
        const response = await fetch(`${API_BASE}/check/?url=${encodeURIComponent(url)}`);

        if (!response.ok) {
            console.error('API error:', response.status);
            return null;
        }

        const result = await response.json();

        // Cache result
        cache.set(domain, result);

        // Update badge if risky
        updateBadge(tabId, result);

        return result;
    } catch (error) {
        console.error('Check URL error:', error);
        return null;
    }
}

// Update extension badge based on risk
function updateBadge(tabId, result) {
    if (!result) return;

    // Only show badge for risky sites
    if (result.risky && result.score >= RISK_THRESHOLD) {
        chrome.action.setBadgeText({ tabId, text: '!' });
        chrome.action.setBadgeBackgroundColor({ tabId, color: '#DC2626' }); // Red
    } else {
        // Clear badge for safe sites - no notification needed
        chrome.action.setBadgeText({ tabId, text: '' });
    }
}

// Clear badge when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
    chrome.action.setBadgeText({ tabId, text: '' });
});
