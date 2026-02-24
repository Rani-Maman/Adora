#!/usr/bin/env bash
# Dispatcher: checks both queues, runs whichever has work.
# Called by cron */10 13-23. Analyze takes priority over price_match.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_NAME="firecrawl"
DB_USER="ubuntu"

# Count unscored ads
UNSCORED=$(psql -U "$DB_USER" -d "$DB_NAME" -t -c \
  "SELECT COUNT(*) FROM ads_with_urls WHERE analysis_score IS NULL;" 2>/dev/null | tr -d ' ')

# Count eligible price match products (aligned with batch_price_match.py filters)
ELIGIBLE=$(psql -U "$DB_USER" -d "$DB_NAME" -t -c \
  "SELECT COUNT(DISTINCT r.id) FROM risk_db r
   JOIN ads_with_urls a ON LOWER(TRIM(r.base_url)) = LOWER(TRIM(
     REPLACE(SPLIT_PART(a.destination_product_url, '/', 3), 'www.', '')))
   WHERE r.risk_score >= 0.6
   AND (a.analysis_category ILIKE '%dropship%' OR a.analysis_category ILIKE '%uncertain%')
   AND a.destination_product_url IS NOT NULL
   AND LENGTH(a.destination_product_url) > 20
   AND a.destination_product_url ~ '^https?://[^/]+/.+'
   AND r.base_url NOT LIKE '%shein.com'
   AND r.base_url NOT LIKE '%aliexpress.com'
   AND r.base_url NOT LIKE '%temu.%'
   AND a.destination_product_url NOT LIKE '%s.click.aliexpress.com%'
   AND (r.price_matches IS NULL
     OR NOT r.price_matches::text LIKE '%' || a.destination_product_url || '%')
   AND (r.price_match_failures IS NULL
     OR NOT r.price_match_failures::text LIKE '%' || a.destination_product_url || '%');" 2>/dev/null | tr -d ' ')

echo "[$(date -Is)] Unscored: ${UNSCORED:-0} | Eligible PM: ${ELIGIBLE:-0}"

if [[ "${UNSCORED:-0}" -gt 0 ]]; then
    echo "Dispatching: analyze"
    exec /usr/bin/python3 "${SCRIPT_DIR}/batch_analyze_ads.py"
elif [[ "${ELIGIBLE:-0}" -gt 0 ]]; then
    echo "Dispatching: price_match"
    exec bash "${SCRIPT_DIR}/run_price_match.sh"
else
    echo "Nothing to do"
fi
