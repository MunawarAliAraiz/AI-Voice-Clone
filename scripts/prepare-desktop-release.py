"""Prepare a verified, local Tauri update feed. Never uploads or reads signing secrets."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import struct
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

REPOSITORY = "MunawarAliAraiz/AI-Voice-Clone"
FEED_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/latest.json"
TARGET = "x86_64-pc-windows-msvc"
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
NSIS_MAGIC = bytes.fromhex("efbeadde") + b"NullsoftInst"


class ReleaseError(ValueError):
    pass


def _base64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ReleaseError(f"Invalid {label} encoding") from exc


def _version(value: object) -> str:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ReleaseError("The app version must be a valid SemVer without a leading v")
    prerelease = SEMVER.fullmatch(value).group(4)
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")
    ):
        raise ReleaseError("Numeric prerelease identifiers cannot have leading zeros")
    return value


def _json_file(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ReleaseError("Missing or oversized configuration/receipt")
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ReleaseError("Malformed configuration/receipt") from exc
    if not isinstance(result, dict):
        raise ReleaseError("Configuration/receipt must be an object")
    return result


def _receipt(receipt: dict, installer: Path, expected_size: int | None) -> tuple[str, int]:
    if receipt.get("target", TARGET) != TARGET:
        raise ReleaseError("Receipt target is not Windows x86_64")
    if "artifacts" in receipt:
        artifacts = receipt["artifacts"]
        if not isinstance(artifacts, list):
            raise ReleaseError("Malformed artifact receipt")
        matches = [
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("file") == installer.name
        ]
        if len(matches) != 1:
            raise ReleaseError("Receipt must identify exactly this installer")
        record = matches[0]
    else:
        if receipt.get("installer") != installer.name:
            raise ReleaseError("Receipt installer filename does not match")
        record = receipt
    sha = record.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise ReleaseError("Receipt requires a SHA256 digest")
    size = record.get("bytes", record.get("size"))
    if size is not None and (type(size) is not int or not 128 <= size <= MAX_INSTALLER_BYTES):
        raise ReleaseError("Receipt installer size is invalid")
    if expected_size is not None and (
        type(expected_size) is not int or not 128 <= expected_size <= MAX_INSTALLER_BYTES
    ):
        raise ReleaseError("Expected installer size is invalid")
    if size is None:
        size = expected_size
    elif expected_size is not None and expected_size != size:
        raise ReleaseError("Expected size and receipt size differ")
    if size is None:
        raise ReleaseError(
            "Supply receipt bytes or --expected-size; filesystem size alone is not evidence"
        )
    return sha.lower(), size


def verify_signature(installer: bytes, signature: str, encoded_key: str) -> dict[str, str]:
    """Validate the same Ed25519/Blake2b and global comment signatures as Minisign."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ReleaseError(
            "Cryptographic verifier unavailable. Use the packaging Python with cryptography; "
            "no feed was prepared."
        ) from exc
    try:
        key_lines = _base64(encoded_key, "public key").decode("utf-8").splitlines()
        lines = _base64(signature, "signature").decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReleaseError("Signature/public key text is not UTF-8") from exc
    if len(key_lines) != 2 or not key_lines[0].startswith("untrusted comment: "):
        raise ReleaseError("Malformed public key")
    if (
        len(lines) != 4
        or not lines[0].startswith("untrusted comment: ")
        or not lines[2].startswith("trusted comment: ")
    ):
        raise ReleaseError("Malformed Minisign signature")
    key = _base64(key_lines[1], "public key body")
    signed = _base64(lines[1], "signature body")
    global_signature = _base64(lines[3], "trusted comment signature")
    if (
        len(key) != 42
        or key[:2] not in {b"Ed", b"ED"}
        or len(signed) != 74
        or len(global_signature) != 64
    ):
        raise ReleaseError("Malformed Minisign key/signature lengths")
    if signed[:2] not in {b"Ed", b"ED"} or key[2:10] != signed[2:10]:
        raise ReleaseError("Signature algorithm or signing key does not match")
    message = (
        hashlib.blake2b(installer, digest_size=64).digest() if signed[:2] == b"ED" else installer
    )
    public = Ed25519PublicKey.from_public_bytes(key[10:])
    trusted = lines[2][len("trusted comment: ") :]
    try:
        public.verify(signed[10:], message)
        public.verify(global_signature, signed[10:] + trusted.encode("utf-8"))
    except InvalidSignature as exc:
        raise ReleaseError(
            "Installer or trusted comment cryptographic signature is invalid"
        ) from exc
    fields = {}
    for field in trusted.split("\t"):
        name, separator, value = field.partition(":")
        if not separator or name in fields:
            raise ReleaseError("Malformed or duplicate trusted comment field")
        fields[name] = value
    return fields


def prepare(
    installer: Path,
    signature_file: Path,
    receipt_file: Path,
    config_file: Path,
    *,
    expected_size: int | None = None,
    notes: str = "",
) -> tuple[dict, dict]:
    config = _json_file(config_file)
    version = _version(config.get("version"))
    updater = config.get("plugins", {}).get("updater", {})
    if (
        not isinstance(updater, dict)
        or updater.get("endpoints") != [FEED_URL]
        or updater.get("requireSignedVersion") is not True
    ):
        raise ReleaseError(
            "Updater must use the fixed HTTPS repository feed and requireSignedVersion"
        )
    encoded_key = updater.get("pubkey")
    if not isinstance(encoded_key, str):
        raise ReleaseError("Embedded updater public key is missing")
    if not installer.is_file() or installer.is_symlink() or installer.suffix.lower() != ".exe":
        raise ReleaseError("NSIS installer is missing or not a regular .exe")
    expected_filename = f"{config.get('productName')}_{version}_x64-setup.exe"
    if installer.name != expected_filename:
        raise ReleaseError(
            "Installer filename does not match the app product/version and x64 NSIS target"
        )
    if not signature_file.is_file() or signature_file.stat().st_size > 16_384:
        raise ReleaseError("Installer .sig file is missing or oversized")
    if signature_file.name != installer.name + ".sig":
        raise ReleaseError("Signature filename must belong to this installer")
    sha, size = _receipt(_json_file(receipt_file), installer, expected_size)
    if installer.stat().st_size != size:
        raise ReleaseError("Installer bytes differ from the approved size")
    data = installer.read_bytes()
    if len(data) < 128 or data[:2] != b"MZ":
        raise ReleaseError("Installer is not a Windows executable")
    pe_offset = struct.unpack_from("<I", data, 60)[0]
    if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ReleaseError("Installer PE header is invalid")
    # NSIS uses an x86 launcher even when its application payload is x64.
    if (
        struct.unpack_from("<H", data, pe_offset + 4)[0] not in {0x14C, 0x8664}
        or NSIS_MAGIC not in data
    ):
        raise ReleaseError("Installer does not contain a supported Windows NSIS launcher")
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != sha:
        raise ReleaseError("Installer SHA256 differs from the approved receipt")
    signature = signature_file.read_text(encoding="ascii").strip()
    fields = verify_signature(data, signature, encoded_key)
    if fields.get("version", "").removeprefix("v") != version:
        raise ReleaseError(
            "Cryptographically signed version differs from the announced app version"
        )
    if fields.get("file") != installer.name:
        raise ReleaseError("Cryptographically signed filename differs from the installer")
    asset_name = f"AI-Voice-Clone-Studio_{version}_x64-setup.exe"
    url = (
        f"https://github.com/{REPOSITORY}/releases/download/"
        f"{quote('v' + version, safe='')}/{quote(asset_name, safe='')}"
    )
    platform = {"url": url, "signature": signature}
    feed = {
        "version": version,
        "notes": notes,
        "pub_date": datetime.now(UTC).isoformat(),
        "platforms": {"windows-x86_64": platform, "windows-x86_64-nsis": platform},
    }
    evidence = {
        "version": version,
        "installer": installer.name,
        "asset_name": asset_name,
        "bytes": size,
        "sha256": actual_sha,
        "target": TARGET,
        "cryptographic_signature_verified": True,
        "signed_version_verified": True,
        "signed_filename_verified": True,
        "authenticode_verified": False,
        "published": False,
        "url": url,
    }
    return feed, evidence


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--tauri-config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "frontend/src-tauri/tauri.conf.json",
    )
    parser.add_argument("--expected-size", type=int)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        notes = args.notes_file.read_text(encoding="utf-8") if args.notes_file else ""
        if len(notes) > 20_000:
            raise ReleaseError("Release notes exceed 20,000 characters")
        feed, evidence = prepare(
            args.installer,
            args.signature,
            args.receipt,
            args.tauri_config,
            expected_size=args.expected_size,
            notes=notes,
        )
        _write_json(args.output_dir / "latest.json", feed)
        _write_json(args.output_dir / "release-evidence.json", evidence)
    except (ReleaseError, OSError, UnicodeError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Release preparation failed: {exc}\n")
    print(f"Verified local update feed prepared for {feed['version']}; nothing was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
