#!/usr/bin/env python3
"""
Daily Adora Report.
Runs daily to summarize ad analysis performance and send email notification.
"""
import subprocess
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

# Load environment variables from adora_ops/.env
load_dotenv("/home/ubuntu/adora_ops/.env")

# Email configuration (all must be set in .env)
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
    yesterday = today - datetime.timedelta(days=1)

    # Total Analyzed Yesterday
    sql_total = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{yesterday} 00:00:00' AND analyzed_at < '{today} 00:00:00';"
    total = int(run_psql(sql_total).stdout.strip() or 0)

    # Risky Ads (Score >= 0.5)
    sql_risky = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{yesterday} 00:00:00' AND analyzed_at < '{today} 00:00:00' AND analysis_score >= 0.5;"
    risky = int(run_psql(sql_risky).stdout.strip() or 0)

    # Safe Ads
    safe = total - risky

    # Risk DB Upserts (Approximation: Risky ads are always upserted)
    upserted = risky

    # Remaining Backlog
    sql_pending = "SELECT COUNT(*) FROM ads_with_urls WHERE analysis_score IS NULL;"
    pending = int(run_psql(sql_pending).stdout.strip() or 0)

    return {
        "date": str(yesterday),
        "total": total,
        "risky": risky,
        "safe": safe,
        "upserted": upserted,
        "pending": pending
    }


def send_email(subject, body):
    """Send email notification."""
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("Warning: Email credentials not fully set, skipping email")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"Email sent to {EMAIL_RECIPIENT}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def main():
    LOG_FILE = "/home/ubuntu/adora_ops/daily_reports.log"
    stats = get_stats()

    pct = stats['risky'] / stats['total'] * 100 if stats['total'] else 0

    report = f"""
=========================================
ADORA DAILY FILTERING REPORT: {stats['date']}
=========================================
Ads Tested:       {stats['total']}
Risky Found:      {stats['risky']} ({pct:.1f}%)
Safe Cleared:     {stats['safe']}
Copied to RiskDB: {stats['upserted']}
Remaining Backlog:{stats['pending']}
=========================================
"""
    print(report)

    # Append to log file
    with open(LOG_FILE, "a") as f:
        f.write(report + "\n")

    # Send email notification
    subject = f"Adora Daily Report: {stats['date']} - {stats['total']} ads tested, {stats['risky']} risky"
    send_email(subject, report)


if __name__ == "__main__":
    main()

