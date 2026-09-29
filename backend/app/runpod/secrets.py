"""Windows user-bound storage for the Runpod management key."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Runpod key storage requires Windows DPAPI")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(_Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    input_blob = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _Blob()
    # UI_FORBIDDEN prevents a background API call from waiting on a desktop prompt.
    if not operation(ctypes.byref(input_blob), None, None, None, None,
                     0x01, ctypes.byref(output_blob)):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI failed")
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


class RunpodKeyStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "secrets" / "runpod-key.dpapi"

    def has_key(self) -> bool:
        return self.path.is_file()

    def get_key(self) -> str | None:
        if not self.has_key():
            return None
        return _crypt(self.path.read_bytes(), protect=False).decode("utf-8")

    def set_key(self, key: str) -> None:
        if not key.strip():
            raise ValueError("Runpod key is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(_crypt(key.strip().encode("utf-8"), protect=True))
        temp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
