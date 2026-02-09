#!/usr/bin/env python3
"""
Batch Ad Analyzer.
Runs Playwright + Gemini Scorer on unscored ads.
Uses subprocess + psql for DB access to avoid auth issues.
"""
import asyncio
import os
import re
import json
import logging
import subprocess
import random
from datetime import datetime
from dataclasses import dataclass
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser
from google import genai
import psycopg2
from psycopg2.extras import Json

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_KEY:
    logger.error("GEMINI_API_KEY not found in environment!")
    # Allow running for testing/scraping even if key missing, but scorer will fail

BATCH_SIZE = 10  # Reduced for 956MB RAM VM
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_BASE_DELAY = 2  # seconds
GEMINI_CALL_DELAY = 2  # seconds between API calls - reduces rate to ~3 calls/min

# --- URL Skip Patterns (unscrape-able or low-value URLs) ---
SKIP_URL_PATTERNS = [
    r'^https?://(?:www\.)?facebook\.com/',     # Facebook login wall
    r'^https?://(?:www\.)?fb\.com/',
    r'^https?://(?:www\.)?instagram\.com/',    # Instagram login wall
    r'^https?://wa\.me/',                       # WhatsApp direct links
    r'^https?://api\.whatsapp\.com/',
    r'^https?://chat\.whatsapp\.com/',
    r'^https?://docs\.google\.com/',            # Google Docs/Forms
    r'^https?://drive\.google\.com/',
    r'^https?://forms\.google\.com/',
    r'^https?://linktr\.ee/',                   # Linktree (just links)
    r'^https?://(?:www\.)?tiktok\.com/',       # TikTok login wall
    r'^https?://(?:www\.)?youtube\.com/',      # YouTube (video platform)
    r'^https?://(?:www\.)?youtu\.be/',
]

def should_skip_url(url: str) -> bool:
    """Return True if URL is known to be unscrape-able or low-value."""
    if not url or len(url) < 15:
        return True
    for pattern in SKIP_URL_PATTERNS:
        if re.match(pattern, url, re.I):
            return True
    return False

# --- Scraper & Scorer (Same as before) ---
@dataclass
class SiteData:
    url: str
    title: str = ""
    product_name: str = ""
    product_price: float = 0.0
    shipping_time: str = ""
    business_id: str = ""
    phone: str = ""
    email: str = ""
    has_countdown_timer: bool = False
    has_scarcity_widget: bool = False
    has_whatsapp_only: bool = False
    page_text: str = ""
    error: str = ""

class SiteScraper:
    """Reuses a single browser instance to reduce memory pressure."""
    
    def __init__(self):
        self.browser: Browser = None
        self.playwright = None
    
    async def start(self):
        """Start browser once for the batch."""
        if self.browser:
            return  # Already started
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',  # Reduces memory usage
                '--disable-gpu',
                '--disable-extensions',
            ]
        )
        logger.info("Browser started.")
    
    async def stop(self):
        """Clean up browser resources."""
        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        except Exception:
            pass
        logger.info("Browser stopped.")
    
    async def restart(self):
        """Restart browser if it crashed."""
        logger.info("Restarting browser...")
        await self.stop()
        await asyncio.sleep(1)
        await self.start()
    
    async def is_browser_alive(self) -> bool:
        """Check if browser is still responsive."""
        try:
            if not self.browser:
                return False
            # Try to get browser contexts - if this fails, browser is dead
            _ = self.browser.contexts
            return True
        except Exception:
            return False
    
    async def scrape(self, url: str) -> SiteData:
        data = SiteData(url=url)
        
        # Check if browser is alive, restart if not
        if not await self.is_browser_alive():
            try:
                await self.restart()
            except Exception as e:
                data.error = f"Browser restart failed: {e}"
                return data
        
        context = None
        try:
            # Create a new context (lighter than new browser)
            context = await self.browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await page.wait_for_timeout(3000)
                
                data.title = await page.title()
                body = await page.inner_text("body")
                data.page_text = body[:4000]

                h1 = await page.query_selector("h1")
                if h1: data.product_name = (await h1.inner_text()).strip()[:200]
                
                m_ship = re.search(r'(\d+[-–]\d+\s*(?:ימי|ימים|days|business days))', body, re.I)
                if m_ship: data.shipping_time = m_ship.group(0)[:50]

                m_hp = re.search(r'ח\.?פ\.?\s*[:\-]?\s*(\d{9})', body)
                if m_hp: data.business_id = m_hp.group(1)
                
                m_ph = re.search(r'(\*\d{4}|\d{2,3}[-\s]?\d{7})', body)
                if m_ph: data.phone = m_ph.group(1)

                data.has_countdown_timer = bool(await page.query_selector("[class*='countdown'], [class*='timer']"))
                data.has_scarcity_widget = bool(re.search(r'רק\s+\d+\s+(?:נותר|נשאר)|only\s+\d+\s+left', body, re.I))
                data.has_whatsapp_only = ("whatsapp" in body.lower() or "wa.me" in body.lower()) and not data.phone
            finally:
                await page.close()
        except Exception as e:
            data.error = str(e)
            logger.error(f"Scrape error for {url}: {e}")
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
        return data

class GeminiScorer:
    def __init__(self):
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("Gemini Scorer initialized without API Key")
        self.client = genai.Client(api_key=api_key) if api_key else None

    async def score(self, site: SiteData) -> dict:
        if not self.client:
             return {"score": 0.0, "reason": "No API Key", "is_risky": False}

        prompt = f"""You are an Israeli e-commerce fraud detector. DISTINGUISH LEGIT VS DROPSHIP.
Rules: Digital/Courses=0.0 (Legit), Viral Gadgets=0.8 (Dropship), Context=Critical.

DATA:
URL: {site.url}
Title: {site.title}
Product: {site.product_name}
Shipping: {site.shipping_time}
Signals: Countdown={site.has_countdown_timer}, Scarcity={site.has_scarcity_widget}
Text: {site.page_text[:800]}

Return JSON: {{ "score": float, "is_risky": bool, "category": "str", "reason": "str", "evidence": ["str"] }}"""
        
        # Retry with exponential backoff for rate limits
        for attempt in range(GEMINI_RETRY_ATTEMPTS):
            try:
                resp = await self.client.aio.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                clean = re.sub(r'^```\w*\n?|```$', '', resp.text.strip())
                result = json.loads(re.search(r'\{[\s\S]*\}', clean).group())
                
                # Small delay between successful calls to avoid rate limits
                await asyncio.sleep(GEMINI_CALL_DELAY)
                return result
                
            except Exception as e:
                error_str = str(e)
                
                # Check if it's a rate limit error (429)
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
                    if attempt < GEMINI_RETRY_ATTEMPTS - 1:
                        delay = GEMINI_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(f"Rate limited. Retrying in {delay:.1f}s (attempt {attempt + 1}/{GEMINI_RETRY_ATTEMPTS})")
                        await asyncio.sleep(delay)
                        continue
                
                logger.error(f"Gemini error: {e}")
                return {"score": 0.0, "is_risky": False, "category": "error", "reason": error_str}
        
        return {"score": 0.0, "is_risky": False, "category": "error", "reason": "Max retries exceeded"}

# --- DB Utilities (Subprocess) ---
def run_psql(sql):
    cmd = ['sudo', '-u', 'postgres', 'psql', '-d', 'firecrawl', '-t', '-P', 'format=unaligned', '-c', sql]
    return subprocess.run(cmd, capture_output=True, text=True)

def fetch_unscored_ads(limit=10):
    sql = f"""SELECT id, destination_product_url FROM ads_with_urls 
    WHERE analysis_score IS NULL 
      AND destination_product_url IS NOT NULL
      AND destination_product_url NOT LIKE '%facebook.com%'
      AND destination_product_url NOT LIKE '%instagram.com%'
      AND destination_product_url NOT LIKE '%fb.com%'
      AND destination_product_url NOT LIKE '%wa.me%'
      AND destination_product_url NOT LIKE '%whatsapp.com%'
      AND destination_product_url NOT LIKE '%tiktok.com%'
      AND destination_product_url NOT LIKE '%youtube.com%'
      AND destination_product_url NOT LIKE '%youtu.be%'
      AND destination_product_url NOT LIKE '%linktr.ee%'
      AND destination_product_url NOT LIKE '%docs.google.com%'
      AND LENGTH(destination_product_url) > 15
    LIMIT {limit};"""
    res = run_psql(sql)
    ads = []
    if res.stdout.strip():
        for line in res.stdout.strip().split('\n'):
            parts = line.split('|')
            if len(parts) >= 2:
                ads.append((parts[0], parts[1]))
    return ads

def update_ad_result(ad_id, result):
    # Escape single quotes for SQL
    reason = str(result.get('reason', '')).replace("'", "''")
    cat = str(result.get('category', '')).replace("'", "''")
    json_str = json.dumps(result).replace("'", "''")
    
    sql = f"""
    UPDATE ads_with_urls 
    SET analysis_score = {result.get('score', 0)},
        analysis_category = '{cat}',
        analysis_reason = '{reason}',
        analysis_json = '{json_str}',
        analyzed_at = NOW()
    WHERE id = {ad_id};
    """
    run_psql(sql)

def upsert_risk_db(url, result):
    if not result.get('is_risky'): return
    
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace('www.', '')
    score = result.get('score', 0)
    evidence = "{" + ",".join([f'"{e}"' for e in result.get('evidence', [])]) + "}"
    
    sql = f"""
    INSERT INTO risk_db (base_url, risk_score, evidence, first_seen, last_updated)
    VALUES ('{domain}', {score}, '{evidence}', NOW(), NOW())
    ON CONFLICT (base_url) 
    DO UPDATE SET risk_score = {score}, evidence = '{evidence}', last_updated = NOW();
    """
    run_psql(sql)

# --- Main ---
async def main():
    logger.info("Starting Batch Processor...")
    scraper = SiteScraper()
    scorer = GeminiScorer()
    
    # Start browser once for the entire batch
    try:
        await scraper.start()
    except Exception as e:
        logger.error(f"Failed to start browser: {e}")
        return
    
    try:
        ads = fetch_unscored_ads(BATCH_SIZE)
        logger.info(f"Fetched {len(ads)} ads.")
        
        for ad_id, url in ads:
            logger.info(f"[{ad_id}] Processing {url[:80]}...")
            site = await scraper.scrape(url)
            if site.error:
                logger.warning(f"Scrape Error: {site.error[:100]}")
                # Mark as analyzed with score=-1 to indicate scrape failure
                update_ad_result(ad_id, {
                    'score': -1,
                    'category': 'scrape_error',
                    'reason': site.error[:200],
                    'is_risky': False,
                    'evidence': []
                })
                continue
                
            res = await scorer.score(site)
            logger.info(f"  -> {res.get('category')} ({res.get('score')})")
            
            update_ad_result(ad_id, res)
            upsert_risk_db(url, res)
    finally:
        # Always clean up browser
        await scraper.stop()
        
    logger.info("Done.")

if __name__ == "__main__":
    asyncio.run(main())
