"""全局配置读写：~/.medkit/config.json。

安全：密钥仅存本机文件；UI 显示掩码。
S2（2026-08 审计）：Windows 下 API Key 用 DPAPI（CryptProtectData，ctypes 零依赖）
加密落盘：dbapi: 前缀 + base64 密文，绑定当前用户账户；macOS/Linux 回退明文。
迁移：读到旧明文 → 下次保存时自动升级为密文。
"""

import base64
import copy
import ctypes
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from .providers import get_provider

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.path.expanduser("~")) / ".medkit"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROMPTS_DIR_USER = CONFIG_DIR / "prompts"   # 提示词影子副本（打包后安装目录只读，可玩性 3A）
PRESETS_DIR = CONFIG_DIR / "presets"        # 用户预设 JSON（可玩性 2C）

DEFAULTS: dict[str, Any] = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model_gen": "deepseek-v4-flash",   # v0.5：2026-08 换代（旧值 deepseek-chat 在 load 时自动迁移）
    "model_qc": "deepseek-v4-flash",
    "web_search": {"enabled": False, "backend": "auto", "api_key": ""},
    "mineru": {"api_key": "", "auto_ocr": True},
    "projects_dir": str(CONFIG_DIR / "projects"),
    "provider_keys": {},   # v0.5.1：多服务商 Key 存档 {pid: {api_key, base_url, model_gen, model_qc}}
    "features": {},        # v0.8 (IMP-02)：WP 级 feature flag 节 {name: bool}，缺省 True（state.flag 读取）
}

# v0.5：旧默认模型（deepseek 老一代 chat 模型）→ 现行 v4-flash 自动迁移
_LEGACY_MODEL = "deepseek-chat"
_NEW_DEFAULT_MODEL = "deepseek-v4-flash"

_DPAPI_PREFIX = "dpapi:"


# ---------------------------------------------------------------- DPAPI（Windows）
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _protect(data: str) -> str:
    """加密明文 → 'dpapi:<base64>'；非 Windows / 失败时原样返回（回退明文）。"""
    if not data or not _dpapi_available():
        return data
    try:
        raw = data.encode("utf-8")
        in_blob = _DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)),
                                                   ctypes.POINTER(ctypes.c_ubyte)))
        out_blob = _DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(in_blob), None, None, None, None, 1,  # CRYPTPROTECT_UI_FORBIDDEN
                ctypes.byref(out_blob)):
            try:
                blob = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                return _DPAPI_PREFIX + base64.b64encode(blob).decode("ascii")
            finally:
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    except Exception:  # noqa: BLE001  回退明文，不阻塞保存
        pass
    return data


def _unprotect(value: str) -> str:
    """'dpapi:<base64>' → 明文；非法/失败/非 Windows → 原样返回。"""
    if not value or not value.startswith(_DPAPI_PREFIX):
        return value
    if not _dpapi_available():
        return value
    try:
        blob = base64.b64decode(value[len(_DPAPI_PREFIX):])
        in_blob = _DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                                    ctypes.POINTER(ctypes.c_ubyte)))
        out_blob = _DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None, 1, ctypes.byref(out_blob)):
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
            finally:
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    except Exception:  # noqa: BLE001
        pass
    return value


def resolve_key(value: str) -> str:
    """取真实明文密钥（兼容明文迁移期）。"""
    return _unprotect(value)


def encrypt_for_save(value: str) -> str:
    """保存时调用：非空且未加密 → 加密；否则原样（保留 dpapi 或空）。"""
    if value and not value.startswith(_DPAPI_PREFIX):
        return _protect(value)
    return value


# ---------------------------------------------------------------- 读写
def load() -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)  # v0.5：深拷贝（旧实现 dict() 浅拷贝，嵌套 dict 被 update 污染模块级默认值）
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:  # noqa: BLE001  配置损坏：先备份原始文件（抢救 Key），再回退默认值
            try:
                bak = CONFIG_FILE.with_name(
                    f"{CONFIG_FILE.name}.corrupt-{int(time.time())}.bak")
                bak.write_bytes(CONFIG_FILE.read_bytes())
                logger.warning("config.json 解析失败，已备份至 %s，回退默认值：%s", bak, e)
            except Exception:  # noqa: BLE001
                pass
    # provider 无效（如旧版本 ollama 配置）→ 回退 DeepSeek；base_url/模型随 provider 同步
    prov = get_provider(cfg.get("provider", ""))
    if prov is None:
        cfg["provider"] = "deepseek"
        prov = get_provider("deepseek")
    if prov and prov["id"] != "custom":
        if not cfg.get("base_url"):
            cfg["base_url"] = prov["base_url"]
        if not cfg.get("model_gen"):
            cfg["model_gen"] = prov["default_model"]
    # v0.5：默认模型换代自动迁移（旧值 deepseek-chat 无法用联网检索等新能力 → 提示并改写）
    for k in ("model_gen", "model_qc"):
        if cfg.get(k) == _LEGACY_MODEL:
            logger.warning("配置模型「%s」为旧一代 deepseek-chat，已自动迁移为 %s", k, _NEW_DEFAULT_MODEL)
            cfg[k] = _NEW_DEFAULT_MODEL
    return cfg


def save(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)  # 原子写


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "*" * (len(key) - 2)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def public_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """给前端的安全视图：Key 掩码（掩码基于解密后的真实值）。"""
    out = dict(cfg)
    real_api = resolve_key(cfg.get("api_key", ""))
    out["api_key_masked"] = mask_api_key(real_api)
    out["api_key"] = ""
    if isinstance(out.get("web_search"), dict):
        ws = dict(out["web_search"])
        ws["api_key_masked"] = mask_api_key(resolve_key(ws.get("api_key", "")))
        ws["api_key"] = ""
        out["web_search"] = ws
    if isinstance(out.get("mineru"), dict):
        mu = dict(out["mineru"])
        mu["api_key_masked"] = mask_api_key(resolve_key(mu.get("api_key", "")))
        mu["api_key"] = ""
        out["mineru"] = mu
    if isinstance(out.get("provider_keys"), dict):
        pk = {}
        for pid, prof in out["provider_keys"].items():
            if isinstance(prof, dict):
                p = dict(prof)
                p["api_key_masked"] = mask_api_key(resolve_key(p.get("api_key", "")))
                p["api_key"] = ""
                pk[pid] = p
        out["provider_keys"] = pk
    return out
