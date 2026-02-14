"""
Price matching module — finds cheaper alternatives on AliExpress/Temu/Alibaba.
Uses Gemini 2.5 Flash with Google Search grounding for real product lookups.
"""

import json
import os
import re

from google import genai
from google.genai import types

from app.logging_config import get_logger

logger = get_logger("aliexpress_matcher")

MODEL = "gemini-2.5-flash"
ILS_TO_USD = 0.27


class PriceMatcher:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._client = genai.Client(api_key=api_key)

    async def extract_product_info(self, page_text: str) -> dict | None:
        """Extract product info from Hebrew page text. No search grounding."""
        prompt = (
            "Analyze this Israeli product page text and extract product details.\n"
            "Translate the product name to generic English search terms (not brand name).\n"
            "Hebrew names won't work on AliExpress — use descriptive English.\n\n"
            f"Page text:\n{page_text}\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "product_name_hebrew": "original name",\n'
            '  "product_name_english": "generic English search terms",\n'
            '  "price_ils": 0.0,\n'
            '  "category": "electronics|clothing|home|beauty|toys|other",\n'
            '  "key_features": ["feature1", "feature2"],\n'
            '  "search_query": "optimized AliExpress search query"\n'
            "}"
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=MODEL, contents=prompt
            )
            return self._parse_json(response.text)
        except Exception as e:
            logger.error(f"Extract failed: {e}")
            return None

    async def search_cheaper(self, product_info: dict) -> dict:
        """Search for cheaper alternatives using Google Search grounding."""
        name = product_info.get("product_name_english", "")
        features = product_info.get("key_features", [])
        price = product_info.get("price_ils", 0)
        search_q = product_info.get("search_query", name)
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
            response = await self._client.aio.models.generate_content(
                model=MODEL, contents=prompt, config=config
            )
            result = self._parse_json(response.text)
            if result:
                # Attach grounding metadata
                if response.candidates and response.candidates[0].grounding_metadata:
                    meta = response.candidates[0].grounding_metadata
                    if meta.web_search_queries:
                        result["grounding_queries"] = meta.web_search_queries
                return result
        except Exception as e:
            logger.error(f"Search failed: {e}")

        return {"matches": [], "no_match_reason": "API error"}

    def _parse_json(self, text: str) -> dict | None:
        cleaned = re.sub(r"^```\w*\n?|```$", "", text.strip())
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None
