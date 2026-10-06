"""Pod-free model setup orchestration; exact pinned files, checked cache metadata.

Live writes require a reviewed region-and-code qualification receipt.
Every selected volume still requires its own bound credential and space checks.
S3 object inventory is an allocation estimate, never mounted filesystem proof.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..remote_worker.model_install import (
    REQUIRED_MODEL_IDS,
    model_graph,
    model_pin,
    release_manifest_id,
)
from ..remote_worker.model_manifest import (
    MANIFEST_VERSION,
    ManifestFile,
    atomic_json,
    manifest_payload,
    validate_pin,
)
from .storage_access import StorageBinding
from .storage_transfer import ModelStorageTransfer, StorageTransferError, _matches_key, _text, _xml

WRITE_HOLD = (
    "Storage access works. Automatic downloads need a storage compatibility check "
    "before they can start."
)
RESERVE_BYTES = 10_000_000_000


def _empty_xml_element(node) -> bool:
    return not node.attrib and not list(node) and not (node.text or "").strip()


def binding_hash(binding: StorageBinding) -> str:
    value = {
        "account": binding.account,
        "volume_id": binding.volume_id,
        "region": binding.region,
        "prefix": binding.prefix,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def transfer_code_sha256() -> str | None:
    """Root build embeds this digest for frozen releases; no user/env override."""
    if getattr(sys, "frozen", False):
        try:
            from .storage_protocol import TRANSFER_CODE_SHA256

            return (
                TRANSFER_CODE_SHA256
                if re.fullmatch(r"[a-f0-9]{64}", TRANSFER_CODE_SHA256)
                else None
            )
        except (ImportError, TypeError):
            return None
    digest = hashlib.sha256()
    try:
        for name in ("storage_access.py", "storage_transfer.py", "storage_setup.py"):
            digest.update(name.encode() + b"\0")
            digest.update(Path(__file__).with_name(name).read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def storage_writes_qualified(binding: StorageBinding) -> bool:
    path = Path(__file__).with_name("storage-write-qualification.json")
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
            return False
        row = json.loads(path.read_text(encoding="utf-8"))
        return bool(
            row.get("version") == 1
            and row.get("region") == binding.region
            and row.get("protocol_version") == "s3-pinned-relay-v1"
            and transfer_code_sha256() is not None
            and row.get("transfer_code_sha256") == transfer_code_sha256()
            and row.get("graph_manifest_id") == release_manifest_id()
            and all(
                row.get(key) is True
                for key in (
                    "put_readback_verified",
                    "multipart_readback_verified",
                    "owned_cleanup_verified",
                )
            )
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


class StorageModelSetup(ModelStorageTransfer):
    def __init__(self, *args, volume_size_gb: int, qualified=None, **kwargs):
        super().__init__(*args, **kwargs)
        if (
            isinstance(volume_size_gb, bool)
            or not isinstance(volume_size_gb, int)
            or not 1 <= volume_size_gb <= 4000
        ):
            raise ValueError("Choose valid model storage.")
        self.volume_size_gb = volume_size_gb
        self.qualified = qualified or storage_writes_qualified
        self._batch_task = None
        self._batch_lock = asyncio.Lock()
        self._phase = "idle"
        self._detail = ""
        self._capacity = None
        self._installed = {}
        self._manifests = {}
        self._proof = None

    @contextlib.contextmanager
    def _process_lock(self):
        # A setup keeps the OS lock across all files and metadata publication.
        if self._batch_task is asyncio.current_task():
            yield
        else:
            with super()._process_lock():
                yield

    def status(self) -> dict:
        source = super().status()["models"]
        models = {}
        for model_id in REQUIRED_MODEL_IDS:
            if model_id in self._installed:
                models[model_id] = self._installed[model_id]
                continue
            row = source.get(model_id, {})
            state = row.get("state", "not_started")
            completed = row.get("bytes_checked", 0)
            total = row.get("bytes_total")
            # Network/staging progress is separate from checked-file progress.
            models[model_id] = {
                "state": "verifying" if state == "checking" else state,
                "revision": model_pin(model_id)[1],
                "bytes_total": total,
                "bytes_completed": completed,
                "files_total": row.get("files_total"),
                "files_verified": row.get("files_checked", 0),
                "progress_pct": round(100 * completed / total, 2) if total else None,
                "transfer_bytes": row.get("bytes_current", 0),
                "transfer_file_bytes": row.get("file_bytes"),
                "transfer_progress_pct": round(
                    100 * row.get("bytes_current", 0) / row["file_bytes"], 2
                )
                if row.get("file_bytes")
                else None,
                "current_file": row.get("current_file"),
                "detail": row.get("detail"),
            }
        known = all(row["bytes_total"] is not None for row in models.values())
        total = sum(row["bytes_total"] for row in models.values()) if known else None
        completed = sum(row["bytes_completed"] for row in models.values())
        return {
            "ready": self._proof is not None,
            "files_ready": self._proof is not None,
            "manifest_id": release_manifest_id(),
            "models": models,
            "bytes_total": total,
            "bytes_completed": completed,
            "progress_pct": round(100 * completed / total, 2) if total else None,
            "setup_phase": self._phase,
            "detail": self._detail,
            "capacity": self._capacity,
        }

    async def close(self) -> None:
        self._closing = True
        self.pause()
        task = self._batch_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 8)
            except asyncio.CancelledError:
                if not task.done():
                    raise
            except TimeoutError:
                raise StorageTransferError("The storage transfer is still stopping.") from None
        await super().close()

    async def _discover(self) -> dict:
        manifests = {}
        for model_id in REQUIRED_MODEL_IDS:
            self._set(model_id, state="discovering", bytes_checked=0)
            for repo, revision in self.graph(model_id):
                self._check_pause()
                validate_pin(repo, revision)
                if (repo, revision) not in manifests:
                    rows = tuple(await asyncio.to_thread(self.manifest_loader, repo, revision))
                    if (
                        not rows
                        or any(not isinstance(row, ManifestFile) for row in rows)
                        or len({row.path for row in rows}) != len(rows)
                    ):
                        raise StorageTransferError(
                            "The pinned model file list could not be checked."
                        )
                    if any(row.path == ".vcs-manifest.json" for row in rows):
                        raise StorageTransferError("The pinned model file list is not supported.")
                    manifests[(repo, revision)] = rows
            rows = [entry for pin in self.graph(model_id) for entry in manifests[pin]]
            self._set(
                model_id,
                state="pending",
                bytes_total=sum(e.size_bytes for e in rows),
                files_total=len(rows),
                files_checked=0,
            )
        return manifests

    async def _identity_check(self) -> None:
        response = await self._request(
            "GET", self.binding.prefix + ".vcs-volume.json", allow_missing=True
        )
        if response.status_code == 404:
            return
        try:
            row = json.loads(response.content)
            if row.get("app_id") != "studio.voiceclone.desktop" or row.get("volume_id") not in (
                None,
                self.binding.volume_id,
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise StorageTransferError(
                "This model storage folder belongs to another app."
            ) from None

    async def _inventory(self) -> dict[str, int]:
        entries = {}
        token = None
        seen_tokens = set()
        for _ in range(1000):
            params = [("list-type", "2"), ("max-keys", "1000")]
            if token is not None:
                params.append(("continuation-token", token))
            response = await self._request("GET", "", params=tuple(params))
            root = _xml(response.content, "ListBucketResult")
            if _text(root, "Name") != self.binding.volume_id:
                raise StorageTransferError("Storage contents could not be checked.")
            for node in root:
                if node.tag.rsplit("}", 1)[-1] != "Contents":
                    continue
                if _empty_xml_element(node):
                    continue
                key, size = _text(node, "Key"), _text(node, "Size")
                if not key or key in entries or not size or not size.isdecimal() or len(size) > 14:
                    raise StorageTransferError("Storage contents could not be checked.")
                entries[key] = int(size)
            flags = [node.text for node in root if node.tag.rsplit("}", 1)[-1] == "IsTruncated"]
            if len(flags) != 1 or flags[0] not in {"true", "false"}:
                raise StorageTransferError("Storage contents could not be checked.")
            flag = flags[0]
            if flag == "false":
                return entries
            next_token = _text(root, "NextContinuationToken")
            if (
                flag != "true"
                or not next_token
                or next_token in seen_tokens
                or len(next_token) > 4096
            ):
                raise StorageTransferError("Storage contents could not be checked.")
            seen_tokens.add(next_token)
            token = next_token
        raise StorageTransferError("Storage has too many files for automatic setup.")

    async def _multipart_occupied(self, expected: dict) -> int:
        # Include saved multipart bytes as additional occupancy, even if hidden
        # S3 backing files were also listed. Double counting is conservative.
        response = await self._request("GET", "", params=(("uploads", ""), ("max-uploads", "1000")))
        root = _xml(response.content, "ListMultipartUploadsResult")
        flags = [node.text for node in root if node.tag.rsplit("}", 1)[-1] == "IsTruncated"]
        if _text(root, "Bucket") != self.binding.volume_id or flags != ["false"]:
            raise StorageTransferError("Pending storage uploads need checking before setup.")
        known = {}
        for key, (repo, revision, entry) in expected.items():
            _, _, record = self._journal(repo, revision, entry, key)
            if record.get("upload_id") is not None:
                known[(key, record["upload_id"])] = entry
        occupied = 0
        for node in root:
            if node.tag.rsplit("}", 1)[-1] != "Upload":
                continue
            if _empty_xml_element(node):
                continue
            key, upload_id = _text(node, "Key"), _text(node, "UploadId")
            matches = [
                (owned_key, entry)
                for (owned_key, owned_id), entry in known.items()
                if owned_id == upload_id and _matches_key(key, owned_key)
            ]
            if len(matches) != 1:
                raise StorageTransferError("A pending storage upload needs checking before setup.")
            owned_key, entry = matches[0]
            if entry is None:
                raise StorageTransferError("A pending storage upload needs checking before setup.")
            count = math.ceil(entry.size_bytes / self.limits.part_bytes)
            parts = await self._parts(owned_key, upload_id, count)
            occupied += sum(size for size, _ in parts.values())
        return occupied

    async def _preflight(self) -> None:
        await self._identity_check()
        inventory = await self._inventory()
        expected = {
            self._key(repo, rev, entry): (repo, rev, entry)
            for (repo, rev), rows in self._manifests.items()
            for entry in rows
        }
        occupied = sum(inventory.values()) + await self._multipart_occupied(expected)
        existing_sizes = {}
        for key in expected:
            matches = [size for actual, size in inventory.items() if _matches_key(actual, key)]
            if len(matches) > 1:
                raise StorageTransferError("Storage file names could not be checked.")
            existing_sizes[key] = matches[0] if matches else 0
        growth = sum(
            max(0, entry.size_bytes - existing_sizes[key])
            for key, (_, _, entry) in expected.items()
        )
        largest = max(entry.size_bytes for _, _, entry in expected.values())
        reserve = max(RESERVE_BYTES, 2 * largest)
        free = max(0, self.volume_size_gb * 1_000_000_000 - occupied)
        self._capacity = {
            "manifest_id": release_manifest_id(),
            "scan_complete": True,
            "model_files_bytes": sum(entry.size_bytes for _, _, entry in expected.values()),
            "missing_write_bytes": growth,
            "reserve_bytes": reserve,
            "free_bytes": free,
            "required_free_bytes": growth + reserve,
            "sufficient": free >= growth + reserve,
            "space_source": "allocated_capacity_and_object_inventory",
            "mounted_free_space_verified": False,
        }
        if not self._capacity["sufficient"]:
            raise StorageTransferError(
                "Model storage needs more free space. Increase its size or choose another volume."
            )

    async def _publish(self, key: str, payload: bytes) -> str:
        if len(payload) > 256 * 1024:
            raise StorageTransferError("Model setup metadata is too large.")
        await self._request("PUT", key, payload=payload)
        readback = await self._request("GET", key)
        if readback.content != payload:
            raise StorageTransferError("Model setup metadata did not pass its check.")
        return hashlib.sha256(payload).hexdigest()

    async def run_setup(self) -> dict:
        if self._closing:
            raise StorageTransferError("Storage transfers are stopping.")
        if not self.qualified(self.binding):
            self._phase, self._detail = "failed", WRITE_HOLD
            raise StorageTransferError(WRITE_HOLD)
        if self._closing:
            raise StorageTransferError("Storage transfers are stopping.")
        async with self._batch_lock:
            with super()._process_lock():
                self._batch_task = asyncio.current_task()
                self._cancel.clear()
                original_loader = self.manifest_loader
                try:
                    self._phase, self._detail = "checking_files", "Checking model storage."
                    self._manifests = await self._discover()
                    await self._preflight()
                    self.manifest_loader = lambda repo, rev: self._manifests[(repo, rev)]
                    verified = {}
                    self._phase, self._detail = "downloading", "Preparing the required models."
                    for model_id in REQUIRED_MODEL_IDS:
                        result = await self.transfer_model(model_id)
                        expected = {
                            (repo, rev, e.path): e
                            for repo, rev in self.graph(model_id)
                            for e in self._manifests[(repo, rev)]
                        }
                        files = result["files"]
                        if len(files) != len(expected) or result.get("files_checked") is not True:
                            raise StorageTransferError("Not all model files passed their checks.")
                        seen = set()
                        for row in files:
                            identity = (row["repo"], row["revision"], row["path"])
                            entry = expected.get(identity)
                            if (
                                identity in seen
                                or entry is None
                                or row["size_bytes"] != entry.size_bytes
                                or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                            ):
                                raise StorageTransferError(
                                    "Model file evidence did not match the pinned files."
                                )
                            if entry.algorithm == "sha256" and row["sha256"] != entry.digest:
                                raise StorageTransferError(
                                    "Model file evidence failed its checksum check."
                                )
                            seen.add(identity)
                        total = sum(e.size_bytes for e in expected.values())
                        record = {
                            "version": MANIFEST_VERSION,
                            "model_id": model_id,
                            "revision": model_pin(model_id)[1],
                            "bytes_total": total,
                            "files": files,
                        }
                        verified[model_id] = {
                            "state": "installed",
                            "revision": record["revision"],
                            "bytes_total": total,
                            "bytes_completed": total,
                            "files_total": len(files),
                            "files_verified": len(files),
                            "progress_pct": 100,
                            "current_file": None,
                            "detail": None,
                            "evidence": record,
                        }
                    self._phase, self._detail = "verifying", "Finishing model setup."
                    publication = {}
                    for (repo, rev), entries in self._manifests.items():
                        folder = self.binding.prefix + "models--" + repo.replace("/", "--")
                        values = {
                            folder + "/snapshots/" + rev + "/.vcs-manifest.json": json.dumps(
                                manifest_payload(repo, rev, entries), sort_keys=True
                            ).encode(),
                            folder + "/refs/main": rev.encode(),
                        }
                        for key, payload in values.items():
                            publication[key] = await self._publish(key, payload)
                    for model_id, row in verified.items():
                        key = (
                            self.binding.prefix
                            + ".vcs-installed/"
                            + model_id
                            + "-"
                            + row["revision"]
                            + ".json"
                        )
                        publication[key] = await self._publish(
                            key, json.dumps(row["evidence"], sort_keys=True).encode()
                        )
                    key = self.binding.prefix + ".vcs-volume.json"
                    publication[key] = await self._publish(
                        key,
                        json.dumps(
                            {
                                "app_id": "studio.voiceclone.desktop",
                                "version": 1,
                                "volume_id": self.binding.volume_id,
                            },
                            sort_keys=True,
                        ).encode(),
                    )
                    proof = {
                        "version": 1,
                        "binding_hash": binding_hash(self.binding),
                        "manifest_id": release_manifest_id(),
                        "metadata_readback": publication,
                        "models": verified,
                        "checked_at": datetime.now(UTC).isoformat(),
                        "mounted_files_verified": False,
                    }
                    atomic_json(self.root / "setup-proof.json", proof)
                    self._installed, self._proof = verified, proof
                    self._phase, self._detail = "ready", "Required model files are checked."
                    return {**self.status(), "proof": proof}
                except (InterruptedError, asyncio.CancelledError):
                    self._cancel.set()
                    self._phase, self._detail = "cancelled", "Model downloads paused."
                    raise
                except StorageTransferError as exc:
                    self._phase, self._detail = "failed", str(exc)
                    raise
                except Exception:
                    self._phase, self._detail = "failed", "Model setup could not finish. Try again."
                    raise StorageTransferError(self._detail) from None
                finally:
                    self.manifest_loader = original_loader
                    self._batch_task = None


def confirmed_setup_proof(data_dir: Path, binding: StorageBinding, state: dict) -> bool:
    path = data_dir / "cloud" / "storage-transfers" / "setup-proof.json"
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return False
        row = json.loads(path.read_text(encoding="utf-8"))
        metadata = row.get("metadata_readback")
        expected_keys = {binding.prefix + ".vcs-volume.json"}
        for model_id in REQUIRED_MODEL_IDS:
            expected_keys.add(
                binding.prefix
                + ".vcs-installed/"
                + model_id
                + "-"
                + model_pin(model_id)[1]
                + ".json"
            )
            for repo, rev in model_graph(model_id):
                folder = binding.prefix + "models--" + repo.replace("/", "--")
                expected_keys.add(folder + "/refs/main")
                expected_keys.add(folder + "/snapshots/" + rev + "/.vcs-manifest.json")
        if not isinstance(metadata, dict) or set(metadata) != expected_keys:
            return False
        return bool(
            row.get("version") == 1
            and row.get("binding_hash") == binding_hash(binding)
            and row.get("manifest_id") == release_manifest_id()
            and row.get("models") == state.get("models")
            and state.get("storage_proof_hash")
            == hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            and isinstance(row.get("metadata_readback"), dict)
            and row["metadata_readback"]
            and all(
                key.startswith(binding.prefix)
                and isinstance(value, str)
                and re.fullmatch(r"[a-f0-9]{64}", value)
                for key, value in row["metadata_readback"].items()
            )
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
