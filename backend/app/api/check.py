"""
Check API - Lightweight endpoint to query risk_db.
Used by Chrome extension for real-time site checking.
"""

from fastapi import APIRouter, Query
import os
import psycopg2
import time
from urllib.parse import urlparse
from app.logging_config import get_logger

router = APIRouter(prefix="/check", tags=["check"])
logger = get_logger("check")


def get_db_connection():
    """Get database connection."""
    required = ["DB_HOST", "DB_NAME", "DB_USER"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def extract_domain(url: str) -> str:
    """Extract base domain from URL."""
    try:
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


@router.get("/")
async def check_url(url: str = Query(..., description="URL to check")):
    """
    Check if a URL/domain exists in the risk database.
    Returns risk info if found, otherwise returns not risky.
    """
    domain = extract_domain(url)

    if not domain:
        logger.warning(f"Invalid URL provided: {url}")
        return {"risky": False, "domain": "", "error": "Invalid URL"}

    try:
        start_time = time.time()
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query risk_db for exact domain match
        cursor.execute(
            """
            SELECT base_url, risk_score, evidence, advertiser_name, first_seen, price_matches
            FROM risk_db
            WHERE LOWER(TRIM(base_url)) = LOWER(%s)
            LIMIT 1
            """,
            (domain,)
        )

        result = cursor.fetchone()
        query_time = time.time() - start_time
        cursor.close()
        conn.close()

        if result:
            logger.info(
                "Domain lookup: RISKY",
                extra={
                    "domain": domain,
                    "risk_score": float(result[1]) if result[1] else 0.0,
                    "query_time_ms": round(query_time * 1000, 2),
                    "found": True,
                }
            )
            # Parse price_matches — psycopg2 returns JSONB as Python objects
            price_matches = result[5] if result[5] else []

            return {
                "risky": True,
                "domain": result[0],
                "score": float(result[1]) if result[1] else 0.0,
                "evidence": result[2] if result[2] else [],
                "advertiser": result[3] if result[3] else None,
                "first_seen": str(result[4]) if result[4] else None,
                "price_matches": price_matches,
            }

        logger.info(
            "Domain lookup: SAFE",
            extra={
                "domain": domain,
                "query_time_ms": round(query_time * 1000, 2),
                "found": False,
            }
        )
        return {"risky": False, "domain": domain}

    except Exception as e:
        logger.error(
            f"Database error checking domain: {domain}",
            extra={"domain": domain, "error": str(e)},
            exc_info=True
        )
        # On error, default to not risky (fail open)
        return {"risky": False, "domain": domain, "error": str(e)}
