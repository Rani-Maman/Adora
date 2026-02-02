"""
Site scraper using Playwright.
Extracts structured data from Israeli e-commerce sites for dropship analysis.
"""

import asyncio
import re
from typing import Any, Optional
from dataclasses import dataclass, asdict

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from app.logging_config import get_logger

logger = get_logger("site_scraper")


@dataclass
class SiteData:
    """Structured data extracted from a site."""
    url: str
    title: str = ""
    
    # Product info
    product_name: str = ""
    product_price: float = 0.0
    product_description: str = ""
    
    # Shipping
    shipping_time: str = ""
    
    # Business info
    business_id: str = ""  # ח.פ. or ע.מ.
    phone: str = ""
    email: str = ""
    address: str = ""
    
    # Site signals
    has_countdown_timer: bool = False
    has_scarcity_widget: bool = False
    has_whatsapp_only: bool = False
    
    # Raw text for Gemini
    page_text: str = ""
    footer_text: str = ""
    
    # Errors
    error: str = ""


class SiteScraper:
    """Scrapes Israeli e-commerce sites for dropship analysis."""
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
    
    async def scrape(self, url: str) -> SiteData:
        """
        Scrape a site and extract structured data.
        """
        data = SiteData(url=url)
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                # Use standard context without locale forcing to match real behavior
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()
                
                # Navigate
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                await page.wait_for_timeout(2000)  # Wait for JS
                
                # Extract basic info
                data.title = await page.title()
                data.page_text = (await page.inner_text("body"))[:4000]
                
                # Product Name
                h1 = await page.query_selector("h1")
                if h1:
                    data.product_name = (await h1.inner_text()).strip()[:200]
                
                # Price
                await self._extract_price(page, data)
                
                # Shipping
                await self._extract_shipping(page, data)
                
                # Business Info
                await self._extract_business_info(page, data)
                
                # Signals
                data.has_countdown_timer = bool(await page.query_selector("[class*='countdown'], [class*='timer']"))
                data.has_scarcity_widget = bool(re.search(r'רק\s+\d+\s+(?:נותר|נשאר)|only\s+\d+\s+left', data.page_text, re.I))
                
                has_whatsapp = "whatsapp" in data.page_text.lower() or "wa.me" in data.page_text.lower()
                data.has_whatsapp_only = has_whatsapp and not data.phone and not data.email

                await browser.close()
                
        except PlaywrightTimeout:
            data.error = "Timeout loading page"
            logger.error(f"Timeout scraping {url}")
        except Exception as e:
            data.error = str(e)
            logger.error(f"Error scraping {url}: {e}")
        
        return data

    async def _extract_price(self, page: Page, data: SiteData):
        selectors = ["[class*='price']", "[itemprop='price']", ".current-price", ".product-price"]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    match = re.search(r'[\d,]+\.?\d*', text.replace(',', ''))
                    if match:
                        data.product_price = float(match.group())
                        return
            except:
                pass

    async def _extract_shipping(self, page: Page, data: SiteData):
        # Look in page text
        match = re.search(r'(\d+[-–]\d+\s*(?:ימי|ימים|days|business days))', data.page_text, re.I)
        if match:
            data.shipping_time = match.group(0)[:50]

    async def _extract_business_info(self, page: Page, data: SiteData):
        # Business ID
        hp_match = re.search(r'ח\.?פ\.?\s*[:\-]?\s*(\d{9})', data.page_text)
        if hp_match:
            data.business_id = hp_match.group(1)
        
        # Phone
        phone_match = re.search(r'(\*\d{4}|\d{2,3}[-\s]?\d{7})', data.page_text)
        if phone_match:
            data.phone = phone_match.group(1)
            
        # Email
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', data.page_text)
        if email_match:
            data.email = email_match.group(0)


async def scrape_site(url: str) -> dict[str, Any]:
    scraper = SiteScraper()
    data = await scraper.scrape(url)
    return asdict(data)
