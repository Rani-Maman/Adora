"""
FastAPI application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.logging_config import setup_logging, get_logger

# Initialize logging
setup_logging()
logger = get_logger("api")

app = FastAPI(
    title="Adora API",
    description="Dropship scam detection API for Israeli e-commerce",
    version=__version__,
)

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
