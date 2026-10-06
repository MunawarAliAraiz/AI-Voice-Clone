"""Strict, bounded serverless envelope; no arbitrary worker route or credential."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 1
MAX_AUDIO_BYTES = 6_000_000
MAX_ENVELOPE_BYTES = 9_000_000
RESPONSE_HEADERS = frozenset(
    {
        "x-model-id",
        "x-generation-time-sec",
        "x-audio-duration-sec",
        "x-load-time-sec",
        "x-request-id",
        "content-type",
    }
)
PROGRESS_STAGES = frozenset(
    {
        "queued",
        "loading_model",
        "generating",
        "analyzing",
        "converting",
        "checking_files",
        "downloading",
        "verifying",
    }
)


def decode_audio(value: str) -> bytes:
    if len(value) > 4 * ((MAX_AUDIO_BYTES + 2) // 3):
        raise ValueError("Audio exceeds serverless transport limit")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid audio encoding") from exc
    if not decoded or len(decoded) > MAX_AUDIO_BYTES:
        raise ValueError("Audio exceeds serverless transport limit")
    return decoded


def _strict_version(value: Any) -> Any:
    if isinstance(value, dict) and type(value.get("protocol_version", 1)) is not int:
        raise ValueError("Protocol version must be an integer")
    return value


class FlexInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    protocol_version: Literal[1] = 1
    operation_id: UUID
    operation: Literal[
        "synthesize", "analyze", "transliterate", "models", "warm", "setup", "setup_status"
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    reference_audio_b64: str | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> Any:
        return _strict_version(value)

    @model_validator(mode="after")
    def validate_transport(self) -> FlexInput:
        if self.operation_id.version not in range(1, 9):
            raise ValueError("An operation UUID is required")
        if (self.operation == "synthesize") != (self.reference_audio_b64 is not None):
            raise ValueError("Reference audio is required only for synthesis")
        if self.reference_audio_b64 is not None:
            decode_audio(self.reference_audio_b64)
        try:
            encoded = json.dumps(
                {
                    "protocol_version": self.protocol_version,
                    "operation_id": str(self.operation_id),
                    "operation": self.operation,
                    "payload": self.payload,
                    "reference_audio_b64": self.reference_audio_b64,
                },
                allow_nan=False,
            ).encode()
        except (ValueError, TypeError) as exc:
            raise ValueError("Input must contain finite JSON values") from exc
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ValueError("Serverless input exceeds transport limit")
        return self


class FlexOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    protocol_version: Literal[1] = 1
    operation_id: UUID
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    audio_b64: str | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> Any:
        return _strict_version(value)

    @model_validator(mode="after")
    def validate_transport(self) -> FlexOutput:
        if any(key not in RESPONSE_HEADERS for key in self.headers):
            raise ValueError("Unexpected worker response header")
        if self.audio_b64 is not None:
            if self.body is not None or not 200 <= self.status_code < 300:
                raise ValueError("Invalid audio result shape")
            decode_audio(self.audio_b64)
        try:
            encoded = json.dumps(
                {
                    "protocol_version": self.protocol_version,
                    "operation_id": str(self.operation_id),
                    "status_code": self.status_code,
                    "headers": self.headers,
                    "body": self.body,
                    "audio_b64": self.audio_b64,
                },
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("Result must contain finite JSON values") from exc
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ValueError("Serverless result exceeds transport limit")
        return self
