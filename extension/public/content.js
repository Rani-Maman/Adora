// Content Script - Runs on every page
// Detects when user visits a site and triggers analysis

(function () {
  // Only run once per page load
  if (window.__adoraChecked) return;
  window.__adoraChecked = true;

  // Skip non-http pages
  if (!location.href.startsWith('http')) return;

  // Safe domain check is done in background.js using 22k+ domains from whitelist files

  // Send URL to background for check
  chrome.runtime.sendMessage({
    type: 'CHECK_URL',
    url: location.href
  }, (response) => {
    if (response && response.risky && response.score >= 0.6) {
      showWarningBanner(response);
    }
  });

  // Show warning banner for risky sites
  function showWarningBanner(result) {
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
        display: flex;
        align-items: center;
        justify-content: space-between;
      ">
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="font-size: 24px;">⚠️</span>
          <div>
            <strong style="font-size: 15px;">Potential Dropship Site Detected</strong>
            <p style="margin: 4px 0 0 0; opacity: 0.9; font-size: 12px;">
              This site shows patterns commonly associated with dropshipping.
              <span style="opacity: 0.7;">Risk Score: ${(result.score * 100).toFixed(0)}%</span>
            </p>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 16px;">
          <span style="font-size: 11px; opacity: 0.8; max-width: 200px;">
            This is informational only. Adora is not liable for purchasing decisions.
          </span>
          <button id="adora-close-btn" style="
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 500;
          ">Dismiss</button>
        </div>
      </div>
    `;

    document.body.insertBefore(banner, document.body.firstChild);

    // Handle dismiss
    document.getElementById('adora-close-btn').addEventListener('click', () => {
      banner.remove();
    });

    // Add padding to body so content isn't hidden
    document.body.style.marginTop = '80px';
  }
})();
