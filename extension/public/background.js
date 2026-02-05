// Background Service Worker for Adora Extension
// Handles API calls and badge updates

// Import config (includes SAFE_DOMAINS Set from whitelist files)
importScripts('config.js');

if (!self.ADORA_CONFIG?.API_BASE) {
    console.error('Adora: config.js not found or API_BASE not set. Run: npm run build:config');
}

const API_BASE = self.ADORA_CONFIG?.API_BASE;
const RISK_THRESHOLD = self.ADORA_CONFIG?.RISK_THRESHOLD || 0.6;
const SAFE_DOMAINS = self.ADORA_CONFIG?.SAFE_DOMAINS || new Set();

// Cache settings
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const CACHE_MAX_SIZE = 1000; // Max cached domains

// In-memory cache (synced with chrome.storage.local)
let cache = new Map();

// Load cache from storage on startup
chrome.storage.local.get(['adoraCache'], (result) => {
    if (result.adoraCache) {
        const entries = Object.entries(result.adoraCache);
        const now = Date.now();
        // Filter out expired entries
        entries.forEach(([domain, data]) => {
            if (data.timestamp && (now - data.timestamp) < CACHE_TTL_MS) {
                cache.set(domain, data);
            }
        });
        console.log(`Adora: Loaded ${cache.size} cached domains`);
    }
});

// Save cache to storage (debounced)
let saveTimeout = null;
function saveCache() {
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
        const obj = Object.fromEntries(cache);
        chrome.storage.local.set({ adoraCache: obj });
    }, 1000);
}

// Check if domain is in safe list (exact match or subdomain)
function isSafeDomain(domain) {
    // Exact match
    if (SAFE_DOMAINS.has(domain)) return true;
    
    // Check parent domains (e.g., mail.google.com -> google.com)
    const parts = domain.split('.');
    for (let i = 1; i < parts.length - 1; i++) {
        const parent = parts.slice(i).join('.');
        if (SAFE_DOMAINS.has(parent)) return true;
    }
    return false;
}

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
        const domain = new URL(url).hostname.replace('www.', '').toLowerCase();

        // 1. Skip known safe domains (22k+ from whitelist files) - no API call
        if (isSafeDomain(domain)) {
            return { risky: false, reason: 'whitelisted' };
        }

        // 2. Check persistent cache - no API call if fresh
        if (cache.has(domain)) {
            const cached = cache.get(domain);
            const age = Date.now() - (cached.timestamp || 0);
            if (age < CACHE_TTL_MS) {
                updateBadge(tabId, cached);
                return cached;
            }
            // Expired - remove from cache
            cache.delete(domain);
        }

        // 3. Call /check endpoint (only for unknown domains)
        const response = await fetch(`${API_BASE}/check/?url=${encodeURIComponent(url)}`);

        if (!response.ok) {
            console.error('API error:', response.status);
            return null;
        }

        const result = await response.json();
        result.timestamp = Date.now();

        // 4. Cache result (with size limit)
        if (cache.size >= CACHE_MAX_SIZE) {
            // Remove oldest entry
            const oldest = [...cache.entries()].sort((a, b) => a[1].timestamp - b[1].timestamp)[0];
            if (oldest) cache.delete(oldest[0]);
        }
        cache.set(domain, result);
        saveCache();

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
