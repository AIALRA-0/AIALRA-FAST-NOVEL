"""使用 Windows 数据保护接口保存模型密钥，密文只对当前账户可解。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class DataBlob(ctypes.Structure):
    """Windows 数据保护接口使用的字节缓冲区。"""

    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def secret_store_path() -> Path:
    """返回当前 Windows 账户的应用凭据文件。"""

    configured = os.getenv("NOVEL_SECRET_PATH")
    if configured:
        return Path(configured)
    local_data = Path(os.getenv("LOCALAPPDATA", Path.home())) / "NovelAtlas"
    return local_data / "credentials.json"


def _blob(data: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
    """让 Python 缓冲区在 Windows 调用期间保持存活。"""

    buffer = ctypes.create_string_buffer(data)
    blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_secret(value: str) -> bytes:
    """用当前 Windows 用户身份加密字符串。"""

    if sys.platform != "win32":
        raise RuntimeError("安全保存密钥目前只支持 Windows。")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(value.encode("utf-8"))
    output_blob = DataBlob()
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Novel Atlas",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not success:
        raise RuntimeError("Windows 无法加密模型密钥。")
    try:
        del input_buffer
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def unprotect_secret(value: bytes) -> str:
    """用当前 Windows 用户身份解密字符串。"""

    if sys.platform != "win32":
        raise RuntimeError("安全读取密钥目前只支持 Windows。")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(value)
    output_blob = DataBlob()
    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )
    if not success:
        raise RuntimeError("Windows 无法解密模型密钥。")
    try:
        del input_buffer
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def load_stored_secrets() -> dict[str, str]:
    """读取可解密的供应商密钥；损坏项会被忽略。"""

    path = secret_store_path()
    if not path.exists() or sys.platform != "win32":
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, str] = {}
    for provider, encoded in payload.items():
        if provider not in {"deepseek", "moonshot"} or not isinstance(encoded, str):
            continue
        try:
            result[provider] = unprotect_secret(base64.b64decode(encoded))
        except Exception:
            continue
    return result


def save_provider_secret(provider: str, value: str) -> None:
    """原子替换一个供应商密钥，文件中只写入加密字节。"""

    if provider not in {"deepseek", "moonshot"}:
        raise ValueError("未知模型供应商。")
    path = secret_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload[provider] = base64.b64encode(protect_secret(value)).decode("ascii")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def delete_provider_secret(provider: str) -> None:
    """删除一个供应商的加密密钥，其他密钥保持不变。"""

    path = secret_store_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload.pop(provider, None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
