"""Immutable release qualification without registry credentials, Docker or GPU."""

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("prepare_pod_release", ROOT / "scripts/prepare-pod-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
COMMIT = "a" * 40


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode()


def fixtures(tmp_path, *, architecture="amd64", revision=COMMIT, platform="amd64",
             attestations=True, duplicate_runtime=False, bad_attestation=False):
    root = tmp_path / "source"
    locks = root / "pod/requirements"
    locks.mkdir(parents=True)
    (locks / "api.lock").write_text("pinned API dependency\n")
    (locks / "gpu.lock").write_text("pinned GPU dependency\n")
    workflow = root / ".github/workflows/pod-image.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("env:\n  PYTHON_IMAGE: docker.io/library/python@sha256:" + "c" * 64
                        + "\n  UV_IMAGE: ghcr.io/astral-sh/uv@sha256:" + "d" * 64 + "\n")
    artifacts, records, blobs = {}, {}, {}
    for target in ("installer", "gpu"):
        repo = release.REPOSITORY + "-" + target
        config_data = encode({"os": "linux", "architecture": architecture, "config": {"Labels": {
            "org.opencontainers.image.source": release.SOURCE_URL,
            "org.opencontainers.image.revision": revision,
        }}})
        config_digest = release.digest_bytes(config_data)
        manifest_data = encode({"schemaVersion": 2, "mediaType": max(release.MANIFEST_TYPES),
                               "config": {"mediaType": max(release.CONFIG_TYPES),
                                          "digest": config_digest, "size": len(config_data)},
                               "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                                           "digest": "sha256:" + "b" * 64, "size": 1234}]})
        manifest_digest = release.digest_bytes(manifest_data)
        entries = [{"mediaType": max(release.MANIFEST_TYPES),
                    "digest": manifest_digest, "size": len(manifest_data),
                    "platform": {"os": "linux", "architecture": platform}}]
        if duplicate_runtime:
            entries.append(entries[0].copy())
        if attestations:
            entries.append({"mediaType": max(release.MANIFEST_TYPES),
                            "digest": "sha256:" + "e" * 64, "size": 42,
                            "platform": {"os": "unknown", "architecture": "unknown"},
                            "annotations": {
                                "vnd.docker.reference.type": "unexpected" if bad_attestation
                                else "attestation-manifest",
                                "vnd.docker.reference.digest": manifest_digest}})
        root_data = encode({"schemaVersion": 2, "mediaType": max(release.INDEX_TYPES),
                            "manifests": entries}) if attestations or duplicate_runtime else manifest_data
        root_digest = release.digest_bytes(root_data)
        blobs[f"/v2/{repo}/manifests/{root_digest}"] = root_data
        blobs[f"/v2/{repo}/manifests/{manifest_digest}"] = manifest_data
        blobs[f"/v2/{repo}/blobs/{config_digest}"] = config_data
        record = {
            "schema_version": 1, "target": target, "source_commit": COMMIT,
            "image": f"ghcr.io/{repo}@{root_digest}",
            "python_base": "docker.io/library/python@sha256:" + "c" * 64,
            "uv_base": "ghcr.io/astral-sh/uv@sha256:" + "d" * 64,
            "debian_snapshot": "20260901T000000Z",
            "workflow_run": release.SOURCE_URL + "/actions/runs/1234",
            "dependency_locks": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(locks.glob("*.lock"))},
            "qualification": {"runtime_imports": "passed during image build",
                              "cpu_service": "passed" if target == "installer" else "not applicable",
                              "cuda": "not tested", "real_generation": "not tested",
                              "listening": "not tested", "billing": "not tested"},
            "platform_manifest_digest": manifest_digest,
            "compressed_layer_bytes": 1234, "layer_count": 1, "expanded_disk_bytes": None,
        }
        path = tmp_path / f"release-{target}.json"
        path.write_bytes(encode(record))
        artifacts[target], records[target] = path, record
    return root, artifacts, records, blobs


def qualify(tmp_path, handler, fixture):
    root, artifacts, _, _ = fixture
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False,
                      trust_env=False) as client:
        return release.prepare(artifacts["installer"], artifacts["gpu"], COMMIT,
                               tmp_path / "release.json", root=root, client=client)


def handler_for(blobs, *, authenticate=False):
    def handler(request):
        assert request.url.host == "ghcr.io"
        assert not request.headers.get("cookie")
        if request.url.path == "/token":
            assert not request.headers.get("authorization")
            assert request.url.params["service"] == "ghcr.io"
            assert request.url.params["scope"] in {
                f"repository:{release.REPOSITORY}-installer:pull",
                f"repository:{release.REPOSITORY}-gpu:pull",
            }
            return httpx.Response(200, json={"token": "anonymous-only-token"})
        if authenticate and not request.headers.get("authorization"):
            repo = request.url.path.split("/manifests/")[0].removeprefix("/v2/")
            return httpx.Response(401, headers={"www-authenticate":
                'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                f'scope="repository:{repo}:pull"'})
        assert not authenticate or request.headers["authorization"] == "Bearer anonymous-only-token"
        data = blobs[request.url.path]
        return httpx.Response(200, content=data, headers={"docker-content-digest": release.digest_bytes(data)})
    return handler


@pytest.mark.parametrize("attestations", [False, True])
def test_public_manifest_and_provenance_are_verified_atomically(tmp_path, attestations):
    fixture = fixtures(tmp_path, attestations=attestations)
    result = qualify(tmp_path, handler_for(fixture[3], authenticate=True), fixture)
    assert json.loads((tmp_path / "release.json").read_text()) == result
    assert result["installer"].startswith("ghcr.io/" + release.REPOSITORY + "-installer@sha256:")
    assert result["evidence"]["gpu"]["registry"]["platform"] == "linux/amd64"
    assert not result["evidence"]["gpu"]["registry"]["layers_downloaded"]
    assert "anonymous-only-token" not in (tmp_path / "release.json").read_text()
    assert result["evidence"]["gpu"]["qualification"]["real_generation"] == "not tested"


@pytest.mark.parametrize(("field", "value", "message"), [
    ("source_commit", "f" * 40, "source commit mismatch"),
    ("target", "gpu", "Wrong image role"),
    ("image", "ghcr.io/other/project@sha256:" + "b" * 64, "exact repository"),
    ("dependency_locks", {}, "dependency locks"),
    ("python_base", "python:latest", "pinned PYTHON_IMAGE"),
    ("qualification", {"cpu_service": "passed"}, "qualification incomplete"),
    ("workflow_run", release.SOURCE_URL + "/actions/runs/2345", "same successful CI"),
    ("platform_manifest_digest", "sha256:" + "f" * 64, "platform manifest digest"),
    ("compressed_layer_bytes", 1235, "layer sizes/count"),
    ("layer_count", 2, "layer sizes/count"),
    ("expanded_disk_bytes", 1234, "no measurement"),
])
def test_tampered_ci_evidence_does_not_replace_release(tmp_path, field, value, message):
    fixture = fixtures(tmp_path)
    fixture[2]["installer"][field] = value
    fixture[1]["installer"].write_bytes(encode(fixture[2]["installer"]))
    output = tmp_path / "release.json"
    output.write_text("previous qualified release")
    with pytest.raises(release.ReleaseError, match=message):
        qualify(tmp_path, handler_for(fixture[3]), fixture)
    assert output.read_text() == "previous qualified release"


@pytest.mark.parametrize(("options", "message"), [
    ({"architecture": "arm64"}, "config platform"),
    ({"revision": "f" * 40}, "source labels"),
    ({"platform": "arm64"}, "Unexpected image platform"),
    ({"duplicate_runtime": True}, "exactly one"),
    ({"bad_attestation": True}, "provenance descriptor"),
])
def test_registry_platform_source_and_provenance_must_match(tmp_path, options, message):
    fixture = fixtures(tmp_path, **options)
    with pytest.raises(release.ReleaseError, match=message):
        qualify(tmp_path, handler_for(fixture[3]), fixture)
    assert not (tmp_path / "release.json").exists()


def test_registry_content_tamper_rejected(tmp_path):
    fixture = fixtures(tmp_path)
    fixture[3][next(iter(fixture[3]))] += b" "
    with pytest.raises(release.ReleaseError, match="content digest mismatch"):
        qualify(tmp_path, handler_for(fixture[3]), fixture)


def test_private_package_error_never_requests_credentials(tmp_path):
    fixture = fixtures(tmp_path)
    public_handler = handler_for(fixture[3], authenticate=True)
    def handler(request):
        if request.url.path == "/token":
            assert not request.headers.get("authorization")
            return httpx.Response(403)
        return public_handler(request)
    with pytest.raises(release.ReleaseError, match="visibility to Public manually"):
        qualify(tmp_path, handler, fixture)
    assert not (tmp_path / "release.json").exists()


def test_auth_challenge_cannot_redirect_token_or_change_scope(tmp_path):
    fixture = fixtures(tmp_path)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(401, headers={"www-authenticate":
            'Bearer realm="https://attacker.example/token",service="ghcr.io",scope="repository:x:pull"'})
    with pytest.raises(release.ReleaseError, match="authentication challenge"):
        qualify(tmp_path, handler, fixture)
    assert len(calls) == 1


def test_blob_cdn_redirect_gets_no_registry_token(tmp_path):
    fixture = fixtures(tmp_path)
    public_handler = handler_for(fixture[3], authenticate=True)
    def handler(request):
        if request.url.host == "pkg-containers.githubusercontent.com":
            assert not request.headers.get("authorization")
            return httpx.Response(200, content=fixture[3][request.url.params["key"]])
        if "/blobs/" in request.url.path:
            assert request.headers["authorization"] == "Bearer anonymous-only-token"
            url = httpx.URL("https://pkg-containers.githubusercontent.com/ghcr1/blobs/config",
                            params={"key": request.url.path})
            return httpx.Response(307, headers={"location": str(url)})
        return public_handler(request)
    qualify(tmp_path, handler, fixture)


def test_foreign_blob_redirect_and_oversize_documents_are_rejected(tmp_path):
    fixture = fixtures(tmp_path)
    public_handler = handler_for(fixture[3])
    def handler(request):
        if "/blobs/" in request.url.path:
            return httpx.Response(307, headers={"location": "https://attacker.example/config"})
        return public_handler(request)
    with pytest.raises(release.ReleaseError, match="blob redirect"):
        qualify(tmp_path, handler, fixture)
    with pytest.raises(release.ReleaseError, match="download limit"):
        qualify(tmp_path, lambda _: httpx.Response(200, content=b" " * (release.MAX_DOCUMENT + 1)), fixture)


def test_transport_failure_is_clear_and_redacted(tmp_path):
    fixture = fixtures(tmp_path)
    def handler(request):
        raise httpx.ReadTimeout("contains-sensitive-token", request=request)
    with pytest.raises(release.ReleaseError, match="check connectivity") as error:
        qualify(tmp_path, handler, fixture)
    assert "sensitive" not in str(error.value)
    assert not (tmp_path / "release.json").exists()
