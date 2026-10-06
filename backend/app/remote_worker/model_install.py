"""Pinned, checksum-verified model installation with actual byte progress.

The worker never treats a directory or historical completion marker as proof.
Start hashes existing files, adopts matching snapshots, and resumes missing bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..inference.catalog import CATALOG
from .model_manifest import (
    MANIFEST_VERSION,
    atomic_json,
    atomic_text,
    bound_path,
    hub_manifest,
    manifest_payload,
    transfer_file,
    validate_pin,
    verify_file,
)
from .model_pins import AUXILIARY_PINS

# Resolved through the public Hub API on 2026-09-30; optional-reference ASR.
WHISPER_PIN = ("openai/whisper-large-v3-turbo", "41f01f3fe87f28c78e2fbf8b568835947dd65ed9")
MIN_FREE_GB_BEFORE_DOWNLOAD = 10
STORAGE_RESERVE_BYTES = 10_000_000_000
ACTIVE_STATES = {"discovering", "verifying", "downloading"}
MODEL_ALIASES = {"voxcpm2_urdu_arabic": "voxcpm2"}
REQUIRED_MODEL_IDS = ("voxcpm2", "chatterbox_ml_v3", "omnivoice_urdu", *AUXILIARY_PINS)


def model_pin(model_id: str) -> tuple[str, str]:
    spec = CATALOG.get(model_id)
    if spec is not None:
        return spec.hf_repo, spec.hf_revision
    if model_id in AUXILIARY_PINS:
        return AUXILIARY_PINS[model_id]
    raise KeyError(model_id)


def model_graph(model_id: str) -> tuple[tuple[str, str], ...]:
    pin = model_pin(model_id)
    return (pin, WHISPER_PIN) if model_id == "omnivoice_urdu" else (pin,)


def release_manifest_id() -> str:
    payload = {
        "version": MANIFEST_VERSION,
        "models": {model_id: model_graph(model_id) for model_id in REQUIRED_MODEL_IDS},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ModelInstaller:
    def __init__(
        self,
        cache_dir: Path,
        *,
        manifest_loader: Callable = hub_manifest,
        transfer: Callable = transfer_file,
        graph: Callable = model_graph,
    ) -> None:
        self.cache_dir = cache_dir
        self.manifest_loader = manifest_loader
        self.transfer = transfer
        self.graph = graph
        self._tasks: dict[str, asyncio.Task] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        # Serial transfers prevent shared snapshot races and unbounded I/O.
        self._download_lock = asyncio.Lock()
        self._capacity_cancel = threading.Event()
        self._capacity_task: asyncio.Task | None = None
        self._capacity_status: dict = {"state": "not_started"}

    def start_capacity(self) -> dict:
        if self._capacity_task and not self._capacity_task.done():
            return self.capacity_status()
        self._capacity_status = {"state": "checking", "scan_complete": False}
        self._capacity_task = asyncio.create_task(self._run_capacity())
        return self.capacity_status()

    def capacity_status(self) -> dict:
        with self._lock:
            return {**self._capacity_status, "model_progress": self.setup_status()["models"]}

    async def _run_capacity(self) -> None:
        try:
            result = await self.capacity()
            self._capacity_status = {"state": "complete", **result}
        except (InterruptedError, asyncio.CancelledError):
            self._capacity_status = {"state": "cancelled", "scan_complete": False}
            raise
        except Exception:
            self._capacity_status = {
                "state": "failed",
                "scan_complete": False,
                "detail": "Storage check failed. Try again.",
            }

    async def capacity(self) -> dict:
        """Inspect mounted files and free space before any model transfer."""
        async with self._download_lock:
            self._capacity_cancel.clear()
            task = asyncio.create_task(
                asyncio.to_thread(self._capacity_sync, self._capacity_cancel)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self._capacity_cancel.set()
                with contextlib.suppress(InterruptedError):
                    await task
                raise

    def _capacity_sync(self, cancel: threading.Event) -> dict:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        seen: set[tuple[str, str, str]] = set()
        total = verified = remaining = conservative = partial = 0
        models: dict[str, dict] = {}
        for model_id in REQUIRED_MODEL_IDS:
            model_total = model_verified = model_missing = 0
            self._update(
                model_id,
                state="verifying",
                bytes_completed=0,
                bytes_total=None,
                files_verified=0,
                files_total=None,
            )
            for repo, revision in self.graph(model_id):
                entries = self.manifest_loader(repo, revision)
                folder = "models--" + repo.replace("/", "--")
                root = bound_path(self.cache_dir, f"{folder}/snapshots/{revision}")
                for entry in entries:
                    if cancel.is_set():
                        raise InterruptedError("Storage check stopped")
                    identity = (repo, revision, entry.path)
                    model_total += entry.size_bytes
                    path = bound_path(root, entry.path, allowed_root=self.cache_dir)
                    self._update(model_id, current_file=entry.path)
                    try:
                        verify_file(path, entry, cancel)
                        valid = True
                    except ValueError:
                        valid = False
                    if valid:
                        model_verified += entry.size_bytes
                    else:
                        model_missing += entry.size_bytes
                    if identity in seen:
                        continue
                    seen.add(identity)
                    total += entry.size_bytes
                    if valid:
                        verified += entry.size_bytes
                        continue
                    temp = bound_path(
                        root, entry.path + ".vcs-incomplete", allowed_root=self.cache_dir
                    )
                    offset = temp.stat().st_size if temp.is_file() and not temp.is_symlink() else 0
                    offset = min(offset, entry.size_bytes) if offset <= entry.size_bytes else 0
                    resumable = offset
                    if offset == entry.size_bytes and offset:
                        try:
                            verify_file(temp, entry, cancel)
                        except ValueError:
                            resumable = 0
                    partial += resumable
                    remaining += entry.size_bytes - resumable
                    # Range fallback truncates the partial before writing. The
                    # occupied partial bytes are then freed, so only the deficit
                    # is additional space. Corrupt target files remain occupied
                    # until atomic replacement and are already excluded from free.
                    conservative += entry.size_bytes - offset
            models[model_id] = {
                "bytes_total": model_total,
                "bytes_verified": model_verified,
                "bytes_missing": model_missing,
            }
            self._update(
                model_id,
                state="not_started",
                bytes_total=model_total,
                bytes_completed=model_verified,
                current_file=None,
            )
        free = shutil.disk_usage(self.cache_dir).free
        required_free = conservative + STORAGE_RESERVE_BYTES
        return {
            "manifest_id": release_manifest_id(),
            "scan_complete": True,
            "model_files_bytes": total,
            "verified_bytes": verified,
            "partial_bytes": partial,
            "remaining_download_bytes": remaining,
            "missing_write_bytes": conservative,
            "reserve_bytes": STORAGE_RESERVE_BYTES,
            "free_bytes": free,
            "required_free_bytes": required_free,
            "sufficient": free >= required_free,
            "models": models,
        }

    def status(self, model_id: str) -> dict[str, Any]:
        _, revision = model_pin(model_id)
        canonical = MODEL_ALIASES.get(model_id)
        if canonical is not None:
            if model_pin(canonical) != model_pin(model_id):
                raise ValueError("Alias checkpoint differs from canonical model")
            state = self.status(canonical)
            state["alias_of"] = canonical
            if "evidence" in state:
                state["evidence"] = {**state["evidence"], "model_id": model_id}
            return state
        with self._lock:
            if model_id in self._states:
                return dict(self._states[model_id])
        unsupported = model_id == "f5_openbible_urdu"
        return {
            "state": "unsupported" if unsupported else "not_started",
            "revision": revision,
            "bytes_total": None,
            "bytes_completed": 0,
            "files_total": None,
            "files_verified": 0,
            "progress_pct": None,
            "detail": "F5 runtime is not included in this worker" if unsupported else None,
        }

    def _update(self, model_id: str, **changes: Any) -> None:
        with self._lock:
            if model_id not in self._states:
                self._states[model_id] = self.status(model_id)
            self._states[model_id].update(changes)
            state = self._states[model_id]
            total = state.get("bytes_total")
            state["progress_pct"] = (
                round(100 * state["bytes_completed"] / total, 2) if total else None
            )

    def start(self, model_id: str) -> dict[str, Any]:
        if model_id in MODEL_ALIASES:
            self.status(model_id)  # Assert this alias still points at the exact same pin.
            self.start(MODEL_ALIASES[model_id])
            return self.status(model_id)
        state = self.status(model_id)
        if state["state"] in ACTIVE_STATES | {"installed", "unsupported"}:
            return state
        for repo, revision in self.graph(model_id):
            validate_pin(repo, revision)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cancel = threading.Event()
        self._cancels[model_id] = cancel
        with self._lock:
            self._states[model_id] = {
                "state": "discovering",
                "revision": state["revision"],
                "detail": None,
                "bytes_total": None,
                "bytes_completed": 0,
                "files_total": None,
                "files_verified": 0,
                "progress_pct": None,
                "current_file": None,
            }
        self._tasks[model_id] = asyncio.create_task(self._run(model_id, cancel))
        return self.status(model_id)

    async def _run(self, model_id: str, cancel: threading.Event) -> None:
        try:
            async with self._download_lock:
                # Shield the async handle so external cancellation cannot release
                # the snapshot lock while its OS thread is still writing files.
                work = asyncio.create_task(asyncio.to_thread(self._install_sync, model_id, cancel))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    cancel.set()
                    with contextlib.suppress(Exception):
                        await work
                    raise
        except asyncio.CancelledError:
            cancel.set()
            self._update(model_id, state="failed", detail="Installation stopped; retry to resume")
            raise
        except Exception:
            # Never include signed URLs, access tokens, raw HTTP errors or user paths.
            self._update(
                model_id, state="failed", detail="Model verification or download failed; retry"
            )

    def _install_sync(self, model_id: str, cancel: threading.Event) -> None:
        if cancel.is_set():
            raise InterruptedError("Installation cancelled")
        manifests = [
            (repo, rev, self.manifest_loader(repo, rev)) for repo, rev in self.graph(model_id)
        ]
        total = sum(entry.size_bytes for _, _, entries in manifests for entry in entries)
        count = sum(len(entries) for _, _, entries in manifests)
        self._update(
            model_id,
            state="verifying",
            bytes_total=total,
            bytes_completed=0,
            files_total=count,
            files_verified=0,
        )
        completed = 0
        verified = 0
        evidence: list[dict] = []
        missing: list[tuple] = []
        for repo, revision, entries in manifests:
            repo_folder = "models--" + repo.replace("/", "--")
            snapshot = bound_path(self.cache_dir, f"{repo_folder}/snapshots/{revision}")
            snapshot.mkdir(parents=True, exist_ok=True)
            atomic_json(snapshot / ".vcs-manifest.json", manifest_payload(repo, revision, entries))
            for entry in entries:
                if cancel.is_set():
                    raise InterruptedError("Installation cancelled")
                self._update(model_id, current_file=entry.path)
                path = bound_path(snapshot, entry.path, allowed_root=self.cache_dir)
                try:
                    digest = verify_file(path, entry, cancel)
                except ValueError:
                    missing.append((repo, revision, snapshot, entry))
                    continue
                completed += entry.size_bytes
                verified += 1
                evidence.append(
                    {
                        "repo": repo,
                        "revision": revision,
                        "path": entry.path,
                        "size_bytes": entry.size_bytes,
                        "sha256": digest,
                    }
                )
                self._update(model_id, bytes_completed=completed, files_verified=verified)
        needed = 0
        for _, _, snapshot, entry in missing:
            partial_path = bound_path(
                snapshot, entry.path + ".vcs-incomplete", allowed_root=self.cache_dir
            )
            offset = (
                partial_path.stat().st_size
                if partial_path.is_file() and not partial_path.is_symlink()
                else 0
            )
            offset = offset if offset <= entry.size_bytes else 0
            needed += entry.size_bytes - offset
        reserve = STORAGE_RESERVE_BYTES
        if missing and shutil.disk_usage(self.cache_dir).free < needed + reserve:
            raise OSError("Insufficient model storage")
        self._update(model_id, state="downloading" if missing else "verifying")
        for repo, revision, snapshot, entry in missing:
            self._update(model_id, current_file=entry.path)
            base = completed
            digest = self.transfer(
                repo,
                revision,
                entry,
                snapshot,
                progress=lambda amount, base=base: self._update(
                    model_id, bytes_completed=base + amount
                ),
                cancel=cancel,
                cache_root=self.cache_dir,
            )
            completed += entry.size_bytes
            verified += 1
            evidence.append(
                {
                    "repo": repo,
                    "revision": revision,
                    "path": entry.path,
                    "size_bytes": entry.size_bytes,
                    "sha256": digest,
                }
            )
            self._update(model_id, bytes_completed=completed, files_verified=verified)
        # Embedded packages using an unqualified default model name resolve these
        # exact cached refs when the generation container enforces offline mode.
        for repo, revision, _ in manifests:
            refs = bound_path(self.cache_dir, "models--" + repo.replace("/", "--") + "/refs")
            refs.mkdir(parents=True, exist_ok=True)
            atomic_text(refs / "main", revision)
        _, revision = model_pin(model_id)
        record = {
            "version": MANIFEST_VERSION,
            "model_id": model_id,
            "revision": revision,
            "bytes_total": total,
            "files": evidence,
        }
        marker = bound_path(self.cache_dir, f".vcs-installed/{model_id}-{revision}.json")
        atomic_json(marker, record)
        self._update(
            model_id,
            state="installed",
            bytes_completed=total,
            files_verified=count,
            current_file=None,
            evidence=record,
            detail=None,
        )

    def setup_status(self) -> dict[str, Any]:
        models = {model_id: self.status(model_id) for model_id in REQUIRED_MODEL_IDS}
        known = all(row["bytes_total"] is not None for row in models.values())
        total = sum(row["bytes_total"] or 0 for row in models.values()) if known else None
        completed = sum(row["bytes_completed"] for row in models.values())
        return {
            "ready": all(row["state"] == "installed" for row in models.values()),
            "manifest_id": release_manifest_id(),
            "required_model_ids": list(REQUIRED_MODEL_IDS),
            "models": models,
            "bytes_total": total,
            "bytes_completed": completed,
            "progress_pct": round(100 * completed / total, 2) if total else None,
        }

    async def shutdown(self) -> None:
        self._capacity_cancel.set()
        if self._capacity_task and not self._capacity_task.done():
            self._capacity_task.cancel()
            await asyncio.gather(self._capacity_task, return_exceptions=True)
        for cancel in self._cancels.values():
            cancel.set()
        # Do not abandon a to_thread transfer and delete its volume underneath it.
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
