"""Keep existing text assistance on the Pod while the desktop stays torch-free."""

from __future__ import annotations

from pathlib import Path

from ..exceptions import AnalyzerUnavailableError, GenerationError, TransliteratorUnavailableError
from ..remote_worker.model_pins import AUXILIARY_PINS
from ..runpod.worker_pair import WorkerPairStore
from .analyzer_scheduler import QWEN_ANALYZER_MODEL_ID
from .catalog import CATALOG
from .protocol import AnalyzeResult, TransliterateResult
from .remote_scheduler import RemoteScheduler, RemoteWorkerError
from .transliterator_scheduler import GEMMA_TRANSLITERATOR_MODEL_ID


class RemoteFeatures:
    def __init__(self, data_dir: Path, *, url: str = "", token: str = "", cloud=None) -> None:
        self.store = WorkerPairStore(data_dir)
        self.url, self.token = url, token
        self.cloud = cloud

    async def call(self, path: str, model_id: str, body: dict) -> dict:
        if self.cloud is not None:
            async with self.cloud.session() as remote:
                response = await remote._response("POST", path, json=body)
                return self.validate(response.json(), model_id)
        try:
            if self.url:
                url, token = self.url, self.token
            else:
                pair = self.store.get()
                if pair is None:
                    raise GenerationError("remote", "Connect a Runpod Pod worker first")
                url, token = pair.url, pair.token
            remote = RemoteScheduler(url, token, CATALOG)
            try:
                response = await remote._response("POST", path, json=body)
                return self.validate(response.json(), model_id)
            finally:
                await remote.shutdown()
        except (OSError, KeyError, ValueError) as exc:
            raise GenerationError("remote", "Invalid Pod helper response or connection") from exc

    @staticmethod
    def validate(value: dict, model_id: str) -> dict:
        if (
            value.get("protocol_version") != 1
            or value.get("model_id") != model_id
            or value.get("revision") != AUXILIARY_PINS[model_id][1]
        ):
            raise GenerationError("remote", "Pod helper model or protocol does not match")
        return value


class RemoteAnalyzer:
    def __init__(self, features: RemoteFeatures) -> None:
        self.features = features

    async def classify(self, *, language: str, sentences: tuple[str, ...]) -> AnalyzeResult:
        try:
            value = await self.features.call(
                "/v1/analyze",
                QWEN_ANALYZER_MODEL_ID,
                {"language": language, "sentences": list(sentences)},
            )
            return AnalyzeResult(
                rows=tuple(value["rows"]),
                title=value["title"],
                gen_time_sec=float(value["gen_time_sec"]),
                load_time_sec=float(value["load_time_sec"]),
            )
        except RemoteWorkerError as exc:
            raise AnalyzerUnavailableError(exc.detail) from exc
        except (GenerationError, KeyError, ValueError, TypeError) as exc:
            raise AnalyzerUnavailableError("Pod Speech Direction is unavailable") from exc

    async def shutdown(self) -> None:
        pass


class RemoteTransliterator:
    def __init__(self, features: RemoteFeatures) -> None:
        self.features = features

    async def convert_many(
        self,
        *,
        texts: list[str],
        instruction: str = "",
        source_script: str = "latin",
        target_script: str = "perso_arabic",
        source_language: str | None = None,
    ) -> list[TransliterateResult]:
        try:
            value = await self.features.call(
                "/v1/transliterate",
                GEMMA_TRANSLITERATOR_MODEL_ID,
                {
                    "texts": texts,
                    "instruction": instruction,
                    "source_script": source_script,
                    "target_script": target_script,
                    **({"source_language": source_language} if source_language else {}),
                },
            )
            items = value["results"]
            if source_language and value.get("source_language") != source_language:
                raise ValueError("Pod does not support the requested translation language")
            if len(items) != len(texts):
                raise ValueError("Pod omitted conversion results")
            return [TransliterateResult(**item) for item in items]
        except RemoteWorkerError as exc:
            raise TransliteratorUnavailableError(exc.detail) from exc
        except (GenerationError, KeyError, ValueError, TypeError) as exc:
            raise TransliteratorUnavailableError("Pod script conversion is unavailable") from exc

    async def shutdown(self) -> None:
        pass
