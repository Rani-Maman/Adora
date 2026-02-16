-- Adora DB schema (PostgreSQL 14)
-- 6 tables: scraping pipeline → analysis pipeline → extension lookup

-- ============================================================
-- 1. meta_ads_daily — all scraped ads, deduped by unique key
-- ============================================================
CREATE TABLE IF NOT EXISTS meta_ads_daily (
    id SERIAL PRIMARY KEY,
    ad_unique_key TEXT NOT NULL UNIQUE,
    ad_archive_id TEXT,
    advertiser_name TEXT,
    ad_start_date DATE,
    ad_library_link TEXT,
    ad_text TEXT,
    destination_product_url TEXT,
    source_keyword TEXT,
    source_search_url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_meta_ads_daily_scraped_at ON meta_ads_daily(scraped_at);
CREATE INDEX IF NOT EXISTS idx_meta_ads_daily_start_date ON meta_ads_daily(ad_start_date);

-- ============================================================
-- 2. meta_ads_daily_with_urls — subset with valid external URLs
-- ============================================================
CREATE TABLE IF NOT EXISTS meta_ads_daily_with_urls (
    id SERIAL PRIMARY KEY,
    ad_unique_key TEXT NOT NULL UNIQUE,
    ad_archive_id TEXT,
    advertiser_name TEXT,
    ad_start_date DATE,
    ad_library_link TEXT,
    ad_text TEXT,
    destination_product_url TEXT,
    source_keyword TEXT,
    source_search_url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_meta_ads_daily_with_urls_start_date ON meta_ads_daily_with_urls(ad_start_date);

-- ============================================================
-- 3. advertisers — unique advertisers (deduped by name)
-- ============================================================
CREATE TABLE IF NOT EXISTS advertisers (
    id SERIAL PRIMARY KEY,
    advertiser_name TEXT UNIQUE,
    ad_start_date TEXT,
    ad_library_link TEXT,
    ad_text TEXT,
    destination_product_url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_advertisers_scraped_at ON advertisers(scraped_at);

-- ============================================================
-- 4. ads_with_urls — unique ads by destination URL, scored by batch_analyze
-- ============================================================
CREATE TABLE IF NOT EXISTS ads_with_urls (
    id SERIAL PRIMARY KEY,
    advertiser_name TEXT UNIQUE,
    ad_start_date TEXT,
    ad_library_link TEXT,
    ad_text TEXT,
    destination_product_url TEXT UNIQUE,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Analysis columns (written by batch_analyze_ads.py)
    analysis_score DOUBLE PRECISION,
    analysis_category TEXT,
    analysis_reason TEXT,
    analysis_json JSONB,
    analyzed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ads_with_urls_scraped_at ON ads_with_urls(scraped_at);

-- ============================================================
-- 5. dropship_analysis — detailed Gemini analysis per product URL
-- ============================================================
CREATE TABLE IF NOT EXISTS dropship_analysis (
    id SERIAL PRIMARY KEY,
    advertiser_name TEXT,
    destination_product_url TEXT UNIQUE,
    full_response JSONB,
    domain TEXT,
    page_type TEXT,
    brand_name TEXT,
    currency TEXT,
    shipping_days_max INTEGER,
    return_window_days INTEGER,
    has_email BOOLEAN,
    has_phone BOOLEAN,
    has_address BOOLEAN,
    has_legal_name BOOLEAN,
    has_business_id BOOLEAN,
    reviews_present BOOLEAN,
    review_count INTEGER,
    average_rating DOUBLE PRECISION,
    long_shipping_time_flag BOOLEAN,
    missing_business_identity_flag BOOLEAN,
    policy_boilerplate_flag BOOLEAN,
    single_product_funnel_flag BOOLEAN,
    scrape_status TEXT,
    confidence DOUBLE PRECISION,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 6. risk_db — risky domains (score >= 0.6), queried by extension
-- ============================================================
CREATE TABLE IF NOT EXISTS risk_db (
    id SERIAL PRIMARY KEY,
    base_url TEXT UNIQUE NOT NULL,
    risk_score NUMERIC(3, 2) NOT NULL,
    evidence TEXT[],
    advertiser_name TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    price_matches JSONB DEFAULT '[]'::jsonb,
    price_match_failures JSONB DEFAULT '[]'::jsonb,
    -- risk_db is intended to contain only risky domains (>= 0.6). Enforce at DB level.
    CONSTRAINT risk_db_min_score CHECK (risk_score >= 0.6)
);

CREATE INDEX IF NOT EXISTS idx_risk_db_base_url ON risk_db(base_url);
