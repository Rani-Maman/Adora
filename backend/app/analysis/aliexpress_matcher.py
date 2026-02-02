"""
AliExpress price comparison module.
Extracts product info from Israeli sites and searches for matches on AliExpress.
"""

import os
import re
from typing import Any, Optional

from google import genai

from app.logging_config import get_logger

logger = get_logger("aliexpress_matcher")

DEFAULT_MODEL = "gemini-2.0-flash"


class AliExpressMatcher:
    """
    Compares Israeli product prices with AliExpress.
    Uses Gemini to analyze both the original site and search AliExpress.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._client = None
        self._configure_api()

    def _configure_api(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set")
            return
        self._client = genai.Client(api_key=api_key)
        logger.info("AliExpress matcher initialized")

    async def analyze_product(self, product_url: str) -> dict[str, Any]:
        """
        Analyze a product page and check if it exists on AliExpress at lower price.

        Returns:
            {
                "is_dropship": bool,
                "confidence": float,
                "israeli_price": str,
                "aliexpress_price": str,
                "price_ratio": float,  # Israeli price / AliExpress price
                "evidence": list[str],
                "aliexpress_url": str (if found)
            }
        """
        if not self._client:
            return self._error_result("API not configured")

        # Step 1: Extract product info from Israeli site
        product_info = await self._extract_product_info(product_url)
        if not product_info:
            return self._error_result("Could not extract product info")

        # Step 2: Search for matching product on AliExpress
        match_result = await self._find_aliexpress_match(product_info)

        return match_result

    async def _extract_product_info(self, url: str) -> Optional[dict]:
        """Extract product details from an Israeli e-commerce site."""
        prompt = f"""Visit this Israeli product page and extract details:

URL: {url}

Return ONLY valid JSON:
{{
    "product_name": "exact Hebrew product name",
    "product_name_english": "translated to English",
    "price_ils": 0.0,
    "description_keywords": ["keyword1", "keyword2"],
    "image_description": "brief description of main product image"
}}"""

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name, contents=prompt
            )
            text = response.text.strip()
            text = re.sub(r"^```\w*\n?|```$", "", text)
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                import json

                return json.loads(match.group())
        except Exception as e:
            logger.error(f"Product extraction failed: {e}")
        return None

    async def _find_aliexpress_match(self, product_info: dict) -> dict[str, Any]:
        """Search AliExpress for a matching product and compare prices."""

        product_name = product_info.get("product_name_english", "")
        keywords = product_info.get("description_keywords", [])
        israeli_price = product_info.get("price_ils", 0)
        image_desc = product_info.get("image_description", "")

        # search_query = f"{product_name} {' '.join(keywords[:3])}"

        prompt = f"""Search AliExpress for this product and find the best match:

PRODUCT TO FIND:
- Name (English): {product_name}
- Keywords: {', '.join(keywords)}
- Image description: {image_desc}
- Israeli price: ₪{israeli_price}

TASK:
1. Search AliExpress.com for this product
2. Find the most similar product listing
3. Note the AliExpress price in USD or ILS
4. Compare the prices

ANALYSIS:
If the Israeli store is selling the SAME product at 3x+ the AliExpress price, it's likely dropshipping.

Return ONLY valid JSON:
{{
    "found_match": true/false,
    "aliexpress_product": "name of matching product on AliExpress",
    "aliexpress_price_usd": 0.0,
    "aliexpress_price_ils": 0.0,
    "match_confidence": 0.0-1.0,
    "price_ratio": 0.0,
    "is_dropship": true/false,
    "evidence": ["reason1", "reason2"]
}}"""

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name, contents=prompt
            )
            text = response.text.strip()
            text = re.sub(r"^```\w*\n?|```$", "", text)
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                import json

                result = json.loads(match.group())
                result["israeli_price"] = f"₪{israeli_price}"
                return result
        except Exception as e:
            logger.error(f"AliExpress search failed: {e}")

        return self._error_result("Could not search AliExpress")

    def _error_result(self, reason: str) -> dict[str, Any]:
        return {
            "is_dropship": False,
            "confidence": 0.0,
            "evidence": [reason],
            "error": True,
        }


async def check_if_dropshipping(product_url: str) -> dict[str, Any]:
    """
    Convenience function to check if a product is being dropshipped.

    Args:
        product_url: URL of the Israeli product page

    Returns:
        Analysis result with dropshipping determination
    """
    matcher = AliExpressMatcher()
    return await matcher.analyze_product(product_url)
