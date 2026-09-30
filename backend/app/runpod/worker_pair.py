"""Current user's encrypted connection to one authenticated Runpod Pod worker."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .secrets import _crypt

_POD_ID = re.compile(r"[a-z0-9]{6,32}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{24,512}\Z")


@dataclass(frozen=True, slots=True)
class WorkerPair:
    pod_id: str
    token: str

    def __post_init__(self) -> None:
        if not _POD_ID.fullmatch(self.pod_id):
            raise ValueError("Invalid Runpod Pod ID")
        if not _TOKEN.fullmatch(self.token):
            raise ValueError("Worker token must be 24–512 URL-safe characters")

    @property
    def url(self) -> str:
        return f"https://{self.pod_id}-8000.proxy.runpod.net"


class WorkerPairStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "secrets" / "worker-pair.dpapi"

    def get(self) -> WorkerPair | None:
        if not self.path.is_file():
            return None
        value = json.loads(_crypt(self.path.read_bytes(), protect=False))
        return WorkerPair(pod_id=value["pod_id"], token=value["token"])

    def set(self, pair: WorkerPair) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = json.dumps({"pod_id": pair.pod_id, "token": pair.token}).encode("utf-8")
        temporary.write_bytes(_crypt(payload, protect=True))
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
