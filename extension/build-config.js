// Build script to generate config.js from .env and whitelist files
// Run: npm run build:config

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Read .env file
const envPath = join(__dirname, '.env');
const env = {};

if (existsSync(envPath)) {
    const envContent = readFileSync(envPath, 'utf-8');
    envContent.split('\n').forEach(line => {
        // Skip comments and empty lines
        if (line.trim().startsWith('#') || !line.trim()) return;
        const match = line.match(/^([^=]+)=(.*)$/);
        if (match) {
            env[match[1].trim()] = match[2].trim();
        }
    });
    console.log('Loaded .env:', env);
} else {
    console.log('.env not found at:', envPath);
}

// Read whitelist files from backend/data
const whitelistDir = join(__dirname, '..', 'backend', 'data');
const whitelistFiles = [
    'whitelist_global.txt',
    'whitelist_israel.txt', 
    'whitelist_israel_extra.txt'
];

const safeDomains = new Set();

for (const file of whitelistFiles) {
    const filePath = join(whitelistDir, file);
    if (existsSync(filePath)) {
        const content = readFileSync(filePath, 'utf-8');
        content.split('\n').forEach(line => {
            const domain = line.trim();
            // Skip comments and empty lines
            if (domain && !domain.startsWith('#')) {
                safeDomains.add(domain.toLowerCase());
            }
        });
        console.log(`Loaded ${file}: ${content.split('\n').filter(l => l.trim() && !l.startsWith('#')).length} domains`);
    } else {
        console.log(`Whitelist not found: ${filePath}`);
    }
}

console.log(`Total unique safe domains: ${safeDomains.size}`);

// Required values (no defaults)
if (!env.API_BASE) {
    console.error('ERROR: API_BASE not set in .env file');
    console.error('Create extension/.env with: API_BASE=https://your-api-url.com');
    process.exit(1);
}

const API_BASE = env.API_BASE;
const RISK_THRESHOLD = env.RISK_THRESHOLD || '0.6';

// Convert Set to sorted array for consistent output
const sortedDomains = Array.from(safeDomains).sort();

const configContent = `// Extension Configuration (auto-generated from .env and whitelist files)
// Do not edit directly - modify .env and run: npm run build:config
// Safe domains loaded from: backend/data/whitelist_*.txt

const config = {
    API_BASE: '${API_BASE}',
    RISK_THRESHOLD: ${RISK_THRESHOLD},
    SAFE_DOMAINS: new Set(${JSON.stringify(sortedDomains)})
};

if (typeof window !== 'undefined') {
    window.ADORA_CONFIG = config;
}

if (typeof self !== 'undefined') {
    self.ADORA_CONFIG = config;
}
`;

writeFileSync(join(__dirname, 'public', 'config.js'), configContent);
console.log('Generated public/config.js with API_BASE:', API_BASE);
