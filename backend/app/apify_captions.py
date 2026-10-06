"""Bounded, asynchronous optional Apify captions. No GPU or media downloads.

Only the tested public Actor build may run. Creation POSTs are never retried;
known run IDs survive API restart and are polled without creating another run.
Provider run caps total $0.028/import, with $0.002 indicated fee margin.
Apify's unrelated account usage and later retained-storage fees are not bounded
by a per-run cap. No token or arbitrary provider error enters a response/cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from .runpod.secrets import _crypt
from .youtube_captions import CaptionImportError, CaptionTrack, ImportedCaptions, fetch_captions

ACTOR = "vero-api~youtube-transcript-scraper"
BUILD = "1.0.98"
BUILD_ID = "54VyHvLOpFUbSAqoq"
RUN_CAP_USD = 0.014
IMPORT_QUOTE_USD = 0.03
MAX_BYTES = 4 * 1024 * 1024
CACHE_SECONDS = 24 * 3600
TERMINAL = {"ready", "failed", "cancelled"}
SUPPORTED = {"en", "hi", "ur"}


class ApifyKeyStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "secrets" / "apify-key.dpapi"

    def get(self) -> str | None:
        if not self.path.is_file():
            return None
        return _crypt(self.path.read_bytes(), protect=False).decode("utf-8")

    def set(self, key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _crypt(key.encode("utf-8"), protect=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(encrypted)
        temp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _base(code: str) -> str:
    return code.lower().split("-", 1)[0]


def parse_output(
    item: Any, video_id: str, max_chars: int
) -> tuple[ImportedCaptions | None, tuple[CaptionTrack, ...]]:
    """Treat remote/cached data as untrusted. Translation targets are never tracks."""
    if not isinstance(item, dict) or item.get("videoId") != video_id:
        raise CaptionImportError(
            "caption_provider_invalid",
            "The caption service returned an invalid result. Try again or paste the script.",
        )
    tracks = item.get("availableLanguages")
    if not isinstance(tracks, list) or len(tracks) > 200:
        raise CaptionImportError(
            "caption_provider_invalid",
            "The caption service did not return its available caption tracks.",
        )
    actual = []
    seen = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise CaptionImportError(
                "caption_provider_invalid", "The caption service returned invalid track details."
            )
        code, label, generated = (
            track.get("languageCode"),
            track.get("language"),
            track.get("isGenerated"),
        )
        if (
            not isinstance(code, str)
            or not 1 <= len(code) <= 32
            or not isinstance(label, str)
            or len(label) > 150
            or not isinstance(generated, bool)
        ):
            raise CaptionImportError(
                "caption_provider_invalid", "The caption service returned invalid track details."
            )
        identity = (code.lower(), generated)
        if identity not in seen:
            actual.append(CaptionTrack(label, code, generated))
            seen.add(identity)
    supported = tuple(t for t in actual if _base(t.language_code) in SUPPORTED)
    code, generated = item.get("languageCode"), item.get("isGenerated")
    if (
        not isinstance(code, str)
        or not isinstance(generated, bool)
        or (code.lower(), generated) not in seen
    ):
        raise CaptionImportError(
            "caption_provider_invalid",
            "The returned caption language does not match an available track.",
        )
    text, segments = item.get("text"), item.get("segments")
    if not isinstance(text, str) or not text.strip():
        raise CaptionImportError(
            "captions_empty", "This caption track is empty. Paste the script instead."
        )
    if len(text) > max_chars:
        raise CaptionImportError(
            "captions_too_long",
            f"This transcript exceeds {max_chars:,} characters. Paste a shorter section.",
        )
    if not isinstance(segments, list) or not segments or len(segments) > max_chars:
        raise CaptionImportError(
            "caption_provider_invalid", "The caption service returned invalid caption timings."
        )
    previous = -1.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise CaptionImportError(
                "caption_provider_invalid", "The caption service returned invalid caption timings."
            )
        start, duration = segment.get("start"), segment.get("duration")
        if (
            any(
                isinstance(v, bool)
                or not isinstance(v, (float, int))
                or not math.isfinite(v)
                or v < 0
                for v in [start, duration]
            )
            or start < previous
            or not isinstance(segment.get("text"), str)
            or not segment["text"].strip()
        ):
            raise CaptionImportError(
                "caption_provider_invalid", "The caption service returned invalid caption timings."
            )
        previous = start
    if _base(code) not in SUPPORTED:
        if not supported:
            raise CaptionImportError(
                "captions_unsupported",
                "English, Hindi or Urdu captions are not available for this video.",
            )
        return None, supported
    selected = next(
        t for t in actual if t.language_code.lower() == code.lower() and t.is_generated == generated
    )
    return ImportedCaptions(
        video_id, text.strip(), _base(code), code, selected.is_generated, supported, "apify"
    ), supported


class ApifyCaptions:
    def __init__(self, data_dir: Path, *, transport=None):
        self.root = data_dir / "caption-cache"
        self.keys = ApifyKeyStore(data_dir)
        self.transport = transport
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()

    async def request(self, key: str, method: str, path: str, **kwargs):
        try:
            async with (
                asyncio.timeout(20),
                httpx.AsyncClient(
                    base_url="https://api.apify.com/v2",
                    headers={"Authorization": "Bearer " + key},
                    timeout=httpx.Timeout(12, connect=5),
                    follow_redirects=False,
                    transport=self.transport,
                ) as client,
            ):
                async with client.stream(method, path, **kwargs) as response:
                    if response.status_code in {401, 403}:
                        raise CaptionImportError(
                            "apify_key_invalid",
                            "Apify access was refused. Check the key in YouTube settings.",
                        )
                    if response.status_code in {402}:
                        raise CaptionImportError(
                            "apify_credit_required",
                            "Apify credit is unavailable. "
                            "Check your Apify account or paste the script.",
                        )
                    if response.status_code == 429:
                        raise CaptionImportError(
                            "caption_provider_busy",
                            "The caption service is busy. Try again shortly.",
                        )
                    if response.status_code not in {200, 201}:
                        raise CaptionImportError(
                            "caption_provider_failed",
                            "The caption service could not complete this request. "
                            "Try again or paste the script.",
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise CaptionImportError(
                                "captions_too_large",
                                "The caption result is too large. Paste a shorter script.",
                            )
            return json.loads(content)
        except CaptionImportError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
            raise CaptionImportError(
                "caption_provider_failed",
                "Could not reach the caption service. Try again or paste the script.",
            ) from exc

    async def account(self, key: str) -> dict:
        user = (await self.request(key, "GET", "/users/me")).get("data", {})
        limits = (await self.request(key, "GET", "/users/me/limits")).get("data", {})
        if (
            not isinstance(user, dict)
            or not isinstance(limits, dict)
            or not isinstance(user.get("plan"), dict)
            or not isinstance(limits.get("current"), dict)
        ):
            raise CaptionImportError(
                "caption_provider_invalid",
                "Apify account validation did not finish. Check the token and try again.",
            )
        credits = user.get("plan", {}).get("monthlyUsageCreditsUsd")
        usage = limits.get("current", {}).get("monthlyUsageUsd")
        remaining = (
            max(0, credits - usage)
            if all(
                not isinstance(v, bool)
                and isinstance(v, (int, float))
                and math.isfinite(v)
                and v >= 0
                for v in [credits, usage]
            )
            else None
        )
        return {
            "connected": True,
            "included_credit_remaining_usd": remaining,
            "free_plan": user.get("plan", {}).get("tier") == "FREE",
            "maximum_import_usd": IMPORT_QUOTE_USD,
        }

    def _write(self, name: str, value: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _read(self, name: str) -> dict | None:
        path = self.root / name
        try:
            if path.stat().st_size > MAX_BYTES:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _cache_name(self, video_id, language, provider):
        identity = f"{provider}:{BUILD}:{video_id}:{language or 'auto'}"
        return "caption-" + hashlib.sha256(identity.encode()).hexdigest() + ".json"

    def _cached(self, video_id, language, provider, max_chars):
        record = self._read(self._cache_name(video_id, language, provider))
        try:
            if not record or record.get("build") != BUILD or record.get("video_id") != video_id:
                return None
            saved = record.get("saved_at")
            if (
                not isinstance(saved, (int, float))
                or not math.isfinite(saved)
                or not 0 <= time.time() - saved <= CACHE_SECONDS
            ):
                return None
            raw = record["captions"]
            tracks = tuple(CaptionTrack(**t) for t in raw["available_tracks"])
            result = ImportedCaptions(**{**raw, "available_tracks": tracks})
            if (
                result.video_id != video_id
                or result.provider != provider
                or result.language not in SUPPORTED
                or _base(result.language_code) != result.language
                or not isinstance(result.text, str)
                or not result.text.strip()
                or len(result.text) > max_chars
            ):
                return None
            if language and _base(language) != result.language:
                return None
            if (
                not tracks
                or len(tracks) > 200
                or any(
                    not isinstance(t.language, str)
                    or len(t.language) > 150
                    or not isinstance(t.language_code, str)
                    or _base(t.language_code) not in SUPPORTED
                    or not isinstance(t.is_generated, bool)
                    for t in tracks
                )
            ):
                return None
            if not any(
                t.language_code == result.language_code and t.is_generated == result.is_generated
                for t in tracks
            ):
                return None
            return result
        except (KeyError, TypeError, ValueError, AttributeError):
            return None

    def _save_cache(self, video_id, language, captions):
        self._write(
            self._cache_name(video_id, language, captions.provider),
            {
                "build": BUILD,
                "video_id": video_id,
                "saved_at": time.time(),
                "captions": asdict(captions),
            },
        )

    def public(self, job: dict) -> dict:
        return {
            key: job.get(key)
            for key in [
                "id",
                "phase",
                "detail",
                "error_code",
                "created_at",
                "provider",
                "captions",
                "cached",
                "maximum_import_usd",
            ]
        }

    async def start(
        self, video_id: str, language: str | None, *, max_chars: int, refresh=False
    ) -> dict:
        for name, task in list(self.tasks.items()):
            if task.done():
                self.tasks.pop(name)
            else:
                active = self._read(f"import-{name}.json")
                if active and active["video_id"] == video_id and active.get("language") == language:
                    return self.public(active)
                raise CaptionImportError(
                    "caption_import_busy", "Finish or cancel the current caption import first."
                )
        # Reconcile restart/uncertain delivery before admitting any new charge.
        records = sorted(
            self.root.glob("import-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:64]
        for path in records:
            previous = self._read(path.name)
            if not previous:
                continue
            if any(
                run.get("run_id") is None and time.time() - run.get("attempted_at", 0) < 150
                for run in previous.get("runs", [])
            ):
                raise CaptionImportError(
                    "caption_start_uncertain",
                    "The previous caption start was not confirmed. "
                    "Wait two minutes before importing again.",
                )
            if previous.get("phase") not in TERMINAL and previous["id"] not in self.tasks:
                await self.status(previous["id"])
        # One active import per API/profile; repeat clicks do not create duplicates.
        for name, task in list(self.tasks.items()):
            if task.done():
                self.tasks.pop(name)
        for name, task in self.tasks.items():
            if not task.done():
                existing = self._read(f"import-{name}.json")
                if (
                    existing
                    and existing["video_id"] == video_id
                    and existing.get("language") == language
                ):
                    return self.public(existing)
                raise CaptionImportError(
                    "caption_import_busy", "Finish or cancel the current caption import first."
                )
        key = self.keys.get()
        provider = "apify" if key else "local"
        cached = None if refresh else self._cached(video_id, language, provider, max_chars)
        job = {
            "id": uuid.uuid4().hex,
            "video_id": video_id,
            "language": language,
            "max_chars": max_chars,
            "created_at": time.time(),
            "provider": provider,
            "phase": "ready" if cached else "starting",
            "detail": "Captions ready" if cached else "Finding available captions…",
            "captions": asdict(cached) if cached else None,
            "cached": bool(cached),
            "maximum_import_usd": IMPORT_QUOTE_USD if key else 0,
            "runs": [],
        }
        self._write(f"import-{job['id']}.json", job)
        if not cached:
            self.tasks[job["id"]] = asyncio.create_task(self._run(job, key))
        return self.public(job)

    async def status(self, import_id: str) -> dict:
        job = self._read(f"import-{import_id}.json")
        if not job:
            raise CaptionImportError(
                "caption_import_not_found",
                "This caption import is no longer available. Import the link again.",
            )
        if job["phase"] not in TERMINAL and (
            import_id not in self.tasks or self.tasks[import_id].done()
        ):
            # A restart can resume known runs, never replay an uncertain POST.
            self.tasks[import_id] = asyncio.create_task(self._run(job, self.keys.get()))
        return self.public(job)

    def active(self) -> dict | None:
        records = sorted(
            self.root.glob("import-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:64]
        for path in records:
            job = self._read(path.name)
            if job and job.get("phase") not in TERMINAL:
                return self.public(job)
        return None

    async def cancel(self, import_id: str) -> dict:
        job = self._read(f"import-{import_id}.json")
        if not job:
            raise CaptionImportError(
                "caption_import_not_found", "This caption import is no longer available."
            )
        if job["phase"] not in TERMINAL:
            self.cancelled.add(import_id)
            job["phase"], job["detail"], job["cancel_requested"] = (
                "cancelling",
                "Stopping caption import…",
                True,
            )
            self._write(f"import-{import_id}.json", job)
            if import_id not in self.tasks:
                self.tasks[import_id] = asyncio.create_task(self._run(job, self.keys.get()))
        return self.public(job)

    def _update(self, job, phase, detail):
        job["phase"], job["detail"] = phase, detail
        self._write(f"import-{job['id']}.json", job)

    def _is_cancelled(self, job):
        return job["id"] in self.cancelled or job.get("cancel_requested")

    async def _abort(self, key, run_id):
        await self.request(key, "POST", f"/actor-runs/{run_id}/abort")

    async def _one(self, job, key, language, index):
        runs = job["runs"]
        if len(runs) <= index:
            runs.append({"attempted_at": time.time(), "run_id": None})
            self._write(f"import-{job['id']}.json", job)
            # Persist attempt before POST. Never create a second copy if delivery is uncertain.
            try:
                response = await self.request(
                    key,
                    "POST",
                    f"/acts/{ACTOR}/runs",
                    params={
                        "build": BUILD,
                        "timeout": 120,
                        "maxTotalChargeUsd": RUN_CAP_USD,
                        "maxItems": 1,
                        "restartOnError": "false",
                        "memory": 512,
                    },
                    json={
                        "videoUrl": f"https://www.youtube.com/watch?v={job['video_id']}",
                        "transcriptLanguage": language or "auto",
                        "includeTimestamps": True,
                        "includeVideoDetails": False,
                        "additionalFormats": [],
                    },
                )
            except CaptionImportError as exc:
                if exc.code in {
                    "apify_key_invalid",
                    "apify_credit_required",
                    "caption_provider_busy",
                }:
                    runs.pop()
                    self._write(f"import-{job['id']}.json", job)
                    raise
                raise CaptionImportError(
                    "caption_start_uncertain",
                    "The caption service has not confirmed the import. "
                    "Wait two minutes before trying again.",
                ) from exc
            run = response.get("data", {})
            run_id = run.get("id")
            if not isinstance(run_id, str) or not run_id.isalnum() or len(run_id) > 64:
                raise CaptionImportError(
                    "caption_start_uncertain",
                    "The caption service has not confirmed the import. "
                    "Wait two minutes before trying again.",
                )
            runs[index]["run_id"] = run_id
            self._write(f"import-{job['id']}.json", job)
            options = run.get("options", {})
            if (
                run.get("buildId") != BUILD_ID
                or options.get("maxTotalChargeUsd") != RUN_CAP_USD
                or options.get("timeoutSecs") != 120
            ):
                await self._abort(key, run_id)
                raise CaptionImportError(
                    "caption_limits_failed",
                    "The caption service did not accept the import limits. Import stopped.",
                )
        else:
            run_id = runs[index].get("run_id")
            if not run_id:
                raise CaptionImportError(
                    "caption_start_uncertain",
                    "The caption service has not confirmed the import. "
                    "Wait two minutes before trying again.",
                )
        self._update(
            job,
            "fetching",
            "Fetching captions…"
            if index == 0
            else "Fetching an available supported caption track…",
        )
        stop_at = time.monotonic() + max(
            0, min(130, runs[index]["attempted_at"] + 130 - time.time())
        )
        while True:
            if self._is_cancelled(job):
                await self._abort(key, run_id)
                raise CaptionImportError("caption_cancelled", "Caption import cancelled.")
            run = (await self.request(key, "GET", f"/actor-runs/{run_id}")).get("data", {})
            state = run.get("status")
            if state == "SUCCEEDED":
                break
            if state in {"FAILED", "TIMED-OUT", "ABORTED"}:
                raise CaptionImportError(
                    "caption_fetch_failed",
                    "Captions could not be retrieved. The video may be unavailable "
                    "or have no accessible captions. Paste the script instead.",
                )
            if time.monotonic() >= stop_at:
                await self._abort(key, run_id)
                raise CaptionImportError(
                    "caption_timeout", "Caption import timed out. Try again or paste the script."
                )
            await asyncio.sleep(2)
        dataset_id = run.get("defaultDatasetId")
        if not isinstance(dataset_id, str) or not dataset_id.isalnum() or len(dataset_id) > 64:
            raise CaptionImportError(
                "caption_provider_invalid", "The caption service returned an invalid result."
            )
        self._update(job, "checking", "Checking caption text…")
        items = await self.request(
            key,
            "GET",
            f"/datasets/{dataset_id}/items",
            params={"format": "json", "clean": "true", "limit": 2},
        )
        if not isinstance(items, list) or len(items) != 1:
            raise CaptionImportError(
                "captions_not_available",
                "No accessible captions were returned for this video. Paste the script instead.",
            )
        return parse_output(items[0], job["video_id"], job["max_chars"])

    async def _run(self, job, key):
        try:
            if self._is_cancelled(job):
                if key:
                    for run in job["runs"]:
                        if run.get("run_id"):
                            await self._abort(key, run["run_id"])
                raise CaptionImportError("caption_cancelled", "Caption import cancelled.")
            if job["provider"] == "local":
                self._update(job, "fetching", "Finding available captions…")
                captions = await asyncio.to_thread(
                    fetch_captions, job["video_id"], job["language"], max_chars=job["max_chars"]
                )
            else:
                if not key:
                    raise CaptionImportError(
                        "apify_key_required",
                        "Connect Apify in YouTube settings to continue this import.",
                    )
                account = await self.account(key)
                credit = account["included_credit_remaining_usd"]
                if account["free_plan"] and (credit is None or credit < IMPORT_QUOTE_USD):
                    raise CaptionImportError(
                        "apify_credit_required",
                        "Apify's included credit is used up. "
                        "Paste the script or check your Apify account.",
                    )
                captions, tracks = await self._one(job, key, job["language"], 0)
                if captions is None:
                    chosen = min(
                        tracks,
                        key=lambda t: (
                            t.is_generated,
                            {"en": 0, "hi": 1, "ur": 2}[_base(t.language_code)],
                        ),
                    )
                    captions, _ = await self._one(job, key, _base(chosen.language_code), 1)
                    if captions is None:
                        raise CaptionImportError(
                            "captions_unsupported",
                            "English, Hindi or Urdu captions are not available for this video.",
                        )
                if job["language"] and captions.language != _base(job["language"]):
                    raise CaptionImportError(
                        "caption_provider_invalid",
                        "The caption service returned a different language. "
                        "Paste the script instead.",
                    )
            if self._is_cancelled(job):
                raise CaptionImportError("caption_cancelled", "Caption import cancelled.")
            self._save_cache(job["video_id"], job["language"], captions)
            job["captions"] = asdict(captions)
            self._update(job, "ready", "Captions ready")
        except CaptionImportError as exc:
            job["error_code"] = exc.code
            self._update(
                job, "cancelled" if exc.code == "caption_cancelled" else "failed", str(exc)
            )
        except asyncio.CancelledError:
            # Process shutdown preserves IDs; a later status request polls, not recreates.
            raise
        except Exception:
            job["error_code"] = "caption_import_failed"
            self._update(
                job, "failed", "Caption import could not finish. Try again or paste the script."
            )
        finally:
            self.cancelled.discard(job["id"])


_services: dict[Path, ApifyCaptions] = {}


def caption_service(data_dir: Path) -> ApifyCaptions:
    return _services.setdefault(data_dir, ApifyCaptions(data_dir))
