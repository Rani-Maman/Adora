#!/usr/bin/env python3
"""
HTTP-based Meta Ads Library scraper.
Drop-in replacement for the Playwright-based meta_ads_library_scraper.py.

Approach:
  1. GET the Ad Library page → 403 (challenge)
  2. POST to the challenge URL → get rd_challenge cookie
  3. GET the page again → 200 with full HTML
  4. Extract tokens (lsd, spin, etc.) and doc_id
  5. POST to /api/graphql/ with correct variables → get ads as JSON
  6. Paginate using AdLibrarySearchPaginationQuery (separate doc_id) with cursor variable

Returns the same dict shape that daily_meta_scrape.py expects.
"""

import argparse
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

NAV_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

GQL_HEADERS = {
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Origin": "https://www.facebook.com",
}

# ── known doc_ids ──
FOUNDATION_DOC_ID = "33730956806489539"   # AdLibraryFoundationRootQuery
PAGINATION_DOC_ID = "25464068859919530"   # AdLibrarySearchPaginationQuery


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _build_page_url(
    keyword: str,
    country: str = "IL",
    active_status: str = "active",
    ad_type: str = "all",
    media_type: str = "all",
    search_type: str = "keyword_unordered",
) -> str:
    params = {
        "active_status": active_status,
        "ad_type": ad_type,
        "country": country,
        "media_type": media_type,
        "q": keyword,
        "search_type": search_type,
    }
    return "https://www.facebook.com/ads/library/?" + urlencode(params)


def _solve_challenge(session: requests.Session, page_url: str) -> requests.Response:
    """GET page, solve the JS challenge if needed, return the final response."""
    r = session.get(page_url, timeout=30)
    if r.status_code != 403:
        return r

    m = re.search(r"fetch\('([^']+)'", r.text)
    if not m:
        log.warning("403 but no challenge URL found in response")
        return r

    challenge_url = "https://www.facebook.com" + m.group(1)
    log.debug("Solving challenge: %s", challenge_url[:80])

    session.post(
        challenge_url,
        headers={
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": page_url,
            "Origin": "https://www.facebook.com",
        },
        timeout=30,
    )
    return session.get(page_url, timeout=30)


def _extract_tokens(html: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    patterns = {
        "lsd": [
            r'"LSD"[^}]*"token"\s*:\s*"([^"]+)"',
            r'name="lsd"\s+value="([^"]+)"',
        ],
        "hsi": [r'"hsi"\s*:\s*"([^"]+)"'],
        "__spin_r": [r'"__spin_r"\s*:\s*(\d+)'],
        "__spin_b": [r'"__spin_b"\s*:\s*"([^"]+)"'],
        "__spin_t": [r'"__spin_t"\s*:\s*(\d+)'],
        "__rev": [r'"server_revision"\s*:\s*(\d+)', r'"__spin_r"\s*:\s*(\d+)'],
        "jazoest": [
            r'"jazoest"\s*:\s*"?(\d+)"?',
            r'name="jazoest"\s+value="(\d+)"',
        ],
        "fb_dtsg": [
            r'"DTSGInitData"[^}]*"token"\s*:\s*"([^"]+)"',
            r'name="fb_dtsg"\s+value="([^"]+)"',
        ],
    }
    for key, pats in patterns.items():
        for p in pats:
            m = re.search(p, html)
            if m:
                tokens[key] = m.group(1)
                break
    return tokens


def _extract_doc_id(html: str) -> str | None:
    """Extract the Foundation query doc_id from the page, or use fallback."""
    for m in re.finditer(r'"(?:queryID|doc_id|documentId)"\s*:\s*"(\d+)"', html):
        return m.group(1)
    return None


def _extract_page_variables(html: str, doc_id: str) -> dict[str, Any] | None:
    """Extract the initial variables JSON embedded near the doc_id in page HTML."""
    pattern = re.escape(f'"queryID":"{doc_id}"')
    m = re.search(pattern, html)
    if not m:
        return None
    start = max(0, m.start() - 5000)
    end = min(len(html), m.end() + 2000)
    ctx = html[start:end]
    vm = re.search(r'"variables"\s*:\s*(\{[^}]+\})', ctx)
    if vm:
        try:
            return json.loads(vm.group(1))
        except json.JSONDecodeError:
            pass
    return None


# Mapping from URL sort_data[mode] values → GraphQL enum values
_URL_SORT_MODE_MAP: dict[str, str] = {
    "relevancy_monthly_grouped": "SORT_BY_RELEVANCY_MONTHLY_GROUPED",
    "total_impressions": "SORT_BY_IMPRESSIONS_WITH_INDEX",
    "start_date": "SORT_BY_DATE",
}
_URL_SORT_DIR_MAP: dict[str, str] = {
    "desc": "DESCENDING",
    "asc": "ASCENDING",
}

# Default sort: relevancy descending (matches Meta's UI default for keyword search)
_DEFAULT_SORT_DATA: dict[str, str] = {
    "mode": "SORT_BY_RELEVANCY_MONTHLY_GROUPED",
    "direction": "DESCENDING",
}


def _normalize_sort_data(sort_data: dict[str, str] | None) -> dict[str, str]:
    """Convert URL-format sort values to GraphQL enum values."""
    if not sort_data:
        return _DEFAULT_SORT_DATA
    mode = sort_data.get("mode", "")
    direction = sort_data.get("direction", "desc")
    return {
        "mode": _URL_SORT_MODE_MAP.get(mode, mode),
        "direction": _URL_SORT_DIR_MAP.get(direction, direction),
    }


def _build_variables(
    keyword: str,
    country: str = "IL",
    cursor: str | None = None,
    session_id: str | None = None,
    sort_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the GraphQL variables dict matching the real Meta Ad Library request format.

    For the Foundation query (page 1): no cursor.
    For the Pagination query (page 2+): pass cursor from previous page.
    """
    v: dict[str, Any] = {
        "activeStatus": "ACTIVE",
        "adType": "ALL",
        "audienceTimeframe": "LAST_7_DAYS",
        "bylines": [],
        "collationToken": None,
        "contentLanguages": [],
        "countries": [country],
        "country": country,
        "deeplinkAdID": None,
        "excludedIDs": [],
        "fetchPageInfo": False,
        "fetchSharedDisclaimers": False,
        "hasDeeplinkAdID": False,
        "isAboutTab": False,
        "isAudienceTab": False,
        "isLandingPage": False,
        "isTargetedCountry": False,
        "location": None,
        "mediaType": "ALL",
        "multiCountryFilterMode": None,
        "pageIDs": [],
        "potentialReachInput": [],
        "publisherPlatforms": [],
        "queryString": keyword,
        "regions": [],
        "searchType": "KEYWORD_UNORDERED",
        "sessionID": session_id or str(uuid.uuid4()),
        "shouldFetchCount": cursor is None,
        "sortData": _normalize_sort_data(sort_data),
        "source": None,
        "startDate": None,
        "v": "385782",
        "viewAllPageID": "0",
    }
    if cursor:
        v["cursor"] = cursor
    return v


# ---------------------------------------------------------------------------
# GraphQL call
# ---------------------------------------------------------------------------

def _graphql_search(
    session: requests.Session,
    tokens: dict[str, str],
    variables: dict[str, Any],
    doc_id: str,
    page_url: str,
    friendly_name: str = "AdLibraryFoundationRootQuery",
) -> tuple[list[dict[str, Any]], str | None, bool, int | None]:
    """
    Execute one GraphQL call.
    Returns: (ads_list, cursor | None, has_next_page, total_count | None)
    """
    lsd = tokens.get("lsd", "")
    hdrs = dict(GQL_HEADERS)
    hdrs["Referer"] = page_url
    hdrs["X-FB-LSD"] = lsd
    hdrs["X-FB-Friendly-Name"] = friendly_name

    form: dict[str, str] = {
        "lsd": lsd,
        "__a": "1",
        "doc_id": doc_id,
        "variables": json.dumps(variables),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": friendly_name,
    }
    for k in ("__spin_r", "__spin_b", "__spin_t", "jazoest", "hsi", "__rev"):
        if k in tokens:
            form[k] = tokens[k]
    if "fb_dtsg" in tokens:
        form["fb_dtsg"] = tokens["fb_dtsg"]

    r = session.post(
        "https://www.facebook.com/api/graphql/",
        data=form,
        headers=hdrs,
        timeout=30,
    )
    if r.status_code != 200:
        log.error("GraphQL returned %d", r.status_code)
        return [], None, False, None

    text = r.text
    if text.startswith("for (;;);"):
        text = text[9:]

    # Facebook returns NDJSON (multiple JSON objects, one per line)
    ads: list[dict[str, Any]] = []
    forward_cursor = None
    has_next = False
    total_count = None

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "errors" in data:
            for e in data["errors"]:
                severity = e.get("severity", "")
                if severity == "CRITICAL":
                    log.error("GraphQL CRITICAL error: %s", e.get("message", ""))
                else:
                    log.debug("GraphQL warning: %s", e.get("message", ""))

        if "data" not in data:
            continue

        d = data["data"]
        # Navigate to search_results_connection
        search_conn = None
        if isinstance(d, dict):
            main = d.get("ad_library_main", {})
            if isinstance(main, dict):
                search_conn = main.get("search_results_connection")

        if not search_conn:
            continue

        if "count" in search_conn and total_count is None:
            total_count = search_conn["count"]

        page_info = search_conn.get("page_info", {})
        if page_info:
            forward_cursor = page_info.get("end_cursor")
            has_next = page_info.get("has_next_page", False)

        edges = search_conn.get("edges", [])
        for edge in edges:
            node = edge.get("node", {})
            for ad_raw in node.get("collated_results", []):
                ads.append(ad_raw)

    return ads, forward_cursor, has_next, total_count


# ---------------------------------------------------------------------------
# normalise one ad to the shape daily_meta_scrape expects
# ---------------------------------------------------------------------------

def normalize_ad(ad: dict[str, Any]) -> dict[str, Any]:
    snap = ad.get("snapshot") or {}
    ad_id = ad.get("ad_archive_id")

    body_text = (snap.get("body") or {}).get("text")
    link_url = snap.get("link_url")

    # Build ad library URL
    ad_lib_url = (
        f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else None
    )

    # Extra links from cards
    cards = snap.get("cards") or []
    dest_url = link_url
    if not dest_url and cards:
        for c in cards:
            u = c.get("link_url")
            if u:
                dest_url = u
                break

    # Extra links field
    extra_links = snap.get("extra_links") or []
    if not dest_url and extra_links:
        for el in extra_links:
            u = el.get("link_url") if isinstance(el, dict) else el
            if u:
                dest_url = u
                break

    # Extract epoch dates from top-level ad object
    start_epoch = ad.get("start_date")
    end_epoch = ad.get("end_date")

    return {
        "ad_archive_id": ad_id,
        "page_id": ad.get("page_id") or snap.get("page_id"),
        "page_name": snap.get("page_name"),
        "is_active": ad.get("is_active", True),
        "start_date": start_epoch,
        "end_date": end_epoch,
        "start_date_string": None,
        "end_date_string": None,
        "publisher_platform": [],
        "ad_library_url": ad_lib_url,
        "advertiser_name": snap.get("page_name"),
        "ad_library_link": ad_lib_url,
        "page_profile_uri": snap.get("page_profile_uri"),
        "cta_type": snap.get("cta_type"),
        "cta_text": snap.get("cta_text"),
        "display_format": snap.get("display_format"),
        "title": snap.get("title"),
        "caption": snap.get("caption"),
        "link_url": link_url,
        "link_description": snap.get("link_description"),
        "body_text": body_text,
        "ad_text": body_text,
        "destination_product_url": dest_url,
        "cards_count": len(cards),
        "images_count": len(snap.get("images") or []),
        "videos_count": len(snap.get("videos") or []),
        "page_categories": snap.get("page_categories") or [],
    }


# ---------------------------------------------------------------------------
# main entry point (called by daily_meta_scrape.py)
# ---------------------------------------------------------------------------

async def run_scrape(args: argparse.Namespace) -> dict[str, Any]:
    """
    Drop-in replacement for the Playwright scraper's run_scrape().
    Accepts the same argparse.Namespace produced by build_scrape_namespace().
    """
    url = getattr(args, "url", "")
    target_ads = getattr(args, "target_ads", 100)
    max_runtime = getattr(args, "max_runtime_sec", 120)
    keyword = getattr(args, "query", "")
    country = getattr(args, "country", "IL")

    # Extract keyword and sort params from URL if not provided directly
    sort_data: dict[str, str] | None = None
    if url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if not keyword:
            keyword = qs.get("q", [""])[0]
            country = qs.get("country", [country])[0]
        # Parse sort_data[mode] and sort_data[direction] from URL
        sort_mode = qs.get("sort_data[mode]", [None])[0]
        sort_direction = qs.get("sort_data[direction]", [None])[0]
        if sort_mode:
            sort_data = {"mode": sort_mode, "direction": sort_direction or "desc"}

    if not keyword:
        log.error("No keyword found in args or URL")
        return {"meta": {"error": "no_keyword"}, "ads": []}

    start_ts = time.monotonic()
    log.info("HTTP scraper: keyword=%r country=%s target=%d sort=%s", keyword, country, target_ads, sort_data)

    page_url = url or _build_page_url(keyword, country)

    # ── session setup ──
    session = requests.Session()
    session.headers.update(NAV_HEADERS)

    # ── solve challenge & load page ──
    try:
        resp = _solve_challenge(session, page_url)
    except Exception as e:
        log.error("Challenge/page load failed: %s", e)
        return {"meta": {"error": str(e)}, "ads": []}

    if resp.status_code != 200:
        log.error("Page load failed with status %d", resp.status_code)
        return {"meta": {"error": f"status_{resp.status_code}"}, "ads": []}

    html = resp.text
    log.info("Page loaded: %d bytes", len(html))

    # ── extract tokens ──
    tokens = _extract_tokens(html)
    if "lsd" not in tokens:
        log.error("No LSD token found in page")
        return {"meta": {"error": "no_lsd_token"}, "ads": []}
    log.info("Tokens: %s", list(tokens.keys()))

    # ── extract doc_id (or use the known one) ──
    doc_id = _extract_doc_id(html) or FOUNDATION_DOC_ID
    log.info("doc_id: %s", doc_id)

    # ── try to extract initial variables from page HTML for v= field ──
    page_vars = _extract_page_variables(html, doc_id)
    v_field = page_vars.get("v", "385782") if page_vars else "385782"

    # ── paginated search ──
    all_ads: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor: str | None = None
    total_count: int | None = None
    max_pages = 250  # safety limit (~30 ads/page = ~7500 max)
    session_id = str(uuid.uuid4())

    for page_num in range(max_pages):
        elapsed = time.monotonic() - start_ts
        if elapsed > max_runtime:
            log.warning("Runtime limit reached (%d sec)", max_runtime)
            break
        if len(all_ads) >= target_ads:
            log.info("Target reached: %d ads", len(all_ads))
            break

        # Page 1 uses Foundation query; page 2+ uses Pagination query
        is_first_page = cursor is None
        current_doc_id = doc_id if is_first_page else PAGINATION_DOC_ID
        friendly_name = (
            "AdLibraryFoundationRootQuery" if is_first_page
            else "AdLibrarySearchPaginationQuery"
        )

        variables = _build_variables(
            keyword=keyword,
            country=country,
            cursor=cursor,
            session_id=session_id,
            sort_data=sort_data,
        )
        variables["v"] = v_field

        log.info(
            "GraphQL page %d  cursor=%s  collected=%d  doc=%s",
            page_num + 1,
            (cursor or "NONE")[:30],
            len(all_ads),
            "foundation" if is_first_page else "pagination",
        )

        try:
            ads_raw, next_cursor, has_next, count = _graphql_search(
                session, tokens, variables, current_doc_id, page_url,
                friendly_name=friendly_name,
            )
        except Exception as e:
            log.error("GraphQL call failed: %s", e)
            break

        if count is not None and total_count is None:
            total_count = count
            log.info("Total results from FB: %d", total_count)

        if not ads_raw:
            log.info("No ads in response (page %d)", page_num + 1)
            break

        for ad_raw in ads_raw:
            aid = ad_raw.get("ad_archive_id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                all_ads.append(normalize_ad(ad_raw))

        log.info("Page %d: %d new ads", page_num + 1, len(ads_raw))

        if not has_next or not next_cursor:
            log.info("No more pages")
            break
        cursor = next_cursor

        # Small delay between pages
        time.sleep(1.0)

    elapsed = time.monotonic() - start_ts
    log.info(
        "Scrape complete: %d ads in %.1f sec (target=%d, total=%s)",
        len(all_ads), elapsed, target_ads, total_count,
    )

    return {
        "meta": {
            "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
            "target_url": page_url,
            "responses_seen": 0,
            "payloads_parsed": 0,
            "ads_captured": len(all_ads),
            "navigation_status": 200,
            "proxy_enabled": False,
            "search_results_count_hint": total_count,
            "cursor": cursor,
            "max_scrolls": 0,
            "scroll_delay_ms": 0,
            "idle_rounds": 0,
            "max_runtime_sec": max_runtime,
            "target_ads": target_ads,
        },
        "ads": all_ads,
        "captured_payloads": [],
    }


# ---------------------------------------------------------------------------
# standalone CLI for testing
# ---------------------------------------------------------------------------

def _cli():
    import asyncio

    p = argparse.ArgumentParser(description="HTTP Meta Ads Library scraper")
    p.add_argument("--keyword", "-k", required=True, help="Search keyword")
    p.add_argument("--country", "-c", default="IL")
    p.add_argument("--target", "-t", type=int, default=100)
    p.add_argument("--max-runtime", type=int, default=120)
    p.add_argument("--output", "-o", help="Write JSON output to file")
    p.add_argument("--verbose", "-v", action="store_true")
    cli_args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if cli_args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    # Build an argparse.Namespace that matches what daily_meta_scrape expects
    ns = argparse.Namespace(
        url=_build_page_url(cli_args.keyword, cli_args.country),
        query=cli_args.keyword,
        country=cli_args.country,
        target_ads=cli_args.target,
        max_runtime_sec=cli_args.max_runtime,
    )

    result = asyncio.run(run_scrape(ns))

    print(f"\nAds captured: {result['meta']['ads_captured']}")
    print(f"Total on FB:  {result['meta'].get('search_results_count_hint', '?')}")

    for ad in result["ads"][:5]:
        print(f"\n  [{ad['ad_archive_id']}] {ad['advertiser_name']}")
        print(f"    CTA: {ad['cta_text']}")
        print(f"    URL: {ad['destination_product_url']}")
        body = (ad.get("body_text") or "")[:120]
        print(f"    Body: {body}")

    if cli_args.output:
        with open(cli_args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to {cli_args.output}")


if __name__ == "__main__":
    _cli()
