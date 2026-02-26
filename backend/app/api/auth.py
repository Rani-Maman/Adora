"""
Auth API — Google OAuth login, user profile, logout.
Rate-limited. All secrets from env.
"""

import os
import time
import psycopg2
from collections import defaultdict
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from app.logging_config import get_logger
from app.auth_utils import (
    verify_google_token,
    create_access_token,
    require_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("auth")

# Rate limiting config from env
RATE_LIMIT_AUTH = int(os.getenv("RATE_LIMIT_AUTH_PER_MIN", "10"))
RATE_LIMIT_ME = int(os.getenv("RATE_LIMIT_ME_PER_MIN", "30"))
RATE_WINDOW_SEC = 60

# In-memory rate limit store: {ip: [timestamp, ...]}
_rate_limits = defaultdict(list)


def _check_rate_limit(client_ip: str, limit: int):
    """Sliding window rate limiter. Raises 429 if exceeded."""
    now = time.time()
    window_start = now - RATE_WINDOW_SEC
    # Clean old entries
    _rate_limits[client_ip] = [t for t in _rate_limits[client_ip] if t > window_start]
    if len(_rate_limits[client_ip]) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_limits[client_ip].append(now)


def get_db_connection():
    """Get database connection (same pattern as check.py)."""
    required = ["DB_HOST", "DB_NAME", "DB_USER"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


class GoogleAuthRequest(BaseModel):
    google_token: str


@router.post("/google")
async def auth_google(body: GoogleAuthRequest, request: Request):
    """Authenticate with Google OAuth token. Returns JWT + user."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip, RATE_LIMIT_AUTH)

    # Verify Google token
    google_user = await verify_google_token(body.google_token)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Upsert user
        cursor.execute(
            """
            INSERT INTO users (google_id, email, display_name, avatar_url, last_login)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (google_id) DO UPDATE SET
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                avatar_url = EXCLUDED.avatar_url,
                last_login = CURRENT_TIMESTAMP
            RETURNING id, email, display_name, avatar_url, is_active, created_at
            """,
            (
                google_user["google_id"],
                google_user["email"],
                google_user["display_name"],
                google_user["avatar_url"],
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=500, detail="Failed to create user")

        user_id, email, display_name, avatar_url, is_active, created_at = row

        if not is_active:
            logger.warning("Deactivated user login attempt", extra={"user_id": user_id, "email": email})
            raise HTTPException(status_code=403, detail="Account deactivated")

        # Issue JWT
        access_token = create_access_token(user_id, email)

        logger.info("User authenticated", extra={"user_id": user_id, "email": email, "client_ip": client_ip})

        return {
            "user": {
                "id": user_id,
                "email": email,
                "display_name": display_name,
                "avatar_url": avatar_url,
            },
            "access_token": access_token,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Auth error", extra={"error": str(e)}, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/me")
async def get_me(request: Request, user: dict = Depends(require_user)):
    """Get current user profile."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip, RATE_LIMIT_ME)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, email, display_name, avatar_url, created_at FROM users WHERE id = %s",
            (int(user["sub"]),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "id": row[0],
            "email": row[1],
            "display_name": row[2],
            "avatar_url": row[3],
            "created_at": str(row[4]) if row[4] else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Profile fetch error", extra={"error": str(e), "user_id": user["sub"]})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logout")
async def logout(user: dict = Depends(require_user)):
    """Logout — client clears tokens. Server acknowledges."""
    logger.info("User logged out", extra={"user_id": user["sub"], "email": user["email"]})
    return {"ok": True}
