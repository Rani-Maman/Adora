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
from datetime import datetime
from dataclasses import dataclass
from dotenv import load_dotenv
from playwright.async_api import async_playwright
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

BATCH_SIZE = 30

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
    async def scrape(self, url: str) -> SiteData:
        data = SiteData(url=url)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
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
                    await browser.close()
        except Exception as e:
            data.error = str(e)
            logger.error(f"Scrape error for {url}: {e}")
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
        try:
            resp = await self.client.aio.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            clean = re.sub(r'^```\w*\n?|```$', '', resp.text.strip())
            return json.loads(re.search(r'\{[\s\S]*\}', clean).group())
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return {"score": 0.0, "is_risky": False, "category": "error", "reason": str(e)}

# --- DB Utilities (Subprocess) ---
def run_psql(sql):
    cmd = ['sudo', '-u', 'postgres', 'psql', '-d', 'firecrawl', '-t', '-P', 'format=unaligned', '-c', sql]
    return subprocess.run(cmd, capture_output=True, text=True)

def fetch_unscored_ads(limit=10):
    sql = f"SELECT id, destination_product_url FROM ads_with_urls WHERE analysis_score IS NULL AND destination_product_url IS NOT NULL LIMIT {limit};"
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
    
    ads = fetch_unscored_ads(BATCH_SIZE)
    logger.info(f"Fetched {len(ads)} ads.")
    
    for ad_id, url in ads:
        logger.info(f"[{ad_id}] Processing {url}...")
        site = await scraper.scrape(url)
        if site.error:
            logger.warning(f"Scrape Error: {site.error}")
            update_ad_result(ad_id, {"score": 0, "category": "error", "reason": site.error})
            continue
            
        res = await scorer.score(site)
        logger.info(f"  -> {res.get('category')} ({res.get('score')})")
        
        update_ad_result(ad_id, res)
        upsert_risk_db(url, res)
        
    logger.info("Done.")

if __name__ == "__main__":
    asyncio.run(main())
