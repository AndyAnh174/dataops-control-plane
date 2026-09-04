from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from dataops_control_plane.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with TestClient(create_app(engine=engine), base_url="https://testserver") as test_client:
        yield test_client


def test_bootstrap_creates_the_first_owner_and_authenticated_session(
    client: TestClient,
) -> None:
    """Catches first-run setup that creates data but leaves the owner unauthenticated."""
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": " Owner@Example.com ",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["workspaces"] == [
        {
            "id": body["workspaces"][0]["id"],
            "name": "AndyAnh Lab",
            "role": "OWNER",
        }
    ]
    assert "password" not in body["user"]
    assert "dataops_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]

    current = client.get("/api/v1/me")

    assert current.status_code == 200
    assert current.json() == body


def test_bootstrap_is_permanently_closed_after_the_first_owner(
    client: TestClient,
) -> None:
    """Catches a public caller registering another owner after initial setup."""
    first = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "first@example.com",
            "password": "first secure password",
            "workspace_name": "First workspace",
        },
    )
    second = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "attacker@example.com",
            "password": "attacker password 123",
            "workspace_name": "Attacker workspace",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"detail": "Platform bootstrap has already been completed"}


def test_login_restores_an_authenticated_session(client: TestClient) -> None:
    """Catches valid credentials being unable to restore access after a browser reset."""
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": " OWNER@EXAMPLE.COM ",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "owner@example.com"
    assert response.json()["workspaces"][0]["role"] == "OWNER"
    assert client.get("/api/v1/me").status_code == 200


def test_logout_revokes_the_server_session(client: TestClient) -> None:
    """Catches logout that only clears a browser cookie while leaving its token valid."""
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )
    stolen_token = client.cookies["dataops_session"]

    response = client.post("/api/v1/auth/logout")
    replay = client.get(
        "/api/v1/me",
        headers={"Cookie": f"dataops_session={stolen_token}"},
    )

    assert response.status_code == 204
    assert replay.status_code == 401
