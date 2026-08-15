"""
AI Voice Clone Studio — Worker subprocess entry point (the torch side).

Spawned by the scheduler (via `worker_client.WorkerProcess`), one process per
runtime. NEVER imported by the API process — this is where `import torch`
becomes reachable, and the whole design keeps it here (allowlist:
`inference/worker.py` + `inference/runtimes/`).

PROTOCOL
--------
Line-delimited JSON over stdio, matching `protocol.py`:
  * stdin  : one `WireRequest` {id, op, payload} per line.
  * stdout : one `WireResponse` {id, ok, result|error_*} per line, in order.
The very first stdout line is a READY handshake `{"ready": true, "runtime": …}`
that `WorkerProcess.start()` waits for.

STDOUT DISCIPLINE (load-bearing)
--------------------------------
Torch stacks — voxcpm included — print progress and info to *stdout*. On this
stream that is protocol corruption: one stray "Loaded VoxCPM2Model" line and the
scheduler reads it as a malformed response and kills the worker. So we capture
the real stdout for protocol writes and repoint `sys.stdout` at stderr; every
library print then lands in the log where it belongs, never on the wire.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from .protocol import WireOp
from .runtimes import make_backend


def main(runtime: str) -> int:
    # Capture the real stdout for protocol frames, then send everything else
    # (library chatter, tracebacks, tqdm bars) to stderr.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr

    def send(obj: dict[str, Any]) -> None:
        protocol_out.write(json.dumps(obj) + "\n")
        protocol_out.flush()

    try:
        backend = make_backend(runtime)
    except Exception as exc:  # a runtime we cannot construct is fatal
        send({"ready": False, "runtime": runtime, "error": str(exc)})
        return 1

    send({"ready": True, "runtime": runtime})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rid = int(req["id"])
            op = req["op"]
            payload: dict[str, Any] = req.get("payload") or {}
        except Exception:
            # Unparseable line: we cannot correlate it to a request id, so the
            # stream is desynchronized. Report and let the scheduler kill us —
            # never guess an id.
            send({
                "id": -1, "ok": False,
                "error_code": "ProtocolError",
                "error_message": "unparseable request line",
            })
            continue

        try:
            if op == WireOp.PING:
                result = {"runtime": runtime, "loaded_model_id": backend.loaded_model_id}
            elif op == WireOp.LOAD:
                # lora_* keys are only ever present for specs that declared
                # them (see scheduler._load_into); other backends' load()
                # never sees these kwargs, so this is a no-op for them.
                extra = {
                    k: payload[k] for k in
                    ("lora_local_path", "lora_hf_repo", "lora_hf_revision")
                    if k in payload
                }
                load_sec = backend.load(
                    payload["model_id"], payload["hf_repo"], payload["hf_revision"],
                    **extra,
                )
                result = {"load_time_sec": load_sec}
            elif op == WireOp.SYNTH:
                result = backend.synth(
                    text=payload["text"],
                    reference_audio=payload["reference_audio"],
                    output_path=payload["output_path"],
                    params=payload.get("params") or {},
                    sample_rate=int(payload.get("sample_rate", 44100)),
                    reference_text=payload.get("reference_text"),
                )
            elif op == WireOp.CLASSIFY:
                result = backend.classify(
                    language=payload["language"],
                    sentences=payload["sentences"],
                    params=payload.get("params") or {},
                )
            elif op == WireOp.UNLOAD:
                backend.unload()
                result = {}
            elif op == WireOp.SHUTDOWN:
                send({"id": rid, "ok": True, "result": {}})
                return 0
            else:
                raise ValueError(f"unknown op {op!r}")
            send({"id": rid, "ok": True, "result": result})
        except Exception as exc:
            send({
                "id": rid, "ok": False,
                "error_code": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(),
            })

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m app.inference.worker <runtime>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
