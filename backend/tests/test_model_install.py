"""Catalog-only, pinned model installs stay on the Pod's cache volume."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.inference.catalog import CATALOG
from app.remote_worker.model_install import ModelInstaller


def test_install_is_pinned_and_recovers_from_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.remote_worker.model_install.shutil.disk_usage",
                        lambda _: SimpleNamespace(free=200 * 1024 ** 3))
    seen: list[tuple[str, str]] = []

    def download(repo: str, revision: str, cache: Path) -> str:
        seen.append((repo, revision))
        snapshot = cache / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "config.json").write_text("{}")
        return str(snapshot)

    spec = CATALOG.get("voxcpm2")
    assert spec is not None

    # A partially populated snapshot alone must never be called installed.
    partial = tmp_path / ("models--" + spec.hf_repo.replace("/", "--")) / "snapshots" / spec.hf_revision
    partial.mkdir(parents=True)
    (partial / "incomplete.bin").write_bytes(b"partial")
    assert ModelInstaller(tmp_path).status(spec.id)["state"] == "not_started"

    async def run() -> None:
        installer = ModelInstaller(tmp_path, downloader=download)
        assert installer.start(spec.id)["state"] == "downloading"
        assert installer.start(spec.id)["state"] == "downloading"
        await installer._tasks[spec.id]
        assert installer.status(spec.id)["state"] == "installed"
        assert installer.start(spec.id)["state"] == "installed"
        assert ModelInstaller(tmp_path).status(spec.id)["state"] == "installed"
        await installer.shutdown()

    asyncio.run(run())
    assert seen == [(spec.hf_repo, spec.hf_revision)]
