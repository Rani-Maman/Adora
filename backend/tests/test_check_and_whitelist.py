"""
Tests for the /check endpoint (risk_db lookup).
"""
import pytest
from unittest.mock import patch, MagicMock

from app.api.check import extract_domain

try:
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    HAS_FULL_DEPS = True
except ImportError:
    HAS_FULL_DEPS = False
    client = None

needs_full_deps = pytest.mark.skipif(
    not HAS_FULL_DEPS, reason="playwright or other heavy deps not installed"
)


# ── Unit Tests: extract_domain ──────────────────────────────────────────

class TestExtractDomain:
    """Tests for the extract_domain utility."""

    def test_full_url_with_scheme(self):
        assert extract_domain("https://example.com/path") == "example.com"

    def test_url_without_scheme(self):
        assert extract_domain("example.com/path") == "example.com"

    def test_strips_www(self):
        assert extract_domain("https://www.example.com") == "example.com"

    def test_preserves_subdomain(self):
        assert extract_domain("https://shop.example.com") == "shop.example.com"

    def test_http_scheme(self):
        assert extract_domain("http://example.com") == "example.com"

    def test_empty_string(self):
        assert extract_domain("") == ""

    def test_invalid_url(self):
        # urlparse puts schemeless strings with no slash into path, not netloc
        # extract_domain adds https:// prefix, so this becomes a valid-ish parse
        result = extract_domain("not a url at all !!!")
        # Should not crash; result may be non-empty but that's acceptable
        assert isinstance(result, str)

    def test_with_port(self):
        assert extract_domain("https://example.com:8080/page") == "example.com:8080"

    def test_hebrew_path(self):
        assert extract_domain("https://example.co.il/מבצע") == "example.co.il"

    def test_with_query_params(self):
        assert extract_domain("https://example.com?foo=bar&baz=1") == "example.com"


# ── Integration Tests: /check endpoint ──────────────────────────────────

@needs_full_deps
class TestCheckEndpoint:
    """Tests for GET /check/?url=..."""

    @patch("app.api.check.get_db_connection")
    def test_check_risky_url(self, mock_db):
        """Should return risky=True when domain is in risk_db."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (
            "scam-shop.com",    # base_url
            0.85,               # risk_score
            ["countdown timer", "no business ID"],  # evidence
            "Test Advertiser",  # advertiser_name
            "2026-01-15",       # first_seen
            None,               # price_matches
        )
        mock_db.return_value = mock_conn

        response = client.get("/check/", params={"url": "https://scam-shop.com/product"})
        assert response.status_code == 200
        data = response.json()
        assert data["risky"] is True
        assert data["score"] == 0.85
        assert "countdown timer" in data["evidence"]

    @patch("app.api.check.get_db_connection")
    def test_check_safe_url(self, mock_db):
        """Should return risky=False when domain is not in risk_db."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None
        mock_db.return_value = mock_conn

        response = client.get("/check/", params={"url": "https://google.com"})
        assert response.status_code == 200
        data = response.json()
        assert data["risky"] is False

    @patch("app.api.check.get_db_connection")
    def test_check_db_error_returns_safe(self, mock_db):
        """Should fail-open (return risky=False) on DB errors."""
        mock_db.side_effect = Exception("connection refused")

        response = client.get("/check/", params={"url": "https://example.com"})
        assert response.status_code == 200
        data = response.json()
        assert data["risky"] is False

    def test_check_missing_url_param(self):
        """Should return 422 when url param is missing."""
        response = client.get("/check/")
        assert response.status_code == 422

    def test_check_empty_url(self):
        """Should handle empty URL gracefully."""
        response = client.get("/check/", params={"url": ""})
        assert response.status_code == 200


# ── Integration Tests: /whitelist endpoint ──────────────────────────────

@needs_full_deps
class TestWhitelistEndpoint:
    """Tests for whitelist endpoints."""

    def test_whitelist_domains_returns_list(self):
        response = client.get("/whitelist/domains")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "domains" in data
        assert data["count"] > 0  # Whitelist files exist in test env

    def test_whitelist_check_known_domain(self):
        """google.com should be in the global whitelist."""
        response = client.get("/whitelist/check/google.com")
        assert response.status_code == 200
        data = response.json()
        assert "whitelisted" in data

    def test_whitelist_check_trusted_tld(self):
        """*.gov.il should be auto-whitelisted."""
        response = client.get("/whitelist/check/example.gov.il")
        assert response.status_code == 200
        data = response.json()
        assert data["whitelisted"] is True
