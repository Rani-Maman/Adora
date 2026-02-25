import { useState, useEffect } from 'react'
import './App.css'

const RISK_THRESHOLD = 0.6
const ILS_PER_USD = 1 / 0.27

function normalizeProductName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

function isDuplicate(norm, seen) {
  for (const s of seen) {
    if (norm.includes(s) || s.includes(norm)) return true
  }
  return false
}

function getProductUrl(source, productName, rawUrl) {
  if (rawUrl && !rawUrl.includes('vertexaisearch.cloud.google.com')) return rawUrl
  const q = encodeURIComponent(productName)
  const s = (source || '').toLowerCase()
  if (s.includes('aliexpress')) return `https://www.aliexpress.com/w/wholesale-${q}.html`
  if (s.includes('temu')) return `https://www.temu.com/search_result.html?search_key=${q}`
  if (s.includes('alibaba')) return `https://www.alibaba.com/trade/search?SearchText=${q}`
  return `https://www.google.com/search?q=${q}+${encodeURIComponent(source)}`
}

function getPriceMatches(result) {
  if (!result?.price_matches?.length) return []
  const seen = []
  const items = []
  const sorted = result.price_matches.slice().sort((a, b) => (b.price_ils || 0) - (a.price_ils || 0))
  for (const entry of sorted) {
    if (items.length >= 3) break
    if (!entry.matches?.length) continue
    const norm = normalizeProductName(entry.product_name_english)
    if (isDuplicate(norm, seen)) continue
    seen.push(norm)
    // Get cheapest match per unique source
    const bySource = {}
    const siteIls = entry.price_ils || 0
    for (const m of entry.matches.filter(m => m.price_usd > 0)) {
      // Skip matches more expensive than the site price
      const matchIls = Math.round(m.price_usd * ILS_PER_USD)
      if (siteIls > 0 && matchIls >= siteIls) continue
      const src = m.source || 'AliExpress'
      if (!bySource[src] || m.price_usd < bySource[src].price_usd) bySource[src] = m
    }
    const sources = Object.entries(bySource).sort((a, b) => a[1].price_usd - b[1].price_usd).slice(0, 3)
    if (!sources.length) continue
    const name = entry.product_name_english || 'Product'
    const cheapest = sources[0][1]
    const cheapIls = Math.round(cheapest.price_usd * ILS_PER_USD)
    const markup = siteIls > 0 && cheapIls > 0
      ? (siteIls / cheapIls).toFixed(1) : null
    items.push({
      name,
      sitePrice: entry.price_ils,
      markup,
      sources: sources.map(([src, m]) => ({
        source: src,
        price: Math.round(m.price_usd * ILS_PER_USD),
        url: getProductUrl(src, name, m.url),
      })),
    })
  }
  return items
}

function App() {
  const [loading, setLoading] = useState(true)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [currentUrl, setCurrentUrl] = useState('') // eslint-disable-line no-unused-vars

  useEffect(() => {
    // Get current tab URL
    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      if (!tabs[0]?.url) {
        setLoading(false)
        return
      }

      const url = tabs[0].url
      setCurrentUrl(url)

      // Skip non-http pages
      if (!url.startsWith('http')) {
        setLoading(false)
        return
      }

      try {
        // Ask background.js for cached result (it handles API key + caching)
        const data = await new Promise((resolve, reject) => {
          chrome.runtime.sendMessage({ type: 'CHECK_URL', url }, (response) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message))
            else resolve(response)
          })
        })
        if (data) setResult(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    })
  }, [])

  // Only show content for risky sites
  const isRisky = result && result.risky && result.score >= RISK_THRESHOLD

  return (
    <div className="popup">
      <header className="header">
        <img src="icons/icon48.png" alt="Adora" className="header-logo" />
        <h1>Adora</h1>
        <span className="subtitle">Dropship Detector</span>
      </header>

      {loading && (
        <div className="loading">
          <div className="spinner"></div>
          <p>Analyzing site...</p>
        </div>
      )}

      {error && (
        <div className="status safe">
          <p>Unable to analyze this site</p>
        </div>
      )}

      {!loading && !error && !isRisky && (
        <div className="status safe">
          <span className="icon">✓</span>
          <p>No concerns detected</p>
        </div>
      )}

      {!loading && isRisky && (
        <div className="alert">
          <div className="alert-header">
            <span className="alert-icon">⚠️</span>
            <h2>Potential Dropship Site</h2>
          </div>

          {getPriceMatches(result).length === 0 && (
            <div className="no-matches">
              No cheaper alternatives found yet. Our system is actively scanning - check back soon.
            </div>
          )}

          {getPriceMatches(result).length > 0 && (
            <div className="price-matches">
              <strong>Found Cheaper Elsewhere</strong>
              {getPriceMatches(result).map((pm, i) => (
                <div key={i} className="price-card">
                  <div className="price-card-name">{pm.name}</div>
                  <div className="price-card-row">
                    <span>This site: <strong>{pm.sitePrice > 0 ? `₪${pm.sitePrice}` : '?'}</strong></span>
                    {pm.markup && <span className="markup-badge">{pm.markup}x markup</span>}
                  </div>
                  {pm.sources.map((s, j) => (
                    <div key={j} className="price-card-row">
                      <span>{s.source}: <strong>₪{s.price}</strong></span>
                      <a href={s.url} target="_blank" rel="noopener" className="price-link">
                        View →
                      </a>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}

          <div className="disclaimer">
            ⚖️ This is informational only and not a definitive assessment.
            Adora does not guarantee accuracy and is not liable for any
            purchasing decisions. Always verify sellers independently.
          </div>
        </div>
      )}

      <footer className="footer">
        <span className="info-trigger">ⓘ
          <span className="info-tooltip">Informational only. Adora is not liable for purchasing decisions.</span>
        </span>
      </footer>
    </div>
  )
}

export default App
