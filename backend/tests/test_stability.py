"""
Tests for ElevenLabs-style stability control parameter.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.tts import TTSGenerateRequest


def test_stability_default_is_70() -> None:
    """Verify backward compatibility: missing stability parameter defaults to 70."""
    req = TTSGenerateRequest(text="Hello world", profile_id=1, language="en")
    assert req.stability == 70


def test_stability_valid_range() -> None:
    """Verify stability accepts values from 0 to 100 inclusive."""
    req0 = TTSGenerateRequest(text="Hello", profile_id=1, language="en", stability=0)
    assert req0.stability == 0

    req50 = TTSGenerateRequest(text="Hello", profile_id=1, language="en", stability=50)
    assert req50.stability == 50

    req100 = TTSGenerateRequest(text="Hello", profile_id=1, language="en", stability=100)
    assert req100.stability == 100


def test_stability_out_of_bounds() -> None:
    """Verify pydantic raises ValidationError for values <0 or >100."""
    with pytest.raises(ValidationError):
        TTSGenerateRequest(text="Hello", profile_id=1, language="en", stability=-1)

    with pytest.raises(ValidationError):
        TTSGenerateRequest(text="Hello", profile_id=1, language="en", stability=101)
