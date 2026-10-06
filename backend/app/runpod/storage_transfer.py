"""Offline-qualified Pod-free model transfer primitive; not wired to desktop.

One pinned file is staged locally, checksum-verified, uploaded to the exact Hub
snapshot path and streamed back for verification. This does not install refs,
prove mounted worker readiness, or enforce remote free-space reserves.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from ..remote_worker.model_install import REQUIRED_MODEL_IDS, model_graph, release_manifest_id
from ..remote_worker.model_manifest import (
    ManifestFile,
    atomic_json,
    bound_path,
    hub_manifest,
    validate_pin,
    verify_file,
)
from ..remote_worker.model_manifest import (
    transfer_file as hub_transfer,
)
from .storage_access import REGION_ENDPOINTS, StorageBinding, StorageCredentials, signature_headers

CHUNK = 1024 * 1024
MAX_XML = 512 * 1024
LOCAL_STAGING_RESERVE = 100_000_000


class StorageTransferError(RuntimeError):
    """Only fixed messages, never credentials, signed URLs or provider bodies."""


@dataclass(frozen=True)
class TransferLimits:
    single_put_bytes: int = 499_000_000
    part_bytes: int = 64 * 1024 * 1024
    test_only_small_parts: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.single_put_bytes < 500_000_000:
            raise ValueError("Invalid single-upload limit")
        minimum = 1 if self.test_only_small_parts else 5 * 1024 * 1024
        if not minimum <= self.part_bytes <= 500_000_000:
            raise ValueError("Invalid multipart limit")


def _xml(body: bytes, expected: str) -> ElementTree.Element:
    try:
        text = body.decode("utf-8")
        if (
            len(body) > MAX_XML
            or "\0" in text
            or "<!DOCTYPE" in text.upper()
            or "<!ENTITY" in text.upper()
        ):
            raise ValueError
        root = ElementTree.fromstring(text)  # noqa: S314 -- bounded UTF-8; DTD/entities rejected
        if root.tag.rsplit("}", 1)[-1] != expected:
            raise ValueError
        return root
    except (ValueError, UnicodeError, ElementTree.ParseError):
        raise StorageTransferError("Storage returned an unexpected response.") from None


def _text(root: ElementTree.Element, name: str) -> str | None:
    return next((node.text for node in root if node.tag.rsplit("}", 1)[-1] == name), None)


def _valid_etag(value: str) -> bool:
    # ETags are opaque provider acknowledgments, not model checksum evidence.
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value))


def _part_etag(response: httpx.Response) -> str:
    header = response.headers.get("etag", "")
    strong = header.strip('"') if header and not header.startswith("W/") else None
    if response.content:
        root = _xml(response.content, "UploadPartResult")
        fields = [node.text for node in root if node.tag.rsplit("}", 1)[-1] == "ETag"]
        if len(fields) != 1 or not isinstance(fields[0], str):
            raise StorageTransferError("Storage did not confirm the uploaded part.")
        value = fields[0].strip('"')
        if strong is not None and strong != value:
            raise StorageTransferError("Storage returned conflicting upload confirmations.")
    else:
        value = strong or ""
    if not _valid_etag(value):
        raise StorageTransferError("Storage did not confirm the uploaded part.")
    return value


def _matches_key(actual: str | None, expected: str) -> bool:
    """Runpod XML includes a leading root slash; accept no other normalization."""
    return actual == expected or actual == "/" + expected


def _read_part(path: Path, offset: int, amount: int) -> bytes:
    with path.open("rb") as stream:
        stream.seek(offset)
        result = stream.read(amount)
    if len(result) != amount:
        raise StorageTransferError("The staged model file changed. Check it again.")
    return result


class _CancelStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, cancel: threading.Event):
        self.stream = stream
        self.cancel = cancel

    def __iter__(self):
        for chunk in self.stream:
            if self.cancel.is_set():
                raise InterruptedError("Transfer paused")
            yield chunk

    def close(self):
        self.stream.close()


class _BoundedHubTransport(httpx.BaseTransport):
    """Cancellation checked on raw chunks; blocked socket reads have a short bound."""

    def __init__(self, cancel: threading.Event):
        self.delegate = httpx.HTTPTransport(retries=0)
        self.cancel = cancel

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.cancel.is_set():
            raise InterruptedError("Transfer paused")
        request.extensions["timeout"] = {"connect": 3, "read": 3, "write": 3, "pool": 3}
        response = self.delegate.handle_request(request)
        response.stream = _CancelStream(response.stream, self.cancel)
        return response

    def close(self):
        self.delegate.close()


class ModelStorageTransfer:
    def __init__(
        self,
        credentials: StorageCredentials,
        binding: StorageBinding,
        data_dir: Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        manifest_loader=hub_manifest,
        downloader=hub_transfer,
        graph=model_graph,
        limits: TransferLimits | None = None,
    ) -> None:
        self.credentials = credentials
        self.binding = binding
        self.root = data_dir / "cloud" / "storage-transfers"
        self.manifest_loader = manifest_loader
        self.downloader = downloader
        self.graph = graph
        self.limits = limits or TransferLimits()
        if self.limits.test_only_small_parts and not isinstance(transport, httpx.MockTransport):
            raise ValueError("Small multipart parts are allowed only in offline transport tests")
        self._client = httpx.AsyncClient(
            timeout=30, follow_redirects=False, trust_env=False, transport=transport
        )
        self._exclusive = asyncio.Lock()
        self._cancel = threading.Event()
        self._active_task: asyncio.Task | None = None
        self._closing = False
        if manifest_loader is hub_manifest:
            self.manifest_loader = lambda repo, rev: hub_manifest(
                repo, rev, transport=_BoundedHubTransport(self._cancel)
            )
        if downloader is hub_transfer:
            self.downloader = lambda *args, **kwargs: hub_transfer(
                *args, **kwargs, transport=_BoundedHubTransport(self._cancel)
            )
        self._status_lock = threading.RLock()
        self._models: dict[str, dict] = {}
        self._progress_at = 0.0
        self._load_progress()

    def _identity(self) -> dict:
        return {
            "account": self.binding.account,
            "volume": self.binding.volume_id,
            "region": self.binding.region,
            "prefix": self.binding.prefix,
        }

    def _load_progress(self) -> None:
        path = self.root / "progress.json"
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 262144:
                return
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("binding") != self._identity():
                return
            for model_id, row in record.get("models", {}).items():
                if model_id not in REQUIRED_MODEL_IDS or not isinstance(row, dict):
                    continue
                safe = {"state": "paused", "detail": "Downloads paused."}
                for name in (
                    "bytes_current",
                    "bytes_total",
                    "files_checked",
                    "files_total",
                    "bytes_checked",
                    "file_bytes",
                ):
                    value = row.get(name)
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and 0 <= value <= 4_000_000_000_000
                    ):
                        safe[name] = value
                self._models[model_id] = safe
        except (OSError, ValueError, TypeError, AttributeError):
            return

    @contextlib.contextmanager
    def _process_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / "transfer.lock").open("a+b")
        try:
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise StorageTransferError("Another storage transfer is already running.") from None
            yield
        finally:
            lock.close()

    def status(self) -> dict:
        with self._status_lock:
            return {
                "ready": False,
                "manifest_id": release_manifest_id(),
                "models": json.loads(json.dumps(self._models)),
            }

    def pause(self) -> None:
        self._cancel.set()

    async def close(self) -> None:
        self._closing = True
        self.pause()
        active = self._active_task
        if active is not None and active is not asyncio.current_task() and not active.done():
            active.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(active), timeout=8)
            except asyncio.CancelledError:
                if not active.done():
                    raise
            except TimeoutError:
                raise StorageTransferError("The storage transfer is still stopping.") from None
        async with self._exclusive:
            await self._client.aclose()

    def _set(self, model_id: str, **changes) -> None:
        with self._status_lock:
            previous = self._models.get(model_id, {}).get("state")
            self._models.setdefault(
                model_id, {"state": "pending", "bytes_current": 0, "files_checked": 0}
            ).update(changes)
            now = time.monotonic()
            if previous != self._models[model_id].get("state") or now - self._progress_at >= 1:
                self._progress_at = now
                with contextlib.suppress(OSError):
                    atomic_json(
                        self.root / "progress.json",
                        {"binding": self._identity(), "models": self._models},
                    )

    def _check_pause(self) -> None:
        if self._cancel.is_set():
            raise InterruptedError("Transfer paused")

    def _key(self, repo: str, revision: str, entry: ManifestFile) -> str:
        validate_pin(repo, revision)
        return (
            self.binding.prefix
            + "models--"
            + repo.replace("/", "--")
            + "/snapshots/"
            + revision
            + "/"
            + entry.path
        )

    async def _request(
        self, method: str, key: str, *, params=(), payload=b"", allow_missing=False
    ) -> httpx.Response:
        self._check_pause()
        endpoint = REGION_ENDPOINTS[self.binding.region]
        path = "/" + self.binding.volume_id + ("/" + key if key else "")
        from .storage_access import canonical_query

        headers = signature_headers(
            method=method,
            host=httpx.URL(endpoint).host,
            path=path,
            parameters=params,
            access_key=self.credentials.access_key,
            secret_key=self.credentials.secret_key,
            region=self.binding.region,
            now=datetime.now(UTC),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
        url = endpoint + quote(path, safe="/-_.~")
        if params:
            url += "?" + canonical_query(params)
        try:
            # Used only for small XML/HEAD/part responses; never buffer model GETs here.
            async with asyncio.timeout(60):
                async with self._client.stream(
                    method, url, headers=headers, content=payload
                ) as response:
                    self._response_ok(response, allow_missing)
                    result = bytearray()
                    async for chunk in response.aiter_bytes():
                        self._check_pause()
                        if len(result) + len(chunk) > MAX_XML:
                            raise StorageTransferError("Storage returned an unexpected response.")
                        result.extend(chunk)
                    # aiter_bytes has already decoded gzip/deflate. Reusing the
                    # wire Content-Encoding would decode it twice and lose a
                    # confirmed multipart upload ID. HEAD keeps the object size.
                    headers = {
                        key: value
                        for key, value in response.headers.items()
                        if key not in {"content-encoding", "transfer-encoding"}
                        and (method == "HEAD" or key != "content-length")
                    }
                    return httpx.Response(
                        response.status_code,
                        headers=headers,
                        content=bytes(result),
                        request=response.request,
                    )
        except (httpx.HTTPError, TimeoutError):
            raise StorageTransferError(
                "The storage connection was interrupted. Resume to check it."
            ) from None

    @staticmethod
    def _response_ok(response: httpx.Response, allow_missing: bool = False) -> None:
        if allow_missing and response.status_code == 404:
            return
        if response.status_code in {401, 403}:
            raise StorageTransferError("Storage access was denied. Check the storage keys.")
        if response.status_code not in {200, 201, 204}:
            raise StorageTransferError(
                "Storage could not complete the transfer. Check storage access."
            )

    async def _remote_hash(self, model_id: str, key: str, entry: ManifestFile) -> str | None:
        head = await self._request("HEAD", key, allow_missing=True)
        if head.status_code == 404:
            return None
        if head.headers.get("content-length") != str(entry.size_bytes):
            return None
        endpoint = REGION_ENDPOINTS[self.binding.region]
        path = "/" + self.binding.volume_id + "/" + key
        headers = signature_headers(
            method="GET",
            host=httpx.URL(endpoint).host,
            path=path,
            parameters=(),
            access_key=self.credentials.access_key,
            secret_key=self.credentials.secret_key,
            region=self.binding.region,
            now=datetime.now(UTC),
        )
        sha256 = hashlib.sha256()
        git = hashlib.sha1(usedforsecurity=False)
        git.update(f"blob {entry.size_bytes}\0".encode())
        count = 0
        self._set(model_id, state="checking", bytes_current=0)
        try:
            async with asyncio.timeout(1800):
                async with self._client.stream(
                    "GET", endpoint + quote(path, safe="/-_.~"), headers=headers
                ) as response:
                    self._response_ok(response)
                    async for chunk in response.aiter_bytes(CHUNK):
                        self._check_pause()
                        count += len(chunk)
                        if count > entry.size_bytes:
                            raise StorageTransferError("Saved model size changed during its check.")
                        sha256.update(chunk)
                        git.update(chunk)
                        self._set(model_id, bytes_current=count)
        except (httpx.HTTPError, TimeoutError):
            raise StorageTransferError(
                "The storage check was interrupted. Resume to check it."
            ) from None
        actual = sha256.hexdigest() if entry.algorithm == "sha256" else git.hexdigest()
        return sha256.hexdigest() if count == entry.size_bytes and actual == entry.digest else None

    def _journal(
        self, repo: str, revision: str, entry: ManifestFile, key: str
    ) -> tuple[Path, Path, dict]:
        identity = {
            "account": self.binding.account,
            "volume": self.binding.volume_id,
            "region": self.binding.region,
            "key": key,
            "repo": repo,
            "revision": revision,
            "file": asdict(entry),
        }
        operation = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        folder = self.root / operation
        folder.mkdir(parents=True, exist_ok=True)
        journal = folder / "transfer.json"
        record = {"identity": identity, "state": "staging", "upload_id": None}
        if journal.exists():
            try:
                if journal.is_symlink() or journal.stat().st_size > 8192:
                    raise ValueError
                record = json.loads(journal.read_text(encoding="utf-8"))
                if record["identity"] != identity:
                    raise ValueError
                upload_id = record.get("upload_id")
                if upload_id is not None and (
                    not isinstance(upload_id, str) or not 1 <= len(upload_id) <= 1024
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError, OSError):
                raise StorageTransferError(
                    "Saved transfer could not be read. Check transfer details."
                ) from None
        return journal, folder / "stage", record

    async def _stage(
        self, model_id: str, repo: str, revision: str, entry: ManifestFile, stage: Path
    ) -> tuple[Path, str]:
        await asyncio.to_thread(stage.mkdir, parents=True, exist_ok=True)
        target = bound_path(stage, entry.path, allowed_root=stage)
        try:
            digest = await asyncio.to_thread(verify_file, target, entry, self._cancel)
            self._set(model_id, bytes_current=entry.size_bytes)
            return target, digest
        except ValueError:
            pass
        partial = bound_path(stage, entry.path + ".vcs-incomplete", allowed_root=stage)
        if partial.is_symlink():
            raise StorageTransferError("The staged model file needs checking.")
        offset = partial.stat().st_size if partial.is_file() else 0
        offset = offset if offset <= entry.size_bytes else 0
        free = (await asyncio.to_thread(shutil.disk_usage, stage)).free
        if free < entry.size_bytes - offset + LOCAL_STAGING_RESERVE:
            raise StorageTransferError(
                "This PC needs more free space to download the next model file."
            )
        self._set(model_id, state="downloading", bytes_current=0)
        work = asyncio.create_task(
            asyncio.to_thread(
                self.downloader,
                repo,
                revision,
                entry,
                stage,
                progress=lambda amount: self._set(model_id, bytes_current=amount),
                cancel=self._cancel,
                cache_root=stage,
            )
        )
        try:
            digest = await asyncio.shield(work)
        except asyncio.CancelledError:
            self._cancel.set()
            with contextlib.suppress(Exception):
                await work
            raise
        # Never trust a custom downloader or stale returned digest alone.
        digest = await asyncio.to_thread(verify_file, target, entry, self._cancel)
        return target, digest

    async def _put(
        self, model_id: str, key: str, target: Path, entry: ManifestFile, digest: str
    ) -> None:
        endpoint = REGION_ENDPOINTS[self.binding.region]
        path = "/" + self.binding.volume_id + "/" + key
        headers = signature_headers(
            method="PUT",
            host=httpx.URL(endpoint).host,
            path=path,
            parameters=(),
            access_key=self.credentials.access_key,
            secret_key=self.credentials.secret_key,
            region=self.binding.region,
            now=datetime.now(UTC),
            payload_sha256=digest,
            extra_headers={"content-length": str(entry.size_bytes)},
        )

        async def body():
            sent = 0
            with target.open("rb") as stream:
                while chunk := await asyncio.to_thread(stream.read, CHUNK):
                    self._check_pause()
                    sent += len(chunk)
                    if sent > entry.size_bytes:
                        raise StorageTransferError("The staged model file changed. Check it again.")
                    yield chunk
                    self._set(model_id, bytes_current=sent)
                if sent != entry.size_bytes:
                    raise StorageTransferError("The staged model file changed. Check it again.")

        try:
            async with asyncio.timeout(1800):
                async with self._client.stream(
                    "PUT", endpoint + quote(path, safe="/-_.~"), headers=headers, content=body()
                ) as response:
                    self._response_ok(response)
                    # A small object response can contain an error even with HTTP 200.
                    result = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(result) + len(chunk) > MAX_XML:
                            raise StorageTransferError("Storage returned an unexpected response.")
                        result.extend(chunk)
                    if result:
                        _xml(bytes(result), "PutObjectResult")
        except (httpx.HTTPError, TimeoutError):
            raise StorageTransferError(
                "The storage upload was interrupted. Resume to check it."
            ) from None

    async def _parts(
        self, key: str, upload_id: str, expected_part_count: int
    ) -> dict[int, tuple[int, str]]:
        parts = {}
        marker = "0"
        for _ in range(100):
            response = await self._request(
                "GET", key, params=(("uploadId", upload_id), ("part-number-marker", marker))
            )
            root = _xml(response.content, "ListPartsResult")
            if _text(root, "Bucket") != self.binding.volume_id or not _matches_key(
                _text(root, "Key"), key
            ):
                raise StorageTransferError("Saved upload does not match this model file.")
            for node in root:
                if node.tag.rsplit("}", 1)[-1] != "Part":
                    continue
                try:
                    number = int(_text(node, "PartNumber") or "")
                    size = int(_text(node, "Size") or "")
                    etag = (_text(node, "ETag") or "").strip('"')
                    if (
                        not 1 <= number <= expected_part_count
                        or size < 0
                        or number in parts
                        or not _valid_etag(etag)
                    ):
                        raise ValueError
                    parts[number] = (size, etag)
                except ValueError:
                    raise StorageTransferError("Saved upload has invalid part details.") from None
            flags = [node.text for node in root if node.tag.rsplit("}", 1)[-1] == "IsTruncated"]
            if len(flags) != 1 or flags[0] not in {"true", "false"}:
                raise StorageTransferError("Storage could not list upload progress.")
            if flags[0] == "false":
                return parts
            next_marker = _text(root, "NextPartNumberMarker")
            if not next_marker or not next_marker.isdecimal() or int(next_marker) <= int(marker):
                raise StorageTransferError("Storage could not list upload progress.")
            marker = next_marker
        raise StorageTransferError("Storage returned too many upload pages.")

    async def _multipart(
        self,
        model_id: str,
        key: str,
        target: Path,
        entry: ManifestFile,
        journal: Path,
        record: dict,
    ) -> None:
        if record.get("state") == "completing":
            raise StorageTransferError("Upload completion needs checking before it can resume.")
        upload_id = record.get("upload_id")
        if upload_id is None:
            if record.get("state") in {"creating", "uncertain"}:
                raise StorageTransferError("An upload start needs checking before it can resume.")
            record["state"] = "creating"
            atomic_json(journal, record)
            response = await self._request("POST", key, params=(("uploads", ""),))
            root = _xml(response.content, "InitiateMultipartUploadResult")
            upload_id = _text(root, "UploadId")
            if (
                not upload_id
                or len(upload_id) > 1024
                or _text(root, "Bucket") != self.binding.volume_id
                or not _matches_key(_text(root, "Key"), key)
            ):
                raise StorageTransferError("Storage returned an unexpected upload identity.")
            record.update(state="uploading", upload_id=upload_id, part_bytes=self.limits.part_bytes)
            atomic_json(journal, record)
            existing = {}
        else:
            if record.get("part_bytes") != self.limits.part_bytes:
                raise StorageTransferError("Upload settings changed. Check transfer details.")
            expected_part_count = (
                entry.size_bytes + self.limits.part_bytes - 1
            ) // self.limits.part_bytes
            existing = await self._parts(key, upload_id, expected_part_count)
        complete = ElementTree.Element("CompleteMultipartUpload")
        sent = 0
        for number, offset in enumerate(range(0, entry.size_bytes, self.limits.part_bytes), 1):
            self._check_pause()
            amount = min(self.limits.part_bytes, entry.size_bytes - offset)
            data = await asyncio.to_thread(_read_part, target, offset, amount)
            # MD5 is only a same-part resume hint, never proof of the final model.
            md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
            saved = (record.get("parts") or {}).get(str(number), {})
            observed = existing.get(number)
            if observed == (amount, md5):
                etag = md5
            elif (
                isinstance(saved, dict)
                and _valid_etag(saved.get("etag", ""))
                and saved.get("size") == amount
                and saved.get("md5") == md5
                and observed == (amount, saved["etag"])
            ):
                etag = saved["etag"]
            else:
                response = await self._request(
                    "PUT",
                    key,
                    params=(("partNumber", str(number)), ("uploadId", upload_id)),
                    payload=data,
                )
                etag = _part_etag(response)
            record.setdefault("parts", {})[str(number)] = {"size": amount, "md5": md5, "etag": etag}
            atomic_json(journal, record)
            sent += amount
            self._set(model_id, bytes_current=sent)
            item = ElementTree.SubElement(complete, "Part")
            ElementTree.SubElement(item, "PartNumber").text = str(number)
            ElementTree.SubElement(item, "ETag").text = '"' + etag + '"'
        payload = ElementTree.tostring(complete, encoding="utf-8")
        record["state"] = "completing"
        atomic_json(journal, record)
        response = await self._request(
            "POST", key, params=(("uploadId", upload_id),), payload=payload
        )
        root = _xml(response.content, "CompleteMultipartUploadResult")
        if _text(root, "Bucket") != self.binding.volume_id or not _matches_key(
            _text(root, "Key"), key
        ):
            raise StorageTransferError("Storage did not confirm the completed file.")

    async def _file(self, model_id: str, repo: str, revision: str, entry: ManifestFile) -> dict:
        key = self._key(repo, revision, entry)
        self._set(model_id, current_file=entry.path, file_bytes=entry.size_bytes)
        journal, stage, record = self._journal(repo, revision, entry, key)
        remote_digest = await self._remote_hash(model_id, key, entry)
        if remote_digest is None:
            if record.get("state") == "completing":
                raise StorageTransferError("Upload completion needs checking before it can resume.")
            target, digest = await self._stage(model_id, repo, revision, entry, stage)
            self._set(model_id, state="uploading", bytes_current=0)
            if entry.size_bytes <= self.limits.single_put_bytes:
                await self._put(model_id, key, target, entry, digest)
            else:
                if (
                    entry.size_bytes + self.limits.part_bytes - 1
                ) // self.limits.part_bytes > 10000:
                    raise StorageTransferError("This file needs different upload settings.")
                await self._multipart(model_id, key, target, entry, journal, record)
            record["state"] = "uploaded"
            atomic_json(journal, record)
            remote_digest = await self._remote_hash(model_id, key, entry)
            if remote_digest is None:
                raise StorageTransferError("The saved model file did not pass its checksum check.")
        verified_target = bound_path(stage, entry.path, allowed_root=stage)
        verified_target.unlink(missing_ok=True)
        partial = bound_path(stage, entry.path + ".vcs-incomplete", allowed_root=stage)
        partial.unlink(missing_ok=True)
        record["state"] = "checked"
        atomic_json(journal, record)
        return {
            "repo": repo,
            "revision": revision,
            "path": entry.path,
            "size_bytes": entry.size_bytes,
            "sha256": remote_digest,
        }

    async def transfer_model(self, model_id: str) -> dict:
        """Exclusive staged transfer. Completion is file evidence, never GPU readiness."""
        if model_id not in REQUIRED_MODEL_IDS:
            raise ValueError("This model is not part of required storage setup")
        if self._closing:
            raise StorageTransferError("Storage transfers are stopping.")
        async with self._exclusive:
            if self._closing:
                raise StorageTransferError("Storage transfers are stopping.")
            with self._process_lock():
                self._cancel.clear()
                self._active_task = asyncio.current_task()
                self._set(model_id, state="discovering", bytes_current=0, files_checked=0)
                try:
                    rows = []
                    for repo, revision in self.graph(model_id):
                        validate_pin(repo, revision)
                        entries = await asyncio.to_thread(self.manifest_loader, repo, revision)
                        rows.extend((repo, revision, entry) for entry in entries)
                    total = sum(entry.size_bytes for _, _, entry in rows)
                    self._set(model_id, bytes_total=total, files_total=len(rows), bytes_checked=0)
                    evidence = []
                    checked = 0
                    for repo, revision, entry in rows:
                        self._check_pause()
                        evidence.append(await self._file(model_id, repo, revision, entry))
                        checked += entry.size_bytes
                        self._set(model_id, files_checked=len(evidence), bytes_checked=checked)
                    self._set(model_id, state="files_checked", current_file=None, bytes_current=0)
                    return {
                        "model_id": model_id,
                        "files": evidence,
                        "bytes_total": total,
                        "files_checked": True,
                        "ready": False,
                    }
                except (InterruptedError, asyncio.CancelledError):
                    self._cancel.set()
                    self._set(model_id, state="paused", detail="Downloads paused.")
                    raise
                except StorageTransferError as error:
                    self._set(model_id, state="failed", detail=str(error))
                    raise
                except Exception:
                    self._set(model_id, state="failed", detail="Model transfer could not finish.")
                    raise StorageTransferError("Model transfer could not finish.") from None
                finally:
                    self._active_task = None
