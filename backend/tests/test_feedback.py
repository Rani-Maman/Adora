"""
Tests for the /feedback endpoints.
"""

import pytest
from unittest.mock import MagicMock, patch

try:
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.feedback as feedback_api

    client = TestClient(app)
    HAS_FULL_DEPS = True
except ImportError:
    HAS_FULL_DEPS = False
    client = None
    feedback_api = None

needs_full_deps = pytest.mark.skipif(
    not HAS_FULL_DEPS, reason="playwright or other heavy deps not installed"
)


@needs_full_deps
class TestFeedbackEndpoint:
    def setup_method(self):
        app.dependency_overrides[feedback_api.require_user] = lambda: {"sub": "7", "email": "user@example.com"}

    def teardown_method(self):
        app.dependency_overrides.clear()
        if feedback_api:
            feedback_api._rate_limits.clear()

    @patch("app.api.feedback.get_db_connection")
    def test_get_feedback_summary(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (77,),       # risk_db row exists
            (5, 2),      # counts
            ("like", 1), # user row
        ]

        response = client.get("/feedback/77")

        assert response.status_code == 200
        data = response.json()
        assert data["risk_db_id"] == 77
        assert data["likes"] == 5
        assert data["dislikes"] == 2
        assert data["user_vote"] == "like"
        assert data["switch_count"] == 1
        assert data["switches_remaining"] == 2

    @patch("app.api.feedback.get_db_connection")
    def test_create_like_vote(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (77,),         # risk_db row exists
            None,          # no existing vote row
            (1, 0),        # counts after write
            ("like", 0),   # user row after write
        ]

        response = client.post("/feedback", json={"risk_db_id": 77, "vote": "like", "reason": "ignored"})

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["user_vote"] == "like"
        assert data["likes"] == 1
        assert data["dislikes"] == 0
        insert_sql, insert_params = mock_cursor.execute.call_args_list[2].args
        assert "INSERT INTO site_feedback" in insert_sql
        assert insert_params == (7, 77, "like", None)

    @patch("app.api.feedback.get_db_connection")
    def test_switch_limit_blocks_fourth_flip(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (77,),          # risk_db row exists
            ("like", 3),    # existing vote already at limit
        ]

        response = client.post("/feedback", json={"risk_db_id": 77, "vote": "dislike", "reason": "too expensive"})

        assert response.status_code == 429
        assert response.json()["detail"] == "Vote switch limit reached for this site"
        mock_conn.rollback.assert_called_once()
