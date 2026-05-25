from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Any, Dict


SENSITIVE_SETTING_KEYS = (
    "finnhub_api_keys",
    "active_finnhub_api_key",
    "coingecko_api_key",
)


class SecureSettingsError(RuntimeError):
    pass


def _load_fernet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:
        raise SecureSettingsError(
            "Saving API keys requires the 'cryptography' package. Run: pip install -r requirements-web.txt"
        ) from exc
    return Fernet, InvalidToken


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_crypt(data: bytes, *, protect: bool) -> bytes:
    if not _dpapi_available():
        raise SecureSettingsError("DPAPI encryption is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(data)
    in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecureSettingsError("Windows DPAPI could not process local settings secrets.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _ensure_key(key_path: Path) -> bytes:
    Fernet, _ = _load_fernet()
    if key_path.exists():
        return key_path.read_bytes().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def _encrypt_payload(payload: Dict[str, Any], key_path: Path) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        Fernet, _ = _load_fernet()
        key = _ensure_key(key_path)
        return "fernet:" + Fernet(key).encrypt(encoded).decode("ascii")
    except SecureSettingsError:
        if not _dpapi_available():
            raise
        return "dpapi:" + base64.b64encode(_dpapi_crypt(encoded, protect=True)).decode("ascii")


def _decrypt_payload(token: str, key_path: Path) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        if token.startswith("dpapi:"):
            decoded = _dpapi_crypt(base64.b64decode(token.removeprefix("dpapi:")), protect=False)
        else:
            if token.startswith("fernet:"):
                token = token.removeprefix("fernet:")
            if not key_path.exists():
                return {}
            Fernet, InvalidToken = _load_fernet()
            decoded = Fernet(key_path.read_bytes().strip()).decrypt(token.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise SecureSettingsError(f"Could not decrypt local settings secrets: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def load_secrets(secrets_path: Path, key_path: Path) -> Dict[str, Any]:
    if not secrets_path.exists():
        return {}
    with secrets_path.open("r", encoding="utf-8") as f:
        stored = json.load(f)
    if not isinstance(stored, dict):
        return {}
    token = str(stored.get("encrypted") or "")
    if not token:
        return {}
    return _decrypt_payload(token, key_path)


def save_secrets(secrets_path: Path, key_path: Path, secrets: Dict[str, Any]) -> None:
    clean = {key: secrets.get(key) for key in SENSITIVE_SETTING_KEYS if secrets.get(key)}
    if not clean:
        if secrets_path.exists():
            secrets_path.unlink()
        return
    token = _encrypt_payload(clean, key_path)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    with secrets_path.open("w", encoding="utf-8") as f:
        json.dump({"version": 1, "encrypted": token}, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(secrets_path, 0o600)
    except OSError:
        pass


def strip_sensitive(settings: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(settings)
    for key in SENSITIVE_SETTING_KEYS:
        value = public.pop(key, None)
        if key == "active_finnhub_api_key":
            public["has_active_finnhub_api_key"] = bool(value)
        elif key == "finnhub_api_keys":
            public["finnhub_api_key_count"] = len(value or [])
        elif key == "coingecko_api_key":
            public["has_coingecko_api_key"] = bool(value)
    return public
