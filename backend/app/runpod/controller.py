"""Desktop-owned persistent storage and bounded disposable compute sessions.

Status reads never provision compute. Mutations require a reviewed setup quote
or the user's saved per-session compute policy. Provider deadlines are sent in
the creation request, including during an ambiguous desktop/network failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Settings
from ..exceptions import GenerationError
from ..inference.catalog import CATALOG
from ..inference.protocol import ModelStatus
from ..inference.remote_scheduler import RemoteScheduler
from ..inference.spec import ModelState
from ..remote_worker.model_install import REQUIRED_MODEL_IDS, model_graph, release_manifest_id
from ..remote_worker.model_pins import AUXILIARY_PINS
from .client import RunpodApiError, RunpodClient
from .secrets import RunpodKeyStore
from .worker_pair import WorkerPair, WorkerPairStore

MODEL_FILES_BYTES = 49_014_734_674
CAPACITY_MANIFEST_ID = "b7307be7b6a2308c2ee909e8f1b4ff01ffad9f7a8f387b9657cc1b0dd65e6182"
STORAGE_RESERVE_BYTES = 10_000_000_000
STORAGE_GB = math.ceil((MODEL_FILES_BYTES + STORAGE_RESERVE_BYTES) / 1_000_000_000)
STORAGE_MONTHLY_USD = round(STORAGE_GB * 0.07, 2)
EVIDENCE_VERSION = 2
INSTALLER_TIMEOUT_SEC = 2 * 3600
REQUIRED_MODELS = REQUIRED_MODEL_IDS
# Explicit request/authentication/payment/validation/rate-limit rejections.
# A timeout, conflict, unrecognized error or server failure may follow a
# successful creation, so those must retain the persisted reconciliation fence.
STORAGE_REJECTION_STATUSES = frozenset({400, 401, 402, 403, 422, 429})


class CloudSetupError(RuntimeError):
    pass


class CloudAvailabilityError(CloudSetupError):
    """Safe to retry: no resource creation has been attempted."""


def _available(value: object) -> bool:
    return value in {"HIGH", "MEDIUM", "LOW"}


def gpu_candidates(gpus: list[dict], region: str, max_hourly: float) -> list[dict]:
    """Current regional stock, NVIDIA/CUDA, 48 GB full feature residency."""
    choices = []
    for gpu in gpus:
        rate = gpu.get("price", {}).get("secure")
        regional = next((dc for dc in gpu.get("dataCenters", []) if dc.get("id") == region), {})
        memory = gpu.get("memory", 0)
        if (
            str(gpu.get("id", "")).startswith("NVIDIA ")
            and gpu.get("secure") is not False
            and isinstance(memory, (int, float))
            and memory >= 48
            and isinstance(rate, (int, float))
            and math.isfinite(rate)
            and 0 < rate <= max_hourly
            and _available(regional.get("availability"))
        ):
            choices.append(
                {
                    "id": gpu["id"],
                    "name": gpu.get("name", gpu["id"]),
                    "hourly_usd": rate,
                    "vram_gb": memory,
                }
            )
    return sorted(choices, key=lambda entry: (entry["hourly_usd"], entry["id"]))


def installer_candidates(cpus: list[dict], region: str) -> list[dict]:
    """Exact regional CPU configurations and their per-vCPU quote."""
    choices = []
    for cpu in cpus:
        stock = next((dc for dc in cpu.get("dataCenters", []) if dc.get("id") == region), {})
        flavor = cpu.get("id")
        price = cpu.get("price", {}).get("securePerVcpu")
        count, maximum = cpu.get("vcpu", {}).get("min"), cpu.get("vcpu", {}).get("max")
        ratio = cpu.get("ramGbPerVcpu")
        if (
            not _available(stock.get("availability"))
            or not isinstance(flavor, str)
            or not re.fullmatch(r"[A-Za-z0-9_]+", flavor)
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(ratio)
            or ratio <= 0
        ):
            continue
        count = max(2, count)
        memory = count * ratio
        rate = price * count
        if (
            count > maximum
            or memory < 4
            or not float(memory).is_integer()
            or not math.isfinite(rate)
        ):
            continue
        choices.append(
            {
                "instance_id": f"{flavor}-{count}-{int(memory)}",
                "hourly_usd": rate,
                "vcpu": count,
                "memory_gb": int(memory),
            }
        )
    return sorted(choices, key=lambda item: (item["hourly_usd"], item["instance_id"]))


def verified_models(value: dict) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("manifest_id") != release_manifest_id():
        return False
    models = value.get("models", {})
    if not isinstance(models, dict):
        return False
    for model_id in REQUIRED_MODELS:
        item = models.get(model_id, {})
        if not isinstance(item, dict):
            return False
        spec = CATALOG.get(model_id)
        repo, revision = (spec.hf_repo, spec.hf_revision) if spec else AUXILIARY_PINS[model_id]
        evidence = item.get("evidence", {})
        if not isinstance(evidence, dict):
            return False
        files = evidence.get("files", [])
        expected_graph = set(model_graph(model_id))
        if not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
            return False
        if (
            any(
                not isinstance(f.get("repo"), str) or not isinstance(f.get("revision"), str)
                for f in files
            )
            or {(f.get("repo"), f.get("revision")) for f in files} != expected_graph
        ):
            return False
        if (
            any(
                not isinstance(f.get("size_bytes"), int)
                or f["size_bytes"] < 0
                or not isinstance(f.get("path"), str)
                or not f["path"]
                for f in files
            )
            or item.get("files_total") != len(files)
            or item.get("files_verified") != len(files)
            or sum(f["size_bytes"] for f in files) != item.get("bytes_total")
            or item.get("bytes_completed") != item.get("bytes_total")
            or evidence.get("bytes_total") != item.get("bytes_total")
        ):
            return False
        if (
            item.get("state") != "installed"
            or evidence.get("version") != EVIDENCE_VERSION
            or evidence.get("model_id") != model_id
            or evidence.get("revision") != revision
            or not files
            or not any(f.get("repo") == repo and f.get("revision") == revision for f in files)
            or any(
                not isinstance(f.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", f["sha256"])
                for f in files
            )
            or len({(f.get("repo"), f.get("revision"), f.get("path")) for f in files}) != len(files)
        ):
            return False
    return value.get("ready") is True


class CloudController:
    def __init__(
        self, settings: Settings, *, client_factory=RunpodClient, remote_factory=RemoteScheduler
    ):
        self.settings = settings
        self.path = settings.data_dir / "cloud-state.json"
        self.client_factory = client_factory
        self.remote_factory = remote_factory
        self.lock = asyncio.Lock()
        self.active = 0
        self.setup_task: asyncio.Task | None = None
        self.cancel_task: asyncio.Task | None = None
        self._cancel_requested = False
        self.auto_task: asyncio.Task | None = None
        self.release_task: asyncio.Task | None = None
        self.pending: Callable[[], Awaitable[int]] | None = None
        self.progress: dict = {}
        self.detail = ""
        self.quote: dict | None = None

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError
            return state
        except (ValueError, OSError) as exc:
            raise CloudSetupError(
                "Cannot read saved cloud setup; restore the local state file"
            ) from exc

    def write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.path)

    def key(self) -> str:
        key = RunpodKeyStore(self.settings.data_dir).get_key()
        if not key:
            raise CloudSetupError("Connect your Runpod account first")
        state = self.read()
        if state.get("account") and state["account"] != hashlib.sha256(key.encode()).hexdigest():
            raise CloudSetupError(
                "This setup belongs to a different API key; reconnect the original key"
            )
        return key

    def images(self) -> dict:
        path = Path(__file__).with_name("release.json")
        if not path.is_file():
            raise CloudSetupError(
                "The cloud worker release is still being built. Refresh after the app update."
            )
        images = json.loads(path.read_text(encoding="utf-8"))
        if not all(
            isinstance(images.get(k), str) and "@sha256:" in images[k] for k in ("gpu", "installer")
        ):
            raise CloudSetupError("Cloud worker release evidence is incomplete")
        return images

    def _setup_status(
        self, phase: str, *, error: str | None = None, cleanup_pending: bool = False
    ) -> None:
        state = self.read()
        state.update(
            setup_phase=phase,
            setup_error=error,
            cleanup_pending=cleanup_pending,
            setup_detail=self.detail,
        )
        self.write(state)

    async def snapshot(self) -> dict:
        state = self.read()
        connected = RunpodKeyStore(self.settings.data_dir).has_key()
        stage = "connect_account" if not connected else "select_storage"
        if state.get("volume"):
            stage = "ready" if self.is_ready(state) else "download_models"
        if self.setup_task and not self.setup_task.done():
            stage = "download_models"
        running = bool(self.setup_task and not self.setup_task.done())
        phase = state.get("setup_phase", "ready" if self.is_ready(state) else "idle")
        error = state.get("setup_error")
        active_phases = {
            "starting_worker",
            "checking_files",
            "downloading",
            "verifying",
            "stopping_worker",
        }
        cancelling = bool(self.cancel_task and not self.cancel_task.done())
        if not running and not cancelling and phase == "cancelling":
            phase = "cancelled"
            error = error or (
                "Model download was stopped. Check the pending machine to confirm it is off."
            )
        if not running and (
            phase in active_phases
            or (
                (state.get("compute") or {}).get("kind") == "installer"
                and not self.is_ready(state)
                and phase not in {"cancelling", "cancelled"}
            )
        ):
            phase = "failed"
            error = error or (
                "The last model setup did not finish. Check the pending machine before retrying."
            )
        compute = state.get("compute")
        if compute:
            compute = {**compute, "creation_confirmed": bool(compute.get("pod_id"))}
        return {
            "connected": connected,
            "stage": stage,
            "volume": state.get("volume"),
            "capacity": state.get("capacity"),
            "pause_reason": state.get("pause_reason"),
            "storage_min_gb": STORAGE_GB,
            "model_files_bytes": MODEL_FILES_BYTES,
            "reserve_bytes": STORAGE_RESERVE_BYTES,
            "ready": self.is_ready(state),
            "models": self.progress.get("models", state.get("models", {})),
            "required_model_ids": list(REQUIRED_MODELS),
            "progress_pct": self.progress.get("progress_pct"),
            "bytes_completed": self.progress.get("bytes_completed", 0),
            "bytes_total": self.progress.get("bytes_total"),
            "detail": self.detail or state.get("setup_detail", ""),
            "setup_phase": phase,
            "setup_error": error,
            "setup_running": running,
            "cleanup_pending": bool(
                state.get("cleanup_pending") or (phase in {"failed", "cancelled"} and compute)
            ),
            "auto_setup_enabled": state.get(
                "auto_setup_enabled",
                False if (state.get("last_compute") or {}).get("pod_id") else None,
            ),
            "auto_setup_waiting": bool(state.get("auto_setup_waiting")),
            "auto_setup_retry_at": state.get("auto_setup_retry_at"),
            "auto_setup_requires_resume": bool(
                state.get("auto_setup_requires_resume")
                or (
                    "auto_setup_enabled" not in state
                    and (state.get("last_compute") or {}).get("pod_id")
                )
            ),
            "compute": compute,
            "policy": state.get("policy"),
            "release_available": Path(__file__).with_name("release.json").is_file(),
        }

    def is_ready(self, state: dict) -> bool:
        store = RunpodKeyStore(self.settings.data_dir)
        if not store.has_key():
            return False
        if state.get("account"):
            try:
                key = store.get_key()
                if not key or hashlib.sha256(key.encode()).hexdigest() != state["account"]:
                    return False
            except OSError:
                return False
        return bool(
            state.get("ready")
            and state.get("manifest_id") == release_manifest_id()
            and state.get("evidence_version") == EVIDENCE_VERSION
            and not state.get("cleanup_pending")
            and (state.get("compute") or {}).get("kind") != "installer"
            and state.get("setup_phase")
            not in {
                "starting_worker",
                "checking_files",
                "downloading",
                "verifying",
                "stopping_worker",
                "failed",
                "cancelling",
                "cancelled",
            }
        )

    async def discover(self) -> dict:
        key = self.key()
        client = self.client_factory(key)
        try:
            volumes, balance, centers, gpus, cpus = await asyncio.gather(
                client.list_volumes(),
                client.balance(),
                client.list_data_centers(),
                client.list_gpu_types(),
                client.list_cpu_types(),
            )
        finally:
            await client.close()
        regions = []
        for center in centers:
            region = center.get("id")
            choices = gpu_candidates(gpus, region, 2.0)
            cpu_choices = installer_candidates(cpus, region)
            if choices and cpu_choices:
                regions.append(
                    {
                        "id": region,
                        "name": center.get("name", region),
                        "gpu_hourly_from_usd": choices[0]["hourly_usd"],
                        "installer_hourly_from_usd": cpu_choices[0]["hourly_usd"],
                    }
                )
        regions.sort(key=lambda r: (r["gpu_hourly_from_usd"], r["id"]))
        saved = self.read().get("volume") or {}
        safe = [
            {
                **{k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")},
                "app_owned": volume.get("id") == saved.get("id") and bool(saved.get("app_owned")),
                "app_candidate": bool(str(volume.get("name", "")).startswith("voice-clone-")),
                "selected": volume.get("id") == saved.get("id"),
            }
            for volume in volumes
        ]
        return {
            "volumes": safe,
            "regions": regions,
            **balance,
            "storage_gb": STORAGE_GB,
            "storage_min_gb": STORAGE_GB,
            "model_files_bytes": MODEL_FILES_BYTES,
            "reserve_bytes": STORAGE_RESERVE_BYTES,
            "selected_volume_id": saved.get("id"),
            "storage_monthly_usd": STORAGE_MONTHLY_USD,
            "funding_url": "https://www.runpod.io/console/user/billing",
        }

    async def quote_storage(
        self,
        region: str,
        size_gb: int | None = None,
        name: str | None = None,
        replace_current: bool = False,
    ) -> dict:
        if release_manifest_id() != CAPACITY_MANIFEST_ID:
            raise CloudSetupError(
                "Model storage requirements changed. Update the app before buying storage."
            )
        size_gb = STORAGE_GB if size_gb is None else size_gb
        if (
            isinstance(size_gb, bool)
            or not isinstance(size_gb, int)
            or not STORAGE_GB <= size_gb <= 4000
        ):
            raise CloudSetupError(f"Choose storage between {STORAGE_GB} and 4000 GB")
        name = name.strip() if name is not None else None
        if name is not None and (not name or len(name) > 64 or any(ord(c) < 32 for c in name)):
            raise CloudSetupError("Enter a storage name between 1 and 64 characters")
        discovery = await self.discover()
        selected = next((r for r in discovery["regions"] if r["id"] == region), None)
        if selected is None:
            raise CloudSetupError(
                "No compatible GPU and installer stock in this region; refresh availability"
            )
        # A month of storage is a reserve policy, not an upfront provider invoice.
        monthly = round(min(size_gb, 1000) * 0.07 + max(0, size_gb - 1000) * 0.05, 2)
        reserve = round(monthly / 30 + 1.0, 4)
        balance = discovery["balance_usd"]
        self.quote = {
            "id": secrets.token_hex(16),
            "expires_at": time.time() + 300,
            "region": region,
            "storage_gb": size_gb,
            "storage_name": name,
            "replace_current": replace_current,
            "previous_volume_id": (self.read().get("volume") or {}).get("id"),
            "storage_min_gb": STORAGE_GB,
            "model_files_bytes": MODEL_FILES_BYTES,
            "reserve_bytes": STORAGE_RESERVE_BYTES,
            "capacity_manifest_id": CAPACITY_MANIFEST_ID,
            "monthly_usd": monthly,
            "required_credit_reserve_usd": reserve,
            "balance_usd": balance,
            "can_purchase": balance is not None and balance >= reserve,
            "installer_budget_usd": 1.0,
            **selected,
        }
        # Region ID must not overwrite the opaque approval quote ID.
        self.quote["id"] = secrets.token_hex(16)
        return self.quote

    async def purchase_storage(self, quote_id: str) -> dict:
        async with self.lock:
            self.images()
            quote = self.quote
            if not quote or quote["id"] != quote_id or quote["expires_at"] < time.time():
                raise CloudSetupError("Storage quote expired; refresh the quote before purchasing")
            state = self.read()
            if (
                state.get("compute")
                or self.active
                or any(
                    task and not task.done()
                    for task in (self.setup_task, self.auto_task, self.cancel_task)
                )
            ):
                raise CloudSetupError("Pause setup before changing storage")
            if state.get("volume") and not quote.get("replace_current"):
                raise CloudSetupError("Storage is already selected")
            if (state.get("volume") or {}).get("id") != quote.get("previous_volume_id"):
                if state.get("volume"):
                    raise CloudSetupError(
                        "Storage selection changed. Review the new storage price again."
                    )
            key = self.key()
            client = self.client_factory(key)
            try:
                funds = await client.balance()
                if funds["balance_usd"] is None:
                    raise CloudSetupError("Balance is unknown; enable account reads and refresh")
                if funds["balance_usd"] < quote["required_credit_reserve_usd"]:
                    raise CloudSetupError("Add Runpod credit and refresh before purchasing storage")
                volumes = await client.list_volumes()
                operation = state.get("volume_operation")
                prior_binding = state.get("volume_operation_request")
                if state.get("volume_operation_sent") and not operation:
                    raise CloudSetupError(
                        "An earlier storage purchase is uncertain. Check Runpod first."
                    )
                saved_volume = state.get("volume") or {}
                if operation and not prior_binding and saved_volume:
                    old_matches = [v for v in volumes if v.get("name") == operation]
                    if (
                        len(old_matches) == 1
                        and old_matches[0].get("id") == saved_volume.get("id")
                        and saved_volume.get("name") == operation
                    ):
                        # 0.1.4 kept the completed purchase guard after selecting
                        # its result. Clear only this provider-confirmed old result;
                        # an unknown later POST must remain fenced.
                        for field in (
                            "volume_operation",
                            "volume_operation_sent",
                            "volume_operation_request",
                        ):
                            state.pop(field, None)
                        operation = None
                        self.write(state)
                intent = {
                    "storage_name": quote.get("storage_name"),
                    "region": quote["region"],
                    "storage_gb": quote.get("storage_gb", STORAGE_GB),
                    "previous_volume_id": quote.get("previous_volume_id"),
                    "replace_current": bool(quote.get("replace_current")),
                }
                if operation and state.get("volume_operation_sent"):
                    if (not isinstance(prior_binding, dict)
                            or prior_binding.get("intent") != intent
                            or prior_binding.get("operation_name") != operation
                            or not prior_binding.get("quote_id")):
                        raise CloudSetupError(
                            "An earlier storage purchase is uncertain. "
                            "Check it before buying different storage."
                        )
                elif operation and isinstance(prior_binding, dict):
                    if prior_binding.get("intent") != intent:
                        # An explicit provider rejection is safe to requote with
                        # changed choices. An ambiguous request never reaches here.
                        operation = None
                operation = (
                    operation or quote.get("storage_name") or f"voice-clone-{secrets.token_hex(8)}"
                )
                state.update(
                    account=hashlib.sha256(key.encode()).hexdigest(),
                    volume_operation=operation,
                    volume_operation_request={
                        "quote_id": (prior_binding or {}).get("quote_id", quote["id"])
                        if state.get("volume_operation_sent")
                        else quote["id"],
                        "intent": intent,
                        "operation_name": operation,
                    },
                )
                self.write(
                    state
                )  # Persist before POST; ambiguous retries reconcile by unique name.
                matches = [v for v in volumes if v.get("name") == operation]
                if len(matches) > 1:
                    raise CloudSetupError(
                        "Multiple storage results found; reconcile in Runpod before retrying"
                    )
                if matches and not state.get("volume_operation_sent"):
                    state.pop("volume_operation", None)
                    state.pop("volume_operation_request", None)
                    self.write(state)
                    raise CloudSetupError(
                        "A volume already has this name. Choose another name or use Advanced."
                    )
                if not matches and state.get("volume_operation_sent"):
                    raise CloudSetupError(
                        "Storage purchase result is uncertain; refresh to reconcile before retrying"
                    )
                if matches:
                    volume = matches[0]
                else:
                    state["volume_operation_sent"] = True
                    self.write(state)
                    try:
                        volume = await client.create_volume(
                            name=operation,
                            data_center=quote["region"],
                            size_gb=quote.get("storage_gb", STORAGE_GB),
                        )
                    except RunpodApiError as exc:
                        if exc.status_code not in STORAGE_REJECTION_STATUSES:
                            raise
                        # The provider explicitly rejected this request. Keep
                        # its name for reconciliation, but permit a newly
                        # approved attempt after funds/permissions are fixed.
                        state["volume_operation_sent"] = False
                        self.write(state)
                        self.quote = None
                        message = (
                            "Runpod rejected payment; add credit and refresh the storage quote"
                            if exc.status_code == 402
                            else "Runpod rejected the storage request "
                            f"(HTTP {exc.status_code}); resolve it and refresh the storage quote"
                        )
                        raise CloudSetupError(message) from exc
                if (
                    volume.get("size", 0) != quote.get("storage_gb", STORAGE_GB)
                    or volume.get("dataCenter") != quote["region"]
                    or volume.get("type") != "STANDARD"
                ):
                    raise CloudSetupError("Provider storage does not match the approved quote")
                state["volume"] = {
                    k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")
                }
                state["volume"].update(app_owned=True, cache_namespace="voice-clone")
                state.pop("volume_operation", None)
                state.pop("volume_operation_sent", None)
                state.pop("volume_operation_request", None)
                state["capacity"] = None
                state.update(
                    models={},
                    models_verified=False,
                    auto_setup_enabled=None,
                    setup_phase="idle",
                    setup_error=None,
                    pause_reason=None,
                    auto_setup_attempt_consumed=False,
                    auto_setup_requires_resume=False,
                )
                state["ready"] = False
                self.write(state)
                self.quote = None
                return state["volume"]
            finally:
                await client.close()

    async def select_storage(self, volume_id: str, allow_other_app_volume: bool = False) -> dict:
        async with self.lock:
            state = self.read()
            if (
                state.get("compute")
                or any(
                    task and not task.done()
                    for task in (self.setup_task, self.auto_task, self.cancel_task)
                )
                or self.active
            ):
                raise CloudSetupError("Finish the current cloud operation before changing storage")
            client = self.client_factory(self.key())
            try:
                volumes = await client.list_volumes()
            finally:
                await client.close()
            volume = next((v for v in volumes if v.get("id") == volume_id), None)
            if not volume or volume.get("size", 0) < STORAGE_GB or volume.get("type") != "STANDARD":
                raise CloudSetupError(f"Choose Standard storage with at least {STORAGE_GB} GB")
            saved = state.get("volume") or {}
            if saved.get("id") == volume_id:
                return saved  # Revisiting the current choice keeps verified readiness.
            owned = volume_id == saved.get("id") and bool(saved.get("app_owned"))
            candidate = str(volume.get("name", "")).startswith("voice-clone-")
            if not (owned or candidate or allow_other_app_volume):
                raise CloudSetupError(
                    "Choose this app's storage, or select another volume in Advanced"
                )
            state.update(
                account=hashlib.sha256(self.key().encode()).hexdigest(),
                volume={k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")},
                ready=False,
                models={},
                capacity=None,
                models_verified=False,
                auto_setup_enabled=None,
                auto_setup_attempt_consumed=False,
                auto_setup_requires_resume=False,
                setup_phase="idle",
                setup_error=None,
                pause_reason=None,
            )
            state["volume"].update(
                app_owned=owned,
                app_candidate=candidate,
                explicitly_selected=allow_other_app_volume,
                cache_namespace="isolated"
                if allow_other_app_volume and not (owned or candidate)
                else "legacy",
            )
            self.write(state)
            return state["volume"]

    async def set_policy(self, max_session_usd: float, max_hourly_usd: float) -> None:
        if not (0.1 <= max_session_usd <= 20 and 0.1 <= max_hourly_usd <= 10):
            raise CloudSetupError("Choose session and hourly limits within the displayed range")
        async with self.lock:
            state = self.read()
            state["policy"] = {"max_session_usd": max_session_usd, "max_hourly_usd": max_hourly_usd}
            self.write(state)

    async def start_setup(self) -> None:
        async with self.lock:
            if self.cancel_task and not self.cancel_task.done():
                raise CloudSetupError("Model setup is still being cancelled; wait for cleanup")
            if self.setup_task and not self.setup_task.done():
                return
            if self.read().get("compute"):
                raise CloudSetupError("Check the pending machine before starting model setup again")
            self.images()  # Do not buy resources for a missing/unpublished release.
            if not self.read().get("volume"):
                raise CloudSetupError("Select persistent storage first")
            if self.active:
                raise CloudSetupError("A generation is still active")
            state = self.read()
            state["ready"] = False
            state["models_verified"] = False
            state["auto_setup_retryable"] = False
            self._cancel_requested = False
            self.write(state)
            self.detail = (
                "Starting a temporary CPU worker to verify storage and download missing files"
            )
            self.progress = {}
            self._setup_status("starting_worker")
            self.setup_task = asyncio.create_task(self._setup())

    async def set_auto_setup(self, enabled: bool) -> dict:
        if not enabled:
            return await self.cancel_setup()
        async with self.lock:
            if self.cancel_task and not self.cancel_task.done():
                raise CloudSetupError("Model setup is still being cancelled; wait for cleanup")
            state = self.read()
            if not state.get("volume"):
                raise CloudSetupError("Select persistent storage before automatic model setup")
            if state.get("compute") or self.active:
                raise CloudSetupError("Check the pending machine before resuming automatic setup")
            state.update(
                auto_setup_enabled=True,
                auto_setup_requires_resume=False,
                auto_setup_attempt_consumed=False,
                pause_reason=None,
            )
            self.write(state)
        await self.resume_auto_setup()
        return await self.snapshot()

    async def resume_auto_setup(self) -> None:
        """Resume saved, explicit download intent without buying storage or new GPUs."""
        state = self.read()
        if not state.get("auto_setup_enabled") or not state.get("volume") or self.is_ready(state):
            return
        if state.get("pause_reason") == "app_exit":
            if state.get("compute"):
                async with self.lock:
                    try:
                        await self._release()
                    except (CloudSetupError, RunpodApiError, OSError, ValueError):
                        return  # Preserve intent and the uncertain-resource fence.
                state = self.read()
            if state.get("app_exit_resume_allowed") and not state.get("compute"):
                state.update(
                    auto_setup_attempt_consumed=False,
                    pause_reason=None,
                    app_exit_resume_allowed=False,
                    cleanup_pending=False,
                )
                self.write(state)
        if state.get("compute") or state.get("auto_setup_attempt_consumed"):
            state.update(
                auto_setup_enabled=False,
                auto_setup_requires_resume=True,
                auto_setup_waiting=False,
                auto_setup_retry_at=None,
            )
            self.write(state)
            return
        if not self.auto_task or self.auto_task.done():
            self.auto_task = asyncio.create_task(self._auto_setup())

    async def _auto_setup(self) -> None:
        # Retry only CPU availability before creation. Ten one-minute retries
        # are bounded; a paid/ambiguous attempt always requires explicit resume.
        for attempt in range(11):
            state = self.read()
            if not state.get("auto_setup_enabled") or self.is_ready(state):
                return
            state.update(auto_setup_waiting=False, auto_setup_retry_at=None)
            self.write(state)
            try:
                await self.start_setup()
                if self.setup_task:
                    await asyncio.shield(self.setup_task)
            except (CloudSetupError, RunpodApiError, OSError, ValueError) as exc:
                self.detail = str(exc)
                self._setup_status(
                    "failed", error=self.detail, cleanup_pending=bool(self.read().get("compute"))
                )
            state = self.read()
            if not state.get("auto_setup_enabled") or self.is_ready(state):
                return
            if (
                state.get("auto_setup_retryable")
                and not state.get("compute")
                and not state.get("auto_setup_attempt_consumed")
                and attempt < 10
            ):
                state.update(
                    auto_setup_waiting=True,
                    auto_setup_retry_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
                )
                self.write(state)
                await asyncio.sleep(60)
                continue
            state.update(
                auto_setup_enabled=False,
                auto_setup_requires_resume=True,
                auto_setup_waiting=False,
                auto_setup_retry_at=None,
            )
            if (
                state.get("auto_setup_retryable")
                and not state.get("compute")
                and not state.get("auto_setup_attempt_consumed")
            ):
                self.detail = (
                    "No download machine became available. Setup paused without renting compute. "
                    "Resume when capacity is available."
                )
                state.update(setup_error=self.detail, setup_detail=self.detail)
            self.write(state)
            return

    async def cancel_setup(self) -> dict:
        """Stop setup and owned installer compute, retaining the persistent cache.

        A separate task shields cleanup from an HTTP client disconnect. Cancellation
        must happen before taking the provisioning lock: creation/startup hold it.
        Unknown creation outcomes retain their ownership record until reconciled.
        """
        if not self.cancel_task or self.cancel_task.done():
            state = self.read()
            if self.active or (state.get("compute") or {}).get("kind") == "generation":
                raise CloudSetupError("A voice generation is active; cancel it in the queue first")
            if self.pending and await self.pending():
                raise CloudSetupError("Cancel queued voice generations before stopping cloud setup")
            # Recheck after the queued-job callback yielded.
            state = self.read()
            if self.active or (state.get("compute") or {}).get("kind") == "generation":
                raise CloudSetupError("A voice generation is active; cancel it in the queue first")
            self._cancel_requested = True
            state.update(
                auto_setup_enabled=False,
                auto_setup_waiting=False,
                auto_setup_retry_at=None,
                pause_reason="manual",
                app_exit_resume_allowed=False,
            )
            self.write(state)
            if self.auto_task and not self.auto_task.done():
                self.auto_task.cancel()
            self.cancel_task = asyncio.create_task(self._cancel_setup())
        await asyncio.shield(self.cancel_task)
        return await self.snapshot()

    async def pause_for_app_exit(self, *, queue_paused: bool = False) -> dict:
        """Stop an owned installer on app exit, keeping eligible automatic intent."""
        state = self.read()
        if self.active or (
            not queue_paused and (state.get("compute") or {}).get("kind") == "generation"
        ):
            raise CloudSetupError("Wait for voice generation to finish before closing")
        if not queue_paused and self.pending and await self.pending():
            raise CloudSetupError("Cancel queued generations before closing")
        state = self.read()
        if self.active or (
            not queue_paused and (state.get("compute") or {}).get("kind") == "generation"
        ):
            raise CloudSetupError("Wait for voice generation to finish before closing")
        was_ready = self.is_ready(state)
        if self.is_ready(state) and not state.get("compute"):
            return await self.snapshot()
        intent = bool(state.get("auto_setup_enabled"))
        allow_resume = bool(
            intent
            and state.get("setup_phase") != "failed"
            and not state.get("auto_setup_requires_resume")
        )
        if state.get("pause_reason") == "app_exit":
            allow_resume = bool(state.get("app_exit_resume_allowed"))
        self._cancel_requested = True
        state.update(
            pause_reason="app_exit",
            app_exit_resume_allowed=allow_resume,
            auto_setup_waiting=False,
            auto_setup_retry_at=None,
        )
        self.write(state)
        if self.auto_task and not self.auto_task.done():
            self.auto_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.auto_task
        if not self.cancel_task or self.cancel_task.done():
            self.cancel_task = asyncio.create_task(self._cancel_setup())
        await asyncio.shield(self.cancel_task)
        state = self.read()
        state.update(
            auto_setup_enabled=intent, pause_reason="app_exit", app_exit_resume_allowed=allow_resume
        )
        if was_ready and not state.get("compute") and not state.get("cleanup_pending"):
            state.update(ready=True, setup_phase="ready", setup_error=None)
        self.write(state)
        return await self.snapshot()

    async def _cancel_setup(self) -> None:
        self.detail = (
            "Stopping model setup and the temporary download machine. Stored files are kept."
        )
        state = self.read()
        state["ready"] = False
        self.write(state)
        self._setup_status("cancelling", cleanup_pending=bool(state.get("compute")))
        task = self.setup_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        async with self.lock:
            error = None
            try:
                await self._release()
            except (CloudSetupError, RunpodApiError, OSError, ValueError):
                error = (
                    "Model setup is cancelled, but Runpod has not confirmed the temporary "
                    "machine is off. Downloads and compute charges may continue until it stops. "
                    "Check the pending machine before updating or retrying."
                )
            self.detail = error or (
                "Setup paused."
                if self.read().get("pause_reason") == "app_exit"
                else "Model setup paused."
            )
            models = self.progress.get("models", self.read().get("models", {}))
            models = {key: dict(value) for key, value in models.items()}
            for value in models.values():
                if value.get("state") in {"discovering", "verifying", "downloading"}:
                    value.update(
                        state="cancelled", detail="Stopped; stored bytes are kept for retry"
                    )
            self.progress["models"] = models
            state = self.read()
            state.update(models=models, ready=False)
            self.write(state)
            self._setup_status("cancelled", error=error, cleanup_pending=bool(state.get("compute")))

    async def _worker_call(self, pair: WorkerPair, method: str, path: str) -> dict:
        remote = self.remote_factory(pair.url, pair.token, CATALOG)
        try:
            value = (await remote._response(method, path)).json()
            if not isinstance(value, dict) or value.get("protocol_version") != 1:
                raise CloudSetupError("Cloud worker protocol does not match this app")
            if path == "/v1/setup" and (
                not isinstance(value.get("models"), dict)
                or any(not isinstance(item, dict) for item in value["models"].values())
            ):
                raise CloudSetupError("Cloud worker returned invalid model evidence")
            return value
        finally:
            await remote.shutdown()

    async def _provision(self, *, installer: bool) -> WorkerPair:
        state = self.read()
        if state.get("compute"):
            raise CloudSetupError(
                "An earlier app-owned cloud session needs cleanup before another is started"
            )
        volume = state.get("volume")
        if not volume:
            raise CloudSetupError("Select storage first")
        client = self.client_factory(self.key())
        try:
            cpu_instance_id = None
            if installer:
                choices = installer_candidates(await client.list_cpu_types(), volume["dataCenter"])
                if not choices:
                    raise CloudAvailabilityError(
                        "Waiting for a download machine beside your storage. "
                        "No models are downloading yet; automatic setup will check again."
                    )
                rate, budget, gpu_id = choices[0]["hourly_usd"], 1.0, None
                cpu_instance_id = choices[0]["instance_id"]
            else:
                policy = state.get("policy")
                if not policy:
                    raise CloudSetupError(
                        "Approve the automatic compute spending limits in Runpod setup"
                    )
                choices = gpu_candidates(
                    await client.list_gpu_types(), volume["dataCenter"], policy["max_hourly_usd"]
                )
                if not choices:
                    raise CloudSetupError(
                        "No compatible GPU is available within your limit; refresh later"
                    )
                rate, budget, gpu_id = (
                    choices[0]["hourly_usd"],
                    policy["max_session_usd"],
                    choices[0]["id"],
                )
            funds = await client.balance()
            if funds["balance_usd"] is None or funds["balance_usd"] < budget:
                raise CloudSetupError("Add Runpod credit or enable balance reads, then refresh")
            seconds = min(INSTALLER_TIMEOUT_SEC if installer else 3600, int(budget / rate * 3600))
            if seconds < 300:
                raise CloudSetupError(
                    "Session limit is too small for startup; increase it in Runpod setup"
                )
            deadline = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
            name = f"voice-clone-{'install' if installer else 'generate'}-{secrets.token_hex(8)}"
            token = secrets.token_urlsafe(32)
            # Uncertain POST must never be replayed. Persist name before creation.
            state["compute"] = {
                "name": name,
                "kind": "installer" if installer else "generation",
                "pod_id": None,
                "deadline": deadline,
                "hourly_usd": rate,
                "budget_usd": budget,
                "status": "provisioning",
                "cpu_instance_id": cpu_instance_id,
            }
            if installer:
                state["auto_setup_attempt_consumed"] = True
            self.write(state)
            try:
                pod = await client.create_guarded_pod(
                    name=name,
                    image=self.images()["installer" if installer else "gpu"],
                    data_center=volume["dataCenter"],
                    volume_id=volume["id"],
                    cache_home=(
                        "/workspace/voice-clone/hf-cache"
                        if volume.get("cache_namespace") == "isolated"
                        else "/workspace/hf-cache"
                    ),
                    worker_token=token,
                    terminate_at=deadline,
                    gpu_id=gpu_id,
                    cpu_instance_id=cpu_instance_id,
                )
            except RunpodApiError as exc:
                # Parse/schema rejection proves the deployment resolver never ran.
                # Transport or resolver errors remain ambiguous and keep the fence.
                if exc.request_rejected:
                    state.pop("compute", None)
                    self.write(state)
                raise
            pair = WorkerPair(pod["id"], token)
            WorkerPairStore(self.settings.data_dir).set(pair)
            state["compute"].update(pod_id=pair.pod_id, status="starting")
            self.write(state)
            actual = pod.get("costPerHr")
            if (
                not isinstance(actual, (int, float))
                or not math.isfinite(actual)
                or actual <= 0
                or actual > rate
            ):
                raise CloudSetupError(
                    "Actual Pod price differs from the approved rate; session is being released"
                )
            self.detail = "Starting cloud worker; model loading is billed compute time"
            for _ in range(120):
                try:
                    await self._worker_call(pair, "GET", "/v1/health")
                    state["compute"]["status"] = "running"
                    self.write(state)
                    return pair
                except GenerationError:
                    await asyncio.sleep(5)
            raise CloudSetupError("Cloud worker did not start within 10 minutes")
        finally:
            await client.close()

    async def _setup(self) -> None:
        setup_error = None
        try:
            async with self.lock:
                pair = await self._provision(installer=True)
            self.detail = "Download worker is ready. Checking which stored model files are missing."
            self._setup_status("checking_files")
            await self._worker_call(pair, "POST", "/v1/capacity")
            stop_by = (self.read().get("compute") or {}).get("deadline")
            scan_limit = (
                int((datetime.fromisoformat(stop_by) - datetime.now(UTC)).total_seconds())
                if stop_by
                else INSTALLER_TIMEOUT_SEC
            )
            capacity = {}
            for _ in range(max(1, scan_limit // 2)):
                capacity = await self._worker_call(pair, "GET", "/v1/capacity")
                if isinstance(capacity.get("model_progress"), dict):
                    self.progress["models"] = capacity["model_progress"]
                if capacity.get("state") in {"complete", "failed", "cancelled"}:
                    break
                await asyncio.sleep(2)
            if (
                capacity.get("manifest_id") != release_manifest_id()
                or capacity.get("scan_complete") is not True
                or not isinstance(capacity.get("free_bytes"), int)
                or not isinstance(capacity.get("required_free_bytes"), int)
            ):
                raise CloudSetupError("Storage check did not finish. Try again.")
            state = self.read()
            state["capacity"] = capacity
            if capacity.get("app_owned") and isinstance(state.get("volume"), dict):
                state["volume"]["app_owned"] = True
            self.write(state)
            if capacity.get("sufficient") is not True:
                needed = math.ceil(capacity["required_free_bytes"] / 1_000_000_000)
                raise CloudSetupError(
                    f"Storage needs {needed} GB free. Increase its size in Runpod, then try again."
                )
            await self._worker_call(pair, "POST", "/v1/setup")
            deadline = (self.read().get("compute") or {}).get("deadline")
            remaining = (
                int((datetime.fromisoformat(deadline) - datetime.now(UTC)).total_seconds())
                if deadline
                else INSTALLER_TIMEOUT_SEC
            )
            for _ in range(max(1, remaining // 2)):
                self.progress = await self._worker_call(pair, "GET", "/v1/setup")
                states = {model.get("state") for model in self.progress.get("models", {}).values()}
                phase = (
                    "downloading"
                    if "downloading" in states
                    else ("verifying" if "verifying" in states else "checking_files")
                )
                self.detail = {
                    "downloading": "Downloading missing models to your Runpod storage.",
                    "verifying": "Checking model files before voice generation can start.",
                    "checking_files": "Checking which model files are already on your storage.",
                }[phase]
                if self.read().get("setup_phase") != phase:
                    self._setup_status(phase)
                if verified_models(self.progress):
                    state = self.read()
                    state.update(
                        ready=False,
                        models_verified=True,
                        evidence_version=EVIDENCE_VERSION,
                        manifest_id=release_manifest_id(),
                        models=self.progress["models"],
                    )
                    self.write(state)
                    self.detail = "Models verified. Generation is ready; GPU compute is off."
                    break
                if any(
                    m.get("state") in {"failed", "unsupported"}
                    for m in self.progress.get("models", {}).values()
                ):
                    raise CloudSetupError(
                        "Model setup could not finish; inspect the model status and retry"
                    )
                await asyncio.sleep(2)
            else:
                raise CloudSetupError(
                    "Model setup reached its time limit; verified files remain for the next attempt"
                )
        except (CloudSetupError, RunpodApiError, GenerationError, OSError, ValueError) as exc:
            state = self.read()
            state["auto_setup_retryable"] = isinstance(exc, CloudAvailabilityError)
            self.write(state)
            self.detail = str(exc)
            setup_error = self.detail
            self._setup_status(
                "failed", error=setup_error, cleanup_pending=bool(self.read().get("compute"))
            )
        except asyncio.CancelledError:
            setup_error = (
                "Model setup cancelled. Stored files are kept for retry."
                if self._cancel_requested
                else "Model setup was interrupted. Retry to resume from stored files."
            )
            self.detail = setup_error
            self._setup_status(
                "failed", error=setup_error, cleanup_pending=bool(self.read().get("compute"))
            )
            raise
        except Exception:
            setup_error = "Model setup stopped unexpectedly. Check the pending machine, then retry."
            self.detail = setup_error
            self._setup_status(
                "failed", error=setup_error, cleanup_pending=bool(self.read().get("compute"))
            )
        finally:
            async with self.lock:
                if self._cancel_requested:
                    # The cancellation task owns cleanup after this task is quiescent.
                    # This also prevents normal success from racing a user's stop.
                    self._setup_status(
                        "cancelling", cleanup_pending=bool(self.read().get("compute"))
                    )
                else:
                    if not setup_error:
                        self.detail = "Models verified. Stopping the temporary download worker."
                        self._setup_status(
                            "stopping_worker", cleanup_pending=bool(self.read().get("compute"))
                        )
                    try:
                        await self._release()
                    except (CloudSetupError, RunpodApiError):
                        self.detail = setup_error or (
                            "The rented machine could not be stopped yet. "
                            "Check the pending machine and retry."
                        )
                        self._setup_status("failed", error=self.detail, cleanup_pending=True)
                    else:
                        self.detail = setup_error or "Models are ready. The download worker is off."
                        if not setup_error:
                            state = self.read()
                            state["ready"] = True
                            self.write(state)
                        self._setup_status("failed" if setup_error else "ready", error=setup_error)

    async def _release(self) -> None:
        state = self.read()
        compute = state.get("compute")
        if not compute:
            return
        client = self.client_factory(self.key())
        try:
            pod_id = compute.get("pod_id")
            if not pod_id:
                matches = [p for p in await client.list_pods() if p.get("name") == compute["name"]]
                if len(matches) > 1:
                    raise CloudSetupError(
                        "Multiple app-owned sessions need reconciliation in Runpod"
                    )
                if not matches:
                    # Absence immediately after an ambiguous POST is not proof of failure.
                    deadline = datetime.fromisoformat(compute["deadline"])
                    if datetime.now(UTC) < deadline:
                        raise CloudSetupError(
                            "Creation result is uncertain; wait for reconciliation or expiry"
                        )
                else:
                    pod_id = matches[0]["id"]
            if pod_id:
                try:
                    pod = await client.get_pod(pod_id)
                except RunpodApiError as exc:
                    if exc.status_code != 404:
                        raise
                    pod = {"name": compute["name"], "status": "TERMINATED"}
                if pod.get("name") != compute["name"]:
                    raise CloudSetupError("Session ownership changed; automatic deletion refused")
                compute.update(pod_id=pod_id, status="stopping")
                state["compute"] = compute
                self.write(state)
                await client.terminate_pod(pod_id)
                # Provider reports termination asynchronously. Retain ownership until confirmed.
                for _ in range(12):
                    try:
                        remaining = await client.get_pod(pod_id)
                        if remaining.get("status") == "TERMINATED":
                            break
                    except RunpodApiError as exc:
                        if exc.status_code == 404:
                            break
                        raise
                    await asyncio.sleep(2)
                else:
                    raise CloudSetupError("Provider termination is still pending")
            state["last_compute"] = compute
            state.pop("compute", None)
            state["cleanup_pending"] = False
            self.write(state)
            WorkerPairStore(self.settings.data_dir).clear()
        finally:
            await client.close()

    async def release(self) -> None:
        async with self.lock:
            if self.active or (self.setup_task and not self.setup_task.done()):
                raise CloudSetupError("Wait for active work before releasing compute")
            if self.pending and await self.pending():
                raise CloudSetupError("Wait for queued jobs before releasing compute")
            await self._release()
            self.detail = "Pending machine cleared. Model files remain on your storage."
            state = self.read()
            if state.get("models_verified") and verified_models({**state, "ready": True}):
                state.update(ready=True, setup_phase="ready", setup_error=None)
                self.detail = "Models are ready. The download worker is off."
            state["setup_detail"] = self.detail
            self.write(state)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[RemoteScheduler]:
        async with self.lock:
            state = self.read()
            if not self.is_ready(state):
                raise GenerationError(
                    "remote", "Finish model storage setup in the Runpod tab first"
                )
            if self.release_task and not self.release_task.done():
                self.release_task.cancel()
            try:
                pair = (
                    WorkerPairStore(self.settings.data_dir).get() if state.get("compute") else None
                )
                if pair is None:
                    pair = await self._provision(installer=False)
                    await self._worker_call(pair, "POST", "/v1/setup")
                    for _ in range(1800):
                        value = await self._worker_call(pair, "GET", "/v1/setup")
                        if verified_models(value):
                            break
                        if any(
                            m.get("state") == "failed" for m in value.get("models", {}).values()
                        ):
                            raise CloudSetupError(
                                "Stored model verification failed; run setup again"
                            )
                        await asyncio.sleep(2)
                    else:
                        raise CloudSetupError("Model verification exceeded its startup limit")
                remote = self.remote_factory(pair.url, pair.token, CATALOG)
                self.active += 1
            except (CloudSetupError, RunpodApiError, ValueError, OSError, GenerationError) as exc:
                with contextlib.suppress(CloudSetupError, RunpodApiError):
                    await self._release()
                raise GenerationError("remote", str(exc)) from exc
        try:
            yield remote
        finally:
            try:
                await remote.shutdown()
            finally:
                async with self.lock:
                    self.active -= 1
                    if self.active == 0:
                        self.release_task = asyncio.create_task(self._idle_release())

    async def _idle_release(self) -> None:
        try:
            while True:
                await asyncio.sleep(5)
                async with self.lock:
                    if self.active:
                        return
                    if self.pending and await self.pending():
                        continue
                    await self._release()
                    self.detail = "Compute released; model storage is retained."
                    return
        except (CloudSetupError, RunpodApiError):
            self.detail = (
                "Compute release needs retry. The provider termination deadline remains active."
            )

    async def model_status(self) -> tuple[ModelStatus, ...]:
        state = self.read()
        ready = self.is_ready(state)
        return tuple(
            ModelStatus(
                spec=s,
                state=ModelState.COLD
                if ready and s.runtime.value != "f5"
                else ModelState.NOT_DOWNLOADED,
                est_wait_sec=s.est_load_sec,
            )
            for s in CATALOG.specs
        )

    async def shutdown(self) -> None:
        if self.auto_task and not self.auto_task.done():
            self.auto_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.auto_task
        if self.cancel_task and not self.cancel_task.done():
            await asyncio.shield(self.cancel_task)
        if self.setup_task and not self.setup_task.done():
            self.setup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.setup_task
        if self.release_task and not self.release_task.done():
            self.release_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.release_task
        async with self.lock:
            if not self.active:
                with contextlib.suppress(CloudSetupError, RunpodApiError):
                    await self._release()


_CONTROLLERS: dict[str, CloudController] = {}


def controller(settings: Settings) -> CloudController:
    key = str(settings.data_dir.resolve())
    if key not in _CONTROLLERS:
        _CONTROLLERS[key] = CloudController(settings)
    return _CONTROLLERS[key]
