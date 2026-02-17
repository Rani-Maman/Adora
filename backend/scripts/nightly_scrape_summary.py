#!/usr/bin/env python3
"""
Nightly Meta Ads Scrape Summary.
Runs once at 05:00 after all keyword jobs finish, sends ONE combined email.

Reads per-keyword JSON reports (written by daily_meta_scrape.py) and queries
the DB for accurate counts.  The JSON files use *target_date* (yesterday) in
their filename, so we search for yesterday's date.
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
    """Get a psycopg2 connection using env vars."""
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "firecrawl"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    conn.autocommit = True
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
        cur.connection.rollback()
        return None


def _approx_count(cur, table_name):
    """Fast approximate row count via pg_class."""
    try:
        cur.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
            (table_name,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] > 0 else 0
    except Exception:
        return 0


def get_db_stats():
    """Query DB for today's scrape stats."""
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

        # Total meta_ads_daily inserted today
        stats["total_ads_today"] = sum(keyword_ads.values())

        # Per-keyword ads with valid URLs inserted today
        cur.execute(
            "SELECT source_keyword, count(*) FROM meta_ads_daily_with_urls "
            "WHERE scraped_at >= %s AND scraped_at < %s "
            "GROUP BY source_keyword ORDER BY source_keyword",
            (today, tomorrow),
        )
        keyword_urls = {}
        for kw, cnt in cur.fetchall():
            keyword_urls[kw] = cnt
        stats["keyword_urls"] = keyword_urls
        stats["total_urls_today"] = sum(keyword_urls.values())

        # New advertisers added today (first-ever appearance)
        val = _safe_count(
            cur,
            "SELECT count(*) FROM advertisers WHERE scraped_at >= %s AND scraped_at < %s",
            (today, tomorrow),
        )
        stats["new_advertisers"] = val if val is not None else 0

        # Approx totals
        stats["total_meta_ads_daily"] = _approx_count(cur, "meta_ads_daily")
        stats["total_meta_ads_daily_with_urls"] = _approx_count(cur, "meta_ads_daily_with_urls")
        stats["total_advertisers"] = _approx_count(cur, "advertisers")

        # Runtime window (earliest/latest scraped_at today)
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


def collect_json_reports(target_date_str):
    """Read JSON output files for the given target date.

    The keyword jobs run after midnight but target yesterday's date,
    so file names use yesterday's date: meta_daily_{target_date}_*.json
    """
    pattern = os.path.join(OUTPUT_DIR, f"meta_daily_{target_date_str}_*.json")
    files = sorted(glob.glob(pattern))
    logger.info("Looking for JSON reports: %s -> %d files", pattern, len(files))
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
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
            try:
                t1 = datetime.datetime.strptime(first, fmt)
                break
            except ValueError:
                t1 = None
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
            try:
                t2 = datetime.datetime.strptime(last, fmt)
                break
            except ValueError:
                t2 = None

        if t1 and t2:
            total_sec = int((t2 - t1).total_seconds())
            hours = total_sec // 3600
            minutes = (total_sec % 3600) // 60
            start_str = t1.strftime("%H:%M:%S")
            end_str = t2.strftime("%H:%M:%S")
            dur_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            return start_str, end_str, dur_str
    except Exception:
        pass
    return "N/A", "N/A", "N/A"


def _extract_json_keyword_data(json_reports):
    """Extract per-keyword data from JSON reports.

    Each report = one keyword job. Returns dict keyed by keyword.
    """
    kw_data = {}
    for report in json_reports:
        summary = report.get("summary", {})
        runtime = summary.get("runtime_seconds", 0)
        link_results = summary.get("link_results", [])
        db_results = summary.get("db", {})

        for lr in link_results:
            kw = lr.get("keyword", "")
            if not kw:
                continue
            kw_data[kw] = {
                "selected": lr.get("selected_rows", 0),
                "ads_captured": lr.get("ads_captured", 0),
                "runtime_seconds": runtime,
                "timed_out": lr.get("timed_out", False),
                "meta_ads_daily_inserted": db_results.get("meta_ads_daily_inserted", 0),
                "meta_ads_daily_with_urls_inserted": db_results.get("meta_ads_daily_with_urls_inserted", 0),
                "advertisers_inserted": db_results.get("advertisers_inserted", 0),
                "ads_with_urls_inserted": db_results.get("ads_with_urls_inserted", 0),
            }
    return kw_data


def build_report(db_stats, json_reports):
    """Build the full email report body."""
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    date_str = today.strftime("%B %d, %Y")

    start_time, end_time, duration = parse_runtime(db_stats)

    # If DB has no timestamps, compute total runtime from JSON reports
    if start_time == "N/A" and json_reports:
        total_runtime = sum(
            r.get("summary", {}).get("runtime_seconds", 0) for r in json_reports
        )
        if total_runtime > 0:
            hours = total_runtime // 3600
            minutes = (total_runtime % 3600) // 60
            duration = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            start_time = "~00:01"
            end_time = "~05:00"

    keyword_ads = db_stats.get("keyword_ads", {})
    keyword_urls = db_stats.get("keyword_urls", {})
    json_kw = _extract_json_keyword_data(json_reports)

    # Prefer JSON-derived totals (ground truth) over DB stats.
    # DB counts miss returning ads because ON CONFLICT DO NOTHING
    # leaves scraped_at unchanged, so today's query finds 0 rows.
    total_ads = db_stats.get("total_ads_today", 0)
    total_urls = db_stats.get("total_urls_today", 0)
    if json_kw and total_ads == 0:
        total_ads = sum(d.get("selected", 0) for d in json_kw.values())
        total_urls = sum(d.get("ads_with_urls_inserted", d.get("selected", 0)) for d in json_kw.values())

    new_advertisers = db_stats.get("new_advertisers", 0)
    returning = max(0, total_ads - new_advertisers)

    # --- Per-keyword lines ---
    all_keywords = sorted(set(list(keyword_ads.keys()) + list(json_kw.keys())))
    kw_lines = []
    kw_new_total = 0

    for kw in all_keywords:
        display = KEYWORD_DISPLAY.get(kw, kw)
        db_count = keyword_ads.get(kw, 0)
        url_count = keyword_urls.get(kw, 0)
        jd = json_kw.get(kw, {})

        # Prefer JSON data for selected count; fall back to DB
        selected = jd.get("selected", db_count)
        new = jd.get("advertisers_inserted", 0)
        kw_new_total += new
        ret = max(0, selected - new)

        # Runtime
        runtime_sec = jd.get("runtime_seconds", 0)
        if runtime_sec > 0:
            rm, rs = divmod(runtime_sec, 60)
            rt_str = f"{rm}m {rs}s" if rm > 0 else f"{rs}s"
        else:
            rt_str = ""

        timed_out = jd.get("timed_out", False)
        status = "⚠️" if timed_out else ("✅" if selected > 0 else "❌")

        parts = [f"{status} {display} — {selected} ads"]
        if url_count and url_count != selected:
            parts.append(f"{url_count} with URLs")
        parts.append(f"{new} new, {ret} returning")
        if rt_str:
            parts.append(rt_str)
        kw_lines.append(" | ".join(parts))

    # If JSON reports were found use per-keyword new total;
    # otherwise fall back to DB-level count
    if kw_new_total > 0:
        new_advertisers = kw_new_total
        returning = max(0, total_ads - new_advertisers)

    # --- Build report ---
    lines = [
        f"Adora Nightly Scrape — {date_str}",
        f"Target date: {yesterday.isoformat()}",
        "",
        f"⏰ {start_time} — {end_time} ({duration})",
        "",
        "📊 Results:",
        f"  Ads Scraped: {total_ads}",
        f"  With Valid URLs: {total_urls}",
        f"  New Advertisers: {new_advertisers}",
        f"  Returning Advertisers: {returning}",
        "",
        "📋 By Keyword:",
    ]
    for kl in kw_lines:
        lines.append(f"  {kl}")

    lines += [
        "",
        "💾 Database Totals:",
        f"  meta_ads_daily: ~{db_stats.get('total_meta_ads_daily', 0):,}",
        f"  meta_ads_daily_with_urls: ~{db_stats.get('total_meta_ads_daily_with_urls', 0):,}",
        f"  advertisers: ~{db_stats.get('total_advertisers', 0):,} (+{new_advertisers} today)",
        "",
        "---",
    ]

    return "\n".join(lines)


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
    yesterday = today - datetime.timedelta(days=1)

    logger.info("Generating nightly scrape summary for %s (target date: %s)", today, yesterday)

    db_stats = get_db_stats()
    logger.info("DB stats: %d ads today, %d new advertisers",
                db_stats.get('total_ads_today', 0),
                db_stats.get('new_advertisers', 0))

    # JSON files use target_date (yesterday) in the filename
    json_reports = collect_json_reports(yesterday.isoformat())
    logger.info("Found %d JSON report file(s)", len(json_reports))

    report = build_report(db_stats, json_reports)
    logger.info("Report built successfully")
    print(report)

    # Save report to file
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(
        REPORT_DIR,
        f"scrape_report_{today.strftime('%Y%m%d')}_{datetime.datetime.now().strftime('%H%M%S')}.txt",
    )
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Full report saved: %s", report_file)

    report_with_path = report + f"\nSaved: {os.path.basename(report_file)}"

    total_ads = db_stats.get("total_ads_today", 0)
    new_adv = db_stats.get("new_advertisers", 0)
    subject = (
        f"Adora Nightly Scrape: {today.strftime('%Y-%m-%d')} — "
        f"{total_ads} ads, {new_adv} new"
    )
    send_email(subject, report_with_path)


if __name__ == "__main__":
    main()
