"""Qualify two CI image artifacts using anonymous GHCR pulls, then write release.json.

No Docker, GitHub credentials, Runpod requests, model downloads or GPU calls.
Requires httpx from the desktop backend environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "munawaraliaraiz/ai-voice-clone"
SOURCE_URL = "https://github.com/MunawarAliAraiz/AI-Voice-Clone"
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
LAYER_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}
MAX_DOCUMENT = 2 * 1024**2
MAX_TOTAL = 16 * 1024**2


class ReleaseError(ValueError):
    """A release lacks required immutable/public evidence."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def document(data: bytes) -> dict:
    # Reject duplicate keys instead of silently allowing evidence replacement.
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError("Invalid JSON evidence") from error
    require(isinstance(value, dict), "Expected a JSON object")
    return value


def descriptor(item: dict, types: set[str], max_size: int) -> None:
    require(isinstance(item, dict), "Invalid OCI descriptor")
    require(item.get("mediaType") in types, "Unsupported OCI media type")
    require(isinstance(item.get("digest"), str) and bool(DIGEST.fullmatch(item["digest"])),
            "Invalid OCI content digest")
    size = item.get("size")
    require(type(size) is int and 0 < size <= max_size, "Invalid OCI descriptor size")
    require(not item.get("urls"), "Foreign registry descriptor URLs are not allowed")


def validate_evidence(path: Path, target: str, commit: str, root: Path) -> dict:
    require(path.stat().st_size <= MAX_DOCUMENT, "CI artifact exceeds size limit")
    record = document(path.read_bytes())
    require(type(record.get("schema_version")) is int and record["schema_version"] == 1,
            "Unsupported CI artifact schema")
    require(record.get("target") == target, f"Wrong image role: expected {target}")
    require(record.get("source_commit") == commit, f"{target}: source commit mismatch")
    prefix = f"ghcr.io/{REPOSITORY}-{target}@"
    image = record.get("image")
    require(isinstance(image, str) and image.startswith(prefix)
            and bool(DIGEST.fullmatch(image[len(prefix):])),
            f"{target}: expected exact repository and immutable image digest")
    locks = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "pod/requirements").glob("*.lock"))
    }
    require(bool(locks) and record.get("dependency_locks") == locks,
            f"{target}: dependency locks do not match the local release source")
    workflow = (root / ".github/workflows/pod-image.yml").read_text(encoding="utf-8")
    for key in ("PYTHON_IMAGE", "UV_IMAGE"):
        match = re.search(rf"^  {key}: (\S+)\s*$", workflow, re.MULTILINE)
        require(match is not None and record.get(key.lower().replace("_image", "_base"))
                == match[1], f"{target}: pinned {key} does not match the workflow")
    require(record.get("debian_snapshot") == "20260901T000000Z",
            f"{target}: unexpected Debian snapshot")
    require(isinstance(record.get("workflow_run"), str) and bool(re.fullmatch(
        re.escape(SOURCE_URL) + r"/actions/runs/[1-9][0-9]*", record["workflow_run"])),
        f"{target}: unexpected workflow evidence URL")
    expected = {
        "runtime_imports": "passed during image build",
        "cpu_service": "passed" if target == "installer" else "not applicable",
        "cuda": "not tested", "real_generation": "not tested",
        "listening": "not tested", "billing": "not tested",
    }
    require(record.get("qualification") == expected,
            f"{target}: CI qualification incomplete or unsupported claims")
    require(isinstance(record.get("platform_manifest_digest"), str)
            and bool(DIGEST.fullmatch(record["platform_manifest_digest"])),
            f"{target}: missing platform manifest digest")
    require(type(record.get("compressed_layer_bytes")) is int
            and 0 < record["compressed_layer_bytes"] <= 500 * 1024**3,
            f"{target}: invalid compressed layer bytes")
    require(type(record.get("layer_count")) is int and 0 < record["layer_count"] <= 256,
            f"{target}: invalid layer count")
    require(record.get("expanded_disk_bytes") is None,
            f"{target}: expanded disk size has no measurement qualification")
    # Only recognized evidence fields enter the installer; unknown fields cannot
    # carry credentials, arbitrary registry hosts or mutable references.
    keys = ("target", "source_commit", "image", "python_base", "uv_base",
            "debian_snapshot", "workflow_run", "dependency_locks", "qualification",
            "platform_manifest_digest", "compressed_layer_bytes", "layer_count")
    return {key: record[key] for key in keys}


class Registry:
    """Bounded anonymous-only registry transport; never consult local credentials."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self.deadline = time.monotonic() + 120
        self.remaining = MAX_TOTAL
        self.calls = 0

    def request(self, url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
        self.calls += 1
        require(self.calls <= 20, "Registry request limit exceeded")
        require(time.monotonic() < self.deadline, "Registry qualification timed out")
        with self.client.stream("GET", url, headers=headers or {}) as response:
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                require(time.monotonic() < self.deadline, "Registry qualification timed out")
                self.remaining -= len(chunk)
                data.extend(chunk)
                require(len(data) <= MAX_DOCUMENT and self.remaining >= 0,
                        "Registry document exceeds download limit")
            return response.status_code, dict(response.headers), bytes(data)

    def fetch(self, repo: str, kind: str, digest: str, headers: dict) -> bytes:
        status, response_headers, data = self.request(
            f"https://ghcr.io/v2/{repo}/{kind}/{digest}", headers)
        if kind == "blobs" and status in (302, 307):
            # GHCR may serve config bytes from its own blob CDN. Never forward
            # the anonymous registry bearer token to the redirect destination.
            location = response_headers.get("location", "")
            destination = urlsplit(location)
            require(destination.scheme == "https"
                    and destination.hostname == "pkg-containers.githubusercontent.com"
                    and destination.port in (None, 443) and not destination.username
                    and not destination.password and not destination.fragment,
                    "Unexpected GHCR blob redirect")
            status, response_headers, data = self.request(location)
        require(status == 200, f"Anonymous GHCR {kind} pull failed (HTTP {status}). "
                "If the worker package is private, change its visibility to Public "
                "manually in GitHub package settings and retry; do not add a GitHub token.")
        require(digest_bytes(data) == digest, "Registry content digest mismatch")
        received_digest = response_headers.get("docker-content-digest")
        require(received_digest is None or received_digest == digest,
                "Registry digest header mismatch")
        return data

    def verify(self, record: dict) -> dict:
        image = record["image"]
        repo, root_digest = image.removeprefix("ghcr.io/").split("@")
        headers = {"Accept": ", ".join(sorted(MANIFEST_TYPES | INDEX_TYPES))}
        url = f"https://ghcr.io/v2/{repo}/manifests/{root_digest}"
        status, response_headers, data = self.request(url, headers)
        if status == 401:
            challenge = response_headers.get("www-authenticate", "")
            expected_scope = f"repository:{repo}:pull"
            # The registry controls the challenge, but cannot choose another
            # host, scope or credential destination for our anonymous request.
            match = re.fullmatch(
                r'Bearer realm="https://ghcr\.io/token",service="ghcr\.io",'
                r'scope="([^"]+)"', challenge, re.IGNORECASE)
            require(match is not None and match[1] == expected_scope,
                    "Unexpected GHCR authentication challenge")
            token_url = httpx.URL("https://ghcr.io/token", params={
                "service": "ghcr.io", "scope": expected_scope})
            token_status, _, token_data = self.request(str(token_url))
            require(token_status == 200,
                    "Anonymous GHCR access denied. Change the worker package visibility "
                    "to Public manually in GitHub package settings and retry; "
                    "do not add a GitHub token.")
            token = document(token_data).get("token")
            require(isinstance(token, str) and 0 < len(token) < 32768
                    and all(32 < ord(char) < 127 for char in token),
                    "Invalid anonymous registry token")
            headers["Authorization"] = "Bearer " + token
            data = self.fetch(repo, "manifests", root_digest, headers)
        else:
            require(status == 200, f"Anonymous GHCR manifest access failed (HTTP {status})")
            require(digest_bytes(data) == root_digest, "Registry content digest mismatch")
            require(response_headers.get("docker-content-digest", root_digest) == root_digest,
                    "Registry digest header mismatch")
        manifest = document(data)
        require(manifest.get("schemaVersion") == 2, "Unsupported registry schema")
        platform_digest = root_digest
        if manifest.get("mediaType") in INDEX_TYPES:
            entries = manifest.get("manifests")
            require(isinstance(entries, list) and 0 < len(entries) <= 16,
                    "Invalid OCI manifest index")
            runtime = []
            attestations = []
            for entry in entries:
                descriptor(entry, MANIFEST_TYPES, MAX_DOCUMENT)
                platform = entry.get("platform", {})
                if platform == {"architecture": "amd64", "os": "linux"}:
                    runtime.append(entry)
                elif platform == {"architecture": "unknown", "os": "unknown"}:
                    attestations.append(entry)
                else:
                    raise ReleaseError("Unexpected image platform; expected linux/amd64")
            require(len(runtime) == 1, "Expected exactly one linux/amd64 runtime manifest")
            platform_digest = runtime[0]["digest"]
            for entry in attestations:
                annotations = entry.get("annotations", {})
                require(isinstance(annotations, dict)
                        and annotations.get("vnd.docker.reference.type") == "attestation-manifest"
                        and annotations.get("vnd.docker.reference.digest") == platform_digest,
                        "Unrecognized OCI provenance descriptor")
            child = self.fetch(repo, "manifests", platform_digest, headers)
            require(len(child) == runtime[0]["size"], "Platform descriptor size mismatch")
            manifest = document(child)
        require(manifest.get("schemaVersion") == 2
                and manifest.get("mediaType") in MANIFEST_TYPES, "Expected image manifest")
        require(platform_digest == record["platform_manifest_digest"],
                "CI platform manifest digest does not match registry")
        layers = manifest.get("layers")
        require(isinstance(layers, list) and 0 < len(layers) <= 256,
                "Invalid registry layer list")
        for layer in layers:
            descriptor(layer, LAYER_TYPES, 200 * 1024**3)
        require(len(layers) == record["layer_count"] and sum(layer["size"] for layer in layers)
                == record["compressed_layer_bytes"], "CI layer sizes/count do not match registry")
        config_ref = manifest.get("config")
        descriptor(config_ref, CONFIG_TYPES, MAX_DOCUMENT)
        config_data = self.fetch(repo, "blobs", config_ref["digest"], headers)
        require(len(config_data) == config_ref["size"], "Image config descriptor size mismatch")
        config = document(config_data)
        require(config.get("os") == "linux" and config.get("architecture") == "amd64",
                "Image config platform is not linux/amd64")
        image_config = config.get("config")
        require(isinstance(image_config, dict), "Image config metadata is missing")
        labels = image_config.get("Labels", {})
        require(isinstance(labels, dict)
                and labels.get("org.opencontainers.image.revision") == record["source_commit"]
                and labels.get("org.opencontainers.image.source") == SOURCE_URL,
                "Registry image source labels do not match CI evidence")
        return {"anonymous_pull": "passed", "content_digests": "verified",
                "platform": "linux/amd64", "config_digest": config_ref["digest"],
                "layers_downloaded": False}


def prepare(installer: Path, gpu: Path, commit: str, output: Path,
            *, root: Path = ROOT, client: httpx.Client | None = None) -> dict:
    require(bool(re.fullmatch(r"[a-f0-9]{40}", commit)), "Expected full source commit SHA")
    records = {target: validate_evidence(path, target, commit, root)
               for target, path in (("installer", installer), ("gpu", gpu))}
    require(records["installer"]["workflow_run"] == records["gpu"]["workflow_run"],
            "Both images must come from the same successful CI workflow run")
    owned = client is None
    if client is None:
        client = httpx.Client(timeout=httpx.Timeout(10, connect=5), trust_env=False,
                              follow_redirects=False, auth=None)
    try:
        registry = Registry(client)
        verified = {target: registry.verify(record) for target, record in records.items()}
    except httpx.HTTPError as error:
        # Do not echo headers, tokens or transport exceptions containing URLs.
        raise ReleaseError("Anonymous GHCR request failed; check connectivity and retry") from error
    finally:
        if owned:
            client.close()
    result = {
        "schema_version": 1, "source_commit": commit,
        "installer": records["installer"]["image"], "gpu": records["gpu"]["image"],
        "verified_at": datetime.now(UTC).isoformat(),
        "evidence": {target: {**record, "registry": verified[target]}
                     for target, record in records.items()},
        "qualification_limits": "Registry/CI verification only; CUDA, real generation, "
                                "listening and Runpod billing have not been tested.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(result, indent=2) + "\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--gpu", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "backend/app/runpod/release.json")
    args = parser.parse_args()
    try:
        result = prepare(args.installer, args.gpu, args.source_commit, args.output)
    except (ReleaseError, OSError) as error:
        parser.exit(1, f"Release not prepared: {error}\n")
    print(f"Qualified public images for {result['source_commit']}; wrote {args.output}.")
    print(result["qualification_limits"])


if __name__ == "__main__":
    main()
