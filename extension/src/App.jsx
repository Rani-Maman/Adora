import { useState, useEffect } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const RISK_THRESHOLD = 0.6

function App() {
  const [loading, setLoading] = useState(true)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [currentUrl, setCurrentUrl] = useState('')

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
        const response = await fetch(`${API_BASE}/analyze/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url })
        })

        if (!response.ok) throw new Error('API Error')

        const data = await response.json()
        setResult(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    })
  }, [])

  // Only show content for risky sites
  const isRisky = result && result.score >= RISK_THRESHOLD

  return (
    <div className="popup">
      <header className="header">
        <h1>🛡️ Adora</h1>
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
            <div>
              <h2>Potential Dropship Site</h2>
              <span className="score">Risk Score: {(result.score * 100).toFixed(0)}%</span>
            </div>
          </div>

          <div className="reason">
            <strong>Reason:</strong> {result.reason || 'Multiple risk indicators detected'}
          </div>

          {result.evidence && result.evidence.length > 0 && (
            <div className="evidence">
              <strong>Evidence:</strong>
              <ul>
                {result.evidence.slice(0, 4).map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
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
        <a href="https://adora.ai" target="_blank" rel="noopener">Learn More</a>
      </footer>
    </div>
  )
}

export default App
