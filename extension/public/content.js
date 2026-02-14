// Content Script - Runs on every page
// Detects when user visits a site and triggers analysis

(function () {
  if (window.__adoraChecked) return;
  window.__adoraChecked = true;

  if (!location.href.startsWith('http')) return;

  chrome.runtime.sendMessage({
    type: 'CHECK_URL',
    url: location.href
  }, (response) => {
    if (response && response.risky && response.score >= 0.6) {
      showWarningBanner(response);
    }
  });

  function normalizeProductName(name) {
    return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function isDuplicate(norm, seen) {
    for (const s of seen) {
      if (norm.includes(s) || s.includes(norm)) return true;
    }
    return false;
  }

  function getProductUrl(source, productName, rawUrl) {
    if (rawUrl && !rawUrl.includes('vertexaisearch.cloud.google.com')) return rawUrl;
    const q = encodeURIComponent(productName);
    const s = (source || '').toLowerCase();
    if (s.includes('aliexpress')) return `https://www.aliexpress.com/w/wholesale-${q}.html`;
    if (s.includes('temu')) return `https://www.temu.com/search_result.html?search_key=${q}`;
    if (s.includes('alibaba')) return `https://www.alibaba.com/trade/search?SearchText=${q}`;
    return `https://www.google.com/search?q=${q}+${encodeURIComponent(source)}`;
  }

  function buildPriceCards(result) {
    const noMatchMsg = `<div style="margin-top:10px;font-size:12px;opacity:0.85;">No cheaper alternatives found yet. Our system is actively scanning - check back soon.</div>`;
    if (!result.price_matches || !result.price_matches.length) return noMatchMsg;
    const ILS_PER_USD = 1 / 0.27;
    const seen = [];
    let cards = '';
    let count = 0;
    const sorted = result.price_matches.slice().sort((a, b) => (b.price_ils || 0) - (a.price_ils || 0));
    for (const entry of sorted) {
      if (count >= 3) break;
      if (!entry.matches || !entry.matches.length) continue;
      const norm = normalizeProductName(entry.product_name_english);
      if (isDuplicate(norm, seen)) continue;
      seen.push(norm);
      // Get cheapest match per unique source
      const bySource = {};
      for (const m of entry.matches.filter(m => m.price_usd > 0)) {
        const src = m.source || 'AliExpress';
        if (!bySource[src] || m.price_usd < bySource[src].price_usd) bySource[src] = m;
      }
      const sources = Object.entries(bySource).sort((a, b) => a[1].price_usd - b[1].price_usd).slice(0, 3);
      if (!sources.length) continue;
      const name = entry.product_name_english || 'Product';
      const cheapest = sources[0][1];
      const cheapIls = Math.round(cheapest.price_usd * ILS_PER_USD);
      const markup = entry.price_ils > 0 && cheapIls > 0
        ? (entry.price_ils / cheapIls).toFixed(1) : null;
      let sourceRows = '';
      for (const [src, m] of sources) {
        const ils = Math.round(m.price_usd * ILS_PER_USD);
        const url = getProductUrl(src, name, m.url);
        sourceRows += `
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;margin-top:2px;">
            <span>${src}: <strong>₪${ils}</strong></span>
            <a href="${url}" target="_blank" rel="noopener" style="color:#67e8f9;font-size:11px;text-decoration:none;">View →</a>
          </div>`;
      }
      count++;
      cards += `
        <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(103,232,249,0.2);border-radius:8px;padding:10px 12px;margin-top:6px;">
          <div style="font-weight:600;font-size:12px;color:#f0f9ff;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${name}</div>
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;">
            <span>This site: <strong>₪${entry.price_ils > 0 ? entry.price_ils : '?'}</strong></span>
            ${markup ? `<span style="background:#dc2626;color:#fff;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;">${markup}x markup</span>` : ''}
          </div>
          ${sourceRows}
        </div>`;
    }
    if (!cards) return noMatchMsg;
    return `<div style="margin-top:10px;"><strong style="color:#67e8f9;font-size:13px;">Found Cheaper Elsewhere</strong>${cards}</div>`;
  }

  function showWarningBanner(result) {
    const priceHtml = buildPriceCards(result);
    const banner = document.createElement('div');
    banner.id = 'adora-warning-banner';
    banner.innerHTML = `
      <div style="
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        background: linear-gradient(135deg, #DC2626 0%, #991B1B 100%);
        color: white;
        padding: 16px 20px;
        z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        max-height: 80vh;
        overflow-y: auto;
      ">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;gap:12px;">
            <span style="font-size:24px;">⚠️</span>
            <div>
              <strong style="font-size:15px;">Potential Dropship Site Detected</strong>
              <p style="margin:4px 0 0 0;opacity:0.9;font-size:12px;">
                This site shows patterns commonly associated with dropshipping.
              </p>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:16px;">
            <span style="font-size:11px;opacity:0.8;max-width:200px;">
              This is informational only. Adora is not liable for purchasing decisions.
            </span>
            <button id="adora-close-btn" style="
              background:rgba(255,255,255,0.2);
              border:none;
              color:white;
              padding:8px 16px;
              border-radius:6px;
              cursor:pointer;
              font-weight:500;
            ">Dismiss</button>
          </div>
        </div>
        ${priceHtml}
      </div>
    `;

    document.body.insertBefore(banner, document.body.firstChild);

    document.getElementById('adora-close-btn').addEventListener('click', () => {
      banner.remove();
      document.body.style.marginTop = '';
    });

    const bannerHeight = banner.firstElementChild.offsetHeight;
    document.body.style.marginTop = bannerHeight + 'px';
  }
})();
