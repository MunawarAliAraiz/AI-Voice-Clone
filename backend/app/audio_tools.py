"""Fetch the local audio runtime directly from its publisher, outside the installer."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import threading
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException, Request

VENDOR_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
)
ARCHIVE_BYTES = 114_768_076
ARCHIVE_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
BINARY_SHA256 = "3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec"
_PREFIX = "ffmpeg-9.0.2-essentials_build/"
FILES = {
    "bin/ffmpeg.exe": ("ffmpeg.exe", BINARY_SHA256, 105_423_872),
    "LICENSE": (
        "LICENSE.txt",
        "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
        35_147,
    ),
    "README.txt": (
        "README.txt",
        "0342de6bb39dd421cbec2c00d89ad274b640d75ad3f2cab09ffd647ce2d8f949",
        41_577,
    ),
}
_DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


def _sha256(path: Path, stop: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if stop.is_set():
                raise InterruptedError("Audio tool setup cancelled")
            digest.update(chunk)
    return digest.hexdigest()


class AudioToolsController:
    def __init__(self, data_dir: Path, *, client_factory=None):
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "tools" / "ffmpeg"
        self.task: asyncio.Task | None = None
        self.stop = threading.Event()
        self.client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=10),
                follow_redirects=False,
                trust_env=False,
            )
        )
        self.state = {
            "stage": "idle",
            "ready": False,
            "detail": "Preparing local audio tools.",
            "bytes_completed": 0,
            "bytes_total": ARCHIVE_BYTES,
            "progress_pct": 0.0,
        }

    def snapshot(self) -> dict:
        return {**self.state, "version": "9.0.2", "source_url": VENDOR_URL}

    def update(self, **changes):
        self.state = {**self.state, **changes}

    def _safe_root(self):
        if not self.root.resolve().is_relative_to(self.data_dir):
            raise ValueError("Audio tools storage points outside the app data directory")
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ["vendor.zip.part", "vendor.zip"] + [entry[0] for entry in FILES.values()]:
            if not (self.root / name).resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Audio tools file points outside its storage directory")
            if not (self.root / (name + ".pending")).resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Audio tools staging file points outside its storage directory")

    def _adopt(self) -> bool:
        for output, expected_hash, expected_bytes in FILES.values():
            path = self.root / output
            if not path.is_file() or path.stat().st_size != expected_bytes:
                return False
            if _sha256(path, self.stop) != expected_hash:
                return False
        return True

    async def start(self):
        if self.task is not None and not self.task.done():
            return self.snapshot()
        if self.state["ready"]:
            return self.snapshot()
        self.stop.clear()
        self.update(stage="verifying", ready=False, detail="Checking cached audio tools.")
        self.task = asyncio.create_task(self._run())
        return self.snapshot()

    async def _thread(self, function, *args):
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.stop.set()
            with contextlib.suppress(Exception):
                await worker
            raise

    async def _run(self):
        try:
            self._safe_root()
            if await self._thread(self._adopt):
                self.update(
                    stage="ready",
                    ready=True,
                    detail="Local audio tools are ready.",
                    progress_pct=100.0,
                )
                return
            if shutil.disk_usage(self.root).free < ARCHIVE_BYTES + 150 * 1024 * 1024:
                raise ValueError(
                    "Free at least 300 MB of disk space for local audio tools, then retry."
                )
            archive = self.root / "vendor.zip"
            if archive.is_file():
                if (
                    archive.stat().st_size != ARCHIVE_BYTES
                    or await self._thread(_sha256, archive, self.stop) != ARCHIVE_SHA256
                ):
                    archive.unlink()
            if not archive.is_file():
                for attempt in range(3):
                    try:
                        async with asyncio.timeout(600):
                            await self._download(archive)
                        break
                    except httpx.HTTPError:
                        if attempt == 2:
                            raise
                        self.update(detail="Connection interrupted. Resuming audio tools download.")
                        await asyncio.sleep(attempt + 1)
            self.update(
                stage="extracting", detail="Verifying and preparing the downloaded audio runtime."
            )
            await self._thread(self._extract, archive)
            archive.unlink(missing_ok=True)
            self.update(
                stage="ready",
                ready=True,
                detail="Local audio tools are ready.",
                bytes_completed=ARCHIVE_BYTES,
                progress_pct=100.0,
            )
        except asyncio.CancelledError:
            self.update(
                stage="cancelled",
                ready=False,
                detail="Audio tool setup paused. Restart or retry to continue.",
            )
            raise
        except TimeoutError:
            self.update(
                stage="failed", ready=False,
                detail="Audio tools download timed out. "
                "Retry setup to continue the saved download.",
            )
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, httpx.HTTPError):
            self.update(
                stage="failed",
                ready=False,
                detail="Could not prepare local audio tools. Check your connection and free disk "
                "space, then retry. Downloaded files must match the pinned publisher checksums.",
            )

    async def _download(self, archive: Path):
        partial = archive.with_suffix(".zip.part")
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > ARCHIVE_BYTES:
            partial.unlink()
            offset = 0
        self.update(
            stage="downloading",
            detail="Downloading audio tools directly from the publisher (about 115 MB).",
            bytes_completed=offset,
            progress_pct=offset * 100 / ARCHIVE_BYTES,
        )
        async with self.client_factory() as client:
            url = VENDOR_URL
            for _ in range(4):
                parsed = urlsplit(url)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in _DOWNLOAD_HOSTS
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError("Unsupported audio tool download location")
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code == 416 and offset == ARCHIVE_BYTES:
                        break
                    response.raise_for_status()
                    if response.status_code == 206:
                        content_range = response.headers.get("content-range", "")
                        if not content_range.startswith(
                            f"bytes {offset}-"
                        ) or not content_range.endswith(f"/{ARCHIVE_BYTES}"):
                            raise ValueError("Audio download returned an invalid byte range")
                    elif response.status_code == 200:
                        offset = 0
                    else:
                        raise ValueError("Unexpected audio tool download status")
                    mode = "ab" if offset else "wb"
                    with partial.open(mode) as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            if self.stop.is_set():
                                raise InterruptedError("Audio tool setup cancelled")
                            if offset + len(chunk) > ARCHIVE_BYTES:
                                raise ValueError("Audio tools archive is larger than expected")
                            output.write(chunk)
                            offset += len(chunk)
                            self.update(
                                bytes_completed=offset, progress_pct=offset * 100 / ARCHIVE_BYTES
                            )
                    break
            else:
                raise ValueError("Too many audio download redirects")
        if offset != ARCHIVE_BYTES:
            raise ValueError("Audio tools download is incomplete")
        self.update(stage="verifying", detail="Checking the publisher archive checksum.")
        if await self._thread(_sha256, partial, self.stop) != ARCHIVE_SHA256:
            partial.unlink(missing_ok=True)
            raise ValueError("Audio tools archive checksum does not match")
        partial.replace(archive)

    def _extract(self, archive: Path):
        staged = []
        try:
            with zipfile.ZipFile(archive) as source:
                if len(source.infolist()) > 100:
                    raise ValueError("Unexpected audio tool archive layout")
                for member, (output, expected_hash, expected_bytes) in FILES.items():
                    info = source.getinfo(_PREFIX + member)
                    if (
                        info.file_size != expected_bytes
                        or (info.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("Unexpected audio runtime file")
                    target = self.root / (output + ".pending")
                    staged.append(target)
                    written = 0
                    with source.open(info) as input_file, target.open("wb") as output_file:
                        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                            if self.stop.is_set():
                                raise InterruptedError("Audio tool setup cancelled")
                            written += len(chunk)
                            if written > expected_bytes:
                                raise ValueError("Expanded audio runtime exceeds expected size")
                            output_file.write(chunk)
                    if written != expected_bytes or _sha256(target, self.stop) != expected_hash:
                        raise ValueError("Extracted audio tool checksum does not match")
            # Commit notices first; executable is the final readiness marker.
            for output in ["LICENSE.txt", "README.txt", "ffmpeg.exe"]:
                (self.root / (output + ".pending")).replace(self.root / output)
        finally:
            for target in staged:
                target.unlink(missing_ok=True)

    async def shutdown(self):
        self.stop.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


def require_audio_tools(request: Request):
    settings = request.app.state.settings
    if settings.desktop_static_dir is None:
        return
    instance = getattr(request.app.state, "audio_tools", None)
    if instance is None or not instance.snapshot()["ready"]:
        raise HTTPException(
            409,
            "Local audio tools are preparing. Wait for the download or retry Audio tools setup.",
        )
