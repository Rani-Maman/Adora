#!/usr/bin/env python3
"""
Batch Ad Analyzer.
Runs Playwright + Gemini Scorer on unscored ads.
"""
import asyncio
import os
import re
import json
import logging
import random
import fcntl
from datetime import datetime
from dataclasses import dataclass
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser
from google import genai
from google.genai import types
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
GEMINI_CALL_DELAY = 4  # seconds between API calls — grounded 2.5-flash needs more time
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
    r'^https?://did\.li/',                      # URL shortener
    r'^https?://bit\.ly/',                      # URL shortener
    r'^https?://tinyurl\.com/',                 # URL shortener
    r'^https?://(?:\w+\.)?shein\.com/',          # Shein (legit marketplace)
    r'^https?://(?:\w+\.)?aliexpress\.com/',     # AliExpress (legit marketplace)
    r'^https?://s\.click\.aliexpress\.com/',     # AliExpress affiliate links
    r'^https?://(?:\w+\.)?temu\.com/',           # Temu (legit marketplace)
    r'^https?://t\.me/',                          # Telegram
    r'^https?://[^/]*vp4\.me/',                   # Group invitations
    r'^https?://[^/]*ravpage\.co\.il/',            # Landing page builder
    r'^https?://[^/]*minisite\.ms/',               # Landing page builder
    r'^https?://urlgeni\.us/',                     # URL shortener
    r'^https?://[^/]*b144websites\.co\.il/',         # B144 Yellow Pages landing pages
    r'^https?://(?:www\.)?b144\.co\.il/',             # B144 Yellow Pages
    r'^https?://(?:www\.)?simplebooking\.it/',        # Hotel booking widget
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
        # Blanket-skip non-profit TLDs (never e-commerce)
        if domain.endswith('.org.il'):
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
                    # Try stripping advertorial suffix first (e.g. /Product/adv → /Product/)
                    product_links = []
                    adv_suffixes = ['/adv', '/advertorial', '/landing', '/adv-', '/lp']
                    cur_url = page.url.rstrip('/')
                    for suffix in adv_suffixes:
                        if cur_url.lower().endswith(suffix):
                            base = cur_url[:len(cur_url) - len(suffix)] + '/'
                            if base:
                                product_links = [base]
                            break

                    # Try /products/ links
                    if not product_links:
                        product_links = await page.eval_on_selector_all(
                            'a[href*="/products/"]',
                            'els => els.map(e => e.href)'
                        )
                    if not product_links:
                        # Look for CTA buttons on advertorial/funnel pages
                        try:
                            cta = await page.evaluate("""() => {
                                var links = Array.from(document.querySelectorAll("a[href]"));
                                var ctaRe = /לרכישה|הזמינו|הזמן|לרכוש|בדיקת זמינות|קבלו|להזמנה|קנו|הוסף לסל|add.to.cart|buy.now|order.now|shop.now|get.yours|לפרטים נוספים|להזמנה עכשיו|לצפייה במוצר|למוצר/i;
                                var productRe = /\\/products?\\/|\\/order/i;
                                var badRe = /\\/(cart|policy|terms|privacy|contact|about|faq|return|shipping)\\/?$/i;
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
                                        if (badRe.test(u.pathname)) continue;
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

        prompt = f"""You are an Israeli e-commerce fraud detector with web search access. Your ONLY goal is to identify sites selling PHYSICAL PRODUCTS that are dropshipped from AliExpress/Temu at inflated prices. If the site does NOT sell a physical product, it is NOT relevant — score 0.0 as service.

USE YOUR SEARCH TOOLS to verify:
1. Search for the business name — does it have real Google reviews, social media, news mentions?
2. Search for the product name on AliExpress/Temu — is it the same product at 3-6x markup?
3. If the site claims a business registration, VERIFY IT: Israeli ח.פ./ע.מ., EU VAT number, UK Companies House, US EIN/state registration, or any other country's company registry. A verified registration in ANY country counts as legitimate identity.
4. If a company name is mentioned (e.g. in legal/about/TOS pages), search for it in the relevant country's business registry to confirm it exists.

SCORING RULES:

DROPSHIP (score 0.7-1.0) — MUST have multiple confirmed signals:
- Product confirmed available on AliExpress/Temu at fraction of the price
- No verifiable business identity in ANY country (no registration, no real address, no Google presence)
- TOS/About admits third-party suppliers, dropshipping, overseas fulfillment
- Fake reviews (WhatsApp screenshots instead of real review platform)
- Single product funnel with heavy urgency tactics

LEGITIMATE (score 0.0-0.2) — any of these is strong evidence:
- Verified business registration in ANY country: Israeli ח.פ., EU VAT number, UK company, US LLC/Corp, etc.
- Real company with verifiable address (Google Maps, company registry, government database)
- Real customer reviews on Google/Trustpilot/Facebook
- Brand has social media presence with history (not just ads)
- Product is unique/handmade/custom — not mass-produced AliExpress goods
- Physical store or established online brand
- Furniture, chairs, tables, home décor — these are almost never dropshipped from AliExpress. Score 0.0-0.2 unless overwhelming evidence.
- Jewelry, watches, accessories — score as legit UNLESS the exact same item is confirmed on AliExpress at a fraction of the price. Branded/artisan jewelry is NOT dropship.
- Products priced under ₪100 — low price alone does not indicate dropship. Only flag if it's a generic gadget/electronics/accessory commonly sold on AliExpress AND the markup is significant (3x+). Food, cosmetics, local brands at low prices are NOT dropship.
IMPORTANT: A company registered abroad (Cyprus, UK, US, etc.) selling to Israel is NOT suspicious by itself. Many legitimate businesses operate internationally. Only flag if the product itself is a generic AliExpress item at inflated prices AND no real business exists.

NON-PHYSICAL / SERVICE (score 0.0) — NOT a physical product, cannot be dropshipped:
- Any service: therapy, fitness, consulting, coaching, cleaning, personal training, healthcare, physiotherapy
- Restaurants, food delivery, catering
- Courses, workshops, education, webinars
- Software, SaaS, apps, bots, chatbots
- Real estate, travel, events, tickets
- Community groups, WhatsApp/Telegram groups, deal aggregators, coupon sites
- Pages with no physical product (newsletters, sign-up pages, portfolios, landing pages for services)
  NOTE: advertorial/lead pages that link TO a physical product are NOT service — score based on that product

UNCERTAIN (score 0.3-0.5) — use ONLY when:
- Product could be from AliExpress but you cannot confirm via search
- Business identity is unclear but not obviously fake
- Mixed signals that search couldn't resolve

BE DECISIVE: if search confirms the product on AliExpress at a fraction of the price AND the site has no real business identity, score 0.8+. If search confirms a real business, score 0.0-0.2. Avoid the 0.4-0.6 range unless genuinely uncertain after searching.

DATA:
URL: {site.url}
Title: {site.title}
Product: {site.product_name}
Price: {"₪" + str(site.product_price) if site.product_price else "unknown"}
Shipping: {site.shipping_time}
Signals: Countdown={site.has_countdown_timer}, Scarcity={site.has_scarcity_widget}
Text: {site.page_text[:800]}
{f"Terms/Policy page: {site.tos_text[:600]}" if site.tos_text else ""}

Return JSON: {{ "score": float, "is_risky": bool, "category": "dropship|legit|service|uncertain", "reason": "str", "evidence": ["str"] }}
Category MUST be exactly one of: "dropship", "legit", "service", "uncertain"."""
        
        # Retry with exponential backoff for rate limits and parse errors
        for attempt in range(GEMINI_RETRY_ATTEMPTS):
            try:
                grounding_config = types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
                resp = await self.client.aio.models.generate_content(
                    model='gemini-2.5-flash', contents=prompt, config=grounding_config
                )
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

                # Normalize category to enum
                valid_cats = {"dropship", "legit", "service", "uncertain"}
                raw_cat = result.get("category", "uncertain").lower().strip()
                if raw_cat not in valid_cats:
                    if "dropship" in raw_cat or "scam" in raw_cat or "fraud" in raw_cat:
                        raw_cat = "dropship"
                    elif any(k in raw_cat for k in (
                        "service", "restaurant", "course", "saas", "event",
                        "travel", "real estate", "software", "digital",
                        "food", "delivery", "marketing", "consulting",
                        "coaching", "fitness", "healthcare", "therapy",
                        "cleaning", "education", "workshop",
                    )):
                        raw_cat = "service"
                    elif any(k in raw_cat for k in ("legit", "legitimate", "brand", "home", "kitchen", "beauty")):
                        raw_cat = "legit"
                    else:
                        raw_cat = "uncertain"
                result["category"] = raw_cat

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
                return {"score": -1, "is_risky": False, "category": "parse_error", "reason": str(e)}

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

# --- DB Utilities ---
def get_db_conn():
    return psycopg2.connect(host='localhost', dbname='firecrawl', user='postgres', password='')

def _mark_skipped(skip_ids):
    """Bulk-mark filtered ads as skipped so they don't clog the backlog."""
    if not skip_ids:
        return
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ads_with_urls
                SET analysis_score = 0.0, analysis_category = 'skipped',
                    analysis_reason = 'Filtered by skip patterns or whitelist',
                    analyzed_at = NOW()
                WHERE id = ANY(%s) AND analysis_score IS NULL
            """, (skip_ids,))
        conn.commit()
        logger.info(f"Marked {cur.rowcount} ads as skipped.")
    finally:
        conn.close()

def fetch_unscored_ads(limit=10):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Over-fetch 5x to compensate for Python-side skip filtering
            cur.execute("""
                SELECT id, destination_product_url, advertiser_name
                FROM ads_with_urls
                WHERE analysis_score IS NULL
                  AND destination_product_url IS NOT NULL
                  AND LENGTH(destination_product_url) > 15
                LIMIT %s
            """, (limit * 5,))
            rows = cur.fetchall()
    finally:
        conn.close()
    # Filter with should_skip_url (single source of truth for skip patterns)
    filtered = []
    skip_ids = []
    for r in rows:
        if should_skip_url(r[1]):
            skip_ids.append(r[0])
        else:
            filtered.append((r[0], r[1], r[2]))
    _mark_skipped(skip_ids)
    return filtered[:limit]

def update_ad_result(ad_id, result):
    score = result.get('score')
    if score is None:
        return
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ads_with_urls
                SET analysis_score = %s, analysis_category = %s,
                    analysis_reason = %s, analysis_json = %s, analyzed_at = NOW()
                WHERE id = %s
            """, (
                score,
                str(result.get('category', '')),
                str(result.get('reason', '')),
                json.dumps(result),
                ad_id,
            ))
        conn.commit()
    finally:
        conn.close()

RISK_SCORE_THRESHOLD = 0.6

def upsert_risk_db(url, result, advertiser_name=None):
    score = result.get('score')
    if score is None or score < RISK_SCORE_THRESHOLD:
        return
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace('www.', '')
    evidence = result.get('evidence', [])
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO risk_db (base_url, risk_score, evidence, advertiser_name, first_seen, last_updated)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (base_url) DO UPDATE SET
                    risk_score = EXCLUDED.risk_score,
                    evidence = EXCLUDED.evidence,
                    advertiser_name = COALESCE(EXCLUDED.advertiser_name, risk_db.advertiser_name),
                    last_updated = NOW()
            """, (domain, score, evidence, advertiser_name))
        conn.commit()
    finally:
        conn.close()

def delete_from_risk_db(url):
    """Remove domain from risk_db when re-analysis scores below threshold."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace('www.', '')
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM risk_db WHERE base_url = %s", (domain,))
            if cur.rowcount > 0:
                logger.info(f"  Removed from risk_db: {domain}")
        conn.commit()
    finally:
        conn.close()


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

        for ad_id, url, adv_name in ads:
            logger.info(f"[{ad_id}] Processing {url[:80]}...")
            site = await scraper.scrape(url)
            if site.error:
                logger.warning(f"Scrape Error: {site.error[:100]}")
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
            upsert_risk_db(url, res, adv_name)

            # If re-analysis dropped below threshold, remove from risk_db
            score = res.get('score')
            if score is not None and 0 <= score < RISK_SCORE_THRESHOLD:
                delete_from_risk_db(url)
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
