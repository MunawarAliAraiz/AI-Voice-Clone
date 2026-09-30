"""Probe a deployed worker; synthesize only with an explicit model/reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import soundfile as sf


def worker_url(value: str) -> str:
    parts = urlsplit(value)
    local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parts.scheme != "https" and not (parts.scheme == "http" and local)) or (
        not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
    ):
        raise argparse.ArgumentTypeError("Use an HTTPS worker URL, or loopback HTTP for Docker")
    return value.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, type=worker_url)
    parser.add_argument("--model")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--text")
    parser.add_argument("--reference-text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    generation = (args.model, args.reference, args.text, args.output)
    if any(generation) and not all(generation):
        parser.error("Generation needs --model, --reference, --text, and --output together")
    token = os.environ.get("POD_WORKER_TOKEN", "")
    if not token:
        parser.error("Set POD_WORKER_TOKEN in the environment")
    with httpx.Client(base_url=args.url, headers={"Authorization": f"Bearer {token}"},
                      timeout=600, follow_redirects=False, trust_env=False) as client:
        health = client.get("/v1/health")
        health.raise_for_status()
        if health.json().get("protocol_version") != 1:
            raise SystemExit("Incompatible worker protocol")
        models = client.get("/v1/models")
        models.raise_for_status()
        print(json.dumps({"health": health.json(), "models": models.json()}, indent=2))
        if not all(generation):
            return
        if args.output.exists():
            raise SystemExit("Choose an output path that does not already exist")
        payload = {"model_id": args.model, "text": args.text,
                   "reference_text": args.reference_text, "params": {}}
        with args.reference.open("rb") as reference:
            response = client.post("/v1/synthesize", data={"request": json.dumps(payload)},
                                   files={"reference_audio": ("reference.wav", reference,
                                                              "audio/wav")})
        response.raise_for_status()
        if response.headers.get("X-Model-Id") != args.model:
            raise SystemExit("Worker changed the resolved model")
        audio = response.content
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids overwriting another result after an inference wait.
        with args.output.open("xb") as output:
            output.write(audio)
        info = sf.info(args.output)
        if info.frames <= 0:
            raise SystemExit("Worker returned empty audio")
        receipt = {"model_id": args.model, "sha256": hashlib.sha256(audio).hexdigest(),
                   "duration_sec": info.duration, "sample_rate": info.samplerate,
                   "generation_time_sec": response.headers.get("X-Generation-Time-Sec"),
                   "load_time_sec": response.headers.get("X-Load-Time-Sec"),
                   "quality": "awaiting listening review", "actual_cost": "not measured"}
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
