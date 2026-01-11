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
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
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
