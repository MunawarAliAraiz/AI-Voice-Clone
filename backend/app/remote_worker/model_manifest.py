"""Pinned Hub metadata, integrity and resumable CPU-only model transfers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

MANIFEST_VERSION = 2
HUB = "https://huggingface.co"
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    size_bytes: int
    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (
            not self.path
            or self.path == "."
            or str(path) != self.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or ":" in self.path
            or "\0" in self.path
        ):
            raise ValueError("Invalid model file path")
        if self.size_bytes < 0 or self.algorithm not in {"sha256", "git-blob-sha1"}:
            raise ValueError("Invalid model file metadata")
        length = 64 if self.algorithm == "sha256" else 40
        if not re.fullmatch(rf"[a-f0-9]{{{length}}}", self.digest):
            raise ValueError("Invalid model file checksum")


def validate_pin(repo: str, revision: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid model repository")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Model revision must be pinned")


def hub_manifest(
    repo: str,
    revision: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[ManifestFile, ...]:
    """Fetch exact commit's paged tree; LFS hashes are content SHA-256.

    Small files use Git blob SHA-1, not raw SHA-1. Optional HF_TOKEN is read
    from the worker environment only and is never persisted in the manifest.
    """
    validate_pin(repo, revision)
    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.getenv("HF_TOKEN") else {}
    url = f"{HUB}/api/models/{repo}/tree/{revision}?recursive=true&expand=false"
    files: list[ManifestFile] = []
    visited: set[str] = set()
    with httpx.Client(transport=transport, timeout=45, follow_redirects=False) as client:
        while url:
            parsed = httpx.URL(url)
            if (
                parsed.scheme != "https"
                or parsed.host != "huggingface.co"
                or parsed.path != f"/api/models/{repo}/tree/{revision}"
                or url in visited
            ):
                raise ValueError("Invalid model metadata pagination")
            visited.add(url)
            response = client.get(url, headers=headers)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("Invalid model metadata")
            for row in rows:
                if row.get("type") != "file":
                    continue
                lfs = row.get("lfs")
                files.append(
                    ManifestFile(
                        row["path"],
                        int(row["size"]),
                        "sha256" if lfs else "git-blob-sha1",
                        lfs["oid"] if lfs else row["oid"],
                    )
                )
            url = response.links.get("next", {}).get("url", "")
    if not files or len({entry.path for entry in files}) != len(files):
        raise ValueError("Empty or duplicate model manifest")
    return tuple(sorted(files, key=lambda entry: entry.path))


def bound_path(root: Path, relative: str, *, allowed_root: Path | None = None) -> Path:
    target = root / relative
    if not target.resolve().is_relative_to((allowed_root or root).resolve()):
        raise ValueError("Model file escaped storage")
    return target


def verify_file(path: Path, entry: ManifestFile, cancel: threading.Event | None = None) -> str:
    """Return raw SHA-256 only after provider checksum and size match."""
    if not path.is_file() or path.stat().st_size != entry.size_bytes:
        raise ValueError("Model file size mismatch")
    sha256 = hashlib.sha256()
    git = hashlib.sha1(usedforsecurity=False)
    git.update(f"blob {entry.size_bytes}\0".encode())
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Model installation cancelled")
            sha256.update(chunk)
            git.update(chunk)
    actual = sha256.hexdigest() if entry.algorithm == "sha256" else git.hexdigest()
    if actual != entry.digest:
        raise ValueError("Model file checksum mismatch")
    return sha256.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    """Publish without following any pre-existing temporary-file symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".vcs-write-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    atomic_text(path, json.dumps(payload, sort_keys=True))


def transfer_file(
    repo: str,
    revision: str,
    entry: ManifestFile,
    snapshot: Path,
    *,
    progress: Callable[[int], None],
    cancel: threading.Event,
    transport: httpx.BaseTransport | None = None,
    cache_root: Path | None = None,
) -> str:
    """Resume pinned bytes, verify, then atomically publish.

    Follow only HTTPS redirects. Never send the Hub token to another host.
    Existing cache blob symlinks may resolve within cache_root, not outside it.
    """
    validate_pin(repo, revision)
    target = bound_path(snapshot, entry.path, allowed_root=cache_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = bound_path(snapshot, entry.path + ".vcs-incomplete", allowed_root=cache_root)
    if temporary.is_symlink():
        raise ValueError("Incomplete model file must not be a symlink")
    if temporary.exists() and temporary.stat().st_size > entry.size_bytes:
        temporary.unlink()
    offset = temporary.stat().st_size if temporary.is_file() else 0
    progress(offset)
    if entry.size_bytes == 0:
        temporary.touch()
    if offset != entry.size_bytes:
        url = f"{HUB}/{repo}/resolve/{revision}/{quote(entry.path, safe='/')}"
        with httpx.Client(transport=transport, timeout=60, follow_redirects=False) as client:
            for _ in range(10):
                if cancel.is_set():
                    raise InterruptedError("Model installation cancelled")
                parsed = httpx.URL(url)
                if parsed.scheme != "https" or parsed.userinfo:
                    raise ValueError("Invalid model download redirect")
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                if parsed.host == "huggingface.co" and os.getenv("HF_TOKEN"):
                    headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
                with client.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        continue
                    response.raise_for_status()
                    if response.status_code == 206:
                        if response.headers.get("Content-Range", "") != (
                            f"bytes {offset}-{entry.size_bytes - 1}/{entry.size_bytes}"
                        ):
                            raise ValueError("Invalid resumed model download")
                    elif response.status_code == 200:
                        offset = 0
                        progress(0)
                    else:
                        raise ValueError("Invalid model download response")
                    with temporary.open("ab" if offset else "wb") as stream:
                        for chunk in response.iter_bytes(CHUNK_BYTES):
                            if cancel.is_set():
                                raise InterruptedError("Model installation cancelled")
                            offset += len(chunk)
                            if offset > entry.size_bytes:
                                raise ValueError("Model download exceeds metadata size")
                            stream.write(chunk)
                            progress(offset)
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
            else:
                raise ValueError("Too many model download redirects")
    try:
        digest = verify_file(temporary, entry, cancel)
    except ValueError:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(target)
    return digest


def manifest_payload(repo: str, revision: str, entries: tuple[ManifestFile, ...]) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "repo": repo,
        "revision": revision,
        "files": [asdict(entry) for entry in entries],
    }
