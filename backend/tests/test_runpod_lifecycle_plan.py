from dataclasses import replace
from decimal import Decimal

import pytest

from app.runpod.lifecycle_plan import (
    FileEvidence,
    GpuOffer,
    RequiredFile,
    SetupStage,
    compute_estimate,
    may_release_compute,
    model_progress,
    ranked_gpu_offers,
    residency_requirement,
    setup_stage,
)

REV = "a" * 40
HASH = "b" * 64
MANIFEST = (
    RequiredFile("tts/weights.safetensors", REV, 100, HASH),
    RequiredFile("helper/tokenizer.json", REV, 10, "c" * 64),
)


def test_reuse_requires_every_pinned_file_hash_not_just_downloaded_bytes():
    evidence = (FileEvidence(MANIFEST[0].path, REV, 100, HASH),
                FileEvidence(MANIFEST[1].path, REV, 10))
    progress = model_progress(MANIFEST, evidence)
    assert progress.percent == 100
    assert not progress.ready
    assert progress.verified_files == 1
    assert progress.pending_paths == (MANIFEST[1].path,)
    complete = model_progress(
        MANIFEST, (evidence[0], replace(evidence[1], verified_sha256="c" * 64))
    )
    assert complete.ready
    assert complete.verified_bytes == 110


def test_partial_wrong_revision_hash_and_extra_files_cannot_unlock_generation():
    progress = model_progress(MANIFEST, (
        FileEvidence(MANIFEST[0].path, "d" * 40, 100, HASH),
        FileEvidence(MANIFEST[1].path, REV, 1000, HASH),
        FileEvidence("unrelated/file", REV, 10000, HASH),
    ))
    assert progress.received_bytes == 10
    assert progress.verified_bytes == 0
    assert not progress.ready
    assert not model_progress((), ()).ready


@pytest.mark.parametrize(
    "path", ["/absolute/file", "../escape", "a/../escape", "a\\b", "", ".", "a//b", "a/./b"]
)
def test_manifest_rejects_paths_outside_root(path):
    with pytest.raises(ValueError):
        RequiredFile(path, REV, 1, HASH)


def test_duplicate_evidence_and_manifest_are_rejected():
    with pytest.raises(ValueError):
        model_progress((MANIFEST[0], MANIFEST[0]), ())
    found = FileEvidence(MANIFEST[0].path, REV, 1)
    with pytest.raises(ValueError):
        model_progress(MANIFEST, (found, found))


def test_setup_requires_account_storage_scan_then_download():
    partial = model_progress(MANIFEST, ())
    assert setup_stage(account_connected=False, volume_selected=True,
                       storage_scan_complete=True, progress=partial) == SetupStage.CONNECT_ACCOUNT
    assert setup_stage(account_connected=True, volume_selected=False,
                       storage_scan_complete=True, progress=partial) == SetupStage.SELECT_STORAGE
    assert setup_stage(account_connected=True, volume_selected=True,
                       storage_scan_complete=False, progress=partial) == SetupStage.VERIFY_STORAGE
    assert setup_stage(account_connected=True, volume_selected=True,
                       storage_scan_complete=True, progress=partial) == SetupStage.DOWNLOAD_MODELS


def offer(name="NVIDIA A40", price="0.49", **kwargs):
    base = GpuOffer(
        name, "EU-RO-1", 48 * 1024, Decimal(price), True, True, frozenset({"tts", "helper"})
    )
    return replace(base, **kwargs)


def test_selects_cheapest_regional_qualified_available_gpu_with_fallback_order():
    valid = offer()
    faster = offer("NVIDIA A100", "1.20", vram_mib=80 * 1024)
    rejected = (
        offer("NVIDIA WrongRegion", "0.01", data_center="US-KS-2"),
        offer("NVIDIA NoStock", "0.01", available=False),
        offer("NVIDIA TooSmall", "0.01", vram_mib=24 * 1024),
        offer("NVIDIA WrongCuda", "0.01", cuda_compatible=False),
        offer("AMD MI300X", "0.01"),
        offer("NVIDIA Untested", "0.01", supported_model_ids=frozenset({"tts"})),
    )
    ranked = ranked_gpu_offers(
        (faster, *rejected, valid), volume_data_center="EU-RO-1",
        required_vram_mib=43548, model_ids=frozenset({"tts", "helper"}),
    )
    assert ranked == (valid, faster)
    assert ranked_gpu_offers((valid,), volume_data_center="EU-RO-1",
                             required_vram_mib=43548, model_ids=frozenset({"tts"}),
                             max_hourly_usd=Decimal("0.40")) == ()


def test_full_feature_residency_is_concurrent_until_eviction_is_qualified():
    reservations = (16000, 19500, 6000)
    assert residency_requirement(reservations, headroom_mib=2048) == 43548
    assert residency_requirement(reservations, headroom_mib=2048,
                                 exclusive_residency_qualified=True) == 21548


def test_startup_and_idle_are_billed_compute_not_just_render_time():
    result = compute_estimate(hourly_usd=Decimal("0.49"), startup_sec=Decimal(120),
                              generation_sec=Decimal(30), idle_sec=Decimal(0))
    assert result.startup_usd == Decimal("0.49") / 30
    assert result.total_compute_usd > result.generation_usd
    with pytest.raises(ValueError):
        compute_estimate(
            hourly_usd=Decimal("NaN"), startup_sec=Decimal(0), generation_sec=Decimal(0)
        )


def test_release_only_owned_compute_after_all_results_durable_and_queue_drained():
    conditions = dict(app_owned_resource=True, queued_jobs=0, active_jobs=0,
                      outputs_durable=True, installer_active=False)
    assert may_release_compute(**conditions)
    for key, value in (("app_owned_resource", False), ("queued_jobs", 1),
                       ("active_jobs", 1), ("outputs_durable", False), ("installer_active", True)):
        assert not may_release_compute(**(conditions | {key: value}))
