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

STORAGE_GB = 200
STORAGE_MONTHLY_USD = 14.0
EVIDENCE_VERSION = 2
REQUIRED_MODELS = REQUIRED_MODEL_IDS


class CloudSetupError(RuntimeError):
    pass


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


def verified_models(value: dict) -> bool:
    if value.get("manifest_id") != release_manifest_id():
        return False
    models = value.get("models", {})
    for model_id in REQUIRED_MODELS:
        item = models.get(model_id, {})
        spec = CATALOG.get(model_id)
        repo, revision = (spec.hf_repo, spec.hf_revision) if spec else AUXILIARY_PINS[model_id]
        evidence = item.get("evidence", {})
        files = evidence.get("files", [])
        expected_graph = set(model_graph(model_id))
        if not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
            return False
        if {(f.get("repo"), f.get("revision")) for f in files} != expected_graph:
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

    async def snapshot(self) -> dict:
        state = self.read()
        connected = RunpodKeyStore(self.settings.data_dir).has_key()
        stage = "connect_account" if not connected else "select_storage"
        if state.get("volume"):
            stage = "ready" if self.is_ready(state) else "download_models"
        if self.setup_task and not self.setup_task.done():
            stage = "download_models"
        return {
            "connected": connected,
            "stage": stage,
            "volume": state.get("volume"),
            "ready": self.is_ready(state),
            "models": self.progress.get("models", state.get("models", {})),
            "progress_pct": self.progress.get("progress_pct"),
            "bytes_completed": self.progress.get("bytes_completed", 0),
            "bytes_total": self.progress.get("bytes_total"),
            "detail": self.detail,
            "compute": state.get("compute"),
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
            cpu_rates = []
            for cpu in cpus:
                stock = next(
                    (dc for dc in cpu.get("dataCenters", []) if dc.get("id") == region), {}
                )
                price = cpu.get("price", {}).get("securePerVcpu")
                count = cpu.get("vcpu", {}).get("min", 2)
                if (
                    _available(stock.get("availability"))
                    and isinstance(price, (int, float))
                    and price > 0
                    and isinstance(count, int)
                ):
                    cpu_rates.append(price * count)
            if choices and cpu_rates:
                regions.append(
                    {
                        "id": region,
                        "name": center.get("name", region),
                        "gpu_hourly_from_usd": choices[0]["hourly_usd"],
                        "installer_hourly_from_usd": min(cpu_rates),
                    }
                )
        regions.sort(key=lambda r: (r["gpu_hourly_from_usd"], r["id"]))
        safe = [
            {k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")}
            for volume in volumes
        ]
        return {
            "volumes": safe,
            "regions": regions,
            **balance,
            "storage_gb": STORAGE_GB,
            "storage_monthly_usd": STORAGE_MONTHLY_USD,
            "funding_url": "https://www.runpod.io/console/user/billing",
        }

    async def quote_storage(self, region: str) -> dict:
        discovery = await self.discover()
        selected = next((r for r in discovery["regions"] if r["id"] == region), None)
        if selected is None:
            raise CloudSetupError(
                "No compatible GPU and installer stock in this region; refresh availability"
            )
        # A month of storage is a reserve policy, not an upfront provider invoice.
        reserve = round(STORAGE_MONTHLY_USD / 30 + 1.0, 4)
        balance = discovery["balance_usd"]
        self.quote = {
            "id": secrets.token_hex(16),
            "expires_at": time.time() + 300,
            "region": region,
            "storage_gb": STORAGE_GB,
            "monthly_usd": STORAGE_MONTHLY_USD,
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
            if state.get("volume"):
                raise CloudSetupError("Storage is already selected")
            key = self.key()
            client = self.client_factory(key)
            try:
                funds = await client.balance()
                if funds["balance_usd"] is None:
                    raise CloudSetupError("Balance is unknown; enable account reads and refresh")
                if funds["balance_usd"] < quote["required_credit_reserve_usd"]:
                    raise CloudSetupError("Add Runpod credit and refresh before purchasing storage")
                operation = state.get("volume_operation") or f"voice-clone-{secrets.token_hex(8)}"
                state.update(
                    account=hashlib.sha256(key.encode()).hexdigest(), volume_operation=operation
                )
                self.write(
                    state
                )  # Persist before POST; ambiguous retries reconcile by unique name.
                matches = [v for v in await client.list_volumes() if v.get("name") == operation]
                if len(matches) > 1:
                    raise CloudSetupError(
                        "Multiple storage results found; reconcile in Runpod before retrying"
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
                    volume = await client.create_volume(
                        name=operation,
                        data_center=quote["region"],
                        size_gb=STORAGE_GB,
                    )
                if (
                    volume.get("size", 0) < STORAGE_GB
                    or volume.get("dataCenter") != quote["region"]
                ):
                    raise CloudSetupError("Provider storage does not match the approved quote")
                state["volume"] = {
                    k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")
                }
                state["ready"] = False
                self.write(state)
                self.quote = None
                return state["volume"]
            finally:
                await client.close()

    async def select_storage(self, volume_id: str) -> dict:
        async with self.lock:
            state = self.read()
            if (
                state.get("compute")
                or (self.setup_task and not self.setup_task.done())
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
                raise CloudSetupError("Choose a Standard network volume with at least 200 GB")
            state.update(
                account=hashlib.sha256(self.key().encode()).hexdigest(),
                volume={k: volume.get(k) for k in ("id", "name", "size", "dataCenter", "type")},
                ready=False,
                models={},
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
            if self.setup_task and not self.setup_task.done():
                return
            self.images()  # Do not buy resources for a missing/unpublished release.
            if not self.read().get("volume"):
                raise CloudSetupError("Select persistent storage first")
            if self.active:
                raise CloudSetupError("A generation is still active")
            state = self.read()
            state["ready"] = False
            self.write(state)
            self.detail = (
                "Starting a temporary CPU worker to verify storage and download missing files"
            )
            self.setup_task = asyncio.create_task(self._setup())

    async def _worker_call(self, pair: WorkerPair, method: str, path: str) -> dict:
        remote = self.remote_factory(pair.url, pair.token, CATALOG)
        try:
            value = (await remote._response(method, path)).json()
            if value.get("protocol_version") != 1:
                raise CloudSetupError("Cloud worker protocol does not match this app")
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
            if installer:
                discovery = await self.discover()
                region = next(
                    (r for r in discovery["regions"] if r["id"] == volume["dataCenter"]), None
                )
                if not region:
                    raise CloudSetupError(
                        "No CPU installer and compatible GPU availability beside this storage"
                    )
                rate, budget, gpu_id = region["installer_hourly_from_usd"], 1.0, None
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
            seconds = min(8 * 3600 if installer else 3600, int(budget / rate * 3600))
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
            }
            self.write(state)
            pod = await client.create_guarded_pod(
                name=name,
                image=self.images()["installer" if installer else "gpu"],
                data_center=volume["dataCenter"],
                volume_id=volume["id"],
                worker_token=token,
                terminate_at=deadline,
                gpu_id=gpu_id,
            )
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
        try:
            async with self.lock:
                pair = await self._provision(installer=True)
            await self._worker_call(pair, "POST", "/v1/setup")
            for _ in range(8 * 3600 // 2):
                self.progress = await self._worker_call(pair, "GET", "/v1/setup")
                self.detail = "Checking checksums and downloading missing models"
                if verified_models(self.progress):
                    state = self.read()
                    state.update(
                        ready=True,
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
            self.detail = str(exc)
        finally:
            async with self.lock:
                try:
                    await self._release()
                except (CloudSetupError, RunpodApiError):
                    self.detail += (
                        " Cloud release is pending; refresh or use Release compute."
                        " The provider deadline remains set."
                    )

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
