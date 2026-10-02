"""Collect exact FFmpeg core source and identify unfinished redistribution evidence.

This prepares a partial source package; it deliberately cannot mark the vendor's
statically linked dependency graph as complete from a README alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

REVISION = "946fcce07b6dcd0331c8cc609192aeff5e1924f8"
SOURCE_URL = f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{REVISION}"
ARCHIVE_NAME = f"ffmpeg-{REVISION}.tar.gz"
MAX_SOURCE_BYTES = 200 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(readme: str) -> list[dict[str, str]]:
    heading = "release-essentials external libraries' versions:"
    if heading not in readme:
        raise ValueError("The expected Gyan essentials dependency inventory is absent")
    entries = []
    for line in readme.split(heading, 1)[1].splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            entries.append({"name": parts[0], "version": parts[1]})
    if not entries:
        raise ValueError("No external library versions were found")
    return entries


def download_source(destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        SOURCE_URL, headers={"User-Agent": "VoiceCloneSourcePreparation/1"}
    )
    count = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            if response.geturl().split(":", 1)[0] != "https":
                raise ValueError("Source download redirected away from HTTPS")
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                count += len(chunk)
                if count > MAX_SOURCE_BYTES:
                    raise ValueError(
                        "Source archive exceeds the 200 MiB preparation limit"
                    )
                output.write(chunk)
        if count == 0:
            raise ValueError("Source archive is empty")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--license", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    binary = args.ffmpeg.resolve(strict=True)
    readme = args.build_info.read_text(encoding="utf-8-sig")
    if "9.0.2-essentials_build" not in readme or "946fcce07b" not in readme:
        raise ValueError("This collector is pinned to Gyan FFmpeg 9.0.2 / 946fcce07b")
    banner = subprocess.run(
        [str(binary), "-version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    if "--enable-nonfree" in banner:
        raise ValueError(
            "A nonfree FFmpeg binary cannot use this redistribution package"
        )
    if not re.search(r"ffmpeg version 9\.0\.2-essentials_build-www\.gyan\.dev", banner):
        raise ValueError("The executable does not match the source package version")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.build_info, output / "vendor-build-info.txt")
    shutil.copyfile(args.license, output / "COPYING.GPLv3.txt")
    (output / "binary-version.txt").write_text(banner, encoding="utf-8")
    archive = output / ARCHIVE_NAME
    if args.download:
        download_source(archive)
    config = next(
        line for line in banner.splitlines() if line.startswith("configuration:")
    )
    receipt = {
        "schema_version": 1,
        "redistribution_ready": False,
        "status": "partial-source-evidence",
        "binary": {
            "name": binary.name,
            "bytes": binary.stat().st_size,
            "sha256": sha256(binary),
        },
        "ffmpeg_core": {
            "repository": "https://github.com/FFmpeg/FFmpeg",
            "revision": REVISION,
            "archive_url": SOURCE_URL,
            "archive_file": ARCHIVE_NAME,
            "archive_present": archive.is_file(),
            "archive_bytes": archive.stat().st_size if archive.is_file() else None,
            "archive_sha256": sha256(archive) if archive.is_file() else None,
            "vendor_changes_verified": False,
        },
        "configuration": config.removeprefix("configuration: "),
        "declared_external_libraries": inventory(readme),
        "remaining": [
            "Vendor build scripts and any FFmpeg patches, or confirmation of unmodified source",
            "Exact source, patches, licenses and build instructions for every statically linked library",
            "Exact transitive MSYS2 package versions and corresponding source/build recipes",
            "Confirmation that the supplied source package corresponds to this binary",
            "Source package bundled alongside binary or hosted beside it by the app distributor",
        ],
        "evidence_urls": [
            "https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2",
            "https://www.gyan.dev/ffmpeg/builds/",
            "https://ffmpeg.org/legal.html",
        ],
    }
    receipt_path = output / "source-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "receipt": str(receipt_path),
                "archive_bytes": receipt["ffmpeg_core"]["archive_bytes"],
                "archive_sha256": receipt["ffmpeg_core"]["archive_sha256"],
                "redistribution_ready": False,
            }
        )
    )


if __name__ == "__main__":
    main()
