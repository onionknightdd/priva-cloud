from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_common.models.auth import UserRecord


USER = UserRecord(username="alice", password_hash="x", role="user")


@pytest.fixture
def file_api(tmp_path, monkeypatch):
    from priva_agent_runner.routers import user_files

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(user_files, "get_user_workspace", lambda _user: str(workspace))

    app = FastAPI()
    app.include_router(user_files.router)
    app.dependency_overrides[user_files.require_user] = lambda: USER

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, workspace=workspace)


def test_create_directory_anchors_relative_parent_to_workspace(file_api):
    (file_api.workspace / "projects").mkdir()

    response = file_api.client.post(
        "/api/sandbox/files/mkdir",
        json={"directory": "projects", "name": "reports"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "path": str(file_api.workspace / "projects" / "reports"),
        "name": "reports",
    }
    assert (file_api.workspace / "projects" / "reports").is_dir()


def test_create_directory_rejects_path_traversal_and_existing_paths(file_api):
    request = {"directory": str(file_api.workspace), "name": "reports"}
    assert file_api.client.post("/api/sandbox/files/mkdir", json=request).status_code == 201

    duplicate = file_api.client.post("/api/sandbox/files/mkdir", json=request)
    traversal = file_api.client.post(
        "/api/sandbox/files/mkdir",
        json={"directory": str(file_api.workspace), "name": "../outside"},
    )

    assert duplicate.status_code == 409
    assert traversal.status_code == 400
    assert not file_api.workspace.parent.joinpath("outside").exists()
