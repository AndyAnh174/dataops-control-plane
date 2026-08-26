from fastapi.testclient import TestClient

from dataops_control_plane.main import create_app


def test_health_reports_service_ready() -> None:
    """Catches a missing or incorrectly mounted health endpoint."""
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "dataops-control-plane"}
