# Adora — Scraping Pipeline & System Architecture

> Last updated: February 2026

## Overview

Adora is an Israeli dropship/scam detection system. It scrapes Facebook/Meta Ad Library for Hebrew keyword ads via HTTP GraphQL API, analyzes advertiser product sites using Playwright + Gemini AI, and serves risk scores to a Chrome extension in real-time.

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
│   05:00  ┌──────────────┐                                   │
│   ──────►│ Keyword Job 6│  (mishloach_chinam / משלוח חינם)   │
│          └──────────────┘                                   │
│   06:00  ┌──────────────────────┐                           │
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

The scraper uses **HTTP requests to Meta's GraphQL API** (`meta_ads_http_scraper.py`) to search the Ad Library for Hebrew keywords targeting Israel and extract ad data (advertiser name, page URL, ad body text, link URLs). No browser automation needed for this stage.

Each keyword runs as an independent cron job, staggered 1 hour apart to avoid rate limits.

### Components

| File | Role |
|------|------|
| `backend/scripts/daily_meta_scrape.py` | Orchestrator: config loading, DB inserts, reporting |
| `backend/scripts/meta_ads_http_scraper.py` | HTTP GraphQL scraper (pagination, extraction) |
| `backend/scripts/run_meta_keyword_job.sh` | Bash wrapper with locking, cleanup, timeouts |
| `backend/scripts/configs/meta_keywords/*.json` | Per-keyword config files (search URL, params) |

### Scraper Flow

```
run_meta_keyword_job.sh
  ├── flock (prevent concurrent runs)
  ├── cleanup Playwright orphans
  ├── timeout --signal=TERM $HARD_TIMEOUT
  └── python3 daily_meta_scrape.py --config $CONFIG
  │     ├── Load keyword config JSON
  │     ├── For each ad library search link:
  │     │     ├── meta_ads_http_scraper.scrape_meta_ads_http()
  │     │     │     ├── POST to Meta GraphQL endpoint
  │     │     │     ├── Paginate via forward_cursor (max 250 pages)
  │     │     │     ├── Extract: advertiser_name, page_url, ad_body, external_links
  │     │     │     └── Stop when: no more results, target_ads reached, or runtime exceeded
  │     │     └── Filter: remove social URLs (fb, ig, wa, messenger)
  │     ├── select_rows_for_keyword() — dedup + select unique advertisers
  │     ├── Dedup by SHA1(date + keyword + normalized_name)
  │     ├── Insert into meta_ads_daily + meta_ads_daily_with_urls
  │     ├── Insert into advertisers + ads_with_urls (legacy tables)
  │     └── Save JSON output + log files
  │
  └── Rate-limit retry (post-run)
        ├── If exit=0 AND runtime <5 min AND ads_captured <1000:
        │     ├── Log "Rate-limited" with ad count + elapsed time
        │     ├── Sleep 25 min (Meta cooldown ~20-30 min)
        │     └── Re-run same scraper command once
        └── Stays within 1h slot (25 min wait + ~15 min retry = ~40 min max)
```

### Scraper Settings (run_meta_keyword_job.sh defaults)

| Setting | Default | Description |
|---------|---------|-------------|
| `retries` | `1` | Retry attempts per search link (2 total tries) |
| `max-runtime-sec` | `1800` | Max scraping time per link (30 min) |
| `target-ads-per-link` | `7500` | Stop pagination after N ads (local only) |
| `max-advertisers-per-keyword` | `0` | Advertiser cap per keyword (0 = uncapped) |
| `max_pages` | `250` | Max GraphQL pagination pages (in scraper code) |
| `per-link-timeout-sec` | `1850` | Hard timeout per link including overhead |
| `max-total-minutes` | `35` | Total job timeout |
| Rate-limit retry: min runtime | `300s` | Jobs finishing faster than this are considered rate-limited |
| Rate-limit retry: min ads | `1000` | Jobs below this ad count trigger retry |
| Rate-limit retry: delay | `1500s` | 25 min wait before retry (Meta cooldown) |

### Deduplication

- **Key**: `SHA1(scrape_date + keyword + normalized_advertiser_name)`
- Normalized = lowercase → strip non-alphanumeric → collapse whitespace
- `ON CONFLICT DO NOTHING` — duplicates silently skipped

### URL Filtering

External URLs are extracted from ad text/links. The following are excluded:
- Social platforms: `facebook.com`, `instagram.com`, `whatsapp.com`, `wa.me`, `messenger.com`
- Marketplace/internal: `marketplace.facebook.com`
- URL shorteners: `bit.ly`, `tinyurl.com`, etc.

---

## 2. Analysis Pipeline — Batch Scoring

### Architecture

Every 10 minutes, `batch_analyze_ads.py` picks up **10 unscored ads** from `ads_with_urls` and runs them through a two-stage analysis. Uses **psycopg2** for all DB writes (parameterized queries). Over-fetches 5x from SQL and filters in Python via `should_skip_url()` (skip patterns + whitelist). Skipped ads are marked `score=0.0, category='skipped'` so they don't clog the backlog.

1. **Playwright Site Scrape** — Visit the advertiser's product URL, extract structured data
2. **Gemini 2.5 Flash AI Scoring** — Send site data to Google's Gemini API with Google Search grounding for verification-based fraud analysis

### Components

| File | Role |
|------|------|
| `backend/batch_analyze_ads.py` | Main batch processor |
| `backend/app/analysis/gemini_scorer.py` | Gemini API scorer (also used by FastAPI) |
| `backend/app/scraping/site_scraper.py` | Playwright site data extractor |

### Scoring Flow

```
batch_analyze_ads.py (cron: */10)
  ├── SELECT 50 rows FROM ads_with_urls WHERE analysis_score IS NULL (5x over-fetch)
  ├── Filter with should_skip_url() → mark skipped (score=0.0), keep up to 10
  ├── Launch single Playwright browser (reused for batch)
  └── For each ad:
        ├── Navigate to product URL
        ├── If no price: follow /products/ links or CTA buttons (advertorial pages)
        ├── Extract SiteData:
        │     title, product_name, price, shipping_time,
        │     business_id (ח.פ.), countdown_timer, scarcity_widgets,
        │     whatsapp_only_contact, page_text (4000 chars)
        ├── Send to Gemini 2.5 Flash with Google Search grounding
        │     └── Gemini verifies: business identity, AliExpress matches, address/ח.פ.
        ├── Parse JSON response: {score, is_risky, category, reason, evidence}
        ├── Normalize category to enum: dropship|legit|service|uncertain
        ├── UPDATE ads_with_urls SET analysis_score, analysis_category, analysis_json
        ├── If is_risky: UPSERT INTO risk_db (domain, score, evidence)
        └── If re-analysis scored < 0.6: DELETE from risk_db (clean false positives)
```

### Score Ranges

| Score | Meaning |
|-------|---------|
| 0.0 | Skipped (whitelisted/filtered URL) |
| 0.0 – 0.2 | Legitimate business |
| 0.3 – 0.5 | Uncertain / needs review |
| 0.6 – 1.0 | Likely dropship / scam |
| -1 | Scrape failure (won't be retried) |

### Gemini Prompt Design

The Gemini 2.5 Flash prompt uses **Google Search grounding** for verification-based analysis:
- Gemini searches the web to verify business identity (Google reviews, social media, ח.פ.)
- Searches AliExpress/Temu for identical products at lower prices
- Verifies physical addresses and business registration claims
- Pushes for decisive scoring — avoids 0.4–0.6 range unless genuinely uncertain after search
- Legitimate bias rules: furniture/home décor (0.0-0.2), jewelry/watches (legit unless confirmed AliExpress match), sub-₪100 items (only flag generic gadgets/electronics with 3x+ markup)
- Returns structured JSON: `{score, is_risky, category, reason, evidence}`
- Category constrained to enum: `dropship`, `legit`, `service`, `uncertain`
- Post-parse normalization maps freeform responses to the enum

### Rate Limiting

- 4-second delay between Gemini API calls (grounded calls are heavier)
- 3 retries with exponential backoff for 429/RESOURCE_EXHAUSTED errors
- 10 ads per 10-minute window = ~30 ads/hour max throughput

---

## 3. Reporting

### Nightly Combined Email (`nightly_scrape_summary.py`)

Runs at **06:00** daily, after all keyword scraping jobs complete. Sends a single email combining results from all 6 keywords.

Data sources: DB queries (per-keyword counts) + JSON report files (runtime, selected counts). Falls back to JSON data when DB returns 0 (due to ON CONFLICT DO NOTHING not updating scraped_at for returning ads).

**Report format:**
```
Adora Nightly Scrape — February 08, 2026
Target date: 2026-02-07

⏰ 00:01:02 — 04:35:50 (4h 34m)

📊 Results:
  Ads Scraped: 1850
  With Valid URLs: 1200
  New Advertisers: 400
  Returning Advertisers: 1450

📋 By Keyword:
  ✅ מבצע — 500 ads | 120 new, 380 returning | 12m 30s
  ✅ מוגבל — 400 ads | 80 new, 320 returning | 10m 15s
  ✅ הנחת — 350 ads | 70 new, 280 returning | 9m 45s
  ✅ שעות — 300 ads | 60 new, 240 returning | 8m 20s
  ✅ עכשיו — 300 ads | 70 new, 230 returning | 7m 50s

💾 Database Totals:
  meta_ads_daily: ~15,000
  meta_ads_daily_with_urls: ~8,000
  advertisers: ~9,500 (+400 today)
```

### Daily Analysis Report (`batch_analyze_daily_summary.py`)

Runs at **23:00** daily. Reports on the *analysis* pipeline (not scraping):
- Ads tested today, risky found, safe cleared
- Scrape errors (score = -1), remaining backlog
- Sent via email

### Price Match Report (`batch_price_match.py`)

Runs at **23:30** daily via `run_price_match.sh` (flock, max 30 min runtime to finish before 00:01 scraper). Sends email with:
- Products processed/matched/skipped/failed, match rate %
- Top 3 highest-markup finds (domain, product, markup ratio)
- Runtime

---

## 4. Price Matching Pipeline

### Architecture

`batch_price_match.py` uses **Gemini 2.5 Flash with Google Search grounding** to find cheaper alternatives for products on risky sites. It queries all domains in `risk_db`, scrapes their product pages for names and ILS prices, then asks Gemini to search AliExpress, Temu, Alibaba, and other wholesale platforms for matching products.

### Components

| File | Role |
|------|------|
| `backend/scripts/batch_price_match.py` | Main batch processor + email summary |
| `backend/scripts/run_price_match.sh` | Cron wrapper with flock, env loading, logging (max runtime 1800s) |

### Flow

```
run_price_match.sh (cron: 23:30, retry at 06:01)
  ├── flock (prevent concurrent runs)
  ├── Load .env, forward extra args ($@) to python
  └── python3 batch_price_match.py --max-runtime 1800 [--retry-failures]
        ├── Pre-filter: skip known-bad URL patterns
        ├── Query risk_db for eligible products (excludes already matched + failed)
        │   (--retry-failures: query price_match_failures with DISTINCT ON dedup)
        ├── For each product:
        │     ├── SiteScraper.scrape() → (text, screenshot)
        │     ├── Gemini extract: Hebrew page → English product name + ILS price
        │     │     └── Prompt looks for [PRICE_HINT], [PRICE_ELEMENT] tags
        │     ├── Screenshot fallback: Gemini visual price extraction
        │     ├── If price > 0: Gemini search (grounded) → AliExpress/Temu/Alibaba
        │     │     └── On parse fail: retry strict prompt → regex fallback
        │     ├── On success: save to price_matches JSONB, clear failure if retry
        │     └── On failure: save to price_match_failures JSONB with reason
        ├── Send email summary (always, via finally block)
        └── Log to /home/ubuntu/adora_ops/logs/price_match/
```

### SiteScraper — Price Extraction Cascade

The scraper uses a multi-layered approach to find product prices on advertorial/funnel pages:

```
SiteScraper.scrape(url) → (text[:12000], screenshot)
  │
  ├── 1. Load page (domcontentloaded, 5s wait)
  │     └── If <200 chars: retry with networkidle
  │
  ├── 2. CSS price selectors on landing page
  │     (.price, [data-price], .product-price, .woocommerce-Price-amount,
  │      [class*="price"], [class*="Price"], .product__price, .current-price)
  │     → Appends [PRICE_ELEMENT] tag
  │
  ├── 3. Advertorial suffix stripping
  │     /adv, /advertorial, /landing, /adv-, /lp → strip to parent URL
  │     Navigate to product page, scroll to bottom, extract text (6000 chars)
  │     + CSS price + price-region extraction + screenshot of product page
  │
  ├── 4. Multi-CTA link following (up to 5 unique links)
  │     JS evaluates all <a> elements for CTA text or /products/ path matches
  │     Iterates links until one has a ₪ price — replaces previous no-price page
  │     Also collects #next-step anchor links for step 5
  │     + CSS price + price-region extraction + screenshot of product page
  │
  │     CTA text patterns (Hebrew + English):
  │       לרכישה, הזמינו עכשיו, הזמינו, הזמן עכשיו, הזמן, לרכוש,
  │       בדיקת זמינות, קבלו, להזמנה, קנה עכשיו, קנה, קנו, לקנייה,
  │       הוסף לסל, למוצר, לפרטים נוספים, להזמנה עכשיו, לצפייה במוצר,
  │       אני רוצה, רוצה להזמין, בדקי, בדוק, צפה, צפו,
  │       add to cart, buy now, order now, shop now, get yours
  │
  │     Product path regex: /products?/ or /order
  │     Bad path filter: /cart, /policy, /terms, /privacy, /contact, /about, /faq, /return, /shipping
  │
  ├── 5. #next-step anchor clicks (up to 3)
  │     Same-page anchors with hash matching: next, order, checkout, buy, step
  │     Clicks anchor on original page, waits 4s, re-extracts body text
  │     → Appends [AFTER_ANCHOR] if ₪ newly appears
  │
  ├── 6. Homepage fallback (if page <200 chars and no CTA found)
  │     Navigate to root domain, scroll, extract text + CSS + price-region + screenshot
  │     → Appends [HOMEPAGE] tag
  │
  ├── 7. CTA button clicks (if still no ₪ in text)
  │     Finds <button>, [role="button"], input[type="submit"] (up to 25)
  │     Matches: קנה, הזמינו, הזמן, לרכוש, הוסף לסל, הוסף להזמנה, buy, order
  │     Clicks first match, waits 3s, re-extracts body text
  │     → Appends [AFTER_CLICK] if ₪ found (checkout drawer/popup)
  │
  ├── 8. Regex price extraction on assembled text
  │     Pattern: ₪\s*(\d[\d,\.]+) or (\d[\d,\.]+)\s*₪
  │     → Prepends [PRICE_HINT: ₪NNN] tag for LLM
  │
  └── 9. Screenshot (JPEG, quality=50, viewport only)
        Targets best page: product page > homepage > landing page
        Used for Gemini visual price extraction fallback
```

### Price-Region Extraction

JS helper that extracts text near price elements + bottom of page (for advertorial pages where price is at the bottom):
- Text around `[class*="price"]`, `[data-price]` elements (500 chars each)
- 400-char window around first ₪ symbol occurrence
- Bottom 2000 chars of page body
- Returns up to 3000 chars as `[PRICE_REGION]` tag

### BAD_URL_PATTERNS (pre-filter)

URLs matching these patterns are immediately skipped as `url_pattern_filtered`:
- Telegram: `t.me`
- Landing page builders: `minisite.ms`, `ravpage.co.il`
- URL shorteners: `bit.ly`, `did.li`, `tinyurl.com`, `urlgeni.us`, `linktr.ee`
- Group invitations: `vp4.me`
- Click trackers: `/click?key=`
- Category pages: `/collections`, `/product-category`, `/categories` (trailing)

### Failure Tracking

Failed products are tracked in `risk_db.price_match_failures` JSONB with reason codes:
- `url_pattern_filtered` — known non-product URL (see BAD_URL_PATTERNS)
- `scrape_empty` — Playwright got no text (JS-only render, bot-blocked, dead page)
- `extraction_failed` — Gemini couldn't parse product info from page text
- `no_product_name` — page has no identifiable product
- `no_price` — all 9 price extraction steps failed to find an ILS price

Failed URLs are excluded from normal runs. `--retry-failures` re-processes them with `DISTINCT ON` dedup (prevents same URL being retried from multiple failure entries).

### Price Match Data Format (JSONB)

```json
[
  {
    "product_name_english": "Electric Facial Hair Remover",
    "price_ils": 197.0,
    "product_url": "https://site.co.il/products/...",
    "matched_at": "2026-02-13T09:13:40",
    "matches": [
      {
        "source": "Temu",
        "title": "Rechargeable Eyebrow Razor...",
        "price_usd": 3.74,
        "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/...",
        "match_type": "similar"
      }
    ]
  }
]
```

**Note:** Most URLs are temporary Gemini grounding redirects (`vertexaisearch.cloud.google.com`). The extension replaces these with platform search links at display time.

---

## 5. Database Schema

7 tables total. Schema defined in `backend/scripts/create_tables.sql`.

```
┌──────────────────┐    ┌────────────────────────┐
│  meta_ads_daily   │    │ meta_ads_daily_with_urls│
│ ─────────────── │    │ ────────────────────── │
│ ad_unique_key(UNQ)│    │ ad_unique_key (UNQ)    │
│ advertiser_name  │    │ advertiser_name        │
│ ad_text          │    │ destination_product_url │
│ source_keyword   │    │ source_keyword         │
│ scraped_at       │    │ scraped_at             │
└──────────────────┘    └────────────────────────┘

┌──────────────────┐    ┌──────────────────────┐
│   advertisers     │    │    ads_with_urls      │
│ ─────────────── │    │ ────────────────────│
│ advertiser_name  │    │ advertiser_name(UNQ) │
│   (UNQ)          │    │ destination_url(UNQ) │
│ scraped_at       │    │ analysis_score       │
└──────────────────┘    │ analysis_json (JSONB)│
                        │ analyzed_at          │
                        └──────────┬───────────┘
                                   │ batch_analyze
                                   │ (score ≥ 0.6)
                                   ▼
                        ┌──────────────────┐
                        │    risk_db         │
                        │ ───────────────── │
                        │ base_url (UNQ)    │
                        │ risk_score        │
                        │ evidence[]        │
                        │ price_matches JSON│
                        │ pm_failures  JSON│
                        │ first/last_updated│
                        └───────────────────┘
                               ▲
                               │ /check/?url=
                        ┌──────┴───────┐
                        │  Chrome Ext  │
                        └──────────────┘
```

### Table Purposes

| Table | Purpose | Write Source |
|-------|---------|--------------|
| `meta_ads_daily` | All scraped ads, deduped by SHA1 key | `daily_meta_scrape.py` |
| `meta_ads_daily_with_urls` | Subset with valid external URLs | `daily_meta_scrape.py` |
| `advertisers` | Unique advertisers by name | `daily_meta_scrape.py` |
| `ads_with_urls` | Unique ads by destination URL, scored by analysis | `daily_meta_scrape.py` + `batch_analyze_ads.py` |
| `risk_db` | Risky domains (score >= 0.6) + price_matches JSONB + advertiser_name, queried by extension | `batch_analyze_ads.py` + `batch_price_match.py` |
| `users` | Google OAuth users (google_id, email, display_name, avatar_url) | `POST /auth/google` |
| `community_reports` | User-submitted dropship site reports (reported_url, cheaper_url, status) | `POST /report` |

---

## 6. Chrome Extension

### Architecture

Manifest V3 Chrome extension with Google OAuth login, floating widget UI, and 3-tier risk checking:

```
User navigates to URL
  ├── Tier 1: Local whitelist (22k+ safe domains) → instant ✅
  ├── Tier 2: Persistent cache (24h TTL, 1000 entries) → instant ✅/⚠️
  └── Tier 3: API call → GET /check/?url=X
        └── FastAPI queries risk_db
              └── Returns {risky, score, evidence} or {risky: false}
```

### Authentication

Google OAuth via `chrome.identity.launchWebAuthFlow()`. Mandatory — all widget content blocked until sign-in.

```
Login flow:
  1. User clicks "Sign in with Google"
  2. chrome.identity.launchWebAuthFlow() → Google OAuth dialog
  3. Extract access_token from redirect URL
  4. POST /auth/google (exchange Google token → JWT)
  5. Store JWT + user profile in chrome.storage.local
  6. Widget re-renders with authenticated content
```

- **Token storage**: `chrome.storage.local` (persists across browser restarts)
- **Token expiry**: JWT valid 30 days (`JWT_EXPIRY_HOURS=720` on VM)
- **401 handling**: background.js clears stored auth on expired token → widget shows login screen
- **Logout**: clears `adoraAccessToken` + `adoraUser` from storage → login screen

### Widget UI (`content.js`)

Floating draggable widget injected via Shadow DOM (`attachShadow({mode: 'closed'})`).

**States:**
1. **Login screen** (not signed in): Sign In button, "What is Adora?" (info modal), "Contact Us" (mailto). All other content blocked.
2. **Risky site** (signed in, score >= 0.6): Warning alert, price comparison cards, dropship info "i" button, disclaimer
3. **Safe site** (signed in, not risky): Education card (Adora intro + collapsible "What is dropshipping?"), community report CTA
4. **Loading**: Spinner while CHECK_URL response pending
5. **Minimized**: Small pill with icon, click to expand

**Header** (all states): Logo, title, lang toggle (EN/עב), theme toggle (light/dark), minimize, close

### Community Reports

Signed-in users can report dropshipping sites from the safe-site view.

```
Report flow:
  1. CTA card shown on safe sites: "Want to report a site?"
  2. Click → modal overlay with form (site URL, cheaper product link)
  3. POST /report → JWT auth, 3/day rate limit (DB-enforced)
  4. GET /report/remaining → shows X/3 reports left today
```

- **Rate limit**: 3 reports per user per 24h (enforced in DB via COUNT with interval)
- **CTA disabled**: when 0 reports remaining (dimmed + "Daily limit reached" text)
- **Validation**: both URLs required, must start with `https?://`, max 2000 chars

### Price Display Logic
- USD→ILS conversion: `price_usd / 0.27`
- Deduplication: similar product names collapsed (substring match after normalization)
- Per product: cheapest match from each unique source (up to 3 sources)
- Expired Google redirect URLs replaced with platform search links (AliExpress, Temu, Alibaba)
- Source names normalized: Gemini output mapped to canonical names (AliExpress, Temu, Alibaba, Amazon, etc.)

### Key Files

| File | Role |
|------|------|
| `extension/public/background.js` | Service worker — API calls, caching, badge updates, auth |
| `extension/public/content.js` | Content script — floating widget with Shadow DOM |
| `extension/public/config.js` | Auto-generated config (API base, whitelist, thresholds) |
| `extension/build-config.js` | Build script — generates config.js from .env + whitelists |
| `extension/public/manifest.json` | Chrome Manifest V3 |

---

## 7. FastAPI Backend

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/` | — | Health check |
| `GET` | `/health` | — | Detailed health info |
| `GET` | `/check/?url=X` | API key | Risk lookup + price_matches from risk_db |
| `POST` | `/analyze/` | API key | On-demand deep analysis (Playwright + Gemini) |
| `GET` | `/whitelist/domains` | API key | Full whitelist |
| `GET` | `/whitelist/check/{domain}` | API key | Single domain whitelist check |
| `POST` | `/auth/google` | API key | Exchange Google token → JWT + upsert user |
| `POST` | `/report` | JWT + API key | Submit community report (3/day limit) |
| `GET` | `/report/remaining` | JWT + API key | Get remaining daily report count |

### Auth
- **API key**: `X-API-Key` header required on all protected paths
- **JWT**: `Authorization: Bearer <token>` for user-specific endpoints (`/report`)
- JWT created via `auth_utils.create_access_token()`, validated via `require_user()` dependency
- JWT payload: `{sub, email, iss, aud, iat, exp}`, signed HS256

### Middleware
- CORS (all origins — configured for extension access)
- API key validation middleware (checks `X-API-Key` header on protected paths)
- Request logging with timing, client IP, user agent

---

## 8. Cron Schedule (VM)

| Time | Job | Description |
|------|-----|-------------|
| `00:01` | `01_mivtsa.json` | Scrape keyword: מבצע |
| `01:00` | `02_mugbal.json` | Scrape keyword: מוגבל |
| `02:00` | `03_hanaha.json` | Scrape keyword: הנחת |
| `03:00` | `04_shaot.json` | Scrape keyword: שעות |
| `04:00` | `05_achshav.json` | Scrape keyword: עכשיו |
| `05:00` | `06_mishloach_chinam.json` | Scrape keyword: משלוח חינם |
| `06:00` | `nightly_scrape_summary.py` | Combined scrape results email |
| `06:01` | `run_price_match.sh --retry-failures` | Retry previously failed price matches |
| `*/10 13-23` | `run_batch_dispatch.sh` | Dispatcher: queries both queues, runs whichever has work |
| `23:00` | `batch_analyze_daily_summary.py` | Daily analysis summary email |
| `23:30` | `run_price_match.sh` | Batch price matching (after analysis window) |

### Dispatcher (`run_batch_dispatch.sh`)

Each */10 cron tick, the dispatcher queries the DB for both queue sizes:
- **Unscored ads** (`ads_with_urls WHERE analysis_score IS NULL`)
- **Eligible price match products** (risk_db domains with dropship ads, no existing match/failure)

Priority: analyze > price_match > nothing.
- If unscored ads > 0 → run `batch_analyze_ads.py`
- Else if eligible products > 0 → run `run_price_match.sh`
- Else → exit (nothing to do)

Price match flock prevents overlap with morning price_match crons.

---

## 9. Infrastructure

- **VM**: Oracle Cloud (Ubuntu 22.04, 956MB RAM, 2 cores)
- **Database**: PostgreSQL 14 (localhost)
- **Python**: 3.10 (system)
- **Meta Scraper**: HTTP GraphQL (no browser needed)
- **Site Analyzer**: Playwright Chromium (headless, batch_analyze only)
- **AI**: Google Gemini 2.5 Flash (analysis + price match, with Google Search grounding)
- **Email**: Gmail SMTP (App Password)
- **Extension**: Chrome extension served locally (dev mode) or via Chrome Web Store
- **API Tunnel**: Cloudflare tunnel (cloudflared service)

---

## 10. End-to-End Data Flow

```
1. SCRAPE (nightly, 6 keywords, staggered hourly)
   Meta Ad Library GraphQL API → HTTP scraper → meta_ads_daily + meta_ads_daily_with_urls + advertisers + ads_with_urls

2. ANALYZE (every 10 min, batch of 10)
   ads_with_urls (unscored) → Playwright site scrape → Gemini 2.5 Flash (grounded) → update ads_with_urls + upsert risk_db
   Category normalized to enum: dropship|legit|service|uncertain
   Re-analysis: if score drops below 0.6 → DELETE from risk_db (latest score replaces old)

3. PRICE MATCH (daily at 23:30, retry at 06:01)
   risk_db (risky domains) → batch_price_match.py → Playwright scrape (multi-layer price extraction)
   → Gemini 2.5 Flash extract product info → Gemini 2.5 Flash (grounded) search AliExpress/Temu/Alibaba
   → price_matches JSONB in risk_db → Email summary with match rate, top markups, runtime

4. SERVE (real-time)
   Chrome Extension → FastAPI /check → risk_db (score + price_matches) → banner + popup
   Extension shows: risk warning, cheaper alternatives (multi-source), markup badges, search links

5. REPORT (daily)
   06:00 → nightly_scrape_summary.py → combined scrape email
   23:30 → run_price_match.sh → price match email with stats + top markups
   23:00 → batch_analyze_daily_summary.py → analysis stats email
```
