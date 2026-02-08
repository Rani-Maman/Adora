# Adora — Scraping Pipeline & System Architecture

> Last updated: February 2026

## Overview

Adora is an Israeli dropship/scam detection system. It scrapes Facebook/Meta Ad Library for Hebrew keyword ads, analyzes advertiser product sites for dropshipping indicators using Playwright + Gemini AI, and serves risk scores to a Chrome extension in real-time.

```
┌─────────────────────────────────────────────────────────────┐
│                     NIGHTLY PIPELINE                        │
│                                                             │
│   00:01  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 1│  (mivtsa / מבצע)                  │
│          └──────────────┘                                   │
│   01:00  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 2│  (mugbal / מוגבל)                  │
│          └──────────────┘                                   │
│   02:00  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 3│  (hanaha / הנחת)                   │
│          └──────────────┘                                   │
│   03:00  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 4│  (shaot / שעות)                    │
│          └──────────────┘                                   │
│   04:00  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 5│  (achshav / עכשיו)                 │
│          └──────────────┘                                   │
│   05:00  ┌──────────────────────┐                           │
│   ──────►│ Nightly Email Summary│  → Gmail                  │
│          └──────────────────────┘                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  CONTINUOUS PIPELINE                         │
│                                                             │
│  Every   ┌────────────────────┐     ┌────────┐             │
│  10 min  │ Batch Analyze (20) │────►│ risk_db│             │
│  ───────►│ Playwright+Gemini  │     └───┬────┘             │
│          └────────────────────┘         │                   │
│                                         ▼                   │
│          ┌─────────────┐         ┌──────────────┐          │
│          │ Chrome Ext  │◄───────►│  FastAPI /check│         │
│          └─────────────┘         └──────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Data Collection — Meta Ad Library Scraper

### Architecture

The scraper uses **Playwright browser automation** to navigate the Meta Ad Library, search Hebrew keywords targeting Israel, and extract ad data (advertiser name, page URL, ad body text, link URLs).

Each keyword runs as an independent cron job, staggered 1 hour apart to avoid rate limits.

### Components

| File | Role |
|------|------|
| `backend/scripts/daily_meta_scrape.py` | Core Playwright scraper (~1050 lines) |
| `backend/scripts/run_meta_keyword_job.sh` | Bash wrapper with locking, cleanup, timeouts |
| `backend/scripts/configs/meta_keywords/*.json` | Per-keyword config files (search URL, params) |

### Scraper Flow

```
run_meta_keyword_job.sh
  ├── flock (prevent concurrent runs)
  ├── cleanup_playwright_orphans() (kill stale Chrome)
  ├── timeout --signal=TERM $HARD_TIMEOUT
  └── python3 daily_meta_scrape.py --config $CONFIG
        ├── Launch Playwright (chromium, headless)
        ├── Load Meta session cookies (storage state)
        ├── For each ad library search link:
        │     ├── Navigate to Meta Ad Library URL
        │     ├── Scroll & collect ads (max 700 scrolls, 45 idle rounds)
        │     ├── Extract: advertiser_name, page_url, ad_body, external_links
        │     └── Filter: remove social URLs (fb, ig, wa, messenger)
        ├── Dedup by SHA1(date + keyword + normalized_name)
        ├── Insert into meta_ads_daily table
        ├── Also insert into legacy advertisers + ads_with_urls tables
        └── Save JSON output + log files
```

### Deduplication

- **Key**: `SHA1(scrape_date + keyword + normalized_advertiser_name)`
- Normalized = lowercase → strip non-alphanumeric → collapse whitespace
- `ON CONFLICT DO NOTHING` — duplicates silently skipped

### URL Filtering

External URLs are extracted from ad text/links. The following are excluded:
- Social platforms: `facebook.com`, `instagram.com`, `whatsapp.com`, `wa.me`, `messenger.com`
- Marketplace/internal: `marketplace.facebook.com`
- URL shorteners: `bit.ly`, `tinyurl.com`, etc.

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `META_DAILY_STORAGE_STATE` | — | Path to Playwright storage state (Meta cookies) |
| `META_DAILY_OUTPUT_DIR` | `./output` | JSON output directory |
| `META_DAILY_LOG_DIR` | `./logs` | Log directory |
| `META_DAILY_HARD_TIMEOUT` | `2100` | Max runtime per keyword (seconds) |
| `META_DAILY_MAX_SCROLLS` | `700` | Max scroll attempts per search link |
| `META_DAILY_IDLE_ROUNDS` | `45` | Stop scrolling after N rounds with no new ads |

---

## 2. Analysis Pipeline — Batch Scoring

### Architecture

Every 10 minutes, `batch_analyze_ads.py` picks up **20 unscored ads** from `ads_with_urls` and runs them through a two-stage analysis:

1. **Playwright Site Scrape** — Visit the advertiser's product URL, extract structured data
2. **Gemini 2.0 Flash AI Scoring** — Send site data to Google's Gemini API for fraud analysis

### Components

| File | Role |
|------|------|
| `backend/batch_analyze_ads.py` | Main batch processor |
| `backend/app/analysis/gemini_scorer.py` | Gemini API scorer (also used by FastAPI) |
| `backend/app/scraping/site_scraper.py` | Playwright site data extractor |

### Scoring Flow

```
batch_analyze_ads.py (cron: */10)
  ├── SELECT 20 rows FROM ads_with_urls WHERE analysis_score IS NULL
  ├── Launch single Playwright browser (reused for batch)
  └── For each ad:
        ├── Navigate to product URL
        ├── Extract SiteData:
        │     title, product_name, price, shipping_time,
        │     business_id (ח.פ.), countdown_timer, scarcity_widgets,
        │     whatsapp_only_contact, page_text (4000 chars)
        ├── Send to Gemini 2.0 Flash with Israeli fraud detection prompt
        ├── Parse JSON response: {score, is_risky, category, reason, evidence}
        ├── UPDATE ads_with_urls SET analysis_score = $score
        └── If is_risky: UPSERT INTO risk_db (domain, score, evidence)
```

### Score Ranges

| Score | Meaning |
|-------|---------|
| 0.0 – 0.2 | Legitimate business |
| 0.3 – 0.5 | Uncertain / needs review |
| 0.6 – 1.0 | Likely dropship / scam |
| -1 | Scrape failure (won't be retried) |

### Gemini Prompt Design

The Gemini prompt is tuned for Israeli e-commerce fraud:
- Distinguishes legitimate Israeli businesses, courses, services from dropship gadgets
- Considers: Hebrew business registration (ח.פ.), countdown timers, scarcity widgets, WhatsApp-only contact, unrealistic shipping times, generic product descriptions
- Returns structured JSON with confidence level

### Rate Limiting

- 2-second delay between Gemini API calls
- 3 retries with exponential backoff for 429/RESOURCE_EXHAUSTED errors
- 20 ads per 10-minute window = ~120 ads/hour max throughput

---

## 3. Reporting

### Nightly Combined Email (`nightly_scrape_summary.py`)

Runs at **05:00** daily, after all keyword scraping jobs complete. Sends a single email combining results from all 5 keywords.

**Report format:**
```
Facebook Ads Scrape Summary - February 08, 2026
⏰ Runtime: 00:01:02 - 04:35:50 (4h 34m)

📊 Results:
Total Ads Found: 450
New Advertisers Added: 120
Duplicates Skipped: 330

By Keyword:
✅🟢 150 - מבצע ads, 120 new, 30 duplicates
✅🟢 100 - מוגבל ads, 80 new, 20 duplicates
✅🟢 80 - הנחת ads, 60 new, 20 duplicates
✅🟢 70 - שעות ads, 50 new, 20 duplicates
✅🟢 50 - עכשיו ads, 40 new, 10 duplicates

📁 Database:
- All Advertisers: ~9500 total (added 120 today)
- Ads with Valid URLs: 4800 total (added 180 today)
```

### Daily Analysis Report (`daily_report.py`)

Runs at **00:01** daily. Reports on the *analysis* pipeline (not scraping):
- Ads tested yesterday, risky found, safe cleared
- Scrape errors (score = -1), remaining backlog
- Sent via email + appended to log

---

## 4. Database Schema

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐
│  meta_ads_daily   │    │   advertisers     │    │ ads_with_urls│
│ ─────────────── │    │ ─────────────── │    │ ────────────│
│ id (PK)          │    │ id (PK)          │    │ id (PK)      │
│ advertiser_name  │    │ advertiser_name  │    │ ad_url       │
│ page_url         │    │ page_url         │    │ advertiser   │
│ ad_body          │    │ keyword          │    │ keyword      │
│ external_links   │    │ scraped_at       │    │ scraped_at   │
│ source_keyword   │    └──────────────────┘    │analysis_score│
│ scraped_at       │                            └──────┬───────┘
│ dedup_key (UNQ)  │                                   │
└──────────────────┘                                   │ batch_analyze
                                                       │ (score ≥ 0.6)
                                                       ▼
                         ┌──────────────────┐    ┌──────────────┐
                         │ dropship_analysis│    │   risk_db    │
                         │ ─────────────── │    │ ────────────│
                         │ analysis details │    │ base_url(UNQ)│
                         │ red_flags        │    │ risk_score   │
                         │ aliexpress match │    │ evidence[]   │
                         │ scoring          │    │ advertiser   │
                         └──────────────────┘    │ first_seen   │
                                                 │ last_updated │
                                                 └──────────────┘
                                                       ▲
                                                       │ /check/?url=
                                                 ┌─────┴──────┐
                                                 │ Chrome Ext │
                                                 └────────────┘
```

### Table Purposes

| Table | Purpose | Write Source |
|-------|---------|--------------|
| `meta_ads_daily` | Per-day deduped ads from Playwright scraper | `daily_meta_scrape.py` |
| `advertisers` | All scraped advertisers (legacy + new) | `daily_meta_scrape.py` |
| `ads_with_urls` | Filtered subset with valid external URLs | `daily_meta_scrape.py` |
| `dropship_analysis` | Detailed analysis results | `batch_analyze_ads.py` |
| `risk_db` | Final risk DB — only risky sites, queried by extension | `batch_analyze_ads.py` |

---

## 5. Chrome Extension

### Architecture

Manifest V3 Chrome extension with a 3-tier checking system:

```
User navigates to URL
  ├── Tier 1: Local whitelist (22k+ safe domains) → instant ✅
  ├── Tier 2: Persistent cache (24h TTL, 1000 entries) → instant ✅/⚠️
  └── Tier 3: API call → GET /check/?url=X
        └── FastAPI queries risk_db
              └── Returns {risky, score, evidence} or {risky: false}
```

### Badge Behavior
- **No badge**: Site not in risk_db or whitelisted
- **Red "!"**: Risk score ≥ 0.6 — popup shows warning with evidence

### Key Files

| File | Role |
|------|------|
| `extension/public/background.js` | Service worker — auto-checks on tab navigation |
| `extension/public/config.js` | API base URL configuration |
| `extension/src/App.jsx` | React popup UI |
| `extension/public/manifest.json` | Chrome Manifest V3 |

---

## 6. FastAPI Backend

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Health check |
| `GET` | `/health` | Detailed health info |
| `GET` | `/check/?url=X` | Lightweight risk_db lookup (extension uses this) |
| `POST` | `/analyze/` | On-demand deep analysis (Playwright + Gemini) |
| `GET` | `/whitelist/domains` | Full whitelist |
| `GET` | `/whitelist/check/{domain}` | Single domain whitelist check |

### Middleware
- CORS (all origins — configured for extension access)
- Request logging with timing, client IP, user agent

---

## 7. Cron Schedule (VM)

| Time | Job | Description |
|------|-----|-------------|
| `00:01` | `daily_report.py` | Yesterday's analysis summary email |
| `00:01` | `01_mivtsa.json` | Scrape keyword: מבצע |
| `01:00` | `02_mugbal.json` | Scrape keyword: מוגבל |
| `02:00` | `03_hanaha.json` | Scrape keyword: הנחת |
| `03:00` | `04_shaot.json` | Scrape keyword: שעות |
| `04:00` | `05_achshav.json` | Scrape keyword: עכשיו |
| `05:00` | `nightly_scrape_summary.py` | Combined scrape results email |
| `*/10` | `batch_analyze_ads.py` | Analyze 20 unscored ads |

---

## 8. Infrastructure

- **VM**: Oracle Cloud (Ubuntu 22.04)
- **Database**: PostgreSQL 14 (localhost)
- **Python**: 3.10 (system)
- **Browser**: Playwright Chromium (headless)
- **AI**: Google Gemini 2.0 Flash
- **Email**: Gmail SMTP (App Password)
- **Extension**: Chrome extension served locally (dev mode) or via Chrome Web Store
- **API Tunnel**: Cloudflare Quick Tunnel (development) or direct IP

---

## 9. End-to-End Data Flow

```
1. SCRAPE (nightly, 5 keywords, staggered hourly)
   Meta Ad Library → Playwright → meta_ads_daily + advertisers + ads_with_urls

2. ANALYZE (every 10 min, batch of 20)
   ads_with_urls (unscored) → Playwright site scrape → Gemini AI → risk_db

3. SERVE (real-time)
   Chrome Extension → FastAPI /check → risk_db → badge + popup warning

4. REPORT (daily)
   05:00 → nightly_scrape_summary.py → combined email
   00:01 → daily_report.py → analysis stats email
```
