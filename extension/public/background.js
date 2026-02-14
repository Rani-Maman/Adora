// Background Service Worker for Adora Extension
// Handles API calls and badge updates

// Import config (includes SAFE_DOMAINS Set from whitelist files)
importScripts('config.js');

if (!self.ADORA_CONFIG?.API_BASE) {
    console.error('[Adora] ERROR: config.js not found or API_BASE not set. Run: npm run build:config');
}

const API_BASE = self.ADORA_CONFIG?.API_BASE;
const API_KEY = self.ADORA_CONFIG?.API_KEY;
const RISK_THRESHOLD = self.ADORA_CONFIG?.RISK_THRESHOLD || 0.6;
const SAFE_DOMAINS = self.ADORA_CONFIG?.SAFE_DOMAINS || new Set();

// Cache settings
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours (risky)
const CACHE_TTL_SAFE_MS = 1 * 60 * 60 * 1000; // 1 hour (not risky)
const CACHE_MAX_SIZE = 1000; // Max cached domains

// In-memory cache (synced with chrome.storage.local)
let cache = new Map();

// Performance metrics
let stats = {
    totalChecks: 0,
    whitelistHits: 0,
    cacheHits: 0,
    apiCalls: 0,
    riskyFound: 0,
    totalApiTime: 0,
};

// Load cache and stats from storage on startup
chrome.storage.local.get(['adoraCache', 'adoraStats'], (result) => {
    if (result.adoraCache) {
        const entries = Object.entries(result.adoraCache);
        const now = Date.now();
        let expired = 0;
        // Filter out expired entries
        entries.forEach(([domain, data]) => {
            if (data.timestamp && (now - data.timestamp) < CACHE_TTL_MS) {
                cache.set(domain, data);
            } else {
                expired++;
            }
        });
        console.log(`[Adora] INFO: Loaded ${cache.size} cached domains (${expired} expired)`);
    }
    
    if (result.adoraStats) {
        stats = { ...stats, ...result.adoraStats };
        console.log(`[Adora] INFO: Stats loaded - Checks: ${stats.totalChecks}, Whitelist: ${stats.whitelistHits}, Cache: ${stats.cacheHits}, API: ${stats.apiCalls}`);
    }
});

// Save cache to storage (debounced)
let saveTimeout = null;
function saveCache() {
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
        const obj = Object.fromEntries(cache);
        chrome.storage.local.set({ adoraCache: obj, adoraStats: stats });
    }, 1000);
}

// Log with consistent format
function log(level, message, extra = {}) {
    const timestamp = new Date().toISOString();
    const prefix = `[Adora] ${level}:`;
    const extraStr = Object.keys(extra).length > 0 ? JSON.stringify(extra) : '';
    console.log(`${prefix} ${message}`, extraStr || '');
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
        checkUrl(message.url, sender.tab?.id).then(sendResponse);
        return true; // Keep channel open for async response
    }
    if (message.type === 'GET_STATS') {
        // Calculate metrics
        const cacheHitRate = stats.totalChecks > 0 
            ? ((stats.cacheHits / stats.totalChecks) * 100).toFixed(1) 
            : 0;
        const whitelistRate = stats.totalChecks > 0 
            ? ((stats.whitelistHits / stats.totalChecks) * 100).toFixed(1) 
            : 0;
        const avgApiTime = stats.apiCalls > 0 
            ? Math.round(stats.totalApiTime / stats.apiCalls) 
            : 0;
        
        sendResponse({
            ...stats,
            cacheHitRate: `${cacheHitRate}%`,
            whitelistRate: `${whitelistRate}%`,
            avgApiTime: `${avgApiTime}ms`,
            cacheSize: cache.size,
        });
        return false;
    }
});

// Check a URL against the API
async function checkUrl(url, tabId) {
    try {
        const domain = new URL(url).hostname.replace('www.', '').toLowerCase();
        stats.totalChecks++;

        // 1. Skip known safe domains (22k+ from whitelist files) - no API call
        if (isSafeDomain(domain)) {
            stats.whitelistHits++;
            log('INFO', `Domain whitelisted: ${domain}`, { source: 'whitelist' });
            return { risky: false, reason: 'whitelisted' };
        }

        // 2. Check persistent cache - no API call if fresh
        if (cache.has(domain)) {
            const cached = cache.get(domain);
            const age = Date.now() - (cached.timestamp || 0);
            const ttl = cached.risky ? CACHE_TTL_MS : CACHE_TTL_SAFE_MS;
            if (age < ttl) {
                stats.cacheHits++;
                const ageHours = Math.round(age / (1000 * 60 * 60));
                log('INFO', `Cache hit: ${domain}`, { 
                    source: 'cache', 
                    age_hours: ageHours,
                    risky: cached.risky 
                });
                updateBadge(tabId, cached);
                saveCache(); // Update stats
                return cached;
            }
            // Expired - remove from cache
            cache.delete(domain);
            log('INFO', `Cache expired: ${domain}`, { age_hours: Math.round(age / (1000 * 60 * 60)) });
        }

        // 3. Call /check endpoint (only for unknown domains)
        stats.apiCalls++;
        const startTime = performance.now();
        
        log('INFO', `API call: ${domain}`, { source: 'api' });
        const response = await fetch(`${API_BASE}/check/?url=${encodeURIComponent(url)}`, {
            headers: API_KEY ? { 'X-API-Key': API_KEY } : {}
        });

        if (!response.ok) {
            log('ERROR', `API error for ${domain}`, { status: response.status });
            return null;
        }

        const result = await response.json();
        const apiTime = performance.now() - startTime;
        stats.totalApiTime += apiTime;
        result.timestamp = Date.now();
        
        if (result.risky) {
            stats.riskyFound++;
            log('WARN', `Risky site detected: ${domain}`, { 
                score: result.score, 
                api_time_ms: Math.round(apiTime) 
            });
        } else {
            log('INFO', `Safe site: ${domain}`, { api_time_ms: Math.round(apiTime) });
        }

        // 4. Cache result (with size limit)
        if (cache.size >= CACHE_MAX_SIZE) {
            // Remove oldest entry
            const oldest = [...cache.entries()].sort((a, b) => a[1].timestamp - b[1].timestamp)[0];
            if (oldest) {
                cache.delete(oldest[0]);
                log('INFO', `Cache eviction: ${oldest[0]}`, { reason: 'max_size' });
            }
        }
        cache.set(domain, result);
        saveCache();

        // Update badge if risky
        updateBadge(tabId, result);

        return result;
    } catch (error) {
        log('ERROR', `Check URL error: ${error.message}`, { url, error: error.stack });
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

// Global function to print stats (can be called from console)
self.printStats = function() {
    const cacheHitRate = stats.totalChecks > 0 
        ? ((stats.cacheHits / stats.totalChecks) * 100).toFixed(1) 
        : 0;
    const whitelistRate = stats.totalChecks > 0 
        ? ((stats.whitelistHits / stats.totalChecks) * 100).toFixed(1) 
        : 0;
    const avgApiTime = stats.apiCalls > 0 
        ? Math.round(stats.totalApiTime / stats.apiCalls) 
        : 0;
    
    console.log('=== Adora Extension Stats ===');
    console.log(`Total checks: ${stats.totalChecks}`);
    console.log(`Whitelist hits: ${stats.whitelistHits} (${whitelistRate}%)`);
    console.log(`Cache hits: ${stats.cacheHits} (${cacheHitRate}%)`);
    console.log(`API calls: ${stats.apiCalls}`);
    console.log(`Risky sites found: ${stats.riskyFound}`);
    console.log(`Average API time: ${avgApiTime}ms`);
    console.log(`Cache size: ${cache.size}/${CACHE_MAX_SIZE}`);
    console.log(`Whitelist size: ${SAFE_DOMAINS.size} domains`);
};

// Log stats on startup
console.log(`[Adora] INFO: Service worker started. Call printStats() to see metrics.`);
