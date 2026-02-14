"""Print price match results from a psql dump file."""
import json, sys

with open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/price_results.txt") as f:
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
            url = p.get("product_url", "")
            matches = p.get("matches", [])
            print(f"\n{'='*70}")
            print(f"SITE: {domain}")
            print(f"Product: {name}")
            print(f"Israeli price: {price} ILS")
            print(f"URL: {url}")
            if not matches:
                print("  (no matches)")
                continue
            for i, m in enumerate(matches[:3], 1):
                src = m.get("source", "?")
                mname = str(m.get("product_name", "?"))[:70]
                mprice = m.get("price_usd", "?")
                murl = m.get("url", "?")
                sim = m.get("similarity", "?")
                print(f"  {i}. [{src}] {mname}")
                print(f"     ${mprice} | {sim}")
                print(f"     {murl}")
