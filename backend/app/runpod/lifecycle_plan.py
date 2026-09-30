"""Pure plans for one-key cloud setup and disposable GPU sessions.

No provider calls, model downloads or resource mutations happen here. A caller
must obtain fresh regional offers and verified, pinned storage evidence. These
contracts do not turn the current manually paired worker into an autoscaler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath


class SetupStage(StrEnum):
    CONNECT_ACCOUNT = "connect_account"
    SELECT_STORAGE = "select_storage"
    VERIFY_STORAGE = "verify_storage"
    DOWNLOAD_MODELS = "download_models"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class RequiredFile:
    """Release manifest entry, including tokenizer/config/runtime dependencies."""

    path: str
    revision: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (not self.path or self.path == "." or str(path) != self.path
                or path.is_absolute() or ".." in path.parts or "\\" in self.path):
            raise ValueError("Manifest paths must stay within the model directory")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("A pinned repository revision is required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("A file SHA-256 is required")
        if self.size_bytes < 0:
            raise ValueError("File sizes cannot be negative")


@dataclass(frozen=True, slots=True)
class FileEvidence:
    """A verifier's observation; bytes alone never establish completion.

    Evidence must belong to the selected volume and current release manifest.
    A transport adapter must hash existing files before accepting them; listing
    a snapshot directory or finding an old install marker is insufficient.
    """

    path: str
    revision: str
    received_bytes: int
    verified_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.received_bytes < 0:
            raise ValueError("Received bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    total_bytes: int
    received_bytes: int
    verified_bytes: int
    total_files: int
    verified_files: int
    pending_paths: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.total_files > 0 and self.verified_files == self.total_files

    @property
    def percent(self) -> float:
        # A transfer reaching 100% still has a separate verification phase.
        return round(100 * self.received_bytes / self.total_bytes, 2) if self.total_bytes else 0.0


def model_progress(
    required: tuple[RequiredFile, ...], observed: tuple[FileEvidence, ...]
) -> DownloadProgress:
    """Combine current-volume evidence without counting extra/wrong-revision files."""
    if len({entry.path for entry in required}) != len(required):
        raise ValueError("Duplicate manifest paths")
    if len({entry.path for entry in observed}) != len(observed):
        raise ValueError("Duplicate evidence paths")
    evidence = {entry.path: entry for entry in observed}
    received = verified = count = 0
    pending: list[str] = []
    for entry in required:
        found = evidence.get(entry.path)
        if found is not None and found.revision == entry.revision:
            received += min(found.received_bytes, entry.size_bytes)
            if found.received_bytes == entry.size_bytes and found.verified_sha256 == entry.sha256:
                verified += entry.size_bytes
                count += 1
                continue
        pending.append(entry.path)
    return DownloadProgress(
        sum(entry.size_bytes for entry in required), received, verified,
        len(required), count, tuple(pending),
    )


def setup_stage(
    *, account_connected: bool, volume_selected: bool,
    storage_scan_complete: bool, progress: DownloadProgress,
) -> SetupStage:
    if not account_connected:
        return SetupStage.CONNECT_ACCOUNT
    if not volume_selected:
        return SetupStage.SELECT_STORAGE
    if not storage_scan_complete:
        return SetupStage.VERIFY_STORAGE
    return SetupStage.READY if progress.ready else SetupStage.DOWNLOAD_MODELS


@dataclass(frozen=True, slots=True)
class GpuOffer:
    """Normalized offer from a fresh regional Secure Cloud catalog query.

    Global catalog availability must not be substituted for regional stock.
    supported_model_ids comes from worker-image qualification, not marketing.
    """

    gpu_id: str
    data_center: str
    vram_mib: int
    hourly_usd: Decimal
    available: bool
    cuda_compatible: bool
    supported_model_ids: frozenset[str]

    def __post_init__(self) -> None:
        if self.vram_mib <= 0 or not self.hourly_usd.is_finite() or self.hourly_usd <= 0:
            raise ValueError("GPU memory and quoted price must be positive")


def ranked_gpu_offers(
    offers: tuple[GpuOffer, ...], *, volume_data_center: str,
    required_vram_mib: int, model_ids: frozenset[str],
    max_hourly_usd: Decimal | None = None,
) -> tuple[GpuOffer, ...]:
    """Cheapest hourly price among available, qualified GPUs beside the volume.

    This is not the cheapest completed-job prediction: faster GPUs can have a
    lower total cost. Requery immediately before provisioning and retry the
    next eligible offer when the provider reports a stock race.
    """
    if required_vram_mib <= 0 or not model_ids:
        raise ValueError("A resolved job and memory requirement are required")
    if max_hourly_usd is not None and (not max_hourly_usd.is_finite() or max_hourly_usd <= 0):
        raise ValueError("Hourly budget must be positive")
    eligible = (
        offer for offer in offers
        if offer.gpu_id.startswith("NVIDIA ")
        and offer.data_center == volume_data_center
        and offer.available and offer.cuda_compatible
        and offer.vram_mib >= required_vram_mib
        and model_ids <= offer.supported_model_ids
        and (max_hourly_usd is None or offer.hourly_usd <= max_hourly_usd)
    )
    return tuple(sorted(eligible, key=lambda offer: (offer.hourly_usd, offer.gpu_id)))


def residency_requirement(
    reservations_mib: tuple[int, ...], *, headroom_mib: int,
    exclusive_residency_qualified: bool = False,
) -> int:
    """Default to current concurrent reservations; exclusive mode needs evidence.

    Exclusive mode requires the caller to have measured and implemented process
    eviction between helper/audio/conversion stages under a single GPU slot.
    Keeping every model on disk does not require every model in VRAM.
    """
    if not reservations_mib or any(value <= 0 for value in reservations_mib) or headroom_mib < 0:
        raise ValueError("Valid memory reservations and headroom are required")
    resident = max(reservations_mib) if exclusive_residency_qualified else sum(reservations_mib)
    return resident + headroom_mib


@dataclass(frozen=True, slots=True)
class ComputeEstimate:
    startup_usd: Decimal
    generation_usd: Decimal
    idle_usd: Decimal

    @property
    def total_compute_usd(self) -> Decimal:
        return self.startup_usd + self.generation_usd + self.idle_usd


def compute_estimate(
    *, hourly_usd: Decimal, startup_sec: Decimal,
    generation_sec: Decimal, idle_sec: Decimal = Decimal(0),
) -> ComputeEstimate:
    """Planning estimate; excludes persistent storage, tax and invoice rounding."""
    values = (hourly_usd, startup_sec, generation_sec, idle_sec)
    if any(not value.is_finite() or value < 0 for value in values) or hourly_usd == 0:
        raise ValueError("Finite nonnegative timings and a positive rate are required")
    rate = hourly_usd / Decimal(3600)
    return ComputeEstimate(rate * startup_sec, rate * generation_sec, rate * idle_sec)


def may_release_compute(
    *, app_owned_resource: bool, queued_jobs: int, active_jobs: int,
    outputs_durable: bool, installer_active: bool,
) -> bool:
    """Use under the same lifecycle lock as admission; never remove the volume."""
    if queued_jobs < 0 or active_jobs < 0:
        raise ValueError("Job counts cannot be negative")
    return (app_owned_resource and queued_jobs == 0 and active_jobs == 0
            and outputs_durable and not installer_active)
