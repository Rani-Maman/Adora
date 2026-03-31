"""
Feedback API — signed-in users vote on risky sites.
One active vote per user+site, with a lifetime switch cap.
"""

import os
import time
from collections import defaultdict
from typing import Literal

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth_utils import require_user
from app.logging_config import get_logger

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = get_logger("feedback")

MAX_SWITCHES = 3
RATE_WINDOW_SEC = 60
RATE_LIMIT_FEEDBACK_READ = int(os.getenv("RATE_LIMIT_FEEDBACK_READ_PER_MIN", "60"))
RATE_LIMIT_FEEDBACK_WRITE = int(os.getenv("RATE_LIMIT_FEEDBACK_WRITE_PER_MIN", "20"))
_rate_limits = defaultdict(list)


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


def _check_rate_limit(key: str, limit: int):
    now = time.time()
    window_start = now - RATE_WINDOW_SEC
    _rate_limits[key] = [t for t in _rate_limits[key] if t > window_start]
    if len(_rate_limits[key]) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_limits[key].append(now)


def _assert_risky_site_exists(cur, risk_db_id: int):
    cur.execute("SELECT id FROM risk_db WHERE id = %s", (risk_db_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Risky site not found")


def _get_feedback_state(cur, user_id: int, risk_db_id: int):
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE vote = 'like') AS likes,
            COUNT(*) FILTER (WHERE vote = 'dislike') AS dislikes
        FROM site_feedback
        WHERE risk_db_id = %s
        """,
        (risk_db_id,),
    )
    counts = cur.fetchone() or (0, 0)

    cur.execute(
        "SELECT vote, switch_count FROM site_feedback WHERE user_id = %s AND risk_db_id = %s",
        (user_id, risk_db_id),
    )
    user_row = cur.fetchone()
    user_vote = user_row[0] if user_row else None
    switch_count = user_row[1] if user_row else 0

    return {
        "risk_db_id": risk_db_id,
        "likes": counts[0] or 0,
        "dislikes": counts[1] or 0,
        "user_vote": user_vote,
        "switch_count": switch_count,
        "switches_remaining": max(0, MAX_SWITCHES - switch_count),
    }


class FeedbackRequest(BaseModel):
    risk_db_id: int
    vote: Literal["like", "dislike"]
    reason: str | None = None


@router.get("/{risk_db_id}")
async def get_feedback(risk_db_id: int, request: Request, user: dict = Depends(require_user)):
    user_id = int(user["sub"])
    limiter_key = f"read:{user_id}:{request.client.host if request.client else 'unknown'}"
    _check_rate_limit(limiter_key, RATE_LIMIT_FEEDBACK_READ)

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        _assert_risky_site_exists(cur, risk_db_id)
        data = _get_feedback_state(cur, user_id, risk_db_id)
        cur.close()
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Feedback read error", extra={"error": str(e), "user_id": user_id, "risk_db_id": risk_db_id}, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            conn.close()


@router.post("")
async def submit_feedback(body: FeedbackRequest, request: Request, user: dict = Depends(require_user)):
    user_id = int(user["sub"])
    limiter_key = f"write:{user_id}:{request.client.host if request.client else 'unknown'}"
    _check_rate_limit(limiter_key, RATE_LIMIT_FEEDBACK_WRITE)

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        _assert_risky_site_exists(cur, body.risk_db_id)

        cur.execute(
            "SELECT vote, switch_count FROM site_feedback WHERE user_id = %s AND risk_db_id = %s FOR UPDATE",
            (user_id, body.risk_db_id),
        )
        existing = cur.fetchone()

        requested_vote = body.vote
        reason = (body.reason or "").strip() or None
        if requested_vote != "dislike":
            reason = None

        if not existing:
            cur.execute(
                """
                INSERT INTO site_feedback (user_id, risk_db_id, vote, reason, switch_count)
                VALUES (%s, %s, %s, %s, 0)
                """,
                (user_id, body.risk_db_id, requested_vote, reason),
            )
            action = "created"
        else:
            current_vote, switch_count = existing
            if current_vote == requested_vote:
                cur.execute(
                    "UPDATE site_feedback SET vote = NULL, reason = NULL WHERE user_id = %s AND risk_db_id = %s",
                    (user_id, body.risk_db_id),
                )
                action = "cleared"
            elif current_vote is None:
                cur.execute(
                    "UPDATE site_feedback SET vote = %s, reason = %s WHERE user_id = %s AND risk_db_id = %s",
                    (requested_vote, reason, user_id, body.risk_db_id),
                )
                action = "set"
            else:
                if switch_count >= MAX_SWITCHES:
                    raise HTTPException(status_code=429, detail="Vote switch limit reached for this site")
                cur.execute(
                    """
                    UPDATE site_feedback
                    SET vote = %s, reason = %s, switch_count = switch_count + 1
                    WHERE user_id = %s AND risk_db_id = %s
                    """,
                    (requested_vote, reason, user_id, body.risk_db_id),
                )
                action = "switched"

        data = _get_feedback_state(cur, user_id, body.risk_db_id)
        conn.commit()
        cur.close()

        logger.info(
            "Feedback updated",
            extra={"user_id": user_id, "risk_db_id": body.risk_db_id, "vote": data["user_vote"], "action": action},
        )
        return {"ok": True, **data}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Feedback write error", extra={"error": str(e), "user_id": user_id, "risk_db_id": body.risk_db_id}, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if conn:
            conn.close()
