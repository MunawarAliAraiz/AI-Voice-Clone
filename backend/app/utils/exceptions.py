"""
AI Voice Clone Studio — Custom Exception Classes
"""


class VoiceCloneError(Exception):
    """Base exception for all Voice Clone Studio errors."""
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class AudioValidationError(VoiceCloneError):
    """Raised when audio file validation fails."""
    def __init__(self, message: str):
        super().__init__(message, code="AUDIO_VALIDATION_ERROR")


class ProfileNotFoundError(VoiceCloneError):
    """Raised when a voice profile is not found."""
    def __init__(self, profile_id: int):
        super().__init__(f"Voice profile {profile_id} not found", code="PROFILE_NOT_FOUND")


class EngineNotFoundError(VoiceCloneError):
    """Raised when a TTS engine is not available."""
    def __init__(self, engine_name: str):
        super().__init__(f"TTS engine '{engine_name}' not found or not installed", code="ENGINE_NOT_FOUND")


class EngineLoadError(VoiceCloneError):
    """Raised when a TTS engine fails to load."""
    def __init__(self, engine_name: str, detail: str):
        super().__init__(f"Failed to load engine '{engine_name}': {detail}", code="ENGINE_LOAD_ERROR")


class GenerationError(VoiceCloneError):
    """Raised when TTS generation fails."""
    def __init__(self, message: str):
        super().__init__(message, code="GENERATION_ERROR")


class ModelNotDownloadedError(VoiceCloneError):
    """Raised when a required model is not downloaded."""
    def __init__(self, model_name: str):
        super().__init__(f"Model '{model_name}' is not downloaded", code="MODEL_NOT_DOWNLOADED")


class GPUNotAvailableError(VoiceCloneError):
    """Raised when GPU is required but not available."""
    def __init__(self):
        super().__init__("NVIDIA GPU with CUDA is required but not available", code="GPU_NOT_AVAILABLE")


class DiskSpaceError(VoiceCloneError):
    """Raised when disk space is insufficient."""
    def __init__(self, required_mb: int, available_mb: int):
        super().__init__(
            f"Insufficient disk space: {required_mb}MB required, {available_mb}MB available",
            code="DISK_SPACE_ERROR",
        )


class VRAMExhaustedError(VoiceCloneError):
    """Raised when GPU VRAM is insufficient to load a model."""
    def __init__(self, engine_name: str, required_vram_mb: int):
        super().__init__(
            f"Insufficient GPU VRAM to load engine '{engine_name}'. Required ~{required_vram_mb}MB",
            code="VRAM_EXHAUSTED_ERROR",
        )


class EngineRegistrationError(VoiceCloneError):
    """Raised when registering an invalid TTS engine."""
    def __init__(self, engine_name: str, detail: str):
        super().__init__(
            f"Failed to register engine '{engine_name}': {detail}",
            code="ENGINE_REGISTRATION_ERROR",
        )

