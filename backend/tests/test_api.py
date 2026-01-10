"""
Tests for the FastAPI endpoints.
"""


def test_root_endpoint(client):
    """Test the root health check endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_endpoint(client):
    """Test the detailed health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "adora-api"
