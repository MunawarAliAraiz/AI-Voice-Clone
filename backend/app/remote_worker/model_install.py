"""Download catalog-pinned weights into a persistent Pod cache."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path

from ..inference.catalog import CATALOG
from .model_pins import AUXILIARY_PINS

MIN_FREE_GB_BEFORE_DOWNLOAD = 20


def model_pin(model_id: str) -> tuple[str, str]:
    spec = CATALOG.get(model_id)
    if spec is not None:
        return spec.hf_repo, spec.hf_revision
    if model_id in AUXILIARY_PINS:
        return AUXILIARY_PINS[model_id]
    raise KeyError(model_id)


def _download(repo_id: str, revision: str, cache_dir: Path) -> str:
    # The Pod image installs this dependency; the Windows sidecar never needs it.
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=repo_id, revision=revision, cache_dir=str(cache_dir))


class ModelInstaller:
    def __init__(
        self,
        cache_dir: Path,
        *,
        downloader: Callable[[str, str, Path], str] = _download,
    ) -> None:
        self.cache_dir = cache_dir
        self.downloader = downloader
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._states: dict[str, dict[str, str]] = {}

    def _marker(self, model_id: str, revision: str) -> Path:
        return self.cache_dir / ".vcs-installed" / f"{model_id}-{revision}.json"

    def status(self, model_id: str) -> dict[str, str]:
        if model_id in self._states:
            return self._states[model_id]
        repo, revision = model_pin(model_id)
        repo_path = "models--" + repo.replace("/", "--")
        snapshot = self.cache_dir / repo_path / "snapshots" / revision
        # A failed/interrupted Hugging Face download may leave a nonempty
        # snapshot directory. Only our post-download marker proves completion.
        if self._marker(model_id, revision).is_file() and snapshot.is_dir():
            return {"state": "installed", "revision": revision}
        return {"state": "not_started", "revision": revision}

    def start(self, model_id: str) -> dict[str, str]:
        repo, revision = model_pin(model_id)
        if self.status(model_id)["state"] in {"downloading", "installed"}:
            return self.status(model_id)
        if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision.lower()):
            raise ValueError("Model revision is not pinned to a commit")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(self.cache_dir).free / 1024**3
        if free_gb < MIN_FREE_GB_BEFORE_DOWNLOAD:
            raise OSError("Less than 20 GB remains on the model volume")
        self._states[model_id] = {"state": "downloading", "revision": revision}
        self._tasks[model_id] = asyncio.create_task(self._run(model_id, repo, revision))
        return self.status(model_id)

    async def _run(self, model_id: str, repo: str, revision: str) -> None:
        try:

            def download_and_validate() -> None:
                path = self.downloader(repo, revision, self.cache_dir)
                resolved = Path(path).resolve()
                if not resolved.is_relative_to(self.cache_dir.resolve()):
                    raise ValueError("Downloaded snapshot escaped the model cache")
                marker = self._marker(model_id, revision)
                marker.parent.mkdir(parents=True, exist_ok=True)
                temporary = marker.with_suffix(".tmp")
                temporary.write_text(json.dumps({"model_id": model_id, "revision": revision}))
                temporary.replace(marker)

            await asyncio.to_thread(download_and_validate)
            self._states[model_id] = {"state": "installed", "revision": revision}
        except Exception:
            # Hugging Face errors can include private URLs, tokens or account
            # details; the public status is deliberately stable.
            self._states[model_id] = {
                "state": "failed",
                "revision": revision,
                "detail": "Download failed; check Pod logs and model access",
            }

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
