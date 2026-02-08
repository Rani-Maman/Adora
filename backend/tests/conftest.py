"""
Pytest fixtures for Adora backend tests.
"""
import pytest

try:
    from fastapi.testclient import TestClient
    from app.main import app

    @pytest.fixture
    def client():
        """Create a test client for the FastAPI app."""
        return TestClient(app)

except ImportError as _import_err:
    # Allow tests that don't need the full API (e.g. unit tests for scripts)
    # to run even when heavy deps like playwright aren't installed locally.
    _skip_reason = str(_import_err)

    @pytest.fixture
    def client():
        pytest.skip(f"FastAPI test client unavailable: {_skip_reason}")
