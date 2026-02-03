// Extension Configuration
// Change this URL when deploying to production

const config = {
    // API endpoint - set your production URL here
    // For local dev: http://localhost:8000
    // For production: https://your-api-domain.com
    API_BASE: 'http://localhost:8000',

    // Risk threshold - sites with score >= this value are flagged
    RISK_THRESHOLD: 0.6,

    // Domains to skip (never analyze these)
    SAFE_DOMAINS: [
        'google.com', 'facebook.com', 'youtube.com', 'twitter.com',
        'linkedin.com', 'github.com', 'microsoft.com', 'apple.com',
        'amazon.com', 'instagram.com', 'whatsapp.com'
    ]
};

// Make available globally
if (typeof window !== 'undefined') {
    window.ADORA_CONFIG = config;
}

// For service worker
if (typeof self !== 'undefined') {
    self.ADORA_CONFIG = config;
}
