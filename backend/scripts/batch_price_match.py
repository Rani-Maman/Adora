#!/usr/bin/env python3
"""
Batch price matcher — finds cheaper AliExpress/Temu alternatives for risk_db products.
Scrapes product pages with Playwright, uses Gemini 2.5 Flash + Google Search grounding.
Stores results in risk_db.price_matches JSONB column.

Usage: python3 batch_price_match.py [--max-runtime 3600] [--retry-failures]
"""

import asyncio
import json
import os
import re
import signal
import sys
import time
import logging
from datetime import datetime, timezone

import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv

# Parse args before load_dotenv so --dotenv-path works
_parser = argparse.ArgumentParser()
_parser.add_argument("--max-runtime", type=int, default=3600)
_parser.add_argument("--dotenv-path", default=None)
_parser.add_argument("--retry-failures", action="store_true",
                     help="Retry previously failed products instead of new ones")
_args, _ = _parser.parse_known_args()

load_dotenv(_args.dotenv_path if _args.dotenv_path else None)

from playwright.async_api import async_playwright, Browser
from google import genai
from google.genai import types
import psycopg2
from psycopg2.extras import Json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Config
MODEL = "gemini-2.5-flash"
ILS_TO_USD = 0.27
GEMINI_CALL_DELAY = 2  # seconds between API calls
MAX_RUNTIME = _args.max_runtime
RETRY_MODE = _args.retry_failures

# URL patterns that are never real product pages
BAD_URL_PATTERNS = [
    r"^https?://(www\.)?t\.me/",
    r"^https?://[^/]*minisite\.ms/",
    r"^https?://[^/]*urlgeni\.us/",
    r"^https?://[^/]*ravpage\.co\.il/",
    r"^https?://[^/]*bit\.ly/",
    r"^https?://[^/]*linktr\.ee/",
    r"/collections/?$",
    r"/product-category/?$",
    r"/categories/?$",
]
_bad_url_re = re.compile("|".join(BAD_URL_PATTERNS), re.IGNORECASE)

# Stats
stats = {"processed": 0, "matched": 0, "failed": 0, "skipped": 0}
top_markups = []  # [(domain, product, markup_x, price_ils, price_usd)]
start_time = time.time()


def time_left():
    return MAX_RUNTIME - (time.time() - start_time)


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "firecrawl"),
        user=os.getenv("DB_USER", "ubuntu"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def get_eligible_products():
    """Get risk_db domains that have Dropship ads with product URLs."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT r.id, r.base_url, r.risk_score,
               a.destination_product_url
        FROM risk_db r
        JOIN ads_with_urls a ON LOWER(TRIM(r.base_url)) = LOWER(TRIM(
            REPLACE(SPLIT_PART(a.destination_product_url, '/', 3), 'www.', '')
        ))
        WHERE a.analysis_category ILIKE '%%dropship%%'
        AND a.destination_product_url IS NOT NULL
        AND LENGTH(a.destination_product_url) > 20
        AND a.destination_product_url ~ '^https?://[^/]+/.+'
        AND r.risk_score >= 0.6
        AND r.base_url NOT LIKE '%%shein.com'
        AND r.base_url NOT LIKE '%%aliexpress.com'
        AND r.base_url NOT LIKE '%%temu.%%'
        AND a.destination_product_url NOT LIKE '%%s.click.aliexpress.com%%'
        AND (r.price_matches IS NULL
             OR NOT r.price_matches::text LIKE '%%' || a.destination_product_url || '%%')
        AND (r.price_match_failures IS NULL
             OR NOT r.price_match_failures::text LIKE '%%' || a.destination_product_url || '%%')
        ORDER BY r.risk_score DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    logger.info(f"Found {len(rows)} eligible products")
    return rows


def get_failed_products():
    """Get products that previously failed, for retry."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.base_url, r.risk_score, f->>'url' as product_url
        FROM risk_db r,
             jsonb_array_elements(COALESCE(r.price_match_failures, '[]'::jsonb)) f
        WHERE r.risk_score >= 0.6
        AND r.base_url NOT LIKE '%%shein.com'
        AND r.base_url NOT LIKE '%%aliexpress.com'
        AND r.base_url NOT LIKE '%%temu.%%'
        ORDER BY r.risk_score DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    logger.info(f"Found {len(rows)} failed products to retry")
    return rows


def save_price_match(risk_db_id: str, product_url: str, result: dict):
    """Append price match result to risk_db.price_matches JSONB array."""
    conn = get_db()
    cur = conn.cursor()

    entry = {
        "product_url": product_url,
        "product_name_english": result.get("product_name_english", ""),
        "price_ils": result.get("price_ils", 0),
        "matches": result.get("matches", []),
        "search_query_used": result.get("search_query_used", ""),
        "matched_at": datetime.now(timezone.utc).isoformat(),
    }

    cur.execute("""
        UPDATE risk_db
        SET price_matches = COALESCE(price_matches, '[]'::jsonb) || %s::jsonb,
            last_updated = NOW()
        WHERE id = %s
    """, (Json([entry]), risk_db_id))

    conn.commit()
    cur.close()
    conn.close()


def save_failure(risk_db_id: str, product_url: str, reason: str):
    """Append failure record to risk_db.price_match_failures JSONB array."""
    conn = get_db()
    cur = conn.cursor()

    entry = {
        "url": product_url,
        "reason": reason,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }

    cur.execute("""
        UPDATE risk_db
        SET price_match_failures = COALESCE(price_match_failures, '[]'::jsonb) || %s::jsonb,
            last_updated = NOW()
        WHERE id = %s
    """, (Json([entry]), risk_db_id))

    conn.commit()
    cur.close()
    conn.close()


def clear_failure(risk_db_id: str, product_url: str):
    """Remove a failure entry after successful retry."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE risk_db
        SET price_match_failures = (
            SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
            FROM jsonb_array_elements(COALESCE(price_match_failures, '[]'::jsonb)) elem
            WHERE elem->>'url' != %s
        )
        WHERE id = %s
    """, (product_url, risk_db_id))
    conn.commit()
    cur.close()
    conn.close()


def is_bad_url(url: str) -> bool:
    """Check if URL matches known-bad patterns."""
    return bool(_bad_url_re.search(url))


class SiteScraper:
    """Reuses a single browser instance."""

    def __init__(self):
        self.browser: Browser = None
        self.playwright = None

    async def start(self):
        if self.browser:
            return
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                   "--disable-gpu", "--disable-extensions"]
        )
        logger.info("Browser started")

    async def stop(self):
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.playwright = None
        logger.info("Browser stopped")

    async def restart(self):
        await self.stop()
        await asyncio.sleep(1)
        await self.start()

    async def scrape(self, url: str) -> str:
        """Scrape page text. Follows CTA links on advertorial pages."""
        try:
            return await asyncio.wait_for(self._scrape(url), timeout=90)
        except asyncio.TimeoutError:
            logger.warning(f"Scrape timeout (90s): {url[:80]}")
            return ""

    async def _scrape(self, url: str) -> str:
        if not self.browser:
            await self.restart()

        context = None
        try:
            context = await self.browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            text = await page.inner_text("body")
            # If very little text, try networkidle for JS-heavy pages
            if len(text.strip()) < 200:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                    await page.wait_for_timeout(3000)
                    text = await page.inner_text("body")
                except Exception:
                    pass  # keep whatever we got from first attempt

            # Follow CTA links on advertorial/funnel pages to find the product
            # These pages have a fake article with a CTA button linking to the real product
            try:
                cta_url = await page.evaluate("""() => {
                    var links = Array.from(document.querySelectorAll("a[href]"));
                    var ctaRe = /לרכישה|הזמינו|הזמן|לרכוש|בדיקת זמינות|קבלו|להזמנה|קנו|הוסף לסל|add.to.cart|buy.now|order.now|shop.now|get.yours/i;
                    var productRe = /\\/products\\/|\\/product\\/|checkout|cart|shop/i;
                    var curPath = location.pathname;
                    var curHost = location.hostname;
                    for (var i = 0; i < links.length; i++) {
                        var a = links[i];
                        var t = (a.innerText || "").trim();
                        var href = a.href || "";
                        if (!href || href.indexOf("javascript:") === 0) continue;
                        try {
                            var u = new URL(href);
                            // Skip same-page anchors
                            if (u.pathname === curPath && u.hostname === curHost) continue;
                            // Match by CTA text
                            if (ctaRe.test(t) && href.indexOf("http") === 0) return href;
                            // Match by product URL pattern on same domain
                            if (u.hostname.indexOf(curHost.replace("www.","")) > -1 && productRe.test(u.pathname)) return href;
                        } catch(e) {}
                    }
                    return null;
                }""")
                if cta_url:
                    logger.info(f"  Following CTA link: {cta_url[:80]}")
                    prod_page = await context.new_page()
                    try:
                        await prod_page.goto(cta_url, wait_until="domcontentloaded", timeout=30000)
                        await prod_page.wait_for_timeout(3000)
                        prod_text = await prod_page.inner_text("body")
                        if prod_text.strip():
                            text += "\n[PRODUCT PAGE]\n" + prod_text[:4000]
                    except Exception:
                        pass
                    finally:
                        await prod_page.close()
            except Exception:
                pass

            return text[:8000]
        except Exception as e:
            logger.warning(f"Scrape failed {url}: {e}")
            return ""
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"^```\w*\n?|```$", "", text.strip())
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


async def extract_product_info(client, page_text: str) -> dict | None:
    """Step 1: Extract product info from Hebrew page (no grounding)."""
    prompt = (
        "Analyze this Israeli product page text and extract product details.\n"
        "Translate the product name to generic English search terms (not brand name).\n"
        "Hebrew names won't work on AliExpress — use descriptive English.\n\n"
        f"Page text:\n{page_text}\n\n"
        "Return ONLY valid JSON:\n"
        '{"product_name_hebrew": "original", "product_name_english": "english terms", '
        '"price_ils": 0.0, "category": "type", '
        '"key_features": ["f1", "f2"], "search_query": "aliexpress query"}'
    )
    try:
        resp = await client.aio.models.generate_content(model=MODEL, contents=prompt)
        await asyncio.sleep(GEMINI_CALL_DELAY)
        return parse_json(resp.text)
    except Exception as e:
        logger.error(f"Extract error: {e}")
        return None


async def search_cheaper(client, product_info: dict) -> dict:
    """Step 2: Search for cheaper alternatives (with google_search grounding)."""
    name = product_info.get("product_name_english", "")
    features = product_info.get("key_features", [])
    raw_price = product_info.get("price_ils", 0)
    search_q = product_info.get("search_query", name)

    # Normalize LLM outputs to expected types.
    if isinstance(name, list):
        name = next(
            (str(x).strip() for x in name if x is not None and str(x).strip()), ""
        )
    elif name is None:
        name = ""
    else:
        name = str(name).strip()

    if isinstance(features, str):
        features = [features]
    elif not isinstance(features, list):
        features = []
    features = [str(x).strip() for x in features if x is not None and str(x).strip()]

    try:
        price = float(raw_price) if raw_price else 0
    except (ValueError, TypeError):
        price = 0

    if isinstance(search_q, list):
        search_q = next(
            (str(x).strip() for x in search_q if x is not None and str(x).strip()), ""
        )
    elif search_q is None:
        search_q = ""
    else:
        search_q = str(search_q).strip()
    if not search_q:
        search_q = name

    usd = round(price * ILS_TO_USD, 2) if price else "?"

    prompt = (
        "You have google_search enabled. "
        "Search for this product on AliExpress, Temu, and Alibaba and "
        "tell me what you find.\n\n"
        f"Product: {name}\n"
        f"Features: {', '.join(features)}\n"
        f"Israeli price: {price} ILS (~${usd})\n"
        f"Search query suggestion: {search_q}\n\n"
        "Search for similar products. For each result you find, tell me:\n"
        "- The product name/title\n"
        "- The price (in USD if possible)\n"
        "- Which site it's from (AliExpress, Temu, Alibaba, etc)\n"
        "- The URL from the search results\n\n"
        "It's OK to include redirect URLs from search. "
        "Include whatever you can find. If prices aren't in the snippet, "
        "estimate based on what you see or say unknown.\n\n"
        "Return up to 5 results as JSON:\n"
        '{"matches": [{"source": "site", "product_name": "title", '
        '"price_usd": 0.00, "url": "url", "similarity": "exact/similar"}], '
        '"search_query_used": "query"}'
    )

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )

    try:
        resp = await client.aio.models.generate_content(
            model=MODEL, contents=prompt, config=config
        )
        await asyncio.sleep(GEMINI_CALL_DELAY)
        result = parse_json(resp.text)
        if result:
            return result

        # Retry with stricter prompt on parse failure
        logger.info("  Parse failed, retrying with strict prompt...")
        retry_prompt = (
            f"Search AliExpress/Temu for: {search_q}\n"
            "Return ONLY this JSON, nothing else:\n"
            '{"matches": [{"source": "site", "product_name": "title", '
            '"price_usd": 0.00, "url": "url", "similarity": "exact/similar"}], '
            '"search_query_used": "query"}'
        )
        resp2 = await client.aio.models.generate_content(
            model=MODEL, contents=retry_prompt, config=config
        )
        await asyncio.sleep(GEMINI_CALL_DELAY)
        result2 = parse_json(resp2.text)
        if result2:
            return result2

        # Last resort: extract price/URL from raw text via regex
        raw = resp.text or ""
        urls = re.findall(r"https?://(?:www\.)?(?:aliexpress|temu|alibaba)\S+", raw)
        prices = re.findall(r"\$(\d+\.?\d*)", raw)
        if urls:
            fallback_matches = []
            for i, u in enumerate(urls[:3]):
                p = float(prices[i]) if i < len(prices) else 0
                fallback_matches.append({
                    "source": "aliexpress" if "aliexpress" in u else "temu" if "temu" in u else "alibaba",
                    "product_name": name[:60],
                    "price_usd": p,
                    "url": u.rstrip(".,)\"'"),
                    "similarity": "similar",
                })
            return {"matches": fallback_matches, "search_query_used": search_q}

        return {"matches": [], "no_match_reason": "parse error"}
    except Exception as e:
        logger.error(f"Search error: {e}")
        return {"matches": [], "no_match_reason": str(e)}


async def process_product(client, scraper, risk_id, domain, score, url):
    """Full pipeline for one product."""
    t0 = time.time()
    logger.info(f"[{stats['processed']+1}] {domain} (score={score}) — {url}")

    # Pre-filter known-bad URLs
    if is_bad_url(url):
        logger.warning(f"  SKIP: bad URL pattern")
        stats["skipped"] += 1
        save_failure(risk_id, url, "url_pattern_filtered")
        return

    # Scrape
    page_text = await scraper.scrape(url)
    if not page_text:
        logger.warning(f"  SKIP: no page text")
        stats["skipped"] += 1
        save_failure(risk_id, url, "scrape_empty")
        return

    # Extract
    info = await extract_product_info(client, page_text)
    if not info:
        logger.warning(f"  SKIP: extraction failed")
        stats["failed"] += 1
        save_failure(risk_id, url, "extraction_failed")
        return

    raw_eng_name = info.get("product_name_english")
    # LLM sometimes returns a list of candidate names; take the first non-empty.
    if isinstance(raw_eng_name, list):
        eng_name = next(
            (
                str(x).strip()
                for x in raw_eng_name
                if x is not None and str(x).strip()
            ),
            "",
        )
    elif raw_eng_name is None:
        eng_name = ""
    else:
        eng_name = str(raw_eng_name).strip()
    info["product_name_english"] = eng_name

    raw_price = info.get("price_ils", 0)
    # Ensure price is numeric (LLM sometimes returns a descriptive string).
    try:
        price = float(raw_price) if raw_price else 0
    except (ValueError, TypeError):
        price = 0
    info["price_ils"] = price
    logger.info(f"  Extracted: {eng_name} — {price} ILS")

    # Skip if extraction found no real product
    if not eng_name or eng_name.lower() in ("none", "error", "n/a", ""):
        logger.warning(f"  SKIP: no product name extracted")
        stats["skipped"] += 1
        save_failure(risk_id, url, "no_product_name")
        return
    if price <= 0:
        logger.warning(f"  SKIP: no price extracted")
        stats["skipped"] += 1
        save_failure(risk_id, url, "no_price")
        return

    # Search
    result = await search_cheaper(client, info)
    matches = result.get("matches", [])

    # Save
    result["product_name_english"] = eng_name
    result["price_ils"] = price
    save_price_match(risk_id, url, result)

    # If this was a retry, clear the old failure entry
    if RETRY_MODE:
        clear_failure(risk_id, url)

    stats["processed"] += 1
    if matches:
        stats["matched"] += 1
        best = min(
            [m for m in matches if isinstance(m.get("price_usd"), (int, float)) and m["price_usd"] > 0],
            key=lambda m: m["price_usd"],
            default=None
        )
        if best:
            logger.info(f"  MATCH: {best['product_name'][:60]} — ${best['price_usd']} on {best['source']}")
            if price > 0 and best["price_usd"] > 0:
                markup = price / (best["price_usd"] / ILS_TO_USD)
                top_markups.append((domain, eng_name, markup, price, best["price_usd"]))
        else:
            logger.info(f"  MATCH: {len(matches)} results (prices unknown)")
    else:
        reason = result.get("no_match_reason", result.get("search_query_used", "?"))
        logger.info(f"  NO MATCH: {reason[:80]}")

    elapsed = time.time() - t0
    logger.info(f"  Done in {elapsed:.1f}s — {time_left():.0f}s remaining")


def log_summary():
    elapsed = time.time() - start_time
    logger.info(f"\n=== SUMMARY ===")
    logger.info(f"Mode: {'retry-failures' if RETRY_MODE else 'normal'}")
    logger.info(f"Runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    logger.info(f"Processed: {stats['processed']}")
    logger.info(f"Matched: {stats['matched']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info(f"Skipped: {stats['skipped']}")
    if stats["processed"] > 0:
        logger.info(f"Avg time/product: {elapsed/stats['processed']:.1f}s")
        logger.info(f"Match rate: {stats['matched']/stats['processed']*100:.0f}%")
        logger.info(f"Projected per hour: {3600/(elapsed/stats['processed']):.0f} products")


async def main():
    mode_str = "RETRY" if RETRY_MODE else "NORMAL"
    logger.info(f"=== Batch Price Match [{mode_str}] — max runtime {MAX_RUNTIME}s ===")

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY not set")
        sys.exit(1)

    client = genai.Client(api_key=gemini_key)

    if RETRY_MODE:
        products = get_failed_products()
    else:
        products = get_eligible_products()

    if not products:
        logger.info("No eligible products found")
        return

    logger.info(f"Processing up to {len(products)} products")

    scraper = SiteScraper()
    await scraper.start()

    try:
        for risk_id, domain, score, url in products:
            if time_left() < 60:
                logger.info("Time limit approaching, stopping")
                break
            await process_product(client, scraper, risk_id, domain, score, url)
    finally:
        await scraper.stop()
        log_summary()
        send_summary_email()


def send_summary_email():
    """Send run summary via email."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")
    if not all([sender, password, recipient]):
        logger.info("Email credentials not set, skipping summary email")
        return

    elapsed = time.time() - start_time
    total = stats["processed"] + stats["failed"] + stats["skipped"]
    match_rate = f"{stats['matched']/stats['processed']*100:.0f}%" if stats["processed"] else "N/A"
    mode_str = "RETRY" if RETRY_MODE else "NORMAL"

    body = (
        f"=== Price Match Summary [{mode_str}] ===\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"Runtime: {elapsed/60:.1f} min\n\n"
        f"Total attempted: {total}\n"
        f"Processed: {stats['processed']}\n"
        f"Matched: {stats['matched']} ({match_rate})\n"
        f"Skipped: {stats['skipped']}\n"
        f"Failed: {stats['failed']}\n"
    )

    # Top markups from this run
    if top_markups:
        body += "\nTop markups:\n"
        for domain, product, markup, price_ils, price_usd in sorted(top_markups, key=lambda x: x[2], reverse=True)[:3]:
            body += f"  {domain}: {product[:40]} — {markup:.1f}x (₪{price_ils} vs ${price_usd})\n"

    subject = f"Adora Price Match [{mode_str}]: {stats['matched']}/{stats['processed']} matched ({match_rate})"

    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(os.getenv("SMTP_SERVER", "smtp.gmail.com"),
                          int(os.getenv("SMTP_PORT", 587)), timeout=30) as server:
            server.starttls()
            server.login(sender, password.replace(" ", ""))
            server.send_message(msg)
        logger.info(f"Summary email sent to {recipient}")
    except Exception as e:
        logger.error(f"Email failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
