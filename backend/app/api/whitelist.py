"""
Whitelist API routes for Chrome extension.
Serves cached whitelist of trusted domains.
"""

from pathlib import Path
from fastapi import APIRouter
from app.logging_config import get_logger

logger = get_logger("whitelist")
router = APIRouter(prefix="/whitelist", tags=["whitelist"])

# Cache the whitelist in memory
_whitelist_cache: set[str] | None = None


def _load_whitelist() -> set[str]:
    """Load all whitelist files into a set."""
    global _whitelist_cache
    if _whitelist_cache is not None:
        return _whitelist_cache

    domains = set()
    data_dir = Path(__file__).parent.parent.parent / "data"

    whitelist_files = [
        "whitelist_global.txt",
        "whitelist_israel.txt",
        "whitelist_israel_extra.txt",
    ]

    for filename in whitelist_files:
        filepath = data_dir / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if line and not line.startswith("#"):
                        domains.add(line.lower())
            logger.info(f"Loaded {filename}: {len(domains)} total domains")

    _whitelist_cache = domains
    logger.info(f"Whitelist loaded: {len(domains)} domains total")
    return domains


# Trusted TLDs that are auto-whitelisted
TRUSTED_TLDS = frozenset(
    [
        ".gov.il",
        ".ac.il",
        ".edu",
        ".gov",
        ".edu.il",
        ".org.il",
        ".muni.il",
        ".idf.il",
        ".k12.il",
    ]
)


@router.get("/domains")
async def get_whitelist():
    """Return the full whitelist for extension caching."""
    domains = _load_whitelist()
    return {
        "domains": list(domains),
        "trusted_tlds": list(TRUSTED_TLDS),
        "count": len(domains),
    }


@router.get("/check/{domain}")
async def check_domain(domain: str):
    """Quick check if a domain is whitelisted."""
    domain = domain.lower().strip()
    domains = _load_whitelist()

    # Check exact match
    if domain in domains:
        return {"domain": domain, "whitelisted": True, "reason": "in_whitelist"}

    # Check trusted TLDs
    for tld in TRUSTED_TLDS:
        if domain.endswith(tld):
            return {"domain": domain, "whitelisted": True, "reason": f"trusted_tld:{tld}"}

    return {"domain": domain, "whitelisted": False, "reason": None}
