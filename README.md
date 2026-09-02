# Adora

<img width="128" height="128" alt="Adora Logo no background (3)" src="https://github.com/user-attachments/assets/d047bac2-c858-455c-9188-56acdbec97c0" />

Adora flags dropshipping storefronts that target Israeli shoppers. It's a browser
extension - Chrome and Safari, desktop and iPhone - backed by a pipeline that scrapes
the Meta Ad Library every night, analyzes the sites behind the ads, and maintains a
risk database the extension queries as you browse.

A lot of "Israeli" shops advertised on Facebook and Instagram are AliExpress
dropshippers with big markups, fake countdown timers and no real support. They churn
domains fast enough that a static blocklist is useless, so Adora finds them where they
actually acquire customers: the ads themselves.

## How it works

1. **Scrape** - nightly cron jobs query the Ad Library's GraphQL endpoint for Hebrew
   sale keywords and collect advertisers and their landing
   URLs. Plain HTTP, no browser automation.
2. **Analyze** - Playwright loads each new site and Gemini 2.5 Flash classifies it
   (dropship / legit / service / uncertain) with a 0.0–1.0 risk score and evidence.
3. **Price match** - products on risky sites are matched against AliExpress listings
   to show the actual markup.
4. **Serve** - anything scoring ≥ 0.6 lands in `risk_db`. The extension calls the
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
extension/         Browser extension (MV3) — Chrome and Safari/iOS builds
docs/              architecture, scraping pipeline, user system, iOS/Safari notes,
                   project poster and presentation
```

## Stack

Python 3.11 / FastAPI / PostgreSQL backend, Gemini 2.5 Flash for site analysis,
Playwright for page scraping. The extension is Manifest V3 with a React popup and a
vanilla JS service worker, built with Vite. The same source builds as a Safari Web
Extension, wrapped as an iOS app so it runs in Safari on iPhone — capabilities that
differ between the two browsers are resolved at runtime rather than forked into
separate builds. See [docs/ios_safari_extension.md](docs/ios_safari_extension.md).

## Deployment

Everything runs on a single small cloud VM: the API as a systemd service behind nginx
and a Cloudflare tunnel, with the scrape → analyze → price-match schedule driven by
cron. Scrape jobs are staggered an hour apart to stay under Meta's rate limits, and
analysis runs are timed so Playwright and the scraper never compete for the VM's
memory. Details in [docs/scraping_pipeline.md](docs/scraping_pipeline.md).

## Project Materials

Authentication and account design is documented in
[docs/user_system_public.md](docs/user_system_public.md). The project poster
([docs/Adora_Poster.jpg](docs/Adora_Poster.jpg)) and presentation
([docs/AdoraPresentation.pptx](docs/AdoraPresentation.pptx)) summarise the product and
architecture for review.
