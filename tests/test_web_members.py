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


def bootstrap_owner(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_owner_provisions_a_member_who_can_sign_in(client: TestClient) -> None:
    """Catches workspace roles existing only in the database with no usable account flow."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]

    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={
            "email": " operator@example.com ",
            "initial_password": "member password 123",
            "role": "OPERATOR",
        },
    )
    members = client.get(f"/api/v1/workspaces/{workspace_id}/members")
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "operator@example.com", "password": "member password 123"},
    )

    assert created.status_code == 201
    assert created.json()["email"] == "operator@example.com"
    assert created.json()["role"] == "OPERATOR"
    assert members.status_code == 200
    assert [(item["email"], item["role"]) for item in members.json()["items"]] == [
        ("owner@example.com", "OWNER"),
        ("operator@example.com", "OPERATOR"),
    ]
    assert login.status_code == 200
    assert login.json()["workspaces"][0]["role"] == "OPERATOR"


def test_non_owner_cannot_manage_workspace_members(client: TestClient) -> None:
    """Catches an operator escalating privileges or provisioning additional accounts."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    member = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={
            "email": "operator@example.com",
            "initial_password": "member password 123",
            "role": "OPERATOR",
        },
    ).json()
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "operator@example.com", "password": "member password 123"},
        ).status_code
        == 200
    )

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/members")
    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={
            "email": "viewer@example.com",
            "initial_password": "viewer password 123",
            "role": "VIEWER",
        },
    )
    changed = client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{member['id']}",
        json={"role": "OWNER"},
    )

    assert listed.status_code == 403
    assert created.status_code == 403
    assert changed.status_code == 403


def test_owner_changes_and_removes_another_member(client: TestClient) -> None:
    """Catches role updates or removals that only change the UI without persisting."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    member = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={
            "email": "viewer@example.com",
            "initial_password": "viewer password 123",
            "role": "VIEWER",
        },
    ).json()

    changed = client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{member['id']}",
        json={"role": "OPERATOR"},
    )
    removed = client.delete(f"/api/v1/workspaces/{workspace_id}/members/{member['id']}")
    members = client.get(f"/api/v1/workspaces/{workspace_id}/members")

    assert changed.status_code == 200
    assert changed.json()["role"] == "OPERATOR"
    assert removed.status_code == 204
    assert [item["email"] for item in members.json()["items"]] == ["owner@example.com"]


def test_owner_cannot_remove_or_demote_their_own_membership(client: TestClient) -> None:
    """Catches an owner accidentally locking themselves out of workspace administration."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    owner = client.get(f"/api/v1/workspaces/{workspace_id}/members").json()["items"][0]

    demoted = client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner['id']}",
        json={"role": "VIEWER"},
    )
    removed = client.delete(f"/api/v1/workspaces/{workspace_id}/members/{owner['id']}")

    assert demoted.status_code == 409
    assert demoted.json() == {"detail": "You cannot demote your own workspace membership"}
    assert removed.status_code == 409
    assert removed.json() == {"detail": "You cannot remove your own workspace membership"}


def test_dashboard_exposes_member_management_only_to_owners(client: TestClient) -> None:
    """Catches the member API shipping without a discoverable, role-appropriate Web UI."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={
            "email": "viewer@example.com",
            "initial_password": "viewer password 123",
            "role": "VIEWER",
        },
    )

    owner_page = client.get("/app")
    client.cookies.clear()
    client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@example.com", "password": "viewer password 123"},
    )
    viewer_page = client.get("/app")

    assert owner_page.status_code == 200
    assert "Manage access" in owner_page.text
    assert "viewer@example.com" in owner_page.text
    assert 'data-api-form="member"' in owner_page.text
    assert viewer_page.status_code == 200
    assert "Manage access" not in viewer_page.text
    assert 'data-api-form="member"' not in viewer_page.text
    assert "Add project" not in viewer_page.text
    assert 'data-api-form="project"' not in viewer_page.text
