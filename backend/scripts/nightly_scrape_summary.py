#!/usr/bin/env python3
"""
Nightly Meta Ads Scrape Summary.
Runs once after all keyword jobs finish, sends ONE combined email report
matching the format:
    Facebook Ads Scrape Summary - February 02, 2026
    Runtime: 00:01:02 - 03:40:50 (3h 39m)
    Results: Total Ads, New Advertisers, Duplicates
    By Keyword: breakdown per keyword
    Database: totals
"""

import datetime
import json
import glob
import logging
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import psycopg2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("adora.nightly_summary")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DOTENV_PATH = os.getenv("SCRAPE_SUMMARY_DOTENV", "/home/ubuntu/adora_ops/.env")
OUTPUT_DIR = os.getenv("SCRAPE_SUMMARY_OUTPUT_DIR", "/home/ubuntu/adora_ops/meta_daily_output")
LOG_DIR = os.getenv("SCRAPE_SUMMARY_LOG_DIR", "/home/ubuntu/adora_ops/logs/meta_daily")
REPORT_DIR = os.getenv("SCRAPE_SUMMARY_REPORT_DIR", "/home/ubuntu/adora_ops/scrape_reports")

# Hebrew keyword display names (keyword -> Hebrew)
KEYWORD_DISPLAY = {
    "mivtsa": "מבצע",
    "mugbal": "מוגבל",
    "hanaha": "הנחת",
    "shaot": "שעות",
    "achshav": "עכשיו",
}


def load_env():
    """Load env vars from dotenv file (simple key=value parser)."""
    if not os.path.isfile(DOTENV_PATH):
        return
    with open(DOTENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not os.getenv(key):
                os.environ[key] = value


def get_db_conn():
    """Get a psycopg2 connection using env vars (same as other Adora scripts)."""
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "firecrawl"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    conn.autocommit = True
    # Prevent any single query from hanging forever
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '30s'")
    cur.close()
    return conn


def _safe_count(cur, sql, params=None):
    """Execute a count query, return 0 on timeout or error."""
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception as e:
        logger.warning("Query timed out or failed: %s", e)
        # Reset the connection state after an error
        cur.connection.rollback()
        return None


def get_db_stats():
    """Query DB for today's scrape stats and totals."""
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    stats = {}

    conn = get_db_conn()
    cur = conn.cursor()

    try:
        # Per-keyword ads inserted into meta_ads_daily today
        cur.execute(
            "SELECT source_keyword, count(*) FROM meta_ads_daily "
            "WHERE scraped_at >= %s AND scraped_at < %s "
            "GROUP BY source_keyword ORDER BY source_keyword",
            (today, tomorrow),
        )
        keyword_ads = {}
        for kw, cnt in cur.fetchall():
            keyword_ads[kw] = cnt
        stats["keyword_ads"] = keyword_ads

        # Total meta_ads_daily inserted today (all keywords)
        stats["total_ads_today"] = _safe_count(
            cur,
            "SELECT count(*) FROM meta_ads_daily WHERE scraped_at >= %s AND scraped_at < %s",
            (today, tomorrow),
        ) or 0

        # New advertisers added today  (may be slow on large table)
        val = _safe_count(
            cur,
            "SELECT count(*) FROM advertisers WHERE scraped_at >= %s AND scraped_at < %s",
            (today, tomorrow),
        )
        stats["new_advertisers"] = val if val is not None else 0

        # Total advertisers - use pg_class estimate (instant) instead of count(*)
        cur.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = 'advertisers'"
        )
        row = cur.fetchone()
        stats["total_advertisers"] = row[0] if row and row[0] > 0 else 0

        # New ads_with_urls added today
        stats["new_ads_with_urls"] = _safe_count(
            cur,
            "SELECT count(*) FROM ads_with_urls WHERE scraped_at >= %s AND scraped_at < %s",
            (today, tomorrow),
        ) or 0

        # Total ads_with_urls
        stats["total_ads_with_urls"] = _safe_count(
            cur,
            "SELECT count(*) FROM ads_with_urls",
        ) or 0

        # Earliest and latest scraped_at today (for runtime window)
        cur.execute(
            "SELECT min(scraped_at), max(scraped_at) FROM meta_ads_daily "
            "WHERE scraped_at >= %s AND scraped_at < %s",
            (today, tomorrow),
        )
        row = cur.fetchone()
        if row and row[0] and row[1]:
            stats["first_scraped"] = str(row[0])
            stats["last_scraped"] = str(row[1])

    finally:
        cur.close()
        conn.close()

    return stats


def collect_json_reports(today_str):
    """Read today's JSON output files for per-keyword detail."""
    pattern = os.path.join(OUTPUT_DIR, f"meta_daily_{today_str}_*.json")
    files = sorted(glob.glob(pattern))
    reports = []
    for fpath in files:
        try:
            with open(fpath) as f:
                data = json.load(f)
            reports.append(data)
        except Exception:
            pass
    return reports


def parse_runtime(stats):
    """Format runtime window from DB timestamps."""
    first = stats.get("first_scraped", "")
    last = stats.get("last_scraped", "")
    if not first or not last:
        return "N/A", "N/A", "N/A"

    try:
        fmt = "%Y-%m-%d %H:%M:%S.%f"
        # Try with microseconds first, then without
        for f in [fmt, "%Y-%m-%d %H:%M:%S"]:
            try:
                t1 = datetime.datetime.strptime(first, f)
                break
            except ValueError:
                t1 = None
        for f in [fmt, "%Y-%m-%d %H:%M:%S"]:
            try:
                t2 = datetime.datetime.strptime(last, f)
                break
            except ValueError:
                t2 = None

        if t1 and t2:
            delta = t2 - t1
            total_sec = int(delta.total_seconds())
            hours = total_sec // 3600
            minutes = (total_sec % 3600) // 60
            start_str = t1.strftime("%H:%M:%S")
            end_str = t2.strftime("%H:%M:%S")
            if hours > 0:
                dur_str = f"{hours}h {minutes}m"
            else:
                dur_str = f"{minutes}m"
            return start_str, end_str, dur_str
    except Exception:
        pass
    return "N/A", "N/A", "N/A"


def build_keyword_lines(db_stats, json_reports):
    """Build per-keyword report lines with emoji checkmarks.

    Uses JSON report data (with db_results) for accurate new/duplicate counts.
    Falls back to DB-only data otherwise.
    """
    keyword_ads = db_stats.get("keyword_ads", {})

    # Extract per-keyword data from JSON reports (each report = one keyword job)
    json_kw_data = {}
    for report in json_reports:
        summary = report.get("summary", {})
        runtime = summary.get("runtime_seconds", 0)
        link_results = summary.get("link_results", [])
        db_results = summary.get("db", {})

        for lr in link_results:
            kw = lr.get("keyword", "")
            if not kw:
                continue
            selected = lr.get("selected_rows", 0)
            json_kw_data[kw] = {
                "selected": selected,
                "runtime_seconds": runtime,
                "timed_out": lr.get("timed_out", False),
                # Real new counts from DB operations
                "advertisers_inserted": db_results.get("advertisers_inserted", 0),
                "ads_with_urls_inserted": db_results.get("ads_with_urls_inserted", 0),
            }

    lines = []
    total_new = 0
    total_dupes = 0
    all_keywords = sorted(set(list(keyword_ads.keys()) + list(json_kw_data.keys())))

    for kw in all_keywords:
        display = KEYWORD_DISPLAY.get(kw, kw)
        jd = json_kw_data.get(kw, {})
        selected = jd.get("selected", keyword_ads.get(kw, 0))
        runtime_sec = jd.get("runtime_seconds", 0)

        # New = advertisers_inserted from db_results (real new count)
        new = jd.get("advertisers_inserted", 0)
        dupes = max(0, selected - new)
        total_new += new
        total_dupes += dupes

        # Format runtime
        if runtime_sec > 0:
            rm = runtime_sec // 60
            rs = runtime_sec % 60
            rt_str = f"({rm}m {rs}s)" if rm > 0 else f"({rs}s)"
        else:
            rt_str = ""

        status = "✅" if selected > 0 else "⚠️"
        line = f"{status} {display} - {selected} ads, {new} new, {dupes} duplicates {rt_str}"
        lines.append(line.strip())

    return lines, total_new, total_dupes


def build_report(db_stats, json_reports):
    """Build the full email report body."""
    today = datetime.date.today()
    date_str = today.strftime("%B %d, %Y")

    start_time, end_time, duration = parse_runtime(db_stats)

    total_ads = db_stats.get("total_ads_today", 0)

    # Get per-keyword lines with accurate new/dup totals
    kw_lines, total_new, total_dupes = build_keyword_lines(db_stats, json_reports)

    # Use per-keyword totals for new advertisers if available,
    # otherwise fall back to DB-level count
    new_advertisers = total_new if total_new > 0 else db_stats.get("new_advertisers", 0)
    duplicates = max(0, total_ads - new_advertisers)

    lines = []
    lines.append(f"Facebook Ads Scrape Summary - {date_str}")
    lines.append("")
    lines.append(f"⏰ Runtime: {start_time} - {end_time} ({duration})")
    lines.append("")
    lines.append("📊 Results:")
    lines.append(f"Total Ads Found: {total_ads}")
    lines.append(f"New Advertisers Added: {new_advertisers}")
    lines.append(f"Duplicates Skipped: {duplicates}")
    lines.append("")
    lines.append("By Keyword:")

    for kl in kw_lines:
        lines.append(kl)

    lines.append("")
    lines.append("💾 Database:")
    lines.append(
        f"- All Advertisers: ~{db_stats['total_advertisers']} total "
        f"(added {new_advertisers} today)"
    )
    lines.append(
        f"- Ads with Valid URLs: {db_stats['total_ads_with_urls']} total "
        f"(added {db_stats['new_ads_with_urls']} today)"
    )

    lines.append("")
    lines.append("---")

    # Save full report to file
    report_text = "\n".join(lines)
    return report_text


def send_email(subject, body):
    """Send email using Gmail SMTP."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("EMAIL_RECIPIENT")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not all([sender, password, recipient]):
        logger.warning("Email credentials not configured, skipping email send")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender, password.replace(" ", ""))
            server.send_message(msg)

        logger.info("Email sent to %s", recipient)
        return True
    except Exception as e:
        logger.error("Email error: %s", e, exc_info=True)
        return False


def main():
    load_env()

    today = datetime.date.today()
    today_str = today.isoformat()

    logger.info("Generating nightly scrape summary for %s", today_str)

    # Get DB stats
    db_stats = get_db_stats()
    logger.info("DB stats: %d ads today, %d new advertisers",
                db_stats.get('total_ads_today', 0),
                db_stats.get('new_advertisers', 0))

    # Collect JSON reports from today
    json_reports = collect_json_reports(today_str)
    logger.info("Found %d JSON report file(s)", len(json_reports))

    # Build report
    report = build_report(db_stats, json_reports)
    logger.info("Report built successfully")
    print(report)  # Also print for cron log capture

    # Save report to file
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(
        REPORT_DIR,
        f"scrape_report_{today.strftime('%Y%m%d')}_{datetime.datetime.now().strftime('%H%M%S')}.txt",
    )
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Full report saved: %s", report_file)

    # Append report path to the report body
    report_with_path = report + f"\nFull report saved on VM: {os.path.basename(report_file)}"

    # Send email
    total_ads = db_stats.get("total_ads_today", 0)
    new_adv = db_stats.get("new_advertisers", 0)
    subject = (
        f"Adora Nightly Scrape: {today.strftime('%Y-%m-%d')} - "
        f"{total_ads} ads found, {new_adv} new advertisers"
    )
    send_email(subject, report_with_path)


if __name__ == "__main__":
    main()
