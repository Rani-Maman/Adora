#!/usr/bin/env python3
"""
Create a Playwright storage_state JSON for Meta (Facebook) Ads Library scraping.

This is a free-only workaround when Meta blocks datacenter/headless traffic:
- Run this once on a machine with a real display (or with xvfb-run on Linux).
- Log into Facebook manually in the opened browser window.
- Script auto-detects login and saves cookies/session to a JSON file.

Then, pass that file to the scraper via:
  daily_meta_scrape.py --storage-state /path/to/meta_storage_state.json
or set env var:
  META_DAILY_STORAGE_STATE=/path/to/meta_storage_state.json
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


DEFAULT_URL = "https://www.facebook.com/ads/library/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Playwright storage_state for Meta scraping.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Start URL to open (default: Ads Library).")
    parser.add_argument(
        "--channel",
        default="chrome",
        help="Browser channel to use (e.g. chrome, msedge). Use empty string to use Playwright-bundled Chromium.",
    )
    parser.add_argument("--max-wait-sec", type=int, default=600, help="How long to wait for login before saving anyway.")
    parser.add_argument("--poll-sec", type=float, default=2.0, help="How often to poll for login cookies.")
    parser.add_argument(
        "--require-login",
        action="store_true",
        help="If set, fail if login cookie is not detected within max-wait-sec.",
    )
    parser.add_argument(
        "--output",
        default=str(Path("backend/scripts/output") / "meta_storage_state.json"),
        help="Where to save storage_state JSON.",
    )
    return parser.parse_args()


async def is_logged_in(context: Any) -> bool:
    # Facebook sets c_user when logged in.
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    for cookie in cookies:
        if cookie.get("name") != "c_user":
            continue
        value = str(cookie.get("value") or "").strip()
        domain = str(cookie.get("domain") or "").lower()
        if value and domain.endswith("facebook.com"):
            return True
    return False


async def run(args: argparse.Namespace) -> None:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        channel = (args.channel or "").strip()
        launch_kwargs: dict[str, Any] = {"headless": False}
        if channel and channel.lower() not in {"none", "playwright", "chromium"}:
            launch_kwargs["channel"] = channel

        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception:
            # Fallback: use Playwright-bundled Chromium if system channel fails.
            launch_kwargs.pop("channel", None)
            browser = await p.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 2200},
            "locale": "en-US",
        }
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        resp = await page.goto(args.url, wait_until="domcontentloaded", timeout=120000)

        print("")
        print("Playwright browser opened.")
        if resp is not None:
            print(f"Navigation status: {resp.status}")
        print("1) If prompted, log into Facebook in this Playwright window.")
        print("2) Leave the window open; I will auto-save storage_state once login is detected.")
        print("")

        deadline = time.monotonic() + max(args.max_wait_sec, 10)
        last_log = 0.0
        logged_in = await is_logged_in(context)
        while not logged_in and time.monotonic() < deadline:
            await asyncio.sleep(max(args.poll_sec, 0.5))
            logged_in = await is_logged_in(context)
            now = time.monotonic()
            if now - last_log >= 10:
                remaining = int(deadline - now)
                print(f"Waiting for login cookie... ({remaining}s left)", flush=True)
                last_log = now

        if not logged_in:
            msg = "Login cookie not detected before timeout."
            if args.require_login:
                raise RuntimeError(msg)
            print(f"Warning: {msg} Saving storage_state anyway.")

        await context.storage_state(path=str(output_path))
        await context.close()
        await browser.close()

    print(f"Saved storage_state: {output_path}")


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
