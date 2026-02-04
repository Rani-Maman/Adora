"""
Check API - Lightweight endpoint to query risk_db.
Used by Chrome extension for real-time site checking.
"""

from fastapi import APIRouter, Query
from typing import Optional
import os
import psycopg2
from urllib.parse import urlparse

router = APIRouter(prefix="/check", tags=["check"])


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", 5432),
        database=os.getenv("DB_NAME", "firecrawl"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def extract_domain(url: str) -> str:
    """Extract base domain from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return url


@router.get("/")
async def check_url(url: str = Query(..., description="URL to check")):
    """
    Check if a URL/domain exists in the risk database.
    Returns risk info if found, otherwise returns not risky.
    """
    domain = extract_domain(url)
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query risk_db for this domain
        cursor.execute(
            """
            SELECT base_url, risk_score, evidence, category, first_seen
            FROM risk_db
            WHERE TRIM(base_url) ILIKE %s OR TRIM(base_url) ILIKE %s
            LIMIT 1
            """,
            (domain, f"%{domain}%")
        )
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            return {
                "risky": True,
                "domain": result[0],
                "score": float(result[1]) if result[1] else 0.0,
                "evidence": result[2] if result[2] else [],
                "category": result[3] if result[3] else "unknown",
                "first_seen": str(result[4]) if result[4] else None,
            }
        
        return {"risky": False, "domain": domain}
        
    except Exception as e:
        # On error, default to not risky (fail open)
        return {"risky": False, "domain": domain, "error": str(e)}
