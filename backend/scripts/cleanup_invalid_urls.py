#!/usr/bin/env python3
"""
One-time script to mark invalid URLs in the backlog as analyzed with score=-1.
This prevents wasting time and API credits on malformed URLs.
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
import psycopg2

# Load environment
script_dir = Path(__file__).resolve().parent
env_path = script_dir.parent / '.env'
load_dotenv(env_path)

# Common valid TLDs
VALID_TLDS = {
    '.com', '.org', '.net', '.edu', '.gov', '.co', '.io', '.ai', '.app',
    '.co.il', '.com.au', '.co.uk', '.ca', '.de', '.fr', '.jp', '.cn',
    '.ru', '.br', '.in', '.mx', '.es', '.it', '.nl', '.se', '.no', '.dk',
    '.info', '.biz', '.me', '.tv', '.shop', '.store', '.online', '.site',
    # Short link / missing from original
    '.ly', '.li', '.be', '.bz', '.to', '.us', '.ps', '.gy', '.cx', '.cc',
    '.cl', '.pro', '.im', '.link', '.page', '.fun', '.center', '.health',
    '.world', '.click', '.blog', '.academy', '.agency', '.studio', '.design',
    '.digital', '.marketing', '.media', '.technology', '.services', '.social',
    '.life', '.live', '.today', '.space', '.ltd', '.rest', '.delivery',
    # Israeli new ccTLD
    '.il',
}

# Strip trailing non-URL characters (emoji, Hebrew, timestamps, markdown)
_TRAILING_GARBAGE = re.compile(r'[\s\u0590-\u05FF\U0001F300-\U0001FFFF✅🚛📲▶️*)()\[\]]+$')

def sanitize_url(url: str) -> str:
    """Strip trailing garbage characters that aren't part of the URL."""
    if not url:
        return url
    return _TRAILING_GARBAGE.sub('', url.strip())

def is_valid_url(url: str) -> bool:
    """Validate URL has proper structure and known TLD."""
    if not url or not url.strip():
        return False

    url = sanitize_url(url)

    try:
        parsed = urlparse(url)
        
        # Must have scheme (http/https)
        if not parsed.scheme or parsed.scheme not in ['http', 'https']:
            return False
        
        # Must have netloc (domain)
        if not parsed.netloc:
            return False
        
        # Check for valid TLD
        netloc_lower = parsed.netloc.lower()
        
        # Remove port if present
        if ':' in netloc_lower:
            netloc_lower = netloc_lower.split(':')[0]
        
        # Check if ends with a known TLD
        has_valid_tld = any(netloc_lower.endswith(tld) for tld in VALID_TLDS)
        
        # Additional check: must have at least one dot in domain
        if '.' not in netloc_lower:
            return False
        
        # Reject if domain is just TLD (e.g., "co.il")
        if netloc_lower in VALID_TLDS or netloc_lower.startswith('.'):
            return False
        
        return has_valid_tld
        
    except Exception:
        return False

def get_db_connection():
    """Connect to PostgreSQL database."""
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def main():
    print("=" * 60)
    print("INVALID URL CLEANUP SCRIPT")
    print("=" * 60)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all unanalyzed ads
    print("\n[1] Fetching unanalyzed ads...")
    cursor.execute("""
        SELECT id, destination_product_url 
        FROM ads_with_urls 
        WHERE analysis_score IS NULL
        ORDER BY id;
    """)
    
    pending_ads = cursor.fetchall()
    print(f"    Found {len(pending_ads)} unanalyzed ads")
    
    # Validate each URL
    print("\n[2] Validating URLs...")
    invalid_ids = []
    invalid_samples = []
    
    for ad_id, url in pending_ads:
        if not is_valid_url(url):
            invalid_ids.append(ad_id)
            if len(invalid_samples) < 10:
                invalid_samples.append(url)
    
    print(f"    Found {len(invalid_ids)} invalid URLs ({len(invalid_ids)/len(pending_ads)*100:.1f}%)")
    
    if invalid_samples:
        print("\n    Sample invalid URLs:")
        for url in invalid_samples:
            print(f"      - {url}")
    
    # Mark invalid URLs
    if invalid_ids:
        print(f"\n[3] Marking {len(invalid_ids)} invalid URLs as analyzed...")
        
        cursor.execute("""
            UPDATE ads_with_urls
            SET analysis_score = -1,
                analysis_category = 'invalid_url',
                analysis_reason = 'URL failed validation: missing TLD, incomplete domain, or malformed structure',
                analyzed_at = NOW()
            WHERE id = ANY(%s);
        """, (invalid_ids,))
        
        conn.commit()
        print(f"    ✓ Marked {cursor.rowcount} ads")
    else:
        print("\n[3] No invalid URLs to mark")
    
    # Summary
    print("\n" + "=" * 60)
    cursor.execute("SELECT COUNT(*) FROM ads_with_urls WHERE analysis_score IS NULL;")
    remaining = cursor.fetchone()[0]
    
    print(f"SUMMARY:")
    print(f"  Total pending:     {len(pending_ads)}")
    print(f"  Invalid marked:    {len(invalid_ids)}")
    print(f"  Remaining backlog: {remaining}")
    print("=" * 60)
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
