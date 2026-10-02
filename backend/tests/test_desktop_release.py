"""Update feeds bind actual installer bytes, signed version and the embedded key."""

import base64
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

pytest.importorskip("cryptography", reason="Release preparation uses the packaging verifier")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare-desktop-release.py"
spec = importlib.util.spec_from_file_location("desktop_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def package(tmp_path: Path) -> dict:
    key = Ed25519PrivateKey.generate()
    key_id = b"test-key"
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    public_text = (
        "untrusted comment: test public key\n" + base64.b64encode(b"Ed" + key_id + public).decode()
    )
    config = {
        "productName": "AI Voice Clone Studio",
        "version": "0.1.1",
        "plugins": {
            "updater": {
                "pubkey": base64.b64encode(public_text.encode()).decode(),
                "requireSignedVersion": True,
                "endpoints": [release.FEED_URL],
            }
        },
    }
    config_path = tmp_path / "tauri.conf.json"
    config_path.write_text(json.dumps(config))
    installer = tmp_path / "AI Voice Clone Studio_0.1.1_x64-setup.exe"
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 80)
    data[80:84] = b"PE\0\0"
    struct.pack_into("<H", data, 84, 0x14C)
    data[120:136] = release.NSIS_MAGIC
    installer.write_bytes(data)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "installer": installer.name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "target": release.TARGET,
            }
        )
    )
    signature_path = tmp_path / (installer.name + ".sig")

    def sign(*, version="0.1.1", filename=None, extra="", algorithm=b"ED"):
        content = installer.read_bytes()
        message = (
            hashlib.blake2b(content, digest_size=64).digest() if algorithm == b"ED" else content
        )
        signed = key.sign(message)
        trusted = f"timestamp:1\tfile:{filename or installer.name}\tversion:{version}" + extra
        global_signature = key.sign(signed + trusted.encode())
        text = "\n".join(
            [
                "untrusted comment: test signature",
                base64.b64encode(algorithm + key_id + signed).decode(),
                "trusted comment: " + trusted,
                base64.b64encode(global_signature).decode(),
            ]
        )
        signature_path.write_text(base64.b64encode(text.encode()).decode())

    sign()
    return {
        "installer": installer,
        "signature": signature_path,
        "receipt": receipt_path,
        "config": config_path,
        "sign": sign,
    }


def prepare(package, **kwargs):
    return release.prepare(
        package["installer"], package["signature"], package["receipt"], package["config"], **kwargs
    )


def test_prepares_verified_feed_without_publication(package) -> None:
    feed, evidence = prepare(package)
    assert feed["version"] == "0.1.1"
    assert set(feed["platforms"]) == {"windows-x86_64", "windows-x86_64-nsis"}
    assert feed["platforms"]["windows-x86_64"]["signature"] == package["signature"].read_text()
    assert (
        feed["platforms"]["windows-x86_64"]["url"]
        == "https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/download/v0.1.1/AI-Voice-Clone-Studio_0.1.1_x64-setup.exe"
    )
    assert evidence["cryptographic_signature_verified"] and evidence["signed_version_verified"]
    assert not evidence["published"] and not evidence["authenticode_verified"]


def test_supports_legacy_ed25519_when_signed_version_is_present(package) -> None:
    package["sign"](algorithm=b"Ed")
    assert prepare(package)[1]["cryptographic_signature_verified"]


@pytest.mark.parametrize("version", ["0.1.0", "", "0.2.0"])
def test_rejects_cryptographically_signed_wrong_or_missing_version(package, version) -> None:
    package["sign"](version=version)
    with pytest.raises(release.ReleaseError, match="version"):
        prepare(package)


def test_rejects_signed_wrong_filename(package) -> None:
    package["sign"](filename="another.exe")
    with pytest.raises(release.ReleaseError, match="filename"):
        prepare(package)


def test_rejects_tampered_trusted_comment(package) -> None:
    text = (
        base64.b64decode(package["signature"].read_text())
        .decode()
        .replace("version:0.1.1", "version:0.9.9")
    )
    package["signature"].write_text(base64.b64encode(text.encode()).decode())
    with pytest.raises(release.ReleaseError, match="cryptographic"):
        prepare(package)


def test_rejects_tampered_artifact_even_if_receipt_is_updated(package) -> None:
    data = bytearray(package["installer"].read_bytes())
    data[-1] ^= 1
    package["installer"].write_bytes(data)
    receipt = json.loads(package["receipt"].read_text())
    receipt["sha256"] = hashlib.sha256(data).hexdigest()
    package["receipt"].write_text(json.dumps(receipt))
    with pytest.raises(release.ReleaseError, match="cryptographic"):
        prepare(package)


def test_rejects_different_embedded_key(package) -> None:
    config = json.loads(package["config"].read_text())
    encoded = config["plugins"]["updater"]["pubkey"]
    lines = base64.b64decode(encoded).decode().splitlines()
    body = bytearray(base64.b64decode(lines[1]))
    body[10] ^= 1
    lines[1] = base64.b64encode(body).decode()
    config["plugins"]["updater"]["pubkey"] = base64.b64encode("\n".join(lines).encode()).decode()
    package["config"].write_text(json.dumps(config))
    with pytest.raises(release.ReleaseError, match="cryptographic"):
        prepare(package)


@pytest.mark.parametrize("signature", ["garbage", "", "dGVzdA=="])
def test_rejects_malformed_signature(package, signature) -> None:
    package["signature"].write_text(signature)
    with pytest.raises(release.ReleaseError):
        prepare(package)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bytes", 999),
        ("sha256", "0" * 64),
        ("installer", "wrong.exe"),
        ("target", "aarch64-pc-windows-msvc"),
    ],
)
def test_rejects_wrong_receipt(package, field, value) -> None:
    receipt = json.loads(package["receipt"].read_text())
    receipt[field] = value
    package["receipt"].write_text(json.dumps(receipt))
    with pytest.raises(release.ReleaseError):
        prepare(package)


def test_hash_only_build_receipt_requires_independent_expected_size(package) -> None:
    original = json.loads(package["receipt"].read_text())
    package["receipt"].write_text(
        json.dumps(
            {
                "target": release.TARGET,
                "artifacts": [{"file": original["installer"], "sha256": original["sha256"]}],
            }
        )
    )
    with pytest.raises(release.ReleaseError, match="expected-size"):
        prepare(package)
    assert prepare(package, expected_size=original["bytes"])[1]["bytes"] == original["bytes"]


def test_fixed_https_repository_and_signed_version_required(package) -> None:
    config = json.loads(package["config"].read_text())
    config["plugins"]["updater"]["endpoints"] = ["http://example.com/latest.json"]
    package["config"].write_text(json.dumps(config))
    with pytest.raises(release.ReleaseError, match="fixed HTTPS"):
        prepare(package)


def test_duplicate_trusted_version_fields_are_rejected(package) -> None:
    package["sign"](extra="\tversion:0.1.1")
    with pytest.raises(release.ReleaseError, match="duplicate"):
        prepare(package)


@pytest.mark.parametrize("version", ["01.1.0", "1.2", "1.0.0-01", "../1.0.0", "v1.0.0"])
def test_invalid_semver_refused(version) -> None:
    with pytest.raises(release.ReleaseError):
        release._version(version)


def test_pinned_minisign_reference_vector_independently_validates() -> None:
    key = "untrusted comment: reference\nRWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
    signature = "\n".join(
        [
            "untrusted comment: signature from minisign secret key",
            "RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=",
            "trusted comment: timestamp:1556193335\tfile:test",
            "y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==",
        ]
    )
    fields = release.verify_signature(
        b"test",
        base64.b64encode(signature.encode()).decode(),
        base64.b64encode(key.encode()).decode(),
    )
    assert fields == {"timestamp": "1556193335", "file": "test"}


@pytest.mark.parametrize("name", ["installer", "signature"])
def test_missing_artifact_refused(package, name) -> None:
    package[name].unlink()
    with pytest.raises(release.ReleaseError, match="missing"):
        prepare(package)


def test_cli_prepares_only_local_feed_and_evidence(package, tmp_path, monkeypatch) -> None:
    output = tmp_path / "prepared"
    monkeypatch.setattr(
        "sys.argv",
        [
            str(SCRIPT),
            "--installer",
            str(package["installer"]),
            "--signature",
            str(package["signature"]),
            "--receipt",
            str(package["receipt"]),
            "--tauri-config",
            str(package["config"]),
            "--output-dir",
            str(output),
        ],
    )
    assert release.main() == 0
    assert sorted(p.name for p in output.iterdir()) == ["latest.json", "release-evidence.json"]
    assert json.loads((output / "release-evidence.json").read_text())["published"] is False


def test_invalid_signature_produces_no_output(package, tmp_path, monkeypatch) -> None:
    output = tmp_path / "not-produced"
    package["signature"].write_text("malformed")
    monkeypatch.setattr(
        "sys.argv",
        [
            str(SCRIPT),
            "--installer",
            str(package["installer"]),
            "--signature",
            str(package["signature"]),
            "--receipt",
            str(package["receipt"]),
            "--tauri-config",
            str(package["config"]),
            "--output-dir",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as result:
        release.main()
    assert result.value.code == 1 and not output.exists()
