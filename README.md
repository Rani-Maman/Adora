# Adora

Adora flags dropshipping storefronts that target Israeli shoppers. It's a browser
extension backed by a pipeline that scrapes the Meta Ad Library every night, analyzes
the sites behind the ads, and maintains a risk database the extension queries as you
browse.

A lot of "Israeli" shops advertised on Facebook and Instagram are AliExpress
dropshippers with big markups, fake countdown timers and no real support. They churn
domains fast enough that a static blocklist is useless, so Adora finds them where they
actually acquire customers: the ads themselves.

## How it works

1. **Scrape** — nightly cron jobs query the Ad Library's GraphQL endpoint for Hebrew
   sale keywords (מבצע, מוגבל, הנחת...) and collect advertisers and their landing
   URLs. Plain HTTP, no browser automation.
2. **Analyze** — Playwright loads each new site and Gemini 2.5 Flash classifies it
   (dropship / legit / service / uncertain) with a 0.0–1.0 risk score and evidence.
3. **Price match** — products on risky sites are matched against AliExpress listings
   to show the actual markup.
4. **Serve** — anything scoring ≥ 0.6 lands in `risk_db`. The extension calls the
   FastAPI `/check` endpoint and shows a warning banner with the price comparison.

The extension ships with a ~22K trusted-domain whitelist baked in at build time, so it
never phones home for known-safe sites. Unknown domains are checked once and cached
locally. Signed-in users can vote on verdicts, which feeds back into the database.

Architecture diagrams and the full pipeline write-up live in [docs/](docs/).

## Repo layout

```
backend/app/       FastAPI app — API routes, Gemini scoring, DB access
backend/scripts/   cron jobs: scraper, batch analysis, price matching, summaries
backend/data/      whitelist files
extension/         Chrome extension (MV3) — React popup, vanilla JS service worker
docs/              architecture and scraping pipeline notes
```

## Running the backend

Needs Python 3.11 and PostgreSQL 14 (schema in `backend/scripts/create_tables.sql`).

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Configuration comes from `backend/.env` — see `app/config.py` for the expected keys
(DB credentials, `GEMINI_API_KEY`, etc.).

Tests: `pytest tests/ -v`. Tests that need Playwright skip themselves when it isn't
installed.

## Building the extension

```bash
cd extension
npm install
npm run build
```

The build step generates `public/config.js` from `extension/.env` (`API_BASE`,
`RISK_THRESHOLD`) and embeds the whitelist. Load `extension/dist/` as an unpacked
extension via `chrome://extensions`.

## Production

Everything runs on a single small cloud VM: the API as a systemd service behind nginx
and a Cloudflare tunnel, with the scrape → analyze → price-match schedule driven by
cron. Scrape jobs are staggered an hour apart to stay under Meta's rate limits, and
analysis runs are timed so Playwright and the scraper never compete for the VM's
memory. Details in [docs/scraping_pipeline.md](docs/scraping_pipeline.md).
