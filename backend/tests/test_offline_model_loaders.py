"""Pinned worker snapshots load offline without optional Hub tree metadata."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.inference.runtimes.chatterbox import ChatterboxBackend
from app.inference.runtimes.gemma_transliterator import GemmaTransliteratorBackend
from app.inference.runtimes.hub_cache import hub_is_offline
from app.inference.runtimes.qwen_analyzer import QwenAnalyzerBackend
from app.inference.runtimes.voxcpm import VoxCPMBackend

_REVISION = "a" * 40
_BACKENDS = (VoxCPMBackend, ChatterboxBackend, QwenAnalyzerBackend, GemmaTransliteratorBackend)


@pytest.fixture(autouse=True)
def clean_offline_environment(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)


@pytest.mark.parametrize("variable", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "ON"])
def test_configured_offline_flags(variable, value, monkeypatch):
    monkeypatch.setenv(variable, value)
    assert hub_is_offline()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_false_flags_preserve_online_downloads(value, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", value)
    assert not hub_is_offline()


def _install_fake_runtime_packages(monkeypatch, model_loads):
    class VoxCPM:
        def __init__(self, *, voxcpm_model_path, enable_denoiser, optimize):
            assert not enable_denoiser and not optimize
            model_loads.append(voxcpm_model_path)
            self.tts_model = SimpleNamespace(sample_rate=48000)

    class Chatterbox:
        @staticmethod
        def from_local(path, device):
            assert device == "cuda"
            model_loads.append(path)
            return SimpleNamespace(sr=24000)

    class Tokenizer:
        @staticmethod
        def from_pretrained(path):
            model_loads.append(path)
            return SimpleNamespace(chat_template="")

    class Model:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert kwargs["device_map"] == "cuda"
            model_loads.append(path)
            return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "voxcpm.core", SimpleNamespace(VoxCPM=VoxCPM))
    monkeypatch.setitem(
        sys.modules, "chatterbox.mtl_tts", SimpleNamespace(ChatterboxMultilingualTTS=Chatterbox),
    )
    monkeypatch.setitem(
        sys.modules, "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True), bfloat16="bfloat16"),
    )
    monkeypatch.setitem(
        sys.modules, "transformers",
        SimpleNamespace(
            AutoTokenizer=Tokenizer,
            AutoModelForCausalLM=Model,
            AutoConfig=SimpleNamespace(from_pretrained=lambda path: SimpleNamespace()),
        ),
    )


@pytest.mark.parametrize("backend_type", _BACKENDS)
@pytest.mark.parametrize("flag", [None, "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
def test_loader_resolves_exact_snapshot_with_configured_offline_contract(
    backend_type, flag, monkeypatch,
):
    if flag:
        monkeypatch.setenv(flag, "1")
    calls, model_loads = [], []
    _install_fake_runtime_packages(monkeypatch, model_loads)

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        assert kwargs["repo_id"] == "test/checkpoint"
        assert kwargs["revision"] == _REVISION
        assert kwargs["local_files_only"] is bool(flag)
        return "/verified/exact-snapshot"

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download),
    )
    runtime = backend_type()
    if hasattr(runtime, "_warm"):
        monkeypatch.setattr(runtime, "_warm", lambda: None)
    runtime.load("test-model", "test/checkpoint", _REVISION)
    assert len(calls) == 1
    assert model_loads and set(model_loads) == {"/verified/exact-snapshot"}
    assert runtime.loaded_model_id == "test-model"
    if backend_type is ChatterboxBackend:
        assert "t3_mtl23ls_v2.safetensors" in calls[0]["allow_patterns"]


@pytest.mark.parametrize("backend_type", _BACKENDS)
def test_missing_offline_snapshot_fails_without_unpinned_or_online_fallback(
    backend_type, monkeypatch,
):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    model_loads, calls = [], []
    _install_fake_runtime_packages(monkeypatch, model_loads)

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        assert kwargs["revision"] == _REVISION
        assert kwargs["local_files_only"] is True
        raise FileNotFoundError("Missing pinned snapshot")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download),
    )
    runtime = backend_type()
    with pytest.raises(FileNotFoundError, match="Missing pinned snapshot"):
        runtime.load("test-model", "test/checkpoint", _REVISION)
    assert len(calls) == 1 and not model_loads
    assert runtime.loaded_model_id is None


def test_voxcpm_lora_preserves_token_and_pin_offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_TOKEN", "test-private-token")
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return "/verified/lora"

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download),
    )
    assert VoxCPMBackend._resolve_lora_dir(None, "test/adapter", _REVISION) == "/verified/lora"
    assert calls == [{
        "repo_id": "test/adapter", "revision": _REVISION,
        "token": "test-private-token", "local_files_only": True,
    }]


def test_voxcpm_local_lora_does_not_contact_hub(tmp_path: Path, monkeypatch):
    (tmp_path / "lora_config.json").write_text("{}", encoding="utf-8")

    def forbidden_download(**kwargs):
        raise AssertionError("Local adapter must not contact Hub")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=forbidden_download),
    )
    result = VoxCPMBackend._resolve_lora_dir(str(tmp_path), "test/adapter", _REVISION)
    assert result == str(tmp_path)


@pytest.mark.parametrize("backend_type", _BACKENDS)
@pytest.mark.parametrize("cache_namespace", ["hf-cache", "voice-clone/hf-cache"])
def test_exact_production_hub_loads_installer_snapshot_without_tree_metadata(
    backend_type, cache_namespace, tmp_path: Path, monkeypatch,
):
    """Opt-in integration: place the production Hub wheel on PYTHONPATH.

    The ordinary torch-free API test environment does not include Hub. This
    check uses the actual snapshot_download implementation, with tiny cache
    files and fake model constructors; it is not a GPU generation test.
    """
    hub = pytest.importorskip("huggingface_hub")
    if hub.__version__ != "1.33.0":
        pytest.skip("Exact worker Hub 1.33.0 is required for this regression")
    import httpx
    from huggingface_hub import constants
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    cache = tmp_path / "workspace" / cache_namespace / "hub"
    snapshot = cache / "models--test--checkpoint" / "snapshots" / _REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    assert not (snapshot.parent.parent / "trees").exists()
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Offline resolution attempted a network request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_network)

    # Missing optional tree metadata reproduces the former loader failure.
    with pytest.raises(OfflineModeIsEnabled):
        hub.snapshot_download(repo_id="test/checkpoint", revision=_REVISION)

    model_loads = []
    _install_fake_runtime_packages(monkeypatch, model_loads)
    runtime = backend_type()
    if hasattr(runtime, "_warm"):
        monkeypatch.setattr(runtime, "_warm", lambda: None)
    runtime.load("test-model", "test/checkpoint", _REVISION)
    assert model_loads and {Path(path) for path in model_loads} == {snapshot}
    assert runtime.loaded_model_id == "test-model"

    # Local-only must still fail for an absent snapshot, without changing pin.
    with pytest.raises(LocalEntryNotFoundError):
        runtime.load("absent-model", "test/absent", _REVISION)
