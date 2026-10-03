"""Local scheduler adapter that delegates resolved synthesis to a Pod worker."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..exceptions import GenerationError, ModelNotFoundError
from ..remote_worker.errors import WORKER_MESSAGES
from ..remote_worker.model_pins import AUXILIARY_PINS
from .catalog import ModelCatalog
from .protocol import ModelStatus, SynthRequest, SynthResult
from .spec import ModelState


class RemoteWorkerError(GenerationError):
    """A recognized worker category with a locally curated, safe explanation."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__("remote", WORKER_MESSAGES[code])
        self.code = code
        self.http_status = status


class RemoteScheduler:
    def __init__(
        self,
        url: str,
        token: str,
        catalog: ModelCatalog,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        ):
            raise ValueError("Remote worker requires HTTPS (or loopback HTTP for testing)")
        if not token:
            raise ValueError("Remote worker token is required")
        self._catalog = catalog
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(900.0, connect=20.0),
            transport=transport,
            follow_redirects=False,
        )

    async def _response(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GenerationError("remote", "Cannot reach the Runpod worker") from exc
        if response.is_error:
            try:
                problem = response.json()
            except ValueError:
                problem = None
            if (
                isinstance(problem, dict)
                and isinstance(problem.get("code"), str)
                and problem["code"] in WORKER_MESSAGES
            ):
                # Never echo arbitrary remote detail or extensions. The selected
                # category maps to fixed local copy, including older workers.
                raise RemoteWorkerError(problem["code"], response.status_code)
            raise GenerationError("remote", f"Runpod worker returned HTTP {response.status_code}")
        return response

    async def status(self) -> tuple[ModelStatus, ...]:
        response = await self._response("GET", "/v1/models")
        data = response.json()
        if data.get("protocol_version") != 1:
            raise GenerationError("remote", "Runpod worker protocol version does not match")
        remote = {item["id"]: item for item in data.get("models", [])}
        statuses: list[ModelStatus] = []
        for spec in self._catalog.specs:
            item = remote.get(spec.id)
            if item is None or item.get("revision") != spec.hf_revision:
                state = ModelState.NOT_DOWNLOADED
            elif item.get("install", {}).get("state") not in (None, "installed"):
                state = ModelState.NOT_DOWNLOADED
            else:
                try:
                    state = ModelState(item["state"])
                except (KeyError, ValueError) as exc:
                    raise GenerationError(
                        "remote", "Runpod worker returned an invalid state"
                    ) from exc
            statuses.append(
                ModelStatus(
                    spec=spec,
                    state=state,
                    est_wait_sec=0.0 if state is ModelState.RESIDENT else spec.est_load_sec,
                )
            )
        return tuple(statuses)

    async def model_installs(self) -> list[dict[str, str]]:
        response = await self._response("GET", "/v1/models")
        data = response.json()
        if data.get("protocol_version") != 1:
            raise GenerationError("remote", "Runpod worker protocol version does not match")
        models: list[dict[str, str]] = []
        for item in data.get("models", []):
            spec = self._catalog.get(item.get("id", ""))
            if spec is None:
                model_id = item.get("id", "")
                if model_id not in AUXILIARY_PINS:
                    continue
                expected_revision = AUXILIARY_PINS[model_id][1]
            else:
                model_id, expected_revision = spec.id, spec.hf_revision
            revision = item.get("revision", "")
            state = (
                item.get("install", {}).get("state", "unknown")
                if revision == expected_revision
                else "revision_mismatch"
            )
            models.append({"id": model_id, "revision": revision, "state": state})
        return models

    async def install(self, model_id: str) -> dict[str, str]:
        if self._catalog.get(model_id) is None and model_id not in AUXILIARY_PINS:
            raise ModelNotFoundError(model_id)
        response = await self._response("POST", f"/v1/models/{model_id}/install")
        value = response.json()
        return {"model_id": model_id, "state": value.get("state", "unknown")}

    async def warm(self, model_id: str) -> None:
        if self._catalog.get(model_id) is None:
            raise ModelNotFoundError(model_id)
        await self._response("POST", f"/v1/models/{model_id}/warm")

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        if self._catalog.get(request.model_id) is None:
            raise ModelNotFoundError(request.model_id)
        payload = {
            "model_id": request.model_id,
            "text": request.text,
            "reference_text": request.reference_text,
            "params": request.params,
            "sample_rate": request.sample_rate,
        }
        try:
            with request.reference_audio.open("rb") as reference:
                response = await self._response(
                    "POST",
                    "/v1/synthesize",
                    data={"request": json.dumps(payload)},
                    files={"reference_audio": ("reference.wav", reference, "audio/wav")},
                )
        except OSError as exc:
            raise GenerationError(request.model_id, "Reference audio could not be read") from exc
        if response.headers.get("X-Model-Id") != request.model_id:
            raise GenerationError(request.model_id, "Runpod worker changed the selected model")
        try:
            duration = float(response.headers["X-Audio-Duration-Sec"])
            elapsed = float(response.headers["X-Generation-Time-Sec"])
            load = float(response.headers.get("X-Load-Time-Sec", "0"))
        except (KeyError, ValueError) as exc:
            raise GenerationError(request.model_id, "Runpod worker omitted audio metadata") from exc
        output: Path = request.output_path
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".part")
        try:
            temporary.write_bytes(response.content)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return SynthResult(
            output_path=output,
            duration_sec=duration,
            gen_time_sec=elapsed,
            sample_rate=request.sample_rate,
            model_id=request.model_id,
            load_time_sec=load,
        )

    @contextlib.asynccontextmanager
    async def reserve_slot(self, reason: str) -> AsyncIterator[None]:
        # GPU serialization is owned by the Pod scheduler. The local desktop
        # never uses this context for a local GPU runtime.
        yield

    async def shutdown(self) -> None:
        await self._client.aclose()
