"""
Combined dropship scorer.
Uses Playwright scraping + Gemini analysis for accurate detection.
"""

import os
from typing import Any

from google import genai

from app.analysis.base import BaseScorer
from app.scraping.site_scraper import SiteScraper, SiteData
from app.logging_config import get_logger

logger = get_logger("gemini_scorer")

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiScorer(BaseScorer):
    """
    Dropship risk scorer using Playwright scraping + Gemini analysis.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._client = None
        self._scraper = SiteScraper(headless=True)
        self._configure_api()

    def _configure_api(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set")
            return
        self._client = genai.Client(api_key=api_key)
        logger.info("Gemini scorer initialized")

    async def score(self, data: dict[str, Any]) -> dict[str, Any]:
        """Score a site for dropship risk."""
        url = data.get("url")
        if not url:
            return self._empty_result("No URL provided")

        # Step 1: Scrape
        logger.info(f"Scraping {url}")
        site_data = await self._scraper.scrape(url)

        if site_data.error:
            logger.error(f"Scrape error: {site_data.error}")
            return self._empty_result(f"Scrape error: {site_data.error}")

        # Step 2: Analyze with Gemini
        if self._client:
            analysis = await self._analyze_with_gemini(site_data)
        else:
            analysis = self._rule_based_analysis(site_data)

        # Enhance result structure
        analysis["scraped_data_summary"] = {
            "title": site_data.title,
            "product": site_data.product_name,
            "price": site_data.product_price,
            "phone": site_data.phone,
            "business_id": site_data.business_id,
        }

        return analysis

    async def _analyze_with_gemini(self, site: SiteData) -> dict[str, Any]:
        """Use Gemini to analyze the scraped data."""

        prompt = (
            f"You are an Israeli e-commerce fraud detector. DISTINGUISH LEGIT VS DROPSHIP.\n\n"
            f"Dropshippers = Sell generic viral gadgets (blankets, posture, lamps) at 4x markup.\n"
            f"Legit Business = Known brands, Niche stores, Handmade, OR **Services/Courses**.\n\n"
            f"SCRAPED DATA:\n"
            f"URL: {site.url}\n"
            f"Title: {site.title}\n"
            f"Product Name: {site.product_name}\n"
            f"Price: {site.product_price}\n"
            f"Shipping Claim: {site.shipping_time}\n"
            f"Business ID (ח.פ.): {site.business_id}\n"
            f"Phone: {site.phone}\n"
            f"Signals: Countdown={site.has_countdown_timer}, "
            f"Scarcity={site.has_scarcity_widget}, WhatsAppOnly={site.has_whatsapp_only}\n\n"
            f"Raw Text Sample:\n"
            f"{site.page_text[:1200]}...\n\n"
            f"ANALYSIS RULES:\n"
            f"1. **DIGITAL PRODUCTS**: If it's a COURSE, WORKSHOP, EBOOK, or SERVICE "
            f'(e.g. "Real Estate", "Math Course") -> **SCORE 0.0 (LEGIT)**. '
            f"Do not flag landing pages for courses as dropshipping.\n"
            f'2. **PRODUCT CHECK**: Is "{site.product_name}" a viral dropship gadget? '
            f"Or a specialized/branded item?\n"
            f"3. **SHIPPING TRUTH**: If they sell generic junk with no address + "
            f'"1-5 day shipping", they are likely lying.\n\n'
            f"SCORING GUIDE:\n"
            f"- 0.0-0.2: Legit (Brands, Niche, **Courses**, **Services**)\n"
            f"- 0.3-0.5: Uncertain / Mixed signals\n"
            f"- 0.6-1.0: Dropship (Generic Gadget + Fake Scarcity + No Identity)\n\n"
            f"Return ONLY valid JSON:\n"
            f"{{\n"
            f'    "score": 0.0-1.0,\n'
            f'    "is_risky": true/false,\n'
            f'    "category": "legit|uncertain|dropship",\n'
            f'    "reason": "1-sentence explanation",\n'
            f'    "evidence": ["list", "of", "factors"],\n'
            f'    "confidence": 0.0-1.0\n'
            f"}}"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name, contents=prompt
            )
            result = self._parse_json(response.text)
            result["scorer"] = self.get_name()
            return result
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return self._rule_based_analysis(site)

    def _rule_based_analysis(self, site: SiteData) -> dict[str, Any]:
        """Fallback rule-based analysis."""
        score = 0.0
        reasons = []

        if site.has_countdown_timer:
            score += 0.2
            reasons.append("Countdown timer")
        if site.has_scarcity_widget:
            score += 0.2
            reasons.append("Scarcity widget")
        if site.has_whatsapp_only:
            score += 0.15
            reasons.append("WhatsApp only")
        if not site.business_id:
            score += 0.15
            reasons.append("No business ID")

        return {
            "score": min(1.0, score),
            "is_risky": score > 0.5,
            "category": "uncertain",
            "evidence": reasons,
            "scorer": f"{self.get_name()}_fallback",
        }

    def _parse_json(self, text: str) -> dict:
        import json
        import re

        clean = re.sub(r"^```\w*\n?|```$", "", text.strip())
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {}

    def _empty_result(self, reason: str) -> dict[str, Any]:
        return {
            "score": 0.0,
            "is_risky": False,
            "category": "unknown",
            "evidence": [reason],
            "confidence": 0.0,
            "scorer": self.get_name(),
        }

    def get_name(self) -> str:
        return "gemini_scorer"


async def analyze_url(url: str) -> dict[str, Any]:
    scorer = GeminiScorer()
    return await scorer.score({"url": url})
