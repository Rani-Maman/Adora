"""
Community Reports API — signed-in users report dropshipping sites.
Rate-limited to 3/day per user (DB-enforced).
"""

import os
import re
import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.logging_config import get_logger
from app.auth_utils import require_user

router = APIRouter(prefix="/report", tags=["report"])
logger = get_logger("report")

MAX_URL_LEN = 2000
DAILY_LIMIT = 3


def get_db_connection():
    required = ["DB_HOST", "DB_NAME", "DB_USER"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _valid_url(u: str) -> bool:
    return bool(u and re.match(r'^https?://', u.strip()) and len(u.strip()) <= MAX_URL_LEN)


def _get_daily_count(cur, user_id: int) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM community_reports WHERE user_id = %s AND created_at > NOW() - INTERVAL '1 day'",
        (user_id,),
    )
    return cur.fetchone()[0]


class ReportRequest(BaseModel):
    reported_url: str
    cheaper_url: str


@router.get("/remaining")
async def get_remaining(user: dict = Depends(require_user)):
    user_id = int(user["sub"])
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        count = _get_daily_count(cur, user_id)
        cur.close()
        return {"remaining": max(0, DAILY_LIMIT - count), "limit": DAILY_LIMIT}
    except Exception as e:
        logger.error("Remaining check error", extra={"error": str(e), "user_id": user_id})
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            conn.close()


@router.post("")
async def submit_report(body: ReportRequest, user: dict = Depends(require_user)):
    user_id = int(user["sub"])
    reported = body.reported_url.strip()
    cheaper = body.cheaper_url.strip()

    if not _valid_url(reported):
        raise HTTPException(status_code=400, detail="Invalid reported URL")
    if not _valid_url(cheaper):
        raise HTTPException(status_code=400, detail="Invalid cheaper product URL")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        count = _get_daily_count(cur, user_id)
        if count >= DAILY_LIMIT:
            raise HTTPException(status_code=429, detail="Daily report limit reached")

        cur.execute(
            "INSERT INTO community_reports (user_id, reported_url, cheaper_url) VALUES (%s, %s, %s) RETURNING id",
            (user_id, reported, cheaper),
        )
        report_id = cur.fetchone()[0]
        conn.commit()
        cur.close()

        remaining = max(0, DAILY_LIMIT - count - 1)
        logger.info("Report submitted", extra={"user_id": user_id, "report_id": report_id, "url": reported})
        return {"ok": True, "id": report_id, "remaining": remaining}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Report error", extra={"error": str(e), "user_id": user_id}, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            conn.close()
