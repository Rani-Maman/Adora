"""
Tests for nightly_scrape_summary.py — report generation and email logic.

These tests exercise the pure functions (no DB or SMTP needed).
"""
import datetime
import pytest
from unittest.mock import patch, MagicMock

# Import the module under test.  It lives in backend/scripts/ which is
# outside the app package, so we import via importlib to avoid path hacks.
import importlib.util, sys, os

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "nightly_scrape_summary.py"
)
spec = importlib.util.spec_from_file_location("nss", os.path.abspath(_SCRIPT_PATH))
nss = importlib.util.module_from_spec(spec)

# Prevent the module from actually connecting to postgres on import
with patch.dict(os.environ, {
    "DB_HOST": "localhost",
    "DB_NAME": "test",
    "DB_USER": "test",
    "DB_PASSWORD": "test",
}):
    spec.loader.exec_module(nss)


# ── Unit Tests: parse_runtime ───────────────────────────────────────────

class TestParseRuntime:
    """Tests for parse_runtime() formatting."""

    def test_normal_timestamps(self):
        stats = {
            "first_scraped": "2026-02-08 00:01:02.123456",
            "last_scraped": "2026-02-08 03:40:50.654321",
        }
        start, end, dur = nss.parse_runtime(stats)
        assert start == "00:01:02"
        assert end == "03:40:50"
        assert "3h" in dur

    def test_same_timestamp(self):
        stats = {
            "first_scraped": "2026-02-08 12:00:00",
            "last_scraped": "2026-02-08 12:00:00",
        }
        start, end, dur = nss.parse_runtime(stats)
        assert start == "12:00:00"
        assert dur == "0m"

    def test_missing_timestamps(self):
        stats = {}
        start, end, dur = nss.parse_runtime(stats)
        assert start == "N/A"
        assert end == "N/A"
        assert dur == "N/A"

    def test_minutes_only(self):
        stats = {
            "first_scraped": "2026-02-08 10:00:00",
            "last_scraped": "2026-02-08 10:25:30",
        }
        _, _, dur = nss.parse_runtime(stats)
        assert "25m" in dur
        assert "h" not in dur


# ── Unit Tests: build_keyword_lines ─────────────────────────────────────

class TestBuildKeywordLines:
    """Tests for per-keyword report line generation."""

    def test_keywords_produce_hebrew(self):
        db_stats = {
            "keyword_ads": {"mivtsa": 100, "hanaha": 50},
        }
        lines = nss.build_keyword_lines(db_stats, [])
        text = "\n".join(lines)
        assert "מבצע" in text
        assert "הנחת" in text

    def test_zero_ads_gets_warning_emoji(self):
        db_stats = {"keyword_ads": {"mivtsa": 0}}
        lines = nss.build_keyword_lines(db_stats, [])
        assert any("⚠️" in line for line in lines)

    def test_positive_ads_gets_checkmark(self):
        db_stats = {"keyword_ads": {"mivtsa": 42}}
        lines = nss.build_keyword_lines(db_stats, [])
        assert any("✅" in line for line in lines)

    def test_empty_keywords(self):
        db_stats = {"keyword_ads": {}}
        lines = nss.build_keyword_lines(db_stats, [])
        assert lines == []


# ── Unit Tests: build_report ────────────────────────────────────────────

class TestBuildReport:
    """Tests for the full report builder."""

    def test_report_contains_header(self):
        stats = {
            "total_ads_today": 200,
            "new_advertisers": 50,
            "total_advertisers": 5000,
            "new_ads_with_urls": 80,
            "total_ads_with_urls": 3000,
            "keyword_ads": {"mivtsa": 100, "mugbal": 100},
        }
        report = nss.build_report(stats, [])
        assert "Facebook Ads Scrape Summary" in report
        assert "200" in report  # total ads
        assert "50" in report   # new advertisers

    def test_report_contains_db_section(self):
        stats = {
            "total_ads_today": 0,
            "new_advertisers": 0,
            "total_advertisers": 9000,
            "new_ads_with_urls": 0,
            "total_ads_with_urls": 4000,
            "keyword_ads": {},
        }
        report = nss.build_report(stats, [])
        assert "Database" in report
        assert "9000" in report


# ── Unit Tests: load_env ──────────────────────────────────────────────

class TestLoadEnv:
    """Tests for the custom .env loader."""

    def test_skips_comments_and_blanks(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nTEST_KEY_1234=hello\n")
        with patch.object(nss, "DOTENV_PATH", str(env_file)):
            # Clear the env var first
            os.environ.pop("TEST_KEY_1234", None)
            nss.load_env()
            assert os.getenv("TEST_KEY_1234") == "hello"
            # Cleanup
            os.environ.pop("TEST_KEY_1234", None)

    def test_does_not_overwrite_existing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_VAR_XYZ=fromfile\n")
        os.environ["MY_VAR_XYZ"] = "original"
        with patch.object(nss, "DOTENV_PATH", str(env_file)):
            nss.load_env()
            assert os.getenv("MY_VAR_XYZ") == "original"
            os.environ.pop("MY_VAR_XYZ", None)


# ── Unit Tests: send_email ──────────────────────────────────────────────

class TestSendEmail:
    """Tests for email sending (SMTP mocked)."""

    @patch("smtplib.SMTP")
    def test_send_email_success(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {
            "EMAIL_SENDER": "test@gmail.com",
            "EMAIL_PASSWORD": "abcd efgh ijkl mnop",
            "EMAIL_RECIPIENT": "user@example.com",
        }):
            result = nss.send_email("Test Subject", "Test body")
            assert result is True

    def test_send_email_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMAIL_SENDER", None)
            os.environ.pop("EMAIL_PASSWORD", None)
            os.environ.pop("EMAIL_RECIPIENT", None)
            result = nss.send_email("Test", "Body")
            assert result is False
