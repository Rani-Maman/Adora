"""
Auth utilities — JWT creation/validation and Google token verification.
All secrets loaded from environment variables.
"""

import time
import os
from typing import Optional
from fastapi import Request, HTTPException
import jwt
import httpx
from app.logging_config import get_logger

logger = get_logger("auth")

# All config from env — never hardcode secrets
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("JWT_ISSUER", "adora-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "adora-extension")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_USERINFO_URL = os.getenv("GOOGLE_USERINFO_URL", "https://www.googleapis.com/oauth2/v3/userinfo")
GOOGLE_USERINFO_TIMEOUT = float(os.getenv("GOOGLE_USERINFO_TIMEOUT", "5.0"))


def create_access_token(user_id: int, email: str) -> str:
    """Create a signed JWT with iss/aud/exp claims."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET not configured")

    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + (JWT_EXPIRY_HOURS * 3600),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate JWT. Raises jwt.PyJWTError on failure."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET not configured")

    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        options={"require": ["sub", "email", "iss", "aud", "iat", "exp"]},
    )


def _extract_bearer_token(request: Request) -> Optional[str]:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def get_current_user(request: Request) -> Optional[dict]:
    """FastAPI dependency — returns decoded JWT claims or None."""
    token = _extract_bearer_token(request)
    if not token:
        return None
    try:
        return decode_access_token(token)
    except jwt.PyJWTError:
        return None


def require_user(request: Request) -> dict:
    """FastAPI dependency — returns decoded JWT claims or raises 401."""
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    try:
        return decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as e:
        logger.warning("Invalid JWT", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail="Invalid token")


async def verify_google_token(google_token: str) -> dict:
    """
    Verify Google access token via Google userinfo API.
    Returns dict with google_id, email, display_name, avatar_url.
    Raises HTTPException on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=GOOGLE_USERINFO_TIMEOUT) as client:
            resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {google_token}"},
            )

        if resp.status_code != 200:
            logger.warning("Google token verification failed", extra={"status": resp.status_code})
            raise HTTPException(status_code=401, detail="Invalid Google token")

        data = resp.json()

        if not data.get("email_verified", False):
            raise HTTPException(status_code=401, detail="Google email not verified")

        required = ["sub", "email", "name"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            logger.warning("Google userinfo missing fields", extra={"missing": missing})
            raise HTTPException(status_code=401, detail="Incomplete Google profile")

        return {
            "google_id": data["sub"],
            "email": data["email"],
            "display_name": data["name"],
            "avatar_url": data.get("picture"),
        }

    except httpx.RequestError as e:
        logger.error("Google API request failed", extra={"error": str(e)})
        raise HTTPException(status_code=502, detail="Failed to verify Google token")
