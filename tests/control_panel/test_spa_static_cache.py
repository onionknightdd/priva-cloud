from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_control_panel.app import (
    SPA_ASSET_CACHE_CONTROL,
    SPA_SHELL_CACHE_CONTROL,
    SpaStaticFiles,
)


def _client_for_dist(dist_dir):
    app = FastAPI()
    app.mount("/", SpaStaticFiles(directory=dist_dir, html=True), name="spa")
    return TestClient(app)


def test_spa_shell_revalidates_on_each_navigation(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    client = _client_for_dist(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == SPA_SHELL_CACHE_CONTROL


def test_vite_assets_are_long_lived_and_immutable(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log('ok')")
    client = _client_for_dist(tmp_path)

    response = client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == SPA_ASSET_CACHE_CONTROL


def test_not_modified_responses_keep_cache_policy(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (assets / "index-abc123.js").write_text("console.log('ok')")
    client = _client_for_dist(tmp_path)

    shell = client.get("/")
    shell_revalidated = client.get("/", headers={"if-none-match": shell.headers["etag"]})
    asset = client.get("/assets/index-abc123.js")
    asset_revalidated = client.get(
        "/assets/index-abc123.js",
        headers={"if-none-match": asset.headers["etag"]},
    )

    assert shell_revalidated.status_code == 304
    assert shell_revalidated.headers["cache-control"] == SPA_SHELL_CACHE_CONTROL
    assert asset_revalidated.status_code == 304
    assert asset_revalidated.headers["cache-control"] == SPA_ASSET_CACHE_CONTROL
