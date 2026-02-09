#!/usr/bin/env python3
"""
Daily Meta Ads Library ingestion job.

What it does:
- Uses URLs from scrape_config.json.
- Scrapes each URL with strict per-link runtime limits.
- Filters to a target date (default: yesterday in Asia/Jerusalem).
- Writes rows to Postgres in dedicated Meta tables.
- Also upserts into legacy advertisers/ads_with_urls tables for compatibility.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional for --no-db runs.
    psycopg2 = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience.
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

# Allow importing the standalone scraper module from the same folder.
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import meta_ads_http_scraper as meta_scraper


LOG = logging.getLogger("daily_meta_scrape")
DEFAULT_TIMEZONE = "Asia/Jerusalem"
SOCIAL_HOST_SUFFIXES = (
    # Social media with login walls
    "facebook.com",
    "fb.com",
    "fb.me",
    "instagram.com",
    "messenger.com",
    "whatsapp.com",
    "wa.me",
    "m.me",
    # Video platforms (login/heavy JS)
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    # Link aggregators (low analysis value)
    "linktr.ee",
    "linkin.bio",
    # Google services
    "docs.google.com",
    "drive.google.com",
    "forms.google.com",
    "sheets.google.com",
)
SOCIAL_PATH_MARKERS = (
    "/messages",
    "/messaging",
    "/direct",
    "/dm",
    "/inbox",
    "/chat",
    "/send",
)
MARKETPLACE_MARKERS = (
    "marketplace",
    "facebook marketplace",
    "fb marketplace",
    "מרקטפלייס",
    "מרקט פלייס",
)
SHORTENER_HOSTS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "buff.ly",
    "is.gd",
    "rb.gy",
    # Common in Israel
    "katzr.net",
}


@dataclass
class LinkResult:
    keyword: str
    search_url: str
    ads_captured: int
    matching_target_date: int
    selected_rows: int
    filtered_invalid_or_social_url: int
    filtered_marketplace: int
    filtered_missing_advertiser: int
    timed_out: bool
    attempts_used: int


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Meta Ads scraper to DB.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("scrape_config.json")),
        help="Path to scrape_config.json containing links.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).with_name("output") / "meta_daily"),
        help="Folder for per-run JSON snapshots.",
    )
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Timezone used for previous-day filtering.")
    parser.add_argument("--target-date", help="Optional override date (YYYY-MM-DD). Defaults to previous day.")
    parser.add_argument(
        "--ignore-date-filter",
        action="store_true",
        help="Disable ad_start_date filtering and keep ads from any date.",
    )
    parser.add_argument("--max-total-minutes", type=int, default=35, help="Overall runtime cap for this run.")
    parser.add_argument("--per-link-timeout-sec", type=int, default=240, help="Hard timeout for each configured URL.")
    parser.add_argument("--retries", type=int, default=1, help="Retries per link if scrape fails or returns zero ads.")
    parser.add_argument("--min-captured-ads-per-link", type=int, default=120, help="Retry if a link captures fewer ads than this.")
    parser.add_argument(
        "--min-selected-rows-per-link",
        type=int,
        default=10,
        help="Retry when selected rows for target date are lower than this.",
    )
    parser.add_argument("--max-scrolls", type=int, default=75, help="Scroll cap per link attempt.")
    parser.add_argument("--scroll-delay-ms", type=int, default=800, help="Delay between scroll attempts.")
    parser.add_argument("--idle-rounds", type=int, default=10, help="Stop after this many no-growth rounds.")
    parser.add_argument("--max-runtime-sec", type=int, default=190, help="Internal scroll runtime cap per link.")
    parser.add_argument("--target-ads-per-link", type=int, default=400, help="Stop per link once this many ads are found.")
    parser.add_argument("--navigation-timeout-ms", type=int, default=90000, help="Playwright page.goto timeout.")
    parser.add_argument("--response-url-filter", default="facebook.com", help="Network response URL filter.")
    parser.add_argument("--proxy-server", help="Optional proxy server, e.g. http://host:port.")
    parser.add_argument("--proxy-username", help="Optional proxy username.")
    parser.add_argument("--proxy-password", help="Optional proxy password.")
    parser.add_argument("--proxy-bypass", help="Optional comma-separated hosts that bypass proxy.")
    parser.add_argument("--storage-state", help="Optional Playwright storage-state JSON path.")
    parser.add_argument(
        "--max-advertisers-per-keyword",
        type=int,
        default=100,
        help="Cap results so each keyword yields at most this many unique advertisers (0 disables).",
    )
    parser.add_argument("--dotenv-path", help="Optional .env path (useful for VM cron jobs).")
    parser.add_argument("--email-summary", action="store_true", help="Send email summary at end of run.")
    parser.add_argument("--email-subject-prefix", default="Meta Ads Nightly", help="Email subject prefix.")
    parser.add_argument("--job-name", help="Optional job label (for per-keyword cron slots).")
    parser.add_argument("--headful", action="store_true", help="Run browser in visible mode.")
    parser.add_argument("--no-db", action="store_true", help="Scrape and export only; skip DB insertion.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def normalize_advertiser_name(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.strip().lower()
    # Collapse punctuation variants so "Brand", "Brand.", and "Brand -" dedupe together.
    cleaned = re.sub(r"[^\w\s\u0590-\u05FF&]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def compute_keyword_advertiser_key(target_date: str, keyword: str, advertiser_name: str) -> str:
    seed = json.dumps(
        {
            "date": target_date,
            "keyword": keyword,
            "advertiser_name": normalize_advertiser_name(advertiser_name),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"kwadv:{hashlib.sha1(seed.encode('utf-8')).hexdigest()}"


def parse_ad_date(ad: dict[str, Any], tz: ZoneInfo) -> date | None:
    start_date_string = ad.get("start_date_string")
    if isinstance(start_date_string, str) and start_date_string.strip():
        value = start_date_string.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(tz).date()
        except ValueError:
            pass

    start_epoch = ad.get("start_date")
    if isinstance(start_epoch, (int, float)):
        try:
            return datetime.fromtimestamp(float(start_epoch), tz=timezone.utc).astimezone(tz).date()
        except Exception:
            return None
    return None


def unwrap_redirect_url(url: str | None) -> str | None:
    if not url:
        return None
    candidate = url.strip()
    if not candidate:
        return None

    for _ in range(3):
        try:
            parsed = urlparse(candidate)
        except Exception:
            return candidate
        netloc = parsed.netloc.lower()
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]

        if "facebook.com" in netloc or "instagram.com" in netloc:
            query = parse_qs(parsed.query)
            wrapped = None
            for key in ("u", "url", "redirect", "target", "destination"):
                values = query.get(key)
                if values and isinstance(values[0], str) and values[0].strip():
                    wrapped = values[0].strip()
                    break
            if wrapped:
                unwrapped = unquote(wrapped).strip()
                if unwrapped and unwrapped != candidate:
                    candidate = unwrapped
                    continue
        break

    return candidate


def is_valid_external_url(url: str | None) -> bool:
    if not url:
        return False
    candidate = unwrap_redirect_url(url)
    if not candidate:
        return False

    try:
        parsed = urlparse(candidate)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False

    netloc = parsed.netloc.lower()
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if "." not in netloc:
        return False
    if netloc.startswith("."):
        return False
    if any(netloc == blocked or netloc.endswith(f".{blocked}") for blocked in SOCIAL_HOST_SUFFIXES):
        return False
    path = (parsed.path or "").lower()
    if any(marker in path for marker in SOCIAL_PATH_MARKERS):
        return False

    return True


def has_product_like_path(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    path = (parsed.path or "").strip("/")
    if not path and not parsed.query:
        return False
    product_hints = ("product", "products", "item", "p/", "dp/", "sku", "shop", "store")
    target = f"{path}?{parsed.query}".lower()
    return any(hint in target for hint in product_hints) or bool(path)


def is_marketplace_ad(ad: dict[str, Any], row: dict[str, Any]) -> bool:
    # Strictly exclude Facebook Marketplace URLs. (Other uses of "marketplace" in ad copy
    # are not reliable indicators and can over-filter.)
    destination = row.get("destination_product_url")
    if isinstance(destination, str) and destination.strip():
        candidate = unwrap_redirect_url(destination)
        if candidate:
            try:
                parsed = urlparse(candidate)
            except Exception:
                parsed = None
            if parsed is not None:
                netloc = (parsed.netloc or "").lower()
                if ":" in netloc:
                    netloc = netloc.split(":", 1)[0]
                path = (parsed.path or "").lower()
                if netloc.endswith("facebook.com") and "marketplace" in path:
                    return True

    # Fallback marker list for Hebrew/English Marketplace mentions.
    parts = [
        row.get("ad_library_link"),
        row.get("destination_product_url"),
        row.get("ad_text"),
        row.get("title"),
        row.get("caption"),
        row.get("link_description"),
    ]
    haystack = " ".join(part for part in parts if isinstance(part, str)).lower()
    return any(marker in haystack for marker in MARKETPLACE_MARKERS)


def is_known_shortener(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    netloc = (parsed.netloc or "").lower()
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc in SHORTENER_HOSTS


def row_quality_score(row: dict[str, Any]) -> int:
    score = 0
    destination = row.get("destination_product_url")
    if is_valid_external_url(destination):
        score += 100
    if has_product_like_path(destination):
        # Strongly prefer product-like links when picking one ad per advertiser.
        score += 500
    if is_known_shortener(destination):
        # Prefer direct merchant domains over shorteners when both are available.
        score -= 80
    ad_text = row.get("ad_text")
    if isinstance(ad_text, str):
        score += min(len(ad_text), 600) // 100
    if row.get("ad_archive_id"):
        score += 1
    return score


def get_target_date(args: argparse.Namespace, tz: ZoneInfo) -> date:
    if args.target_date:
        return datetime.strptime(args.target_date, "%Y-%m-%d").date()
    return (datetime.now(tz) - timedelta(days=1)).date()


def send_summary_email(subject: str, body: str) -> bool:
    email_sender = os.getenv("EMAIL_SENDER")
    email_password = os.getenv("EMAIL_PASSWORD")
    email_recipient = os.getenv("EMAIL_RECIPIENT")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not all([email_sender, email_password, email_recipient]):
        LOG.warning("Email credentials are not fully configured, skipping email summary.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = email_sender
        msg["To"] = email_recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_sender, email_password.replace(" ", ""))
            server.send_message(msg)

        LOG.info("Email summary sent to %s", email_recipient)
        return True
    except Exception as exc:
        LOG.warning("Email summary failed: %s", exc)
        return False


def build_email_body(summary: dict[str, Any], args: argparse.Namespace) -> str:
    lines: list[str] = []
    lines.append("Meta Ads Scrape Summary")
    lines.append("=" * 40)
    if args.job_name:
        lines.append(f"Job: {args.job_name}")
    lines.append(f"Scraped At (UTC): {summary.get('scraped_at_utc')}")
    lines.append(f"Target Date: {summary.get('target_date')}")
    lines.append(f"Ignore Date Filter: {summary.get('ignore_date_filter')}")
    lines.append(f"Timezone: {summary.get('timezone')}")
    lines.append(f"Links Processed: {summary.get('links_processed')}/{summary.get('links_configured')}")
    lines.append(f"Unique Rows Total: {summary.get('unique_rows_total')}")
    lines.append(f"Runtime Seconds: {summary.get('runtime_seconds')}")
    lines.append(f"Output Path: {summary.get('output_path')}")
    lines.append("")
    lines.append("Rows By Keyword:")
    rows_by_keyword = summary.get("rows_selected_by_keyword") or {}
    for keyword, count in rows_by_keyword.items():
        lines.append(f"- {keyword}: {count}")
    lines.append("")
    lines.append("Link Results:")
    for link in summary.get("link_results", []):
        lines.append(
            f"- {link.get('keyword')}: captured={link.get('ads_captured')}, "
            f"selected={link.get('selected_rows')}, invalid_or_social={link.get('filtered_invalid_or_social_url')}, "
            f"marketplace={link.get('filtered_marketplace')}, timed_out={link.get('timed_out')}"
        )

    db_stats = summary.get("db", {})
    if isinstance(db_stats, dict) and not db_stats.get("skipped"):
        lines.append("")
        lines.append("DB:")
        for key in (
            "rows_total",
            "meta_ads_daily_inserted",
            "meta_ads_daily_with_urls_inserted",
            "advertisers_inserted",
            "ads_with_urls_inserted",
        ):
            if key in db_stats:
                lines.append(f"- {key}: {db_stats[key]}")
    return "\n".join(lines)


def get_db_connection() -> psycopg2.extensions.connection:
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed. Install DB deps or run with --no-db.")
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing required DB env vars: {', '.join(missing)}")
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def ensure_tables(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_ads_daily (
                id SERIAL PRIMARY KEY,
                ad_unique_key TEXT UNIQUE NOT NULL,
                ad_archive_id TEXT,
                advertiser_name TEXT,
                ad_start_date DATE,
                ad_library_link TEXT,
                ad_text TEXT,
                destination_product_url TEXT,
                source_keyword TEXT,
                source_search_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_ads_daily_with_urls (
                id SERIAL PRIMARY KEY,
                ad_unique_key TEXT UNIQUE NOT NULL,
                ad_archive_id TEXT,
                advertiser_name TEXT,
                ad_start_date DATE,
                ad_library_link TEXT,
                ad_text TEXT,
                destination_product_url TEXT,
                source_keyword TEXT,
                source_search_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_ads_daily_start_date ON meta_ads_daily(ad_start_date);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_ads_daily_with_urls_start_date ON meta_ads_daily_with_urls(ad_start_date);"
        )

        # Legacy compatibility tables used by existing pipeline scripts.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS advertisers (
                id SERIAL PRIMARY KEY,
                advertiser_name TEXT UNIQUE,
                ad_start_date TEXT,
                ad_library_link TEXT,
                ad_text TEXT,
                destination_product_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ads_with_urls (
                id SERIAL PRIMARY KEY,
                advertiser_name TEXT UNIQUE,
                ad_start_date TEXT,
                ad_library_link TEXT,
                ad_text TEXT,
                destination_product_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    conn.commit()


def insert_rows(conn: psycopg2.extensions.connection, rows: list[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "meta_ads_daily_inserted": 0,
        "meta_ads_daily_with_urls_inserted": 0,
        "advertisers_inserted": 0,
        "ads_with_urls_inserted": 0,
        "rows_total": len(rows),
    }

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO meta_ads_daily (
                    ad_unique_key, ad_archive_id, advertiser_name, ad_start_date, ad_library_link,
                    ad_text, destination_product_url, source_keyword, source_search_url
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ad_unique_key) DO NOTHING;
                """,
                (
                    row["ad_unique_key"],
                    row["ad_archive_id"],
                    row["advertiser_name"],
                    row["ad_start_date"],
                    row["ad_library_link"],
                    row["ad_text"],
                    row["destination_product_url"],
                    row["source_keyword"],
                    row["source_search_url"],
                ),
            )
            if cur.rowcount > 0:
                stats["meta_ads_daily_inserted"] += 1

            cur.execute(
                """
                INSERT INTO advertisers (advertiser_name, ad_start_date, ad_library_link, ad_text, destination_product_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (advertiser_name) DO NOTHING;
                """,
                (
                    row["advertiser_name"],
                    row["ad_start_date"],
                    row["ad_library_link"],
                    row["ad_text"],
                    row["destination_product_url"],
                ),
            )
            if cur.rowcount > 0:
                stats["advertisers_inserted"] += 1

            if not is_valid_external_url(row["destination_product_url"]):
                continue

            cur.execute(
                """
                INSERT INTO meta_ads_daily_with_urls (
                    ad_unique_key, ad_archive_id, advertiser_name, ad_start_date, ad_library_link,
                    ad_text, destination_product_url, source_keyword, source_search_url
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ad_unique_key) DO NOTHING;
                """,
                (
                    row["ad_unique_key"],
                    row["ad_archive_id"],
                    row["advertiser_name"],
                    row["ad_start_date"],
                    row["ad_library_link"],
                    row["ad_text"],
                    row["destination_product_url"],
                    row["source_keyword"],
                    row["source_search_url"],
                ),
            )
            if cur.rowcount > 0:
                stats["meta_ads_daily_with_urls_inserted"] += 1

            cur.execute(
                """
                INSERT INTO ads_with_urls (advertiser_name, ad_start_date, ad_library_link, ad_text, destination_product_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (advertiser_name) DO NOTHING;
                """,
                (
                    row["advertiser_name"],
                    row["ad_start_date"],
                    row["ad_library_link"],
                    row["ad_text"],
                    row["destination_product_url"],
                ),
            )
            if cur.rowcount > 0:
                stats["ads_with_urls_inserted"] += 1

    conn.commit()
    return stats


def build_scrape_namespace(
    *,
    url: str,
    output_path: Path,
    args: argparse.Namespace,
    log_level: str,
    runtime_cap_sec: int | None = None,
) -> argparse.Namespace:
    effective_runtime = args.max_runtime_sec
    if runtime_cap_sec is not None:
        effective_runtime = min(effective_runtime, max(runtime_cap_sec, 20))

    return argparse.Namespace(
        url=url,
        query="",
        country="IL",
        active_status="active",
        ad_type="all",
        media_type="all",
        search_type="keyword_unordered",
        sort_mode="total_impressions",
        sort_direction="desc",
        is_targeted_country=False,
        headful=args.headful,
        storage_state=args.storage_state,
        max_scrolls=args.max_scrolls,
        scroll_delay_ms=args.scroll_delay_ms,
        idle_rounds=args.idle_rounds,
        max_runtime_sec=effective_runtime,
        target_ads=args.target_ads_per_link,
        timeout_ms=args.navigation_timeout_ms,
        proxy_server=args.proxy_server,
        proxy_username=args.proxy_username,
        proxy_password=args.proxy_password,
        proxy_bypass=args.proxy_bypass,
        output=str(output_path),
        csv=None,
        include_raw=False,
        keep_payloads=False,
        response_url_filter=args.response_url_filter,
        log_level=log_level,
    )


def build_row(ad: dict[str, Any], keyword: str, search_url: str, tz: ZoneInfo) -> dict[str, Any] | None:
    ad_date = parse_ad_date(ad, tz)
    if ad_date is None:
        return None

    ad_archive_id = clean_text(ad.get("ad_archive_id"))
    advertiser_name = clean_text(ad.get("advertiser_name") or ad.get("page_name"))
    ad_library_link = clean_text(ad.get("ad_library_link") or ad.get("ad_library_url"))
    ad_text = clean_text(ad.get("ad_text") or ad.get("body_text"))
    destination_product_url = clean_text(unwrap_redirect_url(ad.get("destination_product_url") or ad.get("link_url")))
    title = clean_text(ad.get("title"))
    caption = clean_text(ad.get("caption"))
    link_description = clean_text(ad.get("link_description"))

    return {
        # ad_unique_key is intentionally assigned only after per-keyword advertiser dedupe.
        "ad_unique_key": None,
        "ad_archive_id": ad_archive_id,
        "advertiser_name": advertiser_name,
        "ad_start_date": ad_date.isoformat(),
        "ad_library_link": ad_library_link,
        "ad_text": ad_text,
        "destination_product_url": destination_product_url,
        "title": title,
        "caption": caption,
        "link_description": link_description,
        "source_keyword": keyword,
        "source_search_url": search_url,
    }


def select_rows_for_keyword(
    ads: list[Any],
    *,
    keyword: str,
    search_url: str,
    tz: ZoneInfo,
    target_date_str: str | None,
    max_advertisers: int,
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    matched_for_link = 0
    filtered_invalid_or_social_url = 0
    filtered_marketplace = 0
    filtered_missing_advertiser = 0
    selected_by_advertiser: dict[str, dict[str, Any]] = {}

    for ad in ads:
        if not isinstance(ad, dict):
            continue
        row = build_row(ad, keyword, search_url, tz)
        if row is None:
            continue
        if target_date_str and row["ad_start_date"] != target_date_str:
            continue
        matched_for_link += 1

        advertiser_name = row.get("advertiser_name")
        if not advertiser_name:
            filtered_missing_advertiser += 1
            continue
        if not is_valid_external_url(row.get("destination_product_url")):
            filtered_invalid_or_social_url += 1
            continue
        if is_marketplace_ad(ad, row):
            filtered_marketplace += 1
            continue

        advertiser_key = normalize_advertiser_name(advertiser_name)
        existing = selected_by_advertiser.get(advertiser_key)
        if existing is None or row_quality_score(row) > row_quality_score(existing):
            selected_by_advertiser[advertiser_key] = row

    selected_rows = list(selected_by_advertiser.values())
    selected_rows.sort(key=lambda r: (normalize_advertiser_name(r.get("advertiser_name")), r.get("ad_library_link") or ""))
    if max_advertisers > 0:
        selected_rows = selected_rows[:max_advertisers]
    return (
        selected_rows,
        matched_for_link,
        filtered_invalid_or_social_url,
        filtered_marketplace,
        filtered_missing_advertiser,
    )


async def scrape_single_link(
    *,
    keyword: str,
    search_url: str,
    args: argparse.Namespace,
    output_dir: Path,
    attempt_timeout_sec: int,
    target_date_str: str | None,
    tz: ZoneInfo,
) -> tuple[dict[str, Any], bool, int]:
    best_payload: dict[str, Any] | None = None
    best_selected_rows = -1
    best_matched_for_link = -1
    best_ads_captured = -1
    timed_out = False
    attempts_used = 0
    remaining_budget = max(attempt_timeout_sec, 20)
    max_attempts = max(args.retries + 1, 1)

    for attempt in range(max_attempts):
        if remaining_budget <= 20:
            LOG.info("Stopping retries for keyword=%s because link budget is nearly exhausted.", keyword)
            break
        attempts_used = attempt + 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"meta_daily_link_{attempts_used}_{ts}.json"
        attempts_left = max_attempts - attempt
        timeout_this_attempt = max(20, min(remaining_budget, remaining_budget // attempts_left + 10))
        runtime_cap_sec = max(20, timeout_this_attempt - 20)

        scrape_args = build_scrape_namespace(
            url=search_url,
            output_path=output_path,
            args=args,
            log_level=args.log_level,
            runtime_cap_sec=runtime_cap_sec,
        )
        started_attempt = time.monotonic()
        try:
            payload = await asyncio.wait_for(meta_scraper.run_scrape(scrape_args), timeout=timeout_this_attempt)
        except asyncio.TimeoutError:
            LOG.warning("Link timed out (attempt %s): %s", attempts_used, keyword)
            timed_out = True
            elapsed = int(time.monotonic() - started_attempt)
            remaining_budget -= max(elapsed, timeout_this_attempt)
            continue
        except Exception as exc:
            LOG.warning("Link scrape failed (attempt %s, %s): %s", attempts_used, keyword, exc)
            elapsed = int(time.monotonic() - started_attempt)
            remaining_budget -= max(elapsed, 5)
            continue
        elapsed = int(time.monotonic() - started_attempt)
        remaining_budget -= max(elapsed, 1)

        captured = int(payload.get("meta", {}).get("ads_captured", 0))
        ads = payload.get("ads", [])
        if not isinstance(ads, list):
            ads = []

        selected_rows, matched_for_link, _, _, _ = select_rows_for_keyword(
            ads,
            keyword=keyword,
            search_url=search_url,
            tz=tz,
            target_date_str=target_date_str,
            max_advertisers=args.max_advertisers_per_keyword,
        )

        score = (len(selected_rows), matched_for_link, captured)
        if score > (best_selected_rows, best_matched_for_link, best_ads_captured):
            best_payload = payload
            best_selected_rows, best_matched_for_link, best_ads_captured = score

        enough_selected = len(selected_rows) >= max(args.min_selected_rows_per_link, 1)
        if enough_selected:
            break
        if attempt < args.retries:
            LOG.info(
                "Retrying keyword=%s (selected=%s, matched=%s, captured=%s, remaining_budget=%ss).",
                keyword,
                len(selected_rows),
                matched_for_link,
                captured,
                max(remaining_budget, 0),
            )

    if best_payload is None:
        best_payload = {"meta": {"ads_captured": 0}, "ads": []}
    return best_payload, timed_out, attempts_used


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.dotenv_path:
        load_dotenv(args.dotenv_path)
    else:
        load_dotenv()
    if not args.proxy_server:
        args.proxy_server = os.getenv("META_PROXY_SERVER") or os.getenv("PROXY_SERVER")
    if not args.proxy_username:
        args.proxy_username = os.getenv("META_PROXY_USERNAME")
    if not args.proxy_password:
        args.proxy_password = os.getenv("META_PROXY_PASSWORD")
    if not args.proxy_bypass:
        args.proxy_bypass = os.getenv("META_PROXY_BYPASS")
    if not args.storage_state:
        args.storage_state = os.getenv("META_STORAGE_STATE") or os.getenv("META_DAILY_STORAGE_STATE")
    try:
        tz = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        LOG.warning("Timezone '%s' is unavailable; falling back to UTC.", args.timezone)
        tz = timezone.utc
    target_date = get_target_date(args, tz)
    target_date_str = target_date.isoformat()
    active_date_filter: str | None = None if args.ignore_date_filter else target_date_str
    dedupe_namespace_date = target_date_str

    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    scrapes = config.get("scrapes", [])
    if not isinstance(scrapes, list) or not scrapes:
        raise RuntimeError("No scrapes found in configuration file.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    max_total_sec = max(args.max_total_minutes * 60, 60)

    deduped_rows: dict[str, dict[str, Any]] = {}
    rows_by_keyword: dict[str, list[dict[str, Any]]] = {}
    link_results: list[LinkResult] = []

    for index, entry in enumerate(scrapes, 1):
        elapsed = time.monotonic() - started
        remaining = int(max_total_sec - elapsed)
        if remaining <= 20:
            LOG.warning("Stopping early: total runtime budget reached.")
            break

        keyword = str(entry.get("keyword", f"keyword_{index}"))
        search_url = str(entry.get("search_url", "")).strip()
        if not search_url:
            LOG.warning("Skipping config item %s: missing search_url", index)
            continue

        links_left = len(scrapes) - index + 1
        fair_share_timeout = max((remaining - 5) // max(links_left, 1), 20)
        per_link_timeout = min(args.per_link_timeout_sec, fair_share_timeout)
        per_link_timeout = max(per_link_timeout, 20)

        LOG.info("[%s/%s] Scraping keyword=%s | timeout=%ss", index, len(scrapes), keyword, per_link_timeout)
        payload, timed_out, attempts_used = await scrape_single_link(
            keyword=keyword,
            search_url=search_url,
            args=args,
            output_dir=output_dir,
            attempt_timeout_sec=per_link_timeout,
            target_date_str=active_date_filter,
            tz=tz,
        )

        ads = payload.get("ads", [])
        if not isinstance(ads, list):
            ads = []

        selected_rows, matched_for_link, filtered_invalid_or_social_url, filtered_marketplace, filtered_missing_advertiser = (
            select_rows_for_keyword(
                ads,
                keyword=keyword,
                search_url=search_url,
                tz=tz,
                target_date_str=active_date_filter,
                max_advertisers=args.max_advertisers_per_keyword,
            )
        )

        for row in selected_rows:
            row["ad_unique_key"] = compute_keyword_advertiser_key(dedupe_namespace_date, keyword, row["advertiser_name"])
            deduped_rows[row["ad_unique_key"]] = row

        rows_by_keyword[keyword] = [
            {
                "advertiser_name": row["advertiser_name"],
                "ad_start_date": row["ad_start_date"],
                "ad_library_link": row["ad_library_link"],
                "ad_text": row["ad_text"],
                "destination_product_url": row["destination_product_url"],
            }
            for row in selected_rows
        ]

        link_results.append(
            LinkResult(
                keyword=keyword,
                search_url=search_url,
                ads_captured=int(payload.get("meta", {}).get("ads_captured", 0)),
                matching_target_date=matched_for_link,
                selected_rows=len(selected_rows),
                filtered_invalid_or_social_url=filtered_invalid_or_social_url,
                filtered_marketplace=filtered_marketplace,
                filtered_missing_advertiser=filtered_missing_advertiser,
                timed_out=timed_out,
                attempts_used=attempts_used,
            )
        )
        LOG.info(
            "Done keyword=%s | captured=%s | matched_target_date=%s | selected=%s | unique_total=%s",
            keyword,
            int(payload.get("meta", {}).get("ads_captured", 0)),
            matched_for_link,
            len(selected_rows),
            len(deduped_rows),
        )

    final_rows = list(deduped_rows.values())
    final_rows.sort(key=lambda r: (r.get("source_keyword") or "", normalize_advertiser_name(r.get("advertiser_name"))))
    fields_only = [
        {
            "advertiser_name": row["advertiser_name"],
            "ad_start_date": row["ad_start_date"],
            "ad_library_link": row["ad_library_link"],
            "ad_text": row["ad_text"],
            "destination_product_url": row["destination_product_url"],
        }
        for row in final_rows
    ]

    summary = {
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date_str,
        "date_filter_applied": active_date_filter,
        "ignore_date_filter": args.ignore_date_filter,
        "timezone": args.timezone,
        "links_configured": len(scrapes),
        "links_processed": len(link_results),
        "unique_rows_for_target_date": len(final_rows),
        "unique_rows_total": len(final_rows),
        "rows_selected_by_keyword": {keyword: len(rows) for keyword, rows in rows_by_keyword.items()},
        "runtime_seconds": int(time.monotonic() - started),
        "link_results": [lr.__dict__ for lr in link_results],
    }

    output_bundle = {
        "summary": summary,
        "rows_by_keyword": rows_by_keyword,
        "rows": fields_only,
    }
    out_path = output_dir / f"meta_daily_{target_date_str}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["output_path"] = str(out_path)

    if args.no_db:
        summary["db"] = {"skipped": True}
        result = {"summary": summary, "rows": fields_only}
        if args.email_summary:
            subject = f"{args.email_subject_prefix}: {args.job_name or 'meta_daily'} | {summary.get('unique_rows_total', 0)} rows"
            send_summary_email(subject, build_email_body(summary, args))
        return result

    conn = get_db_connection()
    try:
        ensure_tables(conn)
        db_stats = insert_rows(conn, final_rows)
    finally:
        conn.close()

    summary["db"] = db_stats
    result = {"summary": summary, "rows": fields_only}
    if args.email_summary:
        subject = f"{args.email_subject_prefix}: {args.job_name or 'meta_daily'} | {summary.get('unique_rows_total', 0)} rows"
        send_summary_email(subject, build_email_body(summary, args))
    return result


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)
    result = asyncio.run(run(args))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
