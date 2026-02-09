#!/usr/bin/env python3
"""
Batch Analyze Daily Summary.
Sends a summary email at 23:00 of all ads analyzed that day.
"""
import subprocess
import datetime
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from dotenv import load_dotenv
    load_dotenv("/home/ubuntu/adora_ops/.env")
except ImportError:
    pass  # dotenv not required if env vars are already set

# Email configuration
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))


def run_psql(sql):
    cmd = ['sudo', '-u', 'postgres', 'psql', '-d', 'firecrawl', '-t', '-P', 'format=unaligned', '-c', sql]
    return subprocess.run(cmd, capture_output=True, text=True)


def get_stats():
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    # Total Analyzed Today (excluding scrape errors with score=-1)
    sql_total = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{today} 00:00:00' AND analyzed_at < '{tomorrow} 00:00:00' AND analysis_score >= 0;"
    total = int(run_psql(sql_total).stdout.strip() or 0)

    # Scrape Errors (score=-1)
    sql_errors = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{today} 00:00:00' AND analyzed_at < '{tomorrow} 00:00:00' AND analysis_score = -1;"
    errors = int(run_psql(sql_errors).stdout.strip() or 0)

    # Risky Ads (Score >= 0.5)
    sql_risky = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{today} 00:00:00' AND analyzed_at < '{tomorrow} 00:00:00' AND analysis_score >= 0.5;"
    risky = int(run_psql(sql_risky).stdout.strip() or 0)

    # Safe Ads
    safe = total - risky

    # Remaining Backlog
    sql_pending = "SELECT COUNT(*) FROM ads_with_urls WHERE analysis_score IS NULL;"
    pending = int(run_psql(sql_pending).stdout.strip() or 0)

    # Category breakdown
    sql_categories = f"""
    SELECT analysis_category, COUNT(*) 
    FROM ads_with_urls 
    WHERE analyzed_at >= '{today} 00:00:00' AND analyzed_at < '{tomorrow} 00:00:00' 
    AND analysis_score >= 0
    GROUP BY analysis_category 
    ORDER BY COUNT(*) DESC 
    LIMIT 10;
    """
    cat_result = run_psql(sql_categories)
    categories = {}
    if cat_result.stdout.strip():
        for line in cat_result.stdout.strip().split('\n'):
            if '|' in line:
                parts = line.split('|')
                if len(parts) >= 2:
                    categories[parts[0].strip() or 'unknown'] = int(parts[1].strip())

    return {
        "date": str(today),
        "total": total,
        "errors": errors,
        "risky": risky,
        "safe": safe,
        "pending": pending,
        "categories": categories
    }


def send_email(subject, body, retries=3):
    """Send email with retry logic."""
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("Warning: Email credentials not fully set, skipping email")
        return False

    import time
    for attempt in range(retries):
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_SENDER
            msg['To'] = EMAIL_RECIPIENT
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD.replace(" ", ""))
                server.send_message(msg)

            print(f"Email sent to {EMAIL_RECIPIENT}")
            return True
        except Exception as e:
            print(f"Email attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return False


def main():
    stats = get_stats()

    pct = stats['risky'] / stats['total'] * 100 if stats['total'] else 0

    report_lines = [
        "=========================================",
        f"ADORA BATCH ANALYZE DAILY SUMMARY: {stats['date']}",
        "=========================================",
        f"Ads Tested:       {stats['total']}",
        f"Risky Found:      {stats['risky']} ({pct:.1f}%)",
        f"Safe Cleared:     {stats['safe']}",
        f"Scrape Errors:    {stats['errors']}",
        f"Remaining Backlog:{stats['pending']}",
        "",
    ]

    if stats['categories']:
        report_lines.append("Top Categories:")
        for cat, count in list(stats['categories'].items())[:5]:
            report_lines.append(f"  - {cat}: {count}")
        report_lines.append("")

    report_lines.append("=========================================")
    report = "\n".join(report_lines)

    print(report)

    # Send email notification
    subject = f"Adora Daily Analysis: {stats['date']} - {stats['total']} ads tested, {stats['risky']} risky"
    send_email(subject, report)


if __name__ == "__main__":
    main()
