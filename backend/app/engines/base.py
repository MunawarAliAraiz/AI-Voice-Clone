"""
AI Voice Clone Studio — Abstract TTS Engine Interface

All TTS engines (F5-TTS, Fish Speech, XTTS v2, Mock) implement this interface.
This makes engines hot-swappable without changing any other code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EngineInfo:
    """Metadata about a TTS engine."""
    name: str
    display_name: str
    version: str
    description: str
    supported_languages: list[str]
    requires_gpu: bool = False
    model_size_mb: int = 0
    is_loaded: bool = False


@dataclass
class GenerationResult:
    """Result of a TTS generation."""
    output_path: Path
    duration_sec: float
    gen_time_sec: float
    sample_rate: int
    engine: str
    metadata: dict = field(default_factory=dict)


class TTSEngine(ABC):
    """Abstract base class for all TTS engines."""

    @abstractmethod
    def get_info(self) -> EngineInfo:
        """Get engine metadata."""
        ...

    @abstractmethod
    async def load_model(self, device: str = "cpu") -> None:
        """Load the model into memory.

        Args:
            device: 'cpu' or 'cuda:0'
        """
        ...

    @abstractmethod
    async def unload_model(self) -> None:
        """Unload the model from memory."""
        ...

    @abstractmethod
    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "en",
        output_path: Path | None = None,
        reference_text: str | None = None,
    ) -> GenerationResult:
        """Generate speech from text using a reference voice.

        Args:
            text: The text to convert to speech.
            reference_audio: Path to the reference voice audio file.
            language: Language code ('en', 'ur', 'hi').
            output_path: Where to save the output. Auto-generated if None.
            reference_text: Transcript of the reference audio (some engines need this).

        Returns:
            GenerationResult with output path and metadata.
        """
        ...

    @abstractmethod
    def get_supported_languages(self) -> list[str]:
        """Get list of supported language codes."""
        ...

    def is_language_supported(self, language: str) -> bool:
        """Check if a language is supported."""
        return language in self.get_supported_languages()
