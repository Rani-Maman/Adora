# Adora public website

Static shop-checker page (HTML/CSS/JS) for shoppers who want to check a URL before buying.

It talks to the existing FastAPI backend with a simple DB lookup. **No Gemini runs from the website.**

## Current status

| Item | Status |
|------|--------|
| Live UI on Vercel | Yes — [website-chi-murex-67.vercel.app](https://website-chi-murex-67.vercel.app/) |
| Connected to production API | **Not yet** — `config.js` has an empty `ADORA_API_BASE` |
| Custom domain (e.g. adoracheck.com) | Optional / separate step |
| Legal pages | Draft only (`appeal@adora.placeholder`) |

Until `ADORA_API_BASE` points at a public API that supports the endpoints below, the page loads but **Check does nothing useful**.

## What this folder is

| File | Purpose |
|------|---------|
| `index.html` | Main page: stats banner, URL form, EN/HE toggle |
| `app.js` | Frontend logic (language, check request, result UI) |
| `styles.css` | Layout and styling |
| `config.js` | Public API base URL (safe to commit; not a secret) |
| `config.example.js` | Example config |
| `sources.html` | Citations for banner industry stats |
| `terms.html` / `privacy.html` | Legal drafts |
| `vercel.json` | Vercel static settings (`cleanUrls`) |

## How it works (when API is connected)

```
Browser (Vercel)
  → POST /check/     → FastAPI looks up risk_db
       ├─ found     → "Potential dropshipping"
       └─ not found → "Not in database yet" (+ queue pending_domains)
  → GET /check/stats → count of domains in risk_db (banner)
```

Gemini stays in the nightly/batch pipeline only — never called from this site.

## Local preview

1. Set API in `config.js`:

```javascript
window.ADORA_API_BASE = "http://localhost:8000";
```

2. Run the FastAPI backend (with DB env vars set).

3. Serve this folder:

```powershell
cd website
python -m http.server 3000
```

4. Open http://localhost:3000

## Backend requirements (separate from this PR)

The website expects these API routes (must exist and be deployed on the VM):

- `POST /check/` — public, rate limited, no API key; DB lookup only
- `GET /check/stats` — public count for the banner

Useful env vars on the API server:

```env
ADORA_WEB_ORIGINS=https://website-chi-murex-67.vercel.app
ADORA_WEB_RATE_LIMIT_SECONDS=60
ADORA_API_KEY=...   # still required for the Chrome extension GET /check
```

Schema note: unknown website checks may write to `pending_domains` (see `backend/scripts/create_tables.sql`).

## Deploy / update on Vercel

Already deployed. For future updates from GitHub:

1. Vercel project **Root Directory:** `website`
2. Framework: Other (static) — no build command
3. Set `config.js` → production API URL, then redeploy
4. Keep `ADORA_WEB_ORIGINS` on the API aligned with the Vercel URL (and custom domain later)

### Configure API URL before Check works in production

```javascript
window.ADORA_API_BASE = "https://api.your-domain.com";
```

Then redeploy Vercel.

### Quick test (after API is connected)

- Domain in `risk_db` → **Potential dropshipping**
- Unknown domain → **Not in database yet**
- Second check within ~60s from same IP → **429 rate limited**

## Optional: lock down API while testing

The Vercel site can stay public. If you want only you + partner hitting the API:

- Cloudflare WAF allowlist on `api.*`, or
- nginx `allow` / `deny` on the VM

## Before a real public launch

- [ ] Replace `appeal@adora.placeholder` with a real contact email
- [ ] Lawyer review of Terms / Privacy
- [ ] Point `ADORA_API_BASE` at a stable API hostname
- [ ] Optional: attach custom domain in Vercel
- [ ] Tighten `ADORA_WEB_ORIGINS` to the final site URL(s) only
