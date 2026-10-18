from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

PREFIX = "windows-dpapi-user-v1:"


class Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Local encrypted credentials require Windows. Use OPENAQ_API_KEY instead.")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError(
            "Windows could not protect/unprotect the credential for this user. "
            "Configure OpenAQ again on this computer."
        )
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, wintypes.HLOCAL))


def key_path(root: Path) -> Path:
    return root / "var" / "secrets" / "openaq.key"


def save_openaq_key(root: Path, value: str) -> None:
    value = value.strip()
    if not 16 <= len(value) <= 512 or any(character.isspace() for character in value):
        raise ValueError("The supplied OpenAQ credential has an invalid format.")
    path = key_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = PREFIX + base64.b64encode(_crypt(value.encode("utf-8"), False)).decode("ascii")
    path.write_text(encrypted + "\n", encoding="ascii")


def openaq_key(root: Path) -> str | None:
    environment = os.environ.get("OPENAQ_API_KEY", "").strip()
    if environment:
        return environment
    path = key_path(root)
    if not path.exists():
        return None
    encrypted = path.read_text("ascii").strip()
    if not encrypted.startswith(PREFIX):
        raise RuntimeError("Configure the local OpenAQ credential with Configure-OpenAQ.cmd.")
    try:
        data = base64.b64decode(encrypted.removeprefix(PREFIX), validate=True)
    except ValueError as error:
        raise RuntimeError("The encrypted OpenAQ credential is damaged. Configure it again.") from error
    return _crypt(data, True).decode("utf-8")
