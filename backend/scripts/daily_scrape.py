import importlib
import json
import os
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from datetime import datetime, timedelta
import psycopg2

# Remove current directory from sys.path
CURRENT_DIR = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if p not in ("", str(CURRENT_DIR))]
FirecrawlApp = importlib.import_module("firecrawl").FirecrawlApp

load_dotenv()
api_key = os.getenv("FIRECRAWLER_API_KEY")
if not api_key:
    raise RuntimeError("Missing FIRECRAWLER_API_KEY in environment or .env file")

# Email configuration
email_sender = os.getenv("EMAIL_SENDER")
email_password = os.getenv("EMAIL_PASSWORD")
email_recipient = os.getenv("EMAIL_RECIPIENT")
smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
smtp_port = int(os.getenv("SMTP_PORT", 587))

app = FirecrawlApp(api_key=api_key)

class ExtractSchema(BaseModel):
    advertisers: List[dict]

# Load config
config_path = Path(__file__).with_name("scrape_config.json")
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

# Calculate dynamic date range (yesterday and day before yesterday)
today = datetime.now()
yesterday = today - timedelta(days=1)
day_before_yesterday = today - timedelta(days=2)
date_start = day_before_yesterday.strftime("%b %d, %Y")
date_end = yesterday.strftime("%b %d, %Y")

# Database connection function
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

# Email sending function
def send_email(subject, body):
    if not all([email_sender, email_password, email_recipient]):
        print("⚠ Email not configured, skipping email send")
        return
    
    try:
        msg = MIMEMultipart()
        msg['From'] = email_sender
        msg['To'] = email_recipient
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_sender, email_password.replace(' ', ''))
            server.send_message(msg)
        
        print("✓ Email sent successfully")
    except Exception as e:
        print(f"✗ Email send failed: {e}")

# Create table if not exists
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS advertisers (
        id SERIAL PRIMARY KEY,
        advertiser_name TEXT UNIQUE,
        ad_start_date TEXT,
        ad_library_link TEXT,
        ad_text TEXT,
        destination_product_url TEXT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")

# Create table for ads with valid external URLs
cursor.execute("""
    CREATE TABLE IF NOT EXISTS ads_with_urls (
        id SERIAL PRIMARY KEY,
        advertiser_name TEXT UNIQUE,
        ad_start_date TEXT,
        ad_library_link TEXT,
        ad_text TEXT,
        destination_product_url TEXT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")
conn.commit()

# Get initial counts
cursor.execute("SELECT COUNT(*) FROM advertisers")
initial_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM ads_with_urls")
initial_url_count = cursor.fetchone()[0]
cursor.close()
conn.close()

scrape_start_time = datetime.now()
print(f"Starting multi-keyword scraper at {scrape_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Total keywords to scrape: {len(config['scrapes'])}")

# Prepare report file
report_timestamp = scrape_start_time.strftime("%Y%m%d_%H%M%S")
report_path = Path(__file__).with_name(f"scrape_report_{report_timestamp}.txt")
report_lines = []
report_lines.append(f"SCRAPE REPORT - {scrape_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
report_lines.append("=" * 80)
report_lines.append(f"Date Range: {date_start} to {date_end}")
report_lines.append(f"Total Keywords: {len(config['scrapes'])}")
report_lines.append("=" * 80)
report_lines.append("")

# Track totals for email summary
total_ads_found = 0
total_new_added = 0
total_duplicates = 0
keyword_results = []

# Process each keyword
for idx, scrape_config in enumerate(config['scrapes'], 1):
    keyword = scrape_config['keyword']
    search_url = scrape_config['search_url']
    
    # Special date handling for keywords that should only scrape yesterday
    if keyword in ["עכשיו", "מבצע", "מוגבל"]:
        keyword_date_start = yesterday.strftime("%b %d, %Y")
        keyword_date_end = yesterday.strftime("%b %d, %Y")
    else:
        keyword_date_start = date_start
        keyword_date_end = date_end
    
    keyword_start_time = datetime.now()
    print(f"\n[{idx}/{len(config['scrapes'])}] Scraping keyword: {keyword}")
    print(f"Time: {keyword_start_time.strftime('%H:%M:%S')}")
    
    report_lines.append(f"[{idx}/{len(config['scrapes'])}] KEYWORD: {keyword}")
    report_lines.append("-" * 80)
    report_lines.append(f"Date Range: {keyword_date_start} to {keyword_date_end}")
    report_lines.append(f"Started: {keyword_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    success = False
    retry_count = 0
    max_retries = 2
    
    while not success and retry_count < max_retries:
        try:
            if retry_count > 0:
                print(f"  Retry attempt {retry_count}/{max_retries - 1}...")
                report_lines.append(f"Retry attempt {retry_count}/{max_retries - 1}")
            
            # Build prompt based on date range
            if keyword_date_start == keyword_date_end:
                date_phrase = f"from {keyword_date_start} only"
            else:
                date_phrase = f"between {keyword_date_start} and {keyword_date_end} (inclusive)"
            
            # Call Firecrawl API
            result = app.agent(
                schema=ExtractSchema,
                prompt=f"Using the provided URL, extract a list of unique advertisers. For each advertiser found strictly {date_phrase}, extract: advertiser_name, ad_start_date, ad_library_link, ad_text, and destination_product_url. Maintain a strict unique constraint: each advertiser_name must appear only once in the final list. Target URL: {search_url}",
            )
            
            advertisers = result.model_dump().get("data", {}).get("advertisers", [])
            print(f"✓ Scraped {len(advertisers)} advertisers")
            report_lines.append(f"Total Ads Found: {len(advertisers)}")
            
            # Save to database
            conn = get_db_connection()
            cursor = conn.cursor()
            
            inserted = 0
            skipped = 0
            skipped_names = []
            errors = []
            
            url_inserted = 0
            url_skipped = 0
            
            for ad in advertisers:
                try:
                    cursor.execute("""
                        INSERT INTO advertisers (advertiser_name, ad_start_date, ad_library_link, ad_text, destination_product_url)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (advertiser_name) DO NOTHING;
                    """, (
                        ad.get("advertiser_name"),
                        ad.get("ad_start_date"),
                        ad.get("ad_library_link"),
                        ad.get("ad_text"),
                        ad.get("destination_product_url")
                    ))
                    if cursor.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                        skipped_names.append(ad.get("advertiser_name"))
                    
                    # Check if URL is valid for ads_with_urls table
                    url = ad.get("destination_product_url", "")
                    if url and url.strip():  # Not null or empty
                        url_lower = url.lower()
                        # Filter out unwanted URLs
                        if not any(x in url_lower for x in ["fb.me", "instagram", "whatsapp"]):
                            cursor.execute("""
                                INSERT INTO ads_with_urls (advertiser_name, ad_start_date, ad_library_link, ad_text, destination_product_url)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (advertiser_name) DO NOTHING;
                            """, (
                                ad.get("advertiser_name"),
                                ad.get("ad_start_date"),
                                ad.get("ad_library_link"),
                                ad.get("ad_text"),
                                url
                            ))
                            if cursor.rowcount > 0:
                                url_inserted += 1
                            else:
                                url_skipped += 1
                                
                except Exception as e:
                    error_msg = f"{ad.get('advertiser_name')}: {str(e)}"
                    errors.append(error_msg)
                    print(f"  Error inserting {ad.get('advertiser_name')}: {e}")
            
            conn.commit()
            cursor.close()
            conn.close()
            
            print(f"✓ Database updated: {inserted} new, {skipped} duplicates")
            print(f"✓ URLs table updated: {url_inserted} new, {url_skipped} duplicates/filtered")
            
            report_lines.append(f"New Ads Added to DB: {inserted}")
            report_lines.append(f"Ads with Valid URLs: {url_inserted} new, {url_skipped} duplicates/filtered")
            report_lines.append(f"Duplicate Ads Skipped: {skipped}")
            
            if skipped_names:
                report_lines.append(f"\nAdvertisers Already in DB (not added):")
                for name in skipped_names:
                    report_lines.append(f"  - {name}")
            
            if errors:
                report_lines.append(f"\nErrors Encountered: {len(errors)}")
                for error in errors:
                    report_lines.append(f"  - {error}")
            
            # Save JSON backup
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = Path(__file__).with_name(f"firecrawl_{keyword}_{timestamp}.json")
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(result.model_dump(), f, ensure_ascii=False, indent=2, default=str)
                print(f"✓ JSON backup: {json_path.name}")
            except Exception as e:
                print(f"⚠ JSON backup failed: {e}")
            
            keyword_end_time = datetime.now()
            duration = (keyword_end_time - keyword_start_time).total_seconds()
            report_lines.append(f"Completed: {keyword_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            report_lines.append(f"Duration: {int(duration // 60)} min {int(duration % 60)} sec")
            report_lines.append(f"Status: SUCCESS")
            
            # Track for email
            total_ads_found += len(advertisers)
            total_new_added += inserted
            total_duplicates += skipped
            keyword_note = " - Yesterday only" if keyword in ["עכשיו", "מבצע", "מוגבל"] else ""
            keyword_results.append(f"✅ {keyword} - {len(advertisers)} ads, {inserted} new, {skipped} duplicates ({int(duration // 60)}m {int(duration % 60)}s){keyword_note}")
            
            success = True
            
        except Exception as e:
            retry_count += 1
            error_msg = str(e)
            print(f"✗ Error scraping {keyword}: {error_msg}")
            
            if retry_count >= max_retries:
                keyword_end_time = datetime.now()
                duration = (keyword_end_time - keyword_start_time).total_seconds()
                report_lines.append(f"Total Ads Found: 0")
                report_lines.append(f"New Ads Added to DB: 0")
                report_lines.append(f"Duplicate Ads Skipped: 0")
                report_lines.append(f"Completed: {keyword_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
                report_lines.append(f"Duration: {int(duration // 60)} min {int(duration % 60)} sec")
                report_lines.append(f"Status: FAILED after {max_retries} attempts - {error_msg}")
                
                # Track for email
                keyword_results.append(f"❌ {keyword} - FAILED (Firecrawl API error) ({int(duration // 60)}m {int(duration % 60)}s)")
            else:
                time.sleep(10)  # Wait 10 seconds before retry
    
    report_lines.append("")
    
    # Wait 45 minutes before next keyword (unless it's the last one)
    if idx < len(config['scrapes']):
        print(f"\nWaiting 45 minutes before next keyword...")
        report_lines.append("Waiting 45 minutes before next keyword...")
        report_lines.append("")
        time.sleep(45 * 60)

completion_time = datetime.now()
total_duration = (completion_time - scrape_start_time).total_seconds()
hours = int(total_duration // 3600)
minutes = int((total_duration % 3600) // 60)

print(f"\n✓ All keywords completed at {completion_time.strftime('%Y-%m-%d %H:%M:%S')}")

# Get final counts
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM advertisers")
final_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM ads_with_urls")
final_url_count = cursor.fetchone()[0]
cursor.close()
conn.close()

# Write report to file
report_lines.append("=" * 80)
report_lines.append(f"SCRAPE COMPLETED: {completion_time.strftime('%Y-%m-%d %H:%M:%S')}")
report_lines.append("=" * 80)

with open(report_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(report_lines))

print(f"✓ Report saved: {report_path.name}")

# Send email summary
email_subject = f"Facebook Ads Scrape Report - {today.strftime('%B %d, %Y')}"
email_body = f"""Facebook Ads Scrape Summary - {today.strftime('%B %d, %Y')}

⏰ Runtime: {scrape_start_time.strftime('%H:%M:%S')} - {completion_time.strftime('%H:%M:%S')} ({hours}h {minutes}m)

📊 Results:
Total Ads Found: {total_ads_found}
New Advertisers Added: {total_new_added}
Duplicates Skipped: {total_duplicates}

By Keyword:
{chr(10).join(keyword_results)}

💾 Database: 
- All Advertisers: {final_count} total (was {initial_count}, added {final_count - initial_count} today)
- Ads with Valid URLs: {final_url_count} total (was {initial_url_count}, added {final_url_count - initial_url_count} today)

---
Full report saved on VM: {report_path.name}
"""

send_email(email_subject, email_body)
print("✓ Scrape completed!")
