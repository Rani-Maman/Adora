// Adora Floating Widget — Content Script
// Replaces popup + banner with a single draggable widget on the page

(function () {
  console.log('[Adora] Content script loaded');
  if (window.__adoraWidget) return;
  window.__adoraWidget = true;

  if (!location.href.startsWith('http')) return;

  // ====== Theme palettes ======
  const themes = {
    light: {
      bg: '#faf8f5', bgSec: '#f5f0eb', bgCard: '#ffffff', bgHover: '#f0ebe5',
      text: '#2d1b4e', textSec: '#554466', textMuted: '#7a6d88',
      border: '#e8e0d8', accent: '#7c3aed', accentHover: '#6d28d9',
      accentLight: '#f3edff', accentText: '#ffffff',
      safe: '#10b981', safeBg: '#ecfdf5', safeText: '#065f46',
      danger: '#dc2626', dangerBg: '#fef2f2', dangerText: '#991b1b', dangerBorder: '#fecaca',
      link: '#6d28d9', shadow: '0 8px 32px rgba(45,27,78,0.18)',
    },
    dark: {
      bg: '#15202b', bgSec: '#192734', bgCard: '#1c2938', bgHover: '#22303d',
      text: '#e7e9ea', textSec: '#a0acb8', textMuted: '#8694a2',
      border: '#2f3b47', accent: '#a78bfa', accentHover: '#c4b5fd',
      accentLight: 'rgba(167,139,250,0.12)', accentText: '#15202b',
      safe: '#34d399', safeBg: 'rgba(52,211,153,0.1)', safeText: '#6ee7b7',
      danger: '#f87171', dangerBg: 'rgba(248,113,113,0.1)', dangerText: '#fca5a5', dangerBorder: 'rgba(248,113,113,0.25)',
      link: '#a78bfa', shadow: '0 8px 32px rgba(0,0,0,0.4)',
    }
  };

  // ====== i18n ======
  const i18n = {
    en: {
      signIn: 'Sign in with Google', signOut: 'Sign out',
      analyzing: 'Analyzing site...', noConcerns: 'No alerts found for this site',
      potentialDropship: 'Heads up - this may be a dropship site',
      noMatches: "We're searching for cheaper options for you - check back soon.",
      cheaperElsewhere: 'Found cheaper options for you',
      thisSite: 'This site', markup: 'more expensive', view: 'View',
      viewArrow: '\u2192', showMore: 'Show more results', showLess: 'Show less',
      disclaimer: 'The extension provides information only and does not constitute a final assessment.<br>Adora does not guarantee the accuracy or completeness of the information and is not responsible for purchasing decisions. It is recommended to independently verify the seller and the transaction details before any purchase.',
      dropshipInfo: 'Dropshipping is a selling model where the seller doesn\'t keep stock: they list a product, and after you buy, a third-party supplier ships it. The same item is often sold at a much higher price than the original source price (e.g., on AliExpress/Temu), so it\'s smart to compare prices and check reviews and delivery times.',
      reportCta: 'Want to report a site and help other consumers?',
      reportTitle: 'Report a dropshipping site',
      reportSiteLabel: 'Site URL',
      reportCheaperLabel: 'Cheaper product link',
      reportCopyCurrent: 'Use current site',
      reportSubmit: 'Submit report',
      reportSuccess: 'Report submitted - thank you!',
      reportError: 'Failed to submit report',
      reportLimit: 'Daily report limit reached',
      reportSignInRequired: 'Sign in to report sites',
      reportRemaining: 'reports left today',
      dropshipInfoTitle: 'What is dropshipping?',
      eduIntro: 'Adora automatically checks shopping sites you visit and alerts you if they appear to be dropshipping - selling products at inflated prices from suppliers like AliExpress or Temu.<br>When a site is flagged, we automatically search for the same product at a lower price so you always get the best deal.',
      whatIsAdora: 'What is Adora?',
      contactUs: 'Contact Us',
    },
    he: {
      signIn: '\u05D4\u05EA\u05D7\u05D1\u05E8\u05D5\u05EA \u05E2\u05DD Google', signOut: '\u05D4\u05EA\u05E0\u05EA\u05E7',
      analyzing: '\u05DE\u05E0\u05EA\u05D7 \u05D0\u05EA\u05E8...', noConcerns: '\u05DC\u05D0 \u05E0\u05DE\u05E6\u05D0\u05D5 \u05D4\u05EA\u05E8\u05D0\u05D5\u05EA \u05DC\u05D0\u05EA\u05E8 \u05D6\u05D4',
      potentialDropship: '\u05E9\u05D9\u05DD \u05DC\u05D1 - \u05D9\u05D9\u05EA\u05DB\u05DF \u05E9\u05D6\u05D4\u05D5 \u05D0\u05EA\u05E8 \u05D3\u05E8\u05D5\u05E4\u05E9\u05D9\u05E4\u05D9\u05E0\u05D2',
      noMatches: '\u05D0\u05E0\u05D7\u05E0\u05D5 \u05DE\u05D7\u05E4\u05E9\u05D9\u05DD \u05E2\u05D1\u05D5\u05E8\u05DA \u05D0\u05E4\u05E9\u05E8\u05D5\u05D9\u05D5\u05EA \u05D6\u05D5\u05DC\u05D5\u05EA \u05D9\u05D5\u05EA\u05E8 - \u05D1\u05D3\u05E7\u05D5 \u05E9\u05D5\u05D1 \u05D1\u05E7\u05E8\u05D5\u05D1.',
      cheaperElsewhere: '\u05DE\u05E6\u05D0\u05E0\u05D5 \u05D0\u05E4\u05E9\u05E8\u05D5\u05D9\u05D5\u05EA \u05D6\u05D5\u05DC\u05D5\u05EA \u05D9\u05D5\u05EA\u05E8 \u05D1\u05E9\u05D1\u05D9\u05DC\u05DA',
      thisSite: '\u05D0\u05EA\u05E8 \u05D6\u05D4', markup: '\u05D9\u05E7\u05E8 \u05D9\u05D5\u05EA\u05E8', view: '\u05E6\u05E4\u05D4',
      viewArrow: '\u2190', showMore: '\u05D4\u05E6\u05D2 \u05E2\u05D5\u05D3 \u05EA\u05D5\u05E6\u05D0\u05D5\u05EA', showLess: '\u05D4\u05E6\u05D2 \u05E4\u05D7\u05D5\u05EA',
      disclaimer: '\u05D4\u05EA\u05D5\u05E1\u05E3 \u05DE\u05E1\u05E4\u05E7 \u05DE\u05D9\u05D3\u05E2 \u05D1\u05DC\u05D1\u05D3 \u05D5\u05D0\u05D9\u05E0\u05D5 \u05DE\u05D4\u05D5\u05D5\u05D4 \u05D4\u05E2\u05E8\u05DB\u05D4 \u05E1\u05D5\u05E4\u05D9\u05EA.<br>Adora \u05D0\u05D9\u05E0\u05D4 \u05DE\u05EA\u05D7\u05D9\u05D9\u05D1\u05EA \u05DC\u05D3\u05D9\u05D5\u05E7 \u05D0\u05D5 \u05DC\u05E9\u05DC\u05DE\u05D5\u05EA \u05D4\u05DE\u05D9\u05D3\u05E2 \u05D5\u05D0\u05D9\u05E0\u05D4 \u05D0\u05D7\u05E8\u05D0\u05D9\u05EA \u05DC\u05D4\u05D7\u05DC\u05D8\u05D5\u05EA \u05E8\u05DB\u05D9\u05E9\u05D4. \u05DE\u05D5\u05DE\u05DC\u05E5 \u05DC\u05D1\u05D3\u05D5\u05E7 \u05D0\u05EA \u05D4\u05DE\u05D5\u05DB\u05E8 \u05D5\u05D0\u05EA \u05E4\u05E8\u05D8\u05D9 \u05D4\u05E2\u05E1\u05E7\u05D4 \u05D1\u05D0\u05D5\u05E4\u05DF \u05E2\u05E6\u05DE\u05D0\u05D9 \u05DC\u05E4\u05E0\u05D9 \u05DB\u05DC \u05E8\u05DB\u05D9\u05E9\u05D4.',
      dropshipInfo: '\u05D3\u05E8\u05D5\u05E4\u05E9\u05D9\u05E4\u05D9\u05E0\u05D2 (Dropshipping) \u05D4\u05D5\u05D0 \u05DE\u05D5\u05D3\u05DC \u05DE\u05DB\u05D9\u05E8\u05D4 \u05E9\u05D1\u05D5 \u05D4\u05DE\u05D5\u05DB\u05E8 \u05DC\u05D0 \u05DE\u05D7\u05D6\u05D9\u05E7 \u05DE\u05DC\u05D0\u05D9: \u05D4\u05D5\u05D0 \u05DE\u05E4\u05E8\u05E1\u05DD \u05DE\u05D5\u05E6\u05E8, \u05D5\u05DB\u05E9\u05D0\u05EA\u05DD \u05E7\u05D5\u05E0\u05D9\u05DD - \u05E1\u05E4\u05E7 \u05E6\u05D3-\u05E9\u05DC\u05D9\u05E9\u05D9 \u05E9\u05D5\u05DC\u05D7 \u05D0\u05D5\u05EA\u05D5 \u05D9\u05E9\u05D9\u05E8\u05D5\u05EA. \u05DC\u05E2\u05D9\u05EA\u05D9\u05DD \u05D0\u05D5\u05EA\u05D5 \u05DE\u05D5\u05E6\u05E8 \u05E0\u05DE\u05DB\u05E8 \u05DB\u05D0\u05DF \u05D1\u05DE\u05D7\u05D9\u05E8 \u05D2\u05D1\u05D5\u05D4 \u05DE\u05E9\u05DE\u05E2\u05D5\u05EA\u05D9\u05EA \u05DE\u05D4\u05DE\u05D7\u05D9\u05E8 \u05D4\u05DE\u05E7\u05D5\u05E8\u05D9 (\u05DC\u05DE\u05E9\u05DC \u05D1\u05D0\u05EA\u05E8\u05D9\u05DD \u05DB\u05DE\u05D5 AliExpress/Temu), \u05D5\u05DC\u05DB\u05DF \u05DE\u05D5\u05DE\u05DC\u05E5 \u05DC\u05D4\u05E9\u05D5\u05D5\u05EA \u05DE\u05D7\u05D9\u05E8\u05D9\u05DD \u05D5\u05DC\u05D1\u05D3\u05D5\u05E7 \u05D1\u05D9\u05E7\u05D5\u05E8\u05D5\u05EA \u05D5\u05D6\u05DE\u05E0\u05D9 \u05DE\u05E9\u05DC\u05D5\u05D7.',
      reportCta: '\u05E8\u05D5\u05E6\u05D4 \u05DC\u05D3\u05D5\u05D5\u05D7 \u05E2\u05DC \u05D0\u05EA\u05E8 \u05D5\u05DC\u05E2\u05D6\u05D5\u05E8 \u05DC\u05E6\u05E8\u05DB\u05E0\u05D9\u05DD \u05D0\u05D7\u05E8\u05D9\u05DD?',
      reportTitle: '\u05D3\u05D9\u05D5\u05D5\u05D7 \u05E2\u05DC \u05D0\u05EA\u05E8 \u05D3\u05E8\u05D5\u05E4\u05E9\u05D9\u05E4\u05D9\u05E0\u05D2',
      reportSiteLabel: '\u05DB\u05EA\u05D5\u05D1\u05EA \u05D4\u05D0\u05EA\u05E8',
      reportCheaperLabel: '\u05E7\u05D9\u05E9\u05D5\u05E8 \u05DC\u05DE\u05D5\u05E6\u05E8 \u05D6\u05D5\u05DC \u05D9\u05D5\u05EA\u05E8',
      reportCopyCurrent: '\u05D4\u05E9\u05EA\u05DE\u05E9 \u05D1\u05D0\u05EA\u05E8 \u05D4\u05E0\u05D5\u05DB\u05D7\u05D9',
      reportSubmit: '\u05E9\u05DC\u05D7 \u05D3\u05D9\u05D5\u05D5\u05D7',
      reportSuccess: '\u05D4\u05D3\u05D9\u05D5\u05D5\u05D7 \u05E0\u05E9\u05DC\u05D7 - \u05EA\u05D5\u05D3\u05D4!',
      reportError: '\u05E9\u05DC\u05D9\u05D7\u05EA \u05D4\u05D3\u05D9\u05D5\u05D5\u05D7 \u05E0\u05DB\u05E9\u05DC\u05D4',
      reportLimit: '\u05D4\u05D2\u05E2\u05EA \u05DC\u05DE\u05D2\u05D1\u05DC\u05EA \u05D4\u05D3\u05D9\u05D5\u05D5\u05D7\u05D9\u05DD \u05D4\u05D9\u05D5\u05DE\u05D9\u05EA',
      reportSignInRequired: '\u05D4\u05EA\u05D7\u05D1\u05E8 \u05DB\u05D3\u05D9 \u05DC\u05D3\u05D5\u05D5\u05D7 \u05E2\u05DC \u05D0\u05EA\u05E8\u05D9\u05DD',
      reportRemaining: '\u05D3\u05D9\u05D5\u05D5\u05D7\u05D9\u05DD \u05E0\u05D5\u05EA\u05E8\u05D5 \u05D4\u05D9\u05D5\u05DD',
      dropshipInfoTitle: '\u05DE\u05D4 \u05D6\u05D4 \u05D3\u05E8\u05D5\u05E4\u05E9\u05D9\u05E4\u05D9\u05E0\u05D2?',
      eduIntro: 'Adora \u05D1\u05D5\u05D3\u05E7\u05EA \u05D0\u05D5\u05D8\u05D5\u05DE\u05D8\u05D9\u05EA \u05D0\u05EA\u05E8\u05D9 \u05E7\u05E0\u05D9\u05D5\u05EA \u05E9\u05D0\u05EA\u05DD \u05DE\u05D1\u05E7\u05E8\u05D9\u05DD \u05D1\u05D4\u05DD \u05D5\u05DE\u05EA\u05E8\u05D9\u05E2\u05D4 \u05D0\u05DD \u05D4\u05DD \u05E0\u05E8\u05D0\u05D9\u05DD \u05DB\u05D0\u05EA\u05E8\u05D9 \u05D3\u05E8\u05D5\u05E4\u05E9\u05D9\u05E4\u05D9\u05E0\u05D2 - \u05E9\u05DE\u05D5\u05DB\u05E8\u05D9\u05DD \u05DE\u05D5\u05E6\u05E8\u05D9\u05DD \u05D1\u05DE\u05D7\u05D9\u05E8\u05D9\u05DD \u05DE\u05E0\u05D5\u05E4\u05D7\u05D9\u05DD \u05DE\u05E1\u05E4\u05E7\u05D9\u05DD \u05DB\u05DE\u05D5 AliExpress \u05D0\u05D5 Temu.<br>\u05DB\u05E9\u05D0\u05EA\u05E8 \u05DE\u05E1\u05D5\u05DE\u05DF, \u05D0\u05E0\u05D7\u05E0\u05D5 \u05DE\u05D7\u05E4\u05E9\u05D9\u05DD \u05D0\u05D5\u05D8\u05D5\u05DE\u05D8\u05D9\u05EA \u05D0\u05EA \u05D0\u05D5\u05EA\u05D5 \u05DE\u05D5\u05E6\u05E8 \u05D1\u05DE\u05D7\u05D9\u05E8 \u05D6\u05D5\u05DC \u05D9\u05D5\u05EA\u05E8 \u05DB\u05D3\u05D9 \u05E9\u05EA\u05DE\u05D9\u05D3 \u05EA\u05E7\u05D1\u05DC\u05D5 \u05D0\u05EA \u05D4\u05E2\u05E1\u05E7\u05D4 \u05D4\u05D8\u05D5\u05D1\u05D4 \u05D1\u05D9\u05D5\u05EA\u05E8.',
      whatIsAdora: '\u05DE\u05D4 \u05D6\u05D4 Adora?',
      contactUs: '\u05E6\u05D5\u05E8 \u05E7\u05E9\u05E8',
    }
  };

  // ====== Helpers ======
  const ILS_PER_USD = 1 / 0.27;

  function normalizeProductName(name) {
    return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function isDuplicate(norm, seen) {
    for (const s of seen) {
      if (norm.includes(s) || s.includes(norm)) return true;
    }
    return false;
  }

  function normalizeSource(source, url) {
    const combined = ((source || '') + ' ' + (url || '')).toLowerCase();
    if (combined.includes('aliexpress')) return 'AliExpress';
    if (combined.includes('temu')) return 'Temu';
    if (combined.includes('alibaba')) return 'Alibaba';
    if (combined.includes('amazon')) return 'Amazon';
    if (combined.includes('walmart')) return 'Walmart';
    if (combined.includes('ebay')) return 'eBay';
    return source || 'Unknown';
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

  function getPriceMatches(result) {
    if (!result?.price_matches?.length) return [];
    const seen = [], items = [];
    const sorted = result.price_matches.slice().sort((a, b) => (b.price_ils || 0) - (a.price_ils || 0));
    for (const entry of sorted) {
      if (!entry.matches?.length) continue;
      const norm = normalizeProductName(entry.product_name_english);
      if (isDuplicate(norm, seen)) continue;
      seen.push(norm);
      const bySource = {};
      const siteIls = entry.price_ils || 0;
      for (const m of entry.matches.filter(m => m.price_usd > 0)) {
        const matchIls = Math.round(m.price_usd * ILS_PER_USD);
        if (siteIls > 0 && matchIls >= siteIls) continue;
        const src = normalizeSource(m.source, m.url);
        if (!bySource[src] || m.price_usd < bySource[src].price_usd) bySource[src] = m;
      }
      const sources = Object.entries(bySource).sort((a, b) => a[1].price_usd - b[1].price_usd).slice(0, 3);
      if (!sources.length) continue;
      const name = entry.product_name_english || 'Product';
      const cheapest = sources[0][1];
      const cheapIls = Math.round(cheapest.price_usd * ILS_PER_USD);
      const markup = siteIls > 0 && cheapIls > 0 ? (siteIls / cheapIls).toFixed(1) : null;
      items.push({
        name, sitePrice: entry.price_ils, markup,
        sources: sources.map(([src, m]) => ({
          source: src, price: Math.round(m.price_usd * ILS_PER_USD),
          url: getProductUrl(src, name, m.url),
        })),
      });
    }
    return items;
  }

  // ====== State ======
  let hostEl = null, shadow = null, widgetContainer = null;
  let expanded = true;
  let siteResult = null, siteIsRisky = false;
  let curTheme = 'light', curLang = 'en';
  let authUser = null, authLoading = false;
  let reportRemaining = null; // { remaining: int, limit: int }
  let widgetPos = null; // { x, y }
  const logoUrl = chrome.runtime.getURL('icons/icon48.png');

  // ====== CSS ======
  function buildCSS(t) {
    const isRtl = curLang === 'he';
    return `
      * { margin: 0; padding: 0; box-sizing: border-box; }
      :host {
        position: fixed !important;
        z-index: 2147483647 !important;
        display: block !important;
        width: 420px !important;
        top: 16px;
        right: 16px;
        margin: 0 !important;
        padding: 0 !important;
        border: none !important;
        background: none !important;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .adora-widget {
        position: relative;
        width: 100%; max-height: 80vh;
        border-radius: 14px;
        background: ${t.bg};
        color: ${t.text}; -webkit-user-select: text; user-select: text;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px; line-height: 1.5;
        box-shadow: ${t.shadow};
        border: 1px solid ${t.border};
        overflow: hidden;
        direction: ${isRtl ? 'rtl' : 'ltr'};
      }
      .adora-widget.minimized {
        width: auto; max-height: none; border-radius: 24px;
        cursor: pointer;
      }

      /* Header */
      .header {
        display: flex; align-items: center; gap: 8px;
        padding: 12px 14px;
        background: ${t.bgCard};
        border-bottom: 1px solid ${t.border};
        cursor: grab; user-select: none;
      }
      .header:active { cursor: grabbing; }
      .header-logo { width: 32px; height: 32px; border-radius: 6px; }
      .header-title { font-size: 15px; font-weight: 700; color: ${t.text}; letter-spacing: -0.3px; }
      .header-spacer { flex: 1; }
      .header-btn {
        width: 26px; height: 26px; border: 1px solid ${t.border}; border-radius: 50%;
        background: ${t.bg}; color: ${t.textSec}; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        font-size: 11px; font-weight: 600; transition: all 0.15s; padding: 0;
      }
      .header-btn:hover { background: ${t.bgHover}; border-color: ${t.accent}; color: ${t.accent}; }
      .header-btn.theme { font-size: 13px; font-weight: 400; }
      .header-btn.close:hover { border-color: ${t.danger}; color: ${t.danger}; }

      /* Body */
      .body { padding: 14px; overflow-y: auto; max-height: calc(80vh - 50px); -webkit-user-select: text; user-select: text; cursor: text; }

      /* Auth */
      .auth-bar {
        display: flex; align-items: center; justify-content: space-between;
        padding: 8px 12px; margin-bottom: 12px;
        background: ${t.bgCard}; border: 1px solid ${t.border};
        border-radius: 10px;
      }
      .auth-user { display: flex; align-items: center; gap: 8px; }
      .auth-avatar {
        width: 24px; height: 24px; border-radius: 50%;
        border: 2px solid ${t.accentLight};
      }
      .auth-name { font-size: 12px; font-weight: 500; color: ${t.text}; }
      .auth-logout-btn {
        background: none; border: none; color: ${t.textMuted};
        font-size: 11px; cursor: pointer; padding: 3px 6px; border-radius: 4px;
        transition: all 0.15s;
      }
      .auth-logout-btn:hover { color: ${t.danger}; background: ${t.dangerBg}; }
      .auth-signin-btn {
        display: flex; align-items: center; gap: 8px; width: 100%;
        justify-content: center;
        background: ${t.accent}; border: none; color: ${t.accentText};
        font-size: 12px; font-weight: 500; padding: 9px 14px;
        border-radius: 10px; cursor: pointer; transition: all 0.15s; margin-bottom: 12px;
      }
      .auth-signin-btn:hover { background: ${t.accentHover}; }
      .auth-signin-btn:disabled { opacity: 0.5; cursor: default; }

      /* Login screen */
      .login-screen { display: flex; flex-direction: column; align-items: center; padding: 24px 16px; gap: 12px; }
      .login-logo { width: 48px; height: 48px; border-radius: 12px; margin-bottom: 8px; }
      .login-btn {
        display: flex; align-items: center; gap: 8px; width: 100%; justify-content: center;
        font-size: 13px; font-weight: 500; padding: 10px 14px; border-radius: 10px;
        cursor: pointer; transition: all 0.15s; border: 1px solid ${t.border};
      }
      .login-btn.primary { background: ${t.accent}; border-color: ${t.accent}; color: ${t.accentText}; }
      .login-btn.primary:hover { background: ${t.accentHover}; }
      .login-btn.primary:disabled { opacity: 0.5; cursor: default; }
      .login-btn.secondary { background: ${t.bgCard}; color: ${t.text}; }
      .login-btn.secondary:hover { border-color: ${t.accent}; background: ${t.accentLight}; }

      /* Status */
      .status {
        display: flex; align-items: center; gap: 10px;
        padding: 14px; border-radius: 10px;
        background: ${t.safeBg}; border: 1px solid ${t.border};
        border-inline-start: 4px solid ${t.safe};
      }
      .status-icon { font-size: 20px; color: ${t.safe}; }
      .status-text { font-size: 13px; font-weight: 500; color: ${t.safeText}; }

      /* Alert */
      .alert {
        background: ${t.bgCard}; border: 1px solid ${t.dangerBorder};
        border-inline-start: 4px solid ${t.danger}; border-radius: 10px; padding: 14px;
      }
      .alert-header {
        display: flex; align-items: center; gap: 8px;
        margin-bottom: 12px; padding-bottom: 10px;
        border-bottom: 1px solid ${t.border};
      }
      .alert-icon { font-size: 20px; }
      .alert-title { font-size: 14px; font-weight: 600; color: ${t.danger}; }
      .info-icon {
        display: inline-flex; align-items: center; justify-content: center;
        width: 15px; height: 15px; border-radius: 50%; font-size: 10px; font-weight: 700;
        background: ${t.dangerBg}; color: ${t.danger}; border: 1px solid ${t.danger};
        cursor: pointer; margin-inline-start: 4px; flex-shrink: 0;
        font-style: normal;
      }
      .info-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center;
        z-index: 9999;
      }
      .info-overlay-inline {
        position: absolute; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center;
        z-index: 9999; border-radius: 12px;
      }
      .info-modal {
        position: relative;
        background: ${t.bg}; color: ${t.text}; border: 1px solid ${t.border};
        border-radius: 10px; padding: 18px; padding-top: 40px; margin: 12px;
        font-size: 13px; line-height: 1.6; max-width: 380px; width: 90%;
        text-align: ${isRtl ? 'right' : 'left'}; direction: ${isRtl ? 'rtl' : 'ltr'};
      }
      .info-modal-close {
        position: absolute; top: 10px; right: 12px;
        width: 24px; height: 24px; border-radius: 50%; border: 1px solid ${t.border};
        background: ${t.bg}; color: ${t.textSec}; font-size: 12px; cursor: pointer;
        display: flex; align-items: center; justify-content: center; padding: 0;
      }
      .info-modal-close:hover { border-color: ${t.danger}; color: ${t.danger}; }
      .no-matches {
        font-size: 12px; color: ${t.textSec}; background: ${t.dangerBg};
        padding: 8px 10px; border-radius: 6px; margin-bottom: 12px; line-height: 1.5;
      }

      /* Price matches */
      .pm-section { margin-bottom: 12px; }
      .pm-title { display: block; margin-bottom: 8px; color: ${t.accent}; font-size: 13px; font-weight: 600; }
      .pm-card {
        background: ${t.bgSec}; border: 1px solid ${t.border};
        border-inline-start: 4px solid ${t.accent}; border-radius: 6px;
        padding: 10px 12px; margin-bottom: 6px;
      }
      .pm-name {
        font-weight: 700; font-size: 12px; color: ${t.text};
        margin-bottom: 4px;
      }
      .pm-row {
        display: flex; align-items: center;
        font-size: 12px; margin-bottom: 2px; color: ${t.textSec}; gap: 6px;
      }
      .pm-source { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .pm-price { color: ${t.text}; font-size: 13px; margin-inline-start: auto; white-space: nowrap; }
      .markup-badge {
        background: ${t.accent}; color: #fff; padding: 1px 6px;
        border-radius: 8px; font-size: 11px; font-weight: 600; white-space: nowrap; flex-shrink: 0;
      }
      .pm-link {
        color: ${t.link}; text-decoration: none; font-weight: 500;
        font-size: 11px; white-space: nowrap; flex-shrink: 0;
      }
      .pm-link:hover { text-decoration: underline; }
      .pm-more-cards { display: none; }
      .pm-more-cards.open { display: block; }
      .pm-toggle {
        display: block; width: 100%; padding: 6px 0; margin: 4px 0 8px;
        background: none; border: 1px solid ${t.border}; border-radius: 6px;
        color: ${t.accent}; font-size: 12px; font-weight: 500; cursor: pointer;
        text-align: center;
      }
      .pm-toggle:hover { background: ${t.accentLight}; }

      /* Disclaimer */
      .disclaimer {
        font-size: 10px; color: ${t.textMuted}; background: ${t.bgSec};
        padding: 8px 10px; border-radius: 6px; line-height: 1.5;
      }

      /* Report CTA */
      .report-cta {
        display: flex; align-items: center; justify-content: space-between;
        padding: 12px 14px; margin-top: 0; cursor: pointer;
        background: ${t.bgCard}; border: 1px solid ${t.border}; border-radius: 10px;
        transition: all 0.15s;
      }
      .report-cta:hover { border-color: ${t.accent}; background: ${t.accentLight}; }
      .report-cta.disabled { opacity: 0.5; cursor: default; }
      .report-cta.disabled:hover { border-color: ${t.border}; background: ${t.bgCard}; }
      .report-cta-text { font-size: 12px; font-weight: 500; color: ${t.accent}; }
      .report-remaining { font-size: 11px; color: ${t.textMuted}; white-space: nowrap; margin-inline-start: 8px; }
      .report-hint { font-size: 11px; color: ${t.textMuted}; text-align: center; padding: 12px 0; }

      /* Education card */
      .edu-card { background: ${t.bgSec}; border: 1px solid ${t.border}; border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; }
      .edu-toggle {
        display: flex; align-items: center; gap: 6px; margin-top: 10px;
        cursor: pointer; background: none; border: none; padding: 0; width: 100%;
        text-align: ${isRtl ? 'right' : 'left'};
      }
      .edu-toggle:hover .edu-title { color: ${t.accent}; }
      .edu-icon { font-size: 16px; }
      .edu-title { font-size: 13px; font-weight: 600; color: ${t.text}; transition: color 0.15s; }
      .edu-arrow { font-size: 10px; color: ${t.textMuted}; margin-inline-start: auto; transition: transform 0.2s; }
      .edu-arrow.open { transform: rotate(180deg); }
      .edu-detail { display: none; margin-top: 6px; }
      .edu-detail.open { display: block; }
      .edu-body { font-size: 12px; line-height: 1.5; color: ${t.textSec}; }

      /* Report modal (inside info-overlay) */
      .report-title { font-size: 14px; font-weight: 600; color: ${t.text}; margin-bottom: 12px; display: block; }
      .report-label { font-size: 11px; color: ${t.textSec}; margin-bottom: 4px; display: block; }
      .report-input {
        width: 100%; padding: 7px 10px; font-size: 12px; border-radius: 6px;
        border: 1px solid ${t.border}; background: ${t.bg}; color: ${t.text};
        outline: none; margin-bottom: 8px; direction: ltr; text-align: left;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .report-input:focus { border-color: ${t.accent}; }
      .report-input::placeholder { color: ${t.textMuted}; }
      .report-copy-btn {
        background: none; border: 1px solid ${t.border}; border-radius: 4px;
        color: ${t.accent}; font-size: 10px; padding: 2px 8px; cursor: pointer;
        margin-bottom: 8px;
      }
      .report-copy-btn:hover { background: ${t.accentLight}; }
      .report-btn {
        display: block; width: 100%; padding: 8px; border: none; border-radius: 8px;
        background: ${t.accent}; color: ${t.accentText}; font-size: 12px;
        font-weight: 600; cursor: pointer; transition: all 0.15s;
      }
      .report-btn:hover { background: ${t.accentHover}; }
      .report-btn:disabled { opacity: 0.5; cursor: default; }
      .report-msg { font-size: 11px; margin-top: 6px; padding: 6px 8px; border-radius: 6px; }
      .report-msg.success { color: ${t.safeText}; background: ${t.safeBg}; }
      .report-msg.error { color: ${t.dangerText}; background: ${t.dangerBg}; }

      /* Minimized pill */
      .pill {
        display: flex; align-items: center; gap: 6px;
        padding: 8px 14px;
        background: ${t.bgCard};
        font-size: 12px; font-weight: 600; color: ${t.text};
        user-select: none; cursor: pointer;
      }
      .pill-logo { width: 18px; height: 18px; border-radius: 3px; }
      .pill-arrow { font-size: 10px; color: ${t.textMuted}; }

      /* Loading */
      .spinner {
        width: 24px; height: 24px; border: 3px solid ${t.border};
        border-top-color: ${t.accent}; border-radius: 50%;
        margin: 0 auto 8px; animation: spin 0.8s linear infinite;
      }
      @keyframes spin { to { transform: rotate(360deg); } }
      .loading { text-align: center; padding: 20px 0; }
      .loading-text { color: ${t.textMuted}; font-size: 12px; }
    `;
  }

  // ====== Build HTML ======
  function buildExpandedHTML() {
    const t = themes[curTheme] || themes.light;
    const l = i18n[curLang] || i18n.en;
    let html = '';

    // Header
    html += `<div class="header" id="adora-header">
      <img src="${logoUrl}" alt="" class="header-logo">
      <span class="header-title">Adora</span>
      <span class="header-spacer"></span>
      <button class="header-btn lang" id="adora-lang">${curLang === 'en' ? '\u05E2\u05D1' : 'EN'}</button>
      <button class="header-btn theme" id="adora-theme">${curTheme === 'light' ? '\u263D' : '\u2600'}</button>
      <button class="header-btn" id="adora-min" title="Minimize">\u2500</button>
      <button class="header-btn close" id="adora-close" title="Close">\u2715</button>
    </div>`;

    // Body
    html += '<div class="body">';

    // Login screen — blocks all content when not signed in
    if (!authUser) {
      html += `<div class="login-screen">
        <button class="login-btn primary" id="adora-signin" ${authLoading ? 'disabled' : ''}>
          <svg viewBox="0 0 24 24" width="14" height="14"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
          ${l.signIn}
        </button>
        <button class="login-btn secondary" id="adora-about">${l.whatIsAdora}</button>
        <button class="login-btn secondary" id="adora-contact">${l.contactUs}</button>
      </div>`;
      html += '</div>';
      return html;
    }

    // Auth bar (signed in)
    html += `<div class="auth-bar">
      <div class="auth-user">
        ${authUser.avatar_url ? `<img src="${authUser.avatar_url}" alt="" class="auth-avatar" referrerpolicy="no-referrer">` : ''}
        <span class="auth-name">${authUser.display_name || ''}</span>
      </div>
      <button class="auth-logout-btn" id="adora-signout">${l.signOut}</button>
    </div>`;

    // Result
    if (!siteResult) {
      html += `<div class="loading"><div class="spinner"></div><div class="loading-text">${l.analyzing}</div></div>`;
    } else if (siteIsRisky) {
      html += `<div class="alert">
        <div class="alert-header">
          <span class="alert-icon">\u26A0\uFE0F</span>
          <span class="alert-title">${l.potentialDropship} <i class="info-icon" id="dropship-info-btn">i</i></span>
        </div>`;

      const matches = getPriceMatches(siteResult);
      if (matches.length === 0) {
        html += `<div class="no-matches">${l.noMatches}</div>`;
      } else {
        html += `<div class="pm-section"><strong class="pm-title">${l.cheaperElsewhere}</strong>`;
        const buildCard = (pm) => {
          let c = `<div class="pm-card">
            <div class="pm-name">${pm.name}</div>
            <div class="pm-row">
              <span class="pm-source">${l.thisSite}:</span>
              <strong class="pm-price">${pm.sitePrice > 0 ? '\u20AA' + pm.sitePrice : '?'}</strong>
              ${pm.markup ? `<span class="markup-badge">${pm.markup}x ${l.markup}</span>` : ''}
            </div>`;
          for (const s of pm.sources) {
            c += `<div class="pm-row">
              <span class="pm-source">${s.source}:</span>
              <strong class="pm-price">\u20AA${s.price}</strong>
              <a href="${s.url}" target="_blank" rel="noopener" class="pm-link">${l.view} ${l.viewArrow}</a>
            </div>`;
          }
          return c + '</div>';
        };
        for (let i = 0; i < Math.min(3, matches.length); i++) html += buildCard(matches[i]);
        if (matches.length > 3) {
          html += `<div class="pm-more-cards" id="pm-more">`;
          for (let i = 3; i < matches.length; i++) html += buildCard(matches[i]);
          html += '</div>';
          html += `<button class="pm-toggle" id="pm-toggle">${l.showMore} (${matches.length - 3})</button>`;
        }
        html += '</div>';
      }

      html += `<div class="disclaimer">${l.disclaimer}</div>`;
      html += '</div>'; // .alert
    } else {
      html += `<div class="edu-card">
        <div class="edu-body">${l.eduIntro}</div>
        <button class="edu-toggle" id="edu-toggle">
          <span class="edu-icon">\uD83D\uDCA1</span>
          <span class="edu-title">${l.dropshipInfoTitle}</span>
          <span class="edu-arrow" id="edu-arrow">\u25BC</span>
        </button>
        <div class="edu-detail" id="edu-detail">
          <div class="edu-body">${l.dropshipInfo}</div>
        </div>
      </div>`;
      if (authUser) {
        const rem = reportRemaining ? reportRemaining.remaining : 3;
        const lim = reportRemaining ? reportRemaining.limit : 3;
        html += `<div class="report-cta${rem <= 0 ? ' disabled' : ''}" id="report-cta">
          <span class="report-cta-text">${rem <= 0 ? l.reportLimit : l.reportCta}</span>
          <span class="report-remaining">${rem}/${lim} ${l.reportRemaining}</span>
        </div>`;
      } else {
        html += `<div class="report-hint">${l.reportSignInRequired}</div>`;
      }
    }

    html += '</div>'; // .body
    return html;
  }

  function buildMinimizedHTML() {
    const icon = siteIsRisky ? '\u26A0\uFE0F' : '\uD83D\uDD0D';
    return `<div class="pill" id="adora-pill">
      <span>${icon}</span>
      <img src="${logoUrl}" alt="" class="pill-logo">
      <span>Adora</span>
      <span class="pill-arrow">\u25B2</span>
    </div>`;
  }

  // ====== Render ======
  function render() {
    if (!shadow || !widgetContainer) return;

    // Update CSS
    const style = shadow.querySelector('style');
    if (style) style.textContent = buildCSS(themes[curTheme] || themes.light);

    if (expanded) {
      widgetContainer.classList.remove('minimized');
      widgetContainer.innerHTML = buildExpandedHTML();
      bindExpandedEvents();
    } else {
      widgetContainer.classList.add('minimized');
      widgetContainer.innerHTML = buildMinimizedHTML();
      bindMinimizedEvents();
    }
  }

  // ====== Event Binding ======
  function bindExpandedEvents() {
    const header = shadow.getElementById('adora-header');
    const langBtn = shadow.getElementById('adora-lang');
    const themeBtn = shadow.getElementById('adora-theme');
    const minBtn = shadow.getElementById('adora-min');
    const closeBtn = shadow.getElementById('adora-close');
    const signinBtn = shadow.getElementById('adora-signin');
    const signoutBtn = shadow.getElementById('adora-signout');

    if (header) setupDrag(header);
    if (langBtn) langBtn.onclick = toggleLang;
    if (themeBtn) themeBtn.onclick = toggleTheme;
    if (minBtn) minBtn.onclick = () => { expanded = false; render(); };
    if (closeBtn) closeBtn.onclick = hideWidget;
    if (signinBtn) signinBtn.onclick = handleSignIn;
    if (signoutBtn) signoutBtn.onclick = handleSignOut;

    // Login screen buttons
    const aboutBtn = shadow.getElementById('adora-about');
    if (aboutBtn) aboutBtn.onclick = () => {
      const l = i18n[curLang] || i18n.en;
      const overlay = document.createElement('div');
      overlay.className = 'info-overlay-inline';
      overlay.innerHTML = `<div class="info-modal">${l.eduIntro}<button class="info-modal-close">\u2715</button></div>`;
      overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
      overlay.querySelector('.info-modal-close').onclick = () => overlay.remove();
      widgetContainer.appendChild(overlay);
    };
    const contactBtn = shadow.getElementById('adora-contact');
    if (contactBtn) contactBtn.onclick = () => {
      window.open('mailto:adoraaiextension@gmail.com', '_blank');
    };

    const pmToggle = shadow.getElementById('pm-toggle');
    const pmMore = shadow.getElementById('pm-more');
    if (pmToggle && pmMore) {
      pmToggle.onclick = () => {
        const l = i18n[curLang] || i18n.en;
        const open = pmMore.classList.toggle('open');
        pmToggle.textContent = open ? l.showLess : `${l.showMore} (${pmMore.children.length})`;
      };
    }

    // Education toggle
    const eduToggle = shadow.getElementById('edu-toggle');
    if (eduToggle) {
      eduToggle.onclick = () => {
        const detail = shadow.getElementById('edu-detail');
        const arrow = shadow.getElementById('edu-arrow');
        if (detail) detail.classList.toggle('open');
        if (arrow) arrow.classList.toggle('open');
      };
    }

    // Report CTA → opens modal
    const reportCta = shadow.getElementById('report-cta');
    if (reportCta) {
      reportCta.onclick = () => {
        const l = i18n[curLang] || i18n.en;
        const rem = reportRemaining ? reportRemaining.remaining : 3;
        const lim = reportRemaining ? reportRemaining.limit : 3;
        if (rem <= 0) return;
        const overlay = document.createElement('div');
        overlay.className = 'info-overlay';
        overlay.innerHTML = `<div class="info-modal">
          <button class="info-modal-close" id="report-close">\u2715</button>
          <span class="report-title">${l.reportTitle}</span>
          <label class="report-label">${l.reportSiteLabel}</label>
          <input type="text" class="report-input" id="report-url" placeholder="https://...">
          <button class="report-copy-btn" id="report-copy">${l.reportCopyCurrent}</button>
          <label class="report-label">${l.reportCheaperLabel}</label>
          <input type="text" class="report-input" id="report-cheaper" placeholder="https://...">
          <span class="report-remaining" style="display:block;margin-bottom:8px;">${rem}/${lim} ${l.reportRemaining}</span>
          <button class="report-btn" id="report-submit">${l.reportSubmit}</button>
          <div class="report-msg" id="report-msg" style="display:none;"></div>
        </div>`;
        overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
        overlay.querySelector('#report-close').onclick = () => overlay.remove();
        overlay.querySelector('#report-copy').onclick = () => {
          const inp = overlay.querySelector('#report-url');
          if (inp) inp.value = location.href;
        };
        overlay.querySelector('#report-submit').onclick = () => {
          const urlInput = overlay.querySelector('#report-url');
          const cheaperInput = overlay.querySelector('#report-cheaper');
          const msgEl = overlay.querySelector('#report-msg');
          const submitBtn = overlay.querySelector('#report-submit');
          const url = (urlInput?.value || '').trim();
          const cheaper = (cheaperInput?.value || '').trim();
          if (!url || !url.match(/^https?:\/\//)) {
            if (msgEl) { msgEl.textContent = l.reportError; msgEl.className = 'report-msg error'; msgEl.style.display = ''; }
            return;
          }
          if (!cheaper || !cheaper.match(/^https?:\/\//)) {
            if (msgEl) { msgEl.textContent = l.reportCheaperLabel; msgEl.className = 'report-msg error'; msgEl.style.display = ''; }
            return;
          }
          submitBtn.disabled = true;
          chrome.runtime.sendMessage({
            type: 'SUBMIT_REPORT',
            data: { reported_url: url, cheaper_url: cheaper }
          }, (resp) => {
            submitBtn.disabled = false;
            if (!msgEl) return;
            if (resp?.ok) {
              msgEl.textContent = l.reportSuccess;
              msgEl.className = 'report-msg success';
              msgEl.style.display = '';
              if (resp.remaining !== undefined) {
                reportRemaining = { remaining: resp.remaining, limit: reportRemaining?.limit || 3 };
                const remSpan = overlay.querySelector('.report-remaining');
                if (remSpan) remSpan.textContent = `${resp.remaining}/${reportRemaining.limit} ${l.reportRemaining}`;
                const ctaRem = shadow.querySelector('.report-remaining');
                if (ctaRem) ctaRem.textContent = `${resp.remaining}/${reportRemaining.limit} ${l.reportRemaining}`;
              }
              setTimeout(() => overlay.remove(), 1500);
            } else if (resp?.error?.includes('429') || resp?.error?.includes('limit')) {
              msgEl.textContent = l.reportLimit;
              msgEl.className = 'report-msg error';
            } else {
              msgEl.textContent = resp?.error || l.reportError;
              msgEl.className = 'report-msg error';
            }
            msgEl.style.display = '';
          });
        };
        shadow.appendChild(overlay);
      };
    }

    const infoBtn = shadow.getElementById('dropship-info-btn');
    if (infoBtn) infoBtn.onclick = () => {
      const l = i18n[curLang];
      const overlay = document.createElement('div');
      overlay.className = 'info-overlay-inline';
      overlay.innerHTML = `<div class="info-modal">${l.dropshipInfo}<button class="info-modal-close">\u2715</button></div>`;
      overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
      overlay.querySelector('.info-modal-close').onclick = () => overlay.remove();
      widgetContainer.appendChild(overlay);
    };
  }

  function bindMinimizedEvents() {
    const pill = shadow.getElementById('adora-pill');
    if (pill) {
      pill.onclick = () => { expanded = true; render(); };
      setupDrag(pill);
    }
  }

  // ====== Drag ======
  let dragging = false, dragStartX, dragStartY, elStartX, elStartY;

  function setupDrag(handle) {
    handle.addEventListener('mousedown', (e) => {
      // Only skip if the direct click target is a button or link
      const tag = e.target.tagName;
      if (tag === 'BUTTON' || tag === 'A' || e.target.closest('button, a')) return;
      dragging = true;
      dragStartX = e.clientX;
      dragStartY = e.clientY;
      const rect = hostEl.getBoundingClientRect();
      elStartX = rect.left;
      elStartY = rect.top;
      e.preventDefault();
      e.stopPropagation();
    });
  }

  window.addEventListener('mousemove', (e) => {
    if (!dragging || !hostEl) return;
    e.preventDefault();
    let newX = elStartX + (e.clientX - dragStartX);
    let newY = elStartY + (e.clientY - dragStartY);
    const w = hostEl.offsetWidth || 360;
    const h = hostEl.offsetHeight || 100;
    newX = Math.max(0, Math.min(window.innerWidth - w, newX));
    newY = Math.max(0, Math.min(window.innerHeight - h, newY));
    hostEl.style.setProperty('left', newX + 'px', 'important');
    hostEl.style.setProperty('top', newY + 'px', 'important');
    hostEl.style.setProperty('right', 'auto', 'important');
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (hostEl) {
      const rect = hostEl.getBoundingClientRect();
      widgetPos = { x: rect.left, y: rect.top };
      chrome.storage.local.set({ adoraWidgetPos: widgetPos });
    }
  });

  // ====== Theme / Lang ======
  function toggleTheme() {
    curTheme = curTheme === 'light' ? 'dark' : 'light';
    chrome.storage.local.set({ adoraTheme: curTheme });
    render();
  }

  function toggleLang() {
    curLang = curLang === 'en' ? 'he' : 'en';
    chrome.storage.local.set({ adoraLang: curLang });
    render();
  }

  // ====== Auth ======
  function handleSignIn() {
    authLoading = true;
    render();
    chrome.runtime.sendMessage({ type: 'AUTH_GOOGLE_SIGN_IN' }, (resp) => {
      authLoading = false;
      if (resp && resp.access_token && resp.user) {
        chrome.storage.local.set({
          adoraAccessToken: resp.access_token,
          adoraUser: resp.user,
        });
        authUser = resp.user;
      }
      render();
    });
  }

  function handleSignOut() {
    chrome.runtime.sendMessage({ type: 'AUTH_LOGOUT' });
    chrome.storage.local.remove(['adoraAccessToken', 'adoraUser']);
    authUser = null;
    render();
  }

  // ====== Report Remaining ======
  function fetchReportRemaining(cb) {
    chrome.runtime.sendMessage({ type: 'GET_REPORT_REMAINING' }, (resp) => {
      if (resp?.error === 'session_expired') { authUser = null; render(); return; }
      if (resp) reportRemaining = resp;
      if (cb) cb();
    });
  }

  // ====== Widget Lifecycle ======
  function createWidget() {
    if (hostEl) return;
    hostEl = document.createElement('div');
    hostEl.id = 'adora-widget-host';
    // Position is handled by :host CSS inside shadow DOM
    // Only set saved position overrides as inline styles
    if (widgetPos) {
      hostEl.style.left = widgetPos.x + 'px';
      hostEl.style.top = widgetPos.y + 'px';
      hostEl.style.right = 'auto';
    }

    shadow = hostEl.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = buildCSS(themes[curTheme] || themes.light);
    shadow.appendChild(style);

    widgetContainer = document.createElement('div');
    widgetContainer.className = 'adora-widget';
    shadow.appendChild(widgetContainer);

    document.body.appendChild(hostEl);
    render();
  }

  function showWidget() {
    if (!hostEl) createWidget();
    if (widgetContainer) widgetContainer.style.display = '';
  }

  function hideWidget() {
    if (widgetContainer) widgetContainer.style.display = 'none';
  }

  function toggleWidget() {
    if (!hostEl || !widgetContainer || widgetContainer.style.display === 'none') {
      expanded = true;
      showWidget();
      if (!siteIsRisky && authUser) {
        fetchReportRemaining(() => render());
      } else {
        render();
      }
    } else {
      hideWidget();
    }
  }

  // ====== Init ======
  function init() {
    chrome.storage.local.get(['adoraTheme', 'adoraLang', 'adoraUser', 'adoraWidgetPos'], (r) => {
      curTheme = r.adoraTheme || 'light';
      curLang = r.adoraLang || 'en';
      authUser = r.adoraUser || null;
      widgetPos = r.adoraWidgetPos || null;

      // Check the current page
      try {
        chrome.runtime.sendMessage({ type: 'CHECK_URL', url: location.href }, (response) => {
          if (chrome.runtime.lastError) {
            console.log('[Adora] CHECK_URL error:', chrome.runtime.lastError.message);
            return;
          }
          siteResult = response;
          siteIsRisky = response && response.risky && response.score >= 0.6;

          if (siteIsRisky) {
            console.log('[Adora] Risky site — showing widget');
            expanded = true;
            showWidget();
          }
        });
      } catch (e) {
        console.log('[Adora] Init error:', e.message);
      }
    });
  }

  // Ensure body exists before init
  if (document.body) {
    init();
  } else {
    document.addEventListener('DOMContentLoaded', init);
  }

  // ====== Message Listener (icon click) ======
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'TOGGLE_WIDGET') {
      // If result not loaded yet, load it
      if (!siteResult) {
        chrome.runtime.sendMessage({ type: 'CHECK_URL', url: location.href }, (response) => {
          siteResult = response;
          siteIsRisky = response && response.risky && response.score >= 0.6;
          expanded = true;
          if (!siteIsRisky && authUser) {
            fetchReportRemaining(() => showWidget());
          } else {
            showWidget();
          }
        });
      } else {
        toggleWidget();
      }
    }
  });

  // ====== Storage change listener ======
  chrome.storage.onChanged.addListener((changes) => {
    let needRender = false;
    if (changes.adoraTheme) { curTheme = changes.adoraTheme.newValue || 'light'; needRender = true; }
    if (changes.adoraLang) { curLang = changes.adoraLang.newValue || 'en'; needRender = true; }
    if (changes.adoraUser) { authUser = changes.adoraUser.newValue || null; needRender = true; }
    if (needRender && widgetContainer && widgetContainer.style.display !== 'none') render();
  });
})();
