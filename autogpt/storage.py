"""Local identity metadata and OS credential-store OAuth tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

if os.name != "nt":
    import keyring


def config_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif os.sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / "AutoGPT"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_metadata() -> dict[str, Any]:
    path = config_dir() / "identity.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_metadata(data: dict[str, Any]) -> None:
    path = config_dir() / "identity.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt bytes for the current Windows user using DPAPI."""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer, wintypes.LPCWSTR, blob_pointer, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_pointer
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer, ctypes.POINTER(wintypes.LPWSTR), blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, blob_pointer
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob), "AutoGPT OAuth tokens", None, None, None, flags, ctypes.byref(output_blob)
        )
    else:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), ctypes.byref(description), None, None, None, flags, ctypes.byref(output_blob)
        )
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _windows_token_path() -> Path:
    return config_dir() / "oauth-tokens.dpapi"


def load_tokens() -> dict[str, Any] | None:
    if os.name == "nt":
        path = _windows_token_path()
        if not path.exists():
            return None
        try:
            raw = _dpapi_transform(path.read_bytes(), protect=False).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    else:
        raw = keyring.get_password("autogpt.codex.oauth", "active")
        if not raw:
            return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_tokens(tokens: dict[str, Any]) -> None:
    raw = json.dumps(tokens).encode("utf-8")
    if os.name == "nt":
        path = _windows_token_path()
        temp = path.with_suffix(".tmp")
        temp.write_bytes(_dpapi_transform(raw, protect=True))
        os.replace(temp, path)
    else:
        keyring.set_password("autogpt.codex.oauth", "active", raw.decode("utf-8"))


def clear_tokens() -> None:
    if os.name == "nt":
        _windows_token_path().unlink(missing_ok=True)
        return
    try:
        keyring.delete_password("autogpt.codex.oauth", "active")
    except keyring.errors.PasswordDeleteError:
        pass

