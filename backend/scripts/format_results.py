"""Format price match results from DB dump into readable output."""
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

with open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/price_raw.txt",
          encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line or "|" not in line:
            continue
        domain, raw = line.split("|", 1)
        try:
            products = json.loads(raw)
        except Exception:
            continue
        for p in products:
            name = p.get("product_name_english", "?")
            price = p.get("price_ils", "?")
            prod_url = p.get("product_url", "")
            matches = p.get("matches", [])
            print(f"\n{'='*70}")
            print(f"SITE: {domain}")
            print(f"Product: {name}")
            print(f"Israeli price: {price} ILS")
            print(f"Product page: {prod_url}")
            if not matches:
                print("  (no matches found)")
                continue
            for i, m in enumerate(matches[:5], 1):
                src = m.get("source", "?")
                mname = str(m.get("product_name", "?"))[:80]
                mprice = m.get("price_usd", "?")
                murl = m.get("url", "")
                sim = m.get("similarity", "?")
                print(f"  Match {i}: [{src}] {mname}")
                print(f"    Price: ${mprice}")
                print(f"    Similarity: {sim}")
                # Shorten redirect URLs for readability
                if "grounding-api-redirect" in murl:
                    murl = murl[:80] + "..."
                print(f"    URL: {murl}")
