"""
Analysis API.
Exposes dropship detection scorer.
"""

from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, HttpUrl

from app.analysis.gemini_scorer import analyze_url
from app.logging_config import get_logger

logger = get_logger("analyze_api")
router = APIRouter(prefix="/analyze", tags=["analyze"])


class AnalyzeRequest(BaseModel):
    url: HttpUrl
    force_refresh: bool = False


class AnalyzeResponse(BaseModel):
    url: str
    score: float
    is_risky: bool
    category: str
    reason: Optional[str] = None
    evidence: list[str] = []
    scraped_data: Optional[dict[str, Any]] = None
    scorer: str


@router.post("/", response_model=AnalyzeResponse)
async def analyze_site(request: AnalyzeRequest):
    """
    Analyze a URL for dropshipping risk.
    Uses Playwright scraping + Gemini AI analysis.
    """
    url_str = str(request.url)
    logger.info(f"Analyzing URL: {url_str}")

    try:
        # Call the scorer
        result = await analyze_url(url_str)
        
        # Format response
        return AnalyzeResponse(
            url=url_str,
            score=result.get("score", 0.0),
            is_risky=result.get("is_risky", False),
            category=result.get("category", "unknown"),
            reason=result.get("reason"),
            evidence=result.get("evidence", []),
            scraped_data=result.get("scraped_data_summary"),
            scorer=result.get("scorer", "unknown")
        )

    except Exception as e:
        logger.error(f"Analysis failed for {url_str}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
