"""Adapter contract checks with an injected backend; no GPU generation here."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.inference.runtimes import chatterbox_vc as vc


class FakeBackend:
    sr = 24_000

    def __init__(self, result=None):
        self.result = np.full((1, 2400), 0.125, dtype=np.float32) if result is None else result
        self.calls = []

    def generate(self, audio, target_voice_path):
        self.calls.append((Path(audio), Path(target_voice_path)))
        self.source_bytes = Path(audio).read_bytes()
        self.target_bytes = Path(target_voice_path).read_bytes()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "speech.wav"
    target = tmp_path / "voice.wav"
    sf.write(source, np.full(1600, 0.1), 16000, format="WAV")
    sf.write(target, np.full(2400, 0.2), 24000, format="WAV")
    return {"source_audio": source, "source_sha256": vc.sha256_file(source),
            "target_reference": target, "target_sha256": vc.sha256_file(target),
            "output_path": tmp_path / "result.wav"}


def adapter(tmp_path, monkeypatch, backend):
    snapshot = tmp_path / "snapshots" / vc.HF_REVISION
    snapshot.mkdir(parents=True)
    weight = snapshot / "s3gen.safetensors"
    weight.write_bytes(b"test-only checkpoint")
    monkeypatch.setattr(vc, "WEIGHT_SHA256", vc.sha256_file(weight))
    model = vc.ChatterboxVCAdapter(factory=lambda path: backend)
    model.load(snapshot)
    return model


def test_passes_real_source_and_target_copies_with_provenance(tmp_path, monkeypatch, inputs):
    backend = FakeBackend()
    model = adapter(tmp_path, monkeypatch, backend)
    result = model.convert(**inputs)
    source, target = backend.calls[0]
    assert source != inputs["source_audio"] and target != inputs["target_reference"]
    assert backend.source_bytes == inputs["source_audio"].read_bytes()
    assert backend.target_bytes == inputs["target_reference"].read_bytes()
    assert not source.exists() and not target.exists()
    assert result.output_sha256 == vc.sha256_file(inputs["output_path"])
    assert result.model_id == "chatterbox_vc" and result.hf_revision == vc.HF_REVISION
    assert result.sample_rate == 24000 and result.duration_sec == pytest.approx(0.1)
    assert result.generation_time_sec >= 0 and result.load_time_sec >= 0
    assert result.quality_status == "awaiting_listening_review"


def test_rejects_audio_hash_change_before_model_call(tmp_path, monkeypatch, inputs):
    backend = FakeBackend()
    model = adapter(tmp_path, monkeypatch, backend)
    inputs["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="authorized hash"):
        model.convert(**inputs)
    assert not backend.calls and not inputs["output_path"].exists()


def test_rejects_unpinned_or_corrupt_checkpoint(tmp_path):
    model = vc.ChatterboxVCAdapter(factory=lambda path: FakeBackend())
    with pytest.raises(ValueError, match="pinned"):
        model.load(tmp_path / "main")
    snapshot = tmp_path / vc.HF_REVISION
    snapshot.mkdir()
    (snapshot / "s3gen.safetensors").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        model.load(snapshot)


@pytest.mark.parametrize("bad", [np.array([]), np.array([np.nan]), np.zeros(5),
                                      np.full((2, 5), 0.1), np.full(5, 2.0)])
def test_invalid_audio_never_publishes_output(tmp_path, monkeypatch, inputs, bad):
    model = adapter(tmp_path, monkeypatch, FakeBackend(bad))
    with pytest.raises(ValueError, match="audio"):
        model.convert(**inputs)
    assert not inputs["output_path"].exists()
    assert not list(tmp_path.glob(".vcs-vc-*"))


def test_backend_failure_and_existing_output_preserved(tmp_path, monkeypatch, inputs):
    backend = FakeBackend(RuntimeError("GPU failed"))
    model = adapter(tmp_path, monkeypatch, backend)
    with pytest.raises(RuntimeError, match="GPU failed"):
        model.convert(**inputs)
    source, target = backend.calls[0]
    assert not source.exists() and not target.exists()
    assert not inputs["output_path"].exists()
    inputs["output_path"].write_bytes(b"existing output")
    with pytest.raises(FileExistsError):
        model.convert(**inputs)
    assert inputs["output_path"].read_bytes() == b"existing output"
    assert len(backend.calls) == 1


def test_requires_chunking_before_hour_recording_conversion(tmp_path, monkeypatch, inputs):
    backend = FakeBackend()
    model = adapter(tmp_path, monkeypatch, backend)
    sf.write(inputs["source_audio"], np.full(31 * 8000, 0.1), 8000, format="WAV")
    inputs["source_sha256"] = vc.sha256_file(inputs["source_audio"])
    with pytest.raises(ValueError, match="30 seconds"):
        model.convert(**inputs)
    assert not backend.calls


def test_failed_load_cannot_reuse_previous_backend(tmp_path, monkeypatch):
    model = adapter(tmp_path, monkeypatch, FakeBackend())
    with pytest.raises(ValueError):
        model.load(tmp_path / "main")
    with pytest.raises(RuntimeError, match="Load"):
        model.convert(source_audio=tmp_path, source_sha256="0" * 64,
                      target_reference=tmp_path, target_sha256="0" * 64,
                      output_path=tmp_path / "new.wav")
