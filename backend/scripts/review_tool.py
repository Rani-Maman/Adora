#!/usr/bin/env python3
"""Interactive CLI to triage borderline dropship sites from review_queue.txt."""

import os
import re
import sys

import psycopg2

QUEUE_FILE = os.getenv("REVIEW_QUEUE_FILE", "/home/ubuntu/adora_ops/review_queue.txt")
LEGIT_FILE = os.getenv("REVIEW_LEGIT_FILE", "/home/ubuntu/adora_ops/review_legit.txt")
DOTENV_PATH = os.getenv("REVIEW_DOTENV", "/home/ubuntu/adora_ops/.env")

BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"


def link(url, text=None):
    """OSC 8 clickable hyperlink."""
    text = text or url
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def load_env():
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
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "firecrawl"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def parse_queue(path):
    entries = []
    if not os.path.isfile(path):
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|", 2)]
            domain = parts[0] if len(parts) > 0 else ""
            score_str = parts[1] if len(parts) > 1 else ""
            reason = parts[2] if len(parts) > 2 else ""
            score = 0.5
            m = re.search(r"score=([\d.]+)", score_str)
            if m:
                score = float(m.group(1))
            entries.append({"domain": domain, "score": score, "reason": reason, "raw": line})
    return entries


def insert_risk_db(conn, domain, score):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_db (base_url, risk_score, evidence, first_seen, last_updated)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (base_url) DO UPDATE SET
                risk_score = GREATEST(risk_db.risk_score, EXCLUDED.risk_score),
                last_updated = NOW();
            """,
            (domain, score, ["manual_review"]),
        )
    conn.commit()


def save_remaining(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(e["raw"] + "\n")


def finish(remaining, entries_from, risky, legit, skipped):
    save_remaining(QUEUE_FILE, remaining)
    print(f"\n{BOLD}Session:{RESET} {RED}{risky} risky{RESET} | {GREEN}{legit} legit{RESET} | {YELLOW}{skipped} deferred{RESET}")


def main():
    load_env()
    entries = parse_queue(QUEUE_FILE)

    if not entries:
        print(f"{GREEN}Review queue is empty!{RESET}")
        return

    conn = get_db_conn()
    conn.autocommit = False

    # Load already-decided domains
    with conn.cursor() as cur:
        cur.execute("SELECT LOWER(TRIM(base_url)) FROM risk_db")
        known = {r[0] for r in cur.fetchall()}
    legit_domains = set()
    if os.path.isfile(LEGIT_FILE):
        with open(LEGIT_FILE) as f:
            legit_domains = {l.strip().lower() for l in f if l.strip()}
    skip_domains = known | legit_domains

    # Deduplicate and filter
    seen = set()
    filtered = []
    for e in entries:
        d = e["domain"].lower()
        if d not in seen and d not in skip_domains:
            seen.add(d)
            filtered.append(e)
    entries = filtered
    save_remaining(QUEUE_FILE, entries)

    if not entries:
        print(f"{GREEN}Review queue is empty!{RESET}")
        conn.close()
        return

    remaining = []
    risky_count = 0
    legit_count = 0
    skip_count = 0

    print(f"\n{BOLD}=== Adora Review Tool ==={RESET}")
    print(f"{DIM}{len(entries)} sites to review{RESET}\n")
    print(f"  {RED}r{RESET} = risky    {GREEN}l{RESET} = legit    {YELLOW}s{RESET} = skip    {DIM}q = quit{RESET}\n")

    try:
        for i, entry in enumerate(entries):
            domain = entry["domain"]
            score = entry["score"]
            reason = entry["reason"]
            num = i + 1

            if score >= 0.55:
                sc = f"{RED}{score}{RESET}"
            elif score >= 0.5:
                sc = f"{YELLOW}{score}{RESET}"
            else:
                sc = f"{GREEN}{score}{RESET}"

            url = f"https://{domain}"
            clickable = link(url, url)

            print(f"{BOLD}[{num}/{len(entries)}]{RESET} {CYAN}{domain}{RESET}  score={sc}")
            print(f"  {DIM}{reason}{RESET}")
            print(f"  {clickable}")

            while True:
                try:
                    choice = input(f"  {BOLD}>{RESET} ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    remaining.extend(entries[i:])
                    raise KeyboardInterrupt

                if choice == "r":
                    insert_risk_db(conn, domain, max(score, 0.6))
                    risky_count += 1
                    print(f"  {RED}-> risk_db (score={max(score, 0.6)}){RESET}\n")
                    break
                elif choice == "l":
                    with open(LEGIT_FILE, "a") as lf:
                        lf.write(domain.lower() + "\n")
                    legit_count += 1
                    print(f"  {GREEN}-> legit, removed{RESET}\n")
                    break
                elif choice == "s":
                    remaining.append(entry)
                    skip_count += 1
                    print(f"  {YELLOW}-> skipped{RESET}\n")
                    break
                elif choice == "q":
                    remaining.append(entry)
                    remaining.extend(entries[i + 1:])
                    raise KeyboardInterrupt
                else:
                    print(f"  {DIM}r/l/s/q{RESET}")
    except KeyboardInterrupt:
        pass

    finish(remaining, entries, risky_count, legit_count, skip_count)
    conn.close()


if __name__ == "__main__":
    main()
