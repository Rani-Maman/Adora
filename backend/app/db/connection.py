"""
Shared database configuration module.
All scripts should import this instead of hardcoding credentials.
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load .env from home directory or current directory
load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()


def get_db_connection():
    """Get a database connection using environment variables."""
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_api_keys():
    """Get API keys from environment."""
    return {
        "firecrawl": os.getenv("FIRECRAWL_API_KEY") or os.getenv("FIRECRAWLER_API_KEY"),
        "gemini": os.getenv("GEMINI_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
    }
