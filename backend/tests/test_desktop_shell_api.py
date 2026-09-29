"""Desktop assets are public to the webview; its API stays session-key protected."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def test_desktop_assets_and_api_key(tmp_path: Path) -> None:
    assets = tmp_path / "dist"
    assets.mkdir()
    (assets / "index.html").write_text("<html><head></head><body>Studio</body></html>")
    (assets / "app.js").write_text("console.log('ready')")
    settings = Settings(
        data_dir=tmp_path / "data",
        desktop_static_dir=assets,
        api_key="session-secret",
        allow_fake_runtime=True,
    )
    with TestClient(create_app(scheduler=FakeScheduler(), settings=settings)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'window.__VCS_DESKTOP_KEY__="session-secret"' in page.text
        assert page.headers["cache-control"] == "no-store"
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/models").status_code == 401
        assert client.get("/api/models", headers={"X-API-Key": "session-secret"}).status_code == 200
