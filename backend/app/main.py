"""
FastAPI application entry point.
"""

import time
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.logging_config import setup_logging, get_logger
from app.api.whitelist import router as whitelist_router
from app.api.analyze import router as analyze_router
from app.api.check import router as check_router
from app.api.auth import router as auth_router

# Initialize logging with file output and JSON format
setup_logging(log_file="logs/api.log", json_logs=True)
logger = get_logger("api")

# API key for authenticating extension requests
ADORA_API_KEY = os.getenv("ADORA_API_KEY")

app = FastAPI(
    title="Adora API",
    description="Dropship scam detection API for Israeli e-commerce",
    version=__version__,
)


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    """Verify API key for /check and /analyze endpoints."""
    path = request.url.path
    # Only protect API endpoints, not health checks
    protected_paths = ["/check", "/analyze", "/whitelist", "/auth"]
    needs_auth = any(path.startswith(p) for p in protected_paths)

    if needs_auth and ADORA_API_KEY:
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if api_key != ADORA_API_KEY:
            logger.warning(
                "Unauthorized API access attempt",
                extra={"path": path, "client_ip": request.client.host if request.client else "unknown"}
            )
            return JSONResponse(status_code=403, content={"error": "Invalid or missing API key"})

    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests and their response times."""
    start_time = time.time()

    # Get client IP
    client_ip = request.client.host if request.client else "unknown"

    # Log request
    logger.info(
        "Request started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query) if request.url.query else None,
            "client_ip": client_ip,
            "user_agent": request.headers.get("user-agent", "unknown"),
        }
    )

    # Process request
    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        # Log response
        logger.info(
            "Request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(process_time * 1000, 2),
                "client_ip": client_ip,
            }
        )

        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(
            f"Request failed: {str(e)}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round(process_time * 1000, 2),
                "client_ip": client_ip,
            },
            exc_info=True
        )
        raise

# Include routers
app.include_router(whitelist_router)
app.include_router(analyze_router)
app.include_router(check_router)
app.include_router(auth_router)

# CORS for Chrome extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Log application startup."""
    logger.info(f"Adora API v{__version__} starting up")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "version": __version__}


@app.get("/health")
async def health():
    """Detailed health check."""
    logger.debug("Health check requested")
    return {"status": "healthy", "version": __version__, "service": "adora-api"}


@app.get("/tunnel-url")
async def get_tunnel_url():
    """Get current Cloudflare tunnel URL (auto-updated by cron)."""
    try:
        # Read from file written by update-tunnel-url.sh
        with open("/tmp/tunnel-url.txt", "r") as f:
            url = f.read().strip()
        return {"url": url, "status": "ok"}
    except FileNotFoundError:
        return {"url": None, "status": "not_found", "message": "Tunnel URL not yet detected"}
    except Exception as e:
        logger.error(f"Error reading tunnel URL: {e}")
        return {"url": None, "status": "error", "message": str(e)}
