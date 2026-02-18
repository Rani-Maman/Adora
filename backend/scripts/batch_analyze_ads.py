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
import fcntl
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
LOCK_FILE = "/tmp/batch_analyze.lock"  # Prevent concurrent cron runs

# --- Whitelist (known legit domains — skip analysis entirely) ---
def _load_whitelist() -> set:
    base = os.path.join(os.path.dirname(__file__), '..', 'data')
    # Also check adora_ops/data (VM path)
    vm_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    domains = set()
    for directory in [base, vm_base]:
        for fname in ['whitelist_global.txt', 'whitelist_israel.txt', 'whitelist_israel_extra.txt']:
            path = os.path.join(directory, fname)
            if os.path.exists(path):
                with open(path) as f:
                    for line in f:
                        d = line.strip().lower()
                        if d and not d.startswith('#'):
                            domains.add(d)
    logger.info(f"Loaded {len(domains)} whitelist domains")
    return domains

WHITELIST_DOMAINS = _load_whitelist()

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
    r'^https?://temu\.to/',                    # Temu affiliate redirects (hangs Playwright)
    r'^https?://(?:\w+\.)?shein\.com/',          # Shein (legit marketplace)
    r'^https?://(?:\w+\.)?aliexpress\.com/',     # AliExpress (legit marketplace)
    r'^https?://s\.click\.aliexpress\.com/',     # AliExpress affiliate links
    r'^https?://(?:\w+\.)?temu\.com/',           # Temu (legit marketplace)
]

def should_skip_url(url: str) -> bool:
    """Return True if URL is known to be unscrape-able, low-value, or whitelisted."""
    if not url or len(url) < 15:
        return True
    for pattern in SKIP_URL_PATTERNS:
        if re.match(pattern, url, re.I):
            return True
    # Skip whitelisted domains (known legit — no analysis needed)
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().removeprefix('www.')
        if domain in WHITELIST_DOMAINS:
            return True
        # Check parent domain (e.g. shop.example.com → example.com)
        parts = domain.split('.')
        for i in range(1, len(parts) - 1):
            if '.'.join(parts[i:]) in WHITELIST_DOMAINS:
                return True
    except Exception:
        pass
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
    tos_text: str = ""
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
        try:
            return await asyncio.wait_for(self._scrape(url), timeout=90)
        except asyncio.TimeoutError:
            logger.warning(f"Scrape timeout (90s): {url[:80]}")
            return SiteData(url=url, error="Scrape timeout (90s)")

    async def _scrape(self, url: str) -> SiteData:
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
                # Wait for any post-load redirects to settle
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                try:
                    data.title = await page.title()
                    body = await page.inner_text("body")
                except Exception:
                    # Context destroyed mid-redirect — re-grab current page state
                    await page.wait_for_timeout(2000)
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

                # Extract price
                m_price = re.search(r'[₪$]\s*(\d[\d,\.]+)|(\d[\d,\.]+)\s*[₪$]', body)
                if m_price:
                    raw = (m_price.group(1) or m_price.group(2)).replace(',', '')
                    try: data.product_price = float(raw)
                    except ValueError: pass

                # If no price found (listicle/landing/advertorial page), follow product link
                if not data.product_price:
                    # Try /products/ links first, then CTA buttons
                    product_links = await page.eval_on_selector_all(
                        'a[href*="/products/"]',
                        'els => els.map(e => e.href)'
                    )
                    if not product_links:
                        # Look for CTA buttons on advertorial/funnel pages
                        try:
                            cta = await page.evaluate("""() => {
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
                                        if (u.pathname === curPath && u.hostname === curHost) continue;
                                        if (ctaRe.test(t) && href.indexOf("http") === 0) return href;
                                        if (u.hostname.indexOf(curHost.replace("www.","")) > -1 && productRe.test(u.pathname)) return href;
                                    } catch(e) {}
                                }
                                return null;
                            }""")
                            if cta:
                                product_links = [cta]
                        except Exception:
                            pass
                    if product_links:
                        try:
                            prod_page = await context.new_page()
                            await prod_page.goto(product_links[0], wait_until="domcontentloaded", timeout=20000)
                            prod_body = await prod_page.inner_text("body")
                            await prod_page.close()
                            # Append product page text and re-extract price
                            data.page_text += "\n[PRODUCT PAGE]\n" + prod_body[:1000]
                            m_price2 = re.search(r'[₪$]\s*(\d[\d,\.]+)|(\d[\d,\.]+)\s*[₪$]', prod_body)
                            if m_price2:
                                raw = (m_price2.group(1) or m_price2.group(2)).replace(',', '')
                                try: data.product_price = float(raw)
                                except ValueError: pass
                        except Exception:
                            pass

                # --- TOS / Terms page scraping ---
                try:
                    links = await page.eval_on_selector_all(
                        'a[href]',
                        'els => els.map(e => ({href: e.href, text: (e.innerText||"").trim().substring(0,60)}))'
                    )
                    tos_url = None
                    for link in links:
                        href = (link.get('href') or '').lower()
                        text = (link.get('text') or '').lower()
                        # Match by href path
                        if re.search(r'/(?:terms|tos|policies|policy|terms-of-service|terms-and-conditions|shipping-policy|refund-policy)', href):
                            tos_url = link['href']
                            break
                        # Match by Hebrew/English link text
                        if re.search(r'תנאי|מדיניות|terms|policy', text):
                            tos_url = link['href']
                            break
                    if tos_url:
                        tos_page = await context.new_page()
                        try:
                            await tos_page.goto(tos_url, wait_until="domcontentloaded", timeout=15000)
                            tos_body = await tos_page.inner_text("body")
                            data.tos_text = tos_body[:2000]
                            logger.info(f"  TOS scraped: {len(data.tos_text)} chars from {tos_url[:80]}")
                        except Exception:
                            pass
                        finally:
                            await tos_page.close()
                except Exception:
                    pass

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

        prompt = f"""You are an Israeli e-commerce fraud detector. Identify DROPSHIP/SCAM stores vs legitimate businesses.

STRONG DROPSHIP SIGNALS (each adds 0.2-0.3):
- Reviews shown as WhatsApp/Messenger/chat screenshots instead of real review system
- No physical address, registration number, or real business identity
- Product easily found on AliExpress/Temu/Alibaba (gadgets, bowls, beauty tools, pet items, posture braces, etc.)
- AI-generated product images (too perfect, floating objects, unrealistic lighting)
- Price is 3-6x the typical AliExpress price for the same product category (use your knowledge of AliExpress pricing to evaluate — if the product is the type commonly sold on AliExpress and the Israeli price suggests a typical dropship markup, add 0.2-0.3)
- Heavy urgency: countdown timers, "מבצע", "מוגבל", "רק היום", scarcity widgets
- Single product or very narrow SKU range
- Fake "handmade" or "Israeli-made" claims for obvious AliExpress goods
- No real contact beyond WhatsApp/email
- Terms of Service / About page admits: third-party suppliers, dropshipping, shipping from China/overseas, products sourced externally, AliExpress/Alibaba fulfillment

AUTOMATIC LOW SCORE (0.0-0.1) — NOT physical products, cannot be dropshipped:
- Restaurants, cafes, food delivery, catering
- Services (cleaning, repairs, consulting, coaching, therapy)
- Courses, workshops, online education, webinars
- Flights, travel packages, hotels, tours
- Software, SaaS, apps, subscriptions
- Real estate, rentals
- Events, tickets, experiences
If the site sells any of the above, score 0.0-0.1 regardless of other signals.

LEGITIMATE SIGNALS (each subtracts 0.1-0.2):
- Real Israeli business with address + VAT/registration number
- Genuine review platform (Google, Trustpilot, embedded widget)
- Unique product not available on AliExpress
- Professional brand with history, social media presence
- Physical store or studio mentioned
- Handmade / artisan / custom-made product (pre-order + longer shipping is normal for creators — do NOT penalize single-product stores if the product appears original/handmade, not mass-produced AliExpress goods)

DATA:
URL: {site.url}
Title: {site.title}
Product: {site.product_name}
Price: {"₪" + str(site.product_price) if site.product_price else "unknown"}
Shipping: {site.shipping_time}
Signals: Countdown={site.has_countdown_timer}, Scarcity={site.has_scarcity_widget}
Text: {site.page_text[:800]}
{f"Terms/Policy page: {site.tos_text[:600]}" if site.tos_text else ""}

Score: 0.0=legit, 0.6=borderline dropship, 0.8=clear dropship, 0.9+=scam. MUST be between 0.0 and 1.0, never negative.
Return JSON: {{ "score": float, "is_risky": bool, "category": "str", "reason": "str", "evidence": ["str"] }}"""
        
        # Retry with exponential backoff for rate limits and parse errors
        for attempt in range(GEMINI_RETRY_ATTEMPTS):
            try:
                resp = await self.client.aio.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                clean = re.sub(r'^```\w*\n?|```$', '', resp.text.strip())
                match = re.search(r'\{[\s\S]*\}', clean)
                if not match:
                    raise ValueError("No JSON object in response")
                raw_json = match.group()
                try:
                    result = json.loads(raw_json)
                except json.JSONDecodeError:
                    # Attempt JSON repair: fix trailing commas, unescaped quotes
                    fixed = re.sub(r',\s*}', '}', raw_json)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    fixed = re.sub(r':\s*"([^"]*)"([^",}\]]*)"', r': "\1\2"', fixed)
                    result = json.loads(fixed)
                result['score'] = max(0.0, min(1.0, float(result.get('score', 0))))

                # Small delay between successful calls to avoid rate limits
                await asyncio.sleep(GEMINI_CALL_DELAY)
                return result

            except (json.JSONDecodeError, ValueError, AttributeError) as e:
                # JSON parse failure — retry with explicit JSON instruction
                if attempt < GEMINI_RETRY_ATTEMPTS - 1:
                    logger.warning(f"Parse error, retrying ({attempt + 1}/{GEMINI_RETRY_ATTEMPTS}): {e}")
                    await asyncio.sleep(GEMINI_CALL_DELAY)
                    continue
                logger.error(f"Gemini parse error after {GEMINI_RETRY_ATTEMPTS} attempts: {e}")
                # Return None score so ad stays unscored and gets retried next batch
                return {"score": None, "is_risky": False, "category": "parse_error", "reason": str(e)}

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
                # Return None score so ad stays unscored and gets retried next batch
                return {"score": None, "is_risky": False, "category": "api_error", "reason": error_str}

        return {"score": None, "is_risky": False, "category": "api_error", "reason": "Max retries exceeded"}

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
    score = result.get('score')
    # None score = Gemini error, leave as NULL so it gets retried
    if score is None:
        return
    # Escape single quotes for SQL
    reason = str(result.get('reason', '')).replace("'", "''")
    cat = str(result.get('category', '')).replace("'", "''")
    json_str = json.dumps(result).replace("'", "''")

    sql = f"""
    UPDATE ads_with_urls
    SET analysis_score = {score},
        analysis_category = '{cat}',
        analysis_reason = '{reason}',
        analysis_json = '{json_str}',
        analyzed_at = NOW()
    WHERE id = {ad_id};
    """
    run_psql(sql)

RISK_SCORE_THRESHOLD = 0.6
REVIEW_QUEUE_FILE = os.getenv("REVIEW_QUEUE_FILE", "/home/ubuntu/adora_ops/review_queue.txt")
BORDERLINE_LOW = 0.45
BORDERLINE_HIGH = 0.6  # exclusive — everything below RISK_SCORE_THRESHOLD

def upsert_risk_db(url, result):
    score = result.get('score', 0)
    if score < RISK_SCORE_THRESHOLD: return
    
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace('www.', '')
    score = result.get('score', 0)
    evidence = "{" + ",".join([f'"{e}"' for e in result.get('evidence', [])]) + "}"
    
    sql = f"""
    INSERT INTO risk_db (base_url, risk_score, evidence, first_seen, last_updated)
    VALUES ('{domain}', {score}, '{evidence}', NOW(), NOW())
    ON CONFLICT (base_url) 
    DO UPDATE SET
        -- Keep the highest score ever seen for this domain. This avoids "unflagging"
        -- due to model variability and guards against accidental threshold regressions.
        risk_score = GREATEST(risk_db.risk_score, EXCLUDED.risk_score),
        evidence = EXCLUDED.evidence,
        last_updated = NOW();
    """
    run_psql(sql)


def append_review_queue(url, result):
    """Append borderline sites (0.45-0.59) to review_queue.txt for manual triage."""
    score = result.get('score', 0)
    if not (BORDERLINE_LOW <= score < BORDERLINE_HIGH):
        return

    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace('www.', '')
    reason = result.get('reason', '')[:200]

    # Dedup: skip if domain already in file
    try:
        if os.path.isfile(REVIEW_QUEUE_FILE):
            with open(REVIEW_QUEUE_FILE, 'r', encoding='utf-8') as f:
                existing = f.read()
            if domain in existing:
                return
    except Exception:
        pass

    line = f"{domain} | score={score:.2f} | {reason}\n"
    try:
        with open(REVIEW_QUEUE_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
        logger.info(f"  -> review queue: {domain} (score={score:.2f})")
    except Exception as e:
        logger.warning(f"Failed to write review queue: {e}")


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

        if not ads:
            logger.info("No unscored ads.")
            return

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
            append_review_queue(url, res)
    finally:
        # Always clean up browser
        await scraper.stop()
        
    logger.info("Done.")

if __name__ == "__main__":
    # Use lock file to prevent concurrent cron runs
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.warning("Another instance is already running. Exiting.")
        exit(0)
    
    try:
        asyncio.run(main())
    finally:
        # Release lock
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except:
            pass
