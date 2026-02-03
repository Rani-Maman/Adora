-- Create dropship_analysis table if not exists
CREATE TABLE IF NOT EXISTS dropship_analysis (
    id SERIAL PRIMARY KEY,
    advertiser_id INTEGER REFERENCES advertisers(id),
    url TEXT NOT NULL,
    base_url TEXT NOT NULL,
    
    -- Scrape data
    product_name TEXT,
    israeli_price DECIMAL(10, 2),
    original_price DECIMAL(10, 2),
    currency VARCHAR(10),
    
    -- Red flags detected
    has_long_shipping BOOLEAN DEFAULT FALSE,
    has_no_refund BOOLEAN DEFAULT FALSE,
    has_fake_discount BOOLEAN DEFAULT FALSE,
    has_generic_description BOOLEAN DEFAULT FALSE,
    has_stock_images BOOLEAN DEFAULT FALSE,
    has_no_contact BOOLEAN DEFAULT FALSE,
    
    -- AliExpress comparison
    aliexpress_match_url TEXT,
    aliexpress_price DECIMAL(10, 2),
    price_markup_percent DECIMAL(5, 2),
    
    -- Risk scoring
    risk_score DECIMAL(3, 2) DEFAULT 0.00,
    confidence DECIMAL(3, 2) DEFAULT 0.00,
    evidence TEXT[],
    
    -- Timestamps
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create risk_db table (only risky sites above threshold)
CREATE TABLE IF NOT EXISTS risk_db (
    id SERIAL PRIMARY KEY,
    base_url TEXT UNIQUE NOT NULL,
    risk_score DECIMAL(3, 2) NOT NULL,
    evidence TEXT[],
    advertiser_name TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for fast lookups
CREATE INDEX IF NOT EXISTS idx_risk_db_base_url ON risk_db(base_url);
CREATE INDEX IF NOT EXISTS idx_dropship_analysis_base_url ON dropship_analysis(base_url);

-- Show tables
\dt
