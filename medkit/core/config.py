"""全局配置读写：~/.medkit/config.json。

安全：密钥仅存本机文件；UI 显示掩码。
S2（2026-08 审计）：Windows 下 API Key 用 DPAPI（CryptProtectData，ctypes 零依赖）
加密落盘：dpapi: 前缀 + base64 密文，绑定当前用户账户；macOS/Linux 回退明文。
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

from . import errors as _errs
from .fsutil import write_json_atomic
from .providers import get_provider

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.path.expanduser("~")) / ".medkit"


_DIR_WARN: str | None = None


def dir_permission_warning() -> str | None:
    """数据目录权限告警（None = 正常）。由 `harden_config_dir()` 在启动时置位。"""
    return _DIR_WARN


def harden_config_dir(path: Path | None = None) -> str | None:
    """把数据目录收紧到「仅本用户可访问」（S3-13 / R8+W）。返回告警文案（None = 正常）。

    - **POSIX**：目录权限过宽（组/其他可读或可写）→ `chmod 0o700`。这是本应用自有目录，
      收紧不会影响他人，属低风险高收益。
    - **Windows**：**只检查、不修改**——读 `icacls`，若 `BUILTIN\\Users` / `Everyone` 有访问权
      则返回告警文案交上层常驻展示。不程序化改 ACL 的原因：Windows ACL 继承规则复杂，
      误删继承项可能把用户自己锁在目录外（低收益、高风险），故只提示不动手。
    """
    p = path or CONFIG_DIR
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"数据目录不可创建：{e}"
    if os.name != "nt":
        try:
            mode = p.stat().st_mode & 0o777
            if mode & 0o077:
                p.chmod(0o700)
        except OSError as e:  # noqa: BLE001  权限收紧失败不影响使用，但要留痕
            _errs.record("config.harden_config_dir", "chmod 收紧失败", e=e)
        return None
    try:
        import subprocess
        # ⚠️ 不能用 text=True：icacls 输出走系统代码页（中文 Windows 是 GBK），
        # 按 UTF-8 解码会在读取线程里抛 UnicodeDecodeError，stdout 变 None → 检查静默失效。
        out = subprocess.run(["icacls", str(p)], capture_output=True, timeout=8)
        text = (out.stdout or b"").decode("utf-8", errors="ignore").lower()
    except Exception as e:  # noqa: BLE001  无 icacls / 超时 → 无法检查，留痕即可
        _errs.record("config.harden_config_dir", "icacls 检查失败", e=e)
        return None
    open_principals = [k for k in ("everyone", "builtin\\users", "users:") if k in text]
    global _DIR_WARN
    if open_principals:
        _DIR_WARN = ("数据目录权限较宽（" + "、".join(open_principals) + " 可访问）——"
                     "目录内含 API Key 与学习数据，建议手动收紧："
                     f'icacls "{p}" /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"')
        return _DIR_WARN
    _DIR_WARN = None
    return None
CONFIG_FILE = CONFIG_DIR / "config.json"
PROMPTS_DIR_USER = CONFIG_DIR / "prompts"   # 提示词影子副本（打包后安装目录只读，可玩性 3A）
PRESETS_DIR = CONFIG_DIR / "presets"        # 用户预设 JSON（可玩性 2C）

DEFAULTS: dict[str, Any] = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model_gen": "deepseek-v4-flash",   # v0.5：2026-08 换代（旧值 deepseek-chat 在 load 时自动迁移）
    "model_qc": "deepseek-v4-flash",
    "web_search": {"enabled": False, "backend": "auto", "api_key": "",
                 "trusted_only": False, "trusted_domains": []},
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


# S3-10 / S3-12（R8+W）：密钥安全状态——供 UI 常驻角标（原只在加密失败时弹一次性 toast）。
# 取值：None=正常；"plaintext"=加密不可用已回退明文；"decrypt_failed"=密文解不开。
_SECURITY_WARN: str | None = None


def security_warning() -> str | None:
    """当前密钥存储的安全告警（None = 正常）。供配置页/诊断常驻展示。"""
    return _SECURITY_WARN


def _set_security_warn(kind: str) -> None:
    global _SECURITY_WARN
    if _SECURITY_WARN != "decrypt_failed":   # 解密失败优先级更高，不被覆盖
        _SECURITY_WARN = kind


def _protect(data: str) -> str:
    """加密明文 → 'dpapi:<base64>'；非 Windows / 失败时原样返回（回退明文）。"""
    if not data:
        return data
    if not _dpapi_available():
        # S3-10（R8+W）：非 Windows / 缺依赖 → 只能明文存储，必须常驻可见（不静默）
        _set_security_warn("plaintext")
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
    except Exception as e:  # noqa: BLE001  回退明文，不阻塞保存
        # S3-10：回退明文要**常驻可见**（原仅一次性 toast，重启后用户以为已加密）
        _set_security_warn("plaintext")
        _errs.record("config._protect", "DPAPI 加密失败，已回退明文存储", e=e)
    else:
        if data:
            _set_security_warn("plaintext")   # 非 Windows：未加密
    return data


def _unprotect(value: str) -> str:
    """'dpapi:<base64>' → 明文。

    S3-12（R8+W）：**解不开时返回空串**，不再「原样回吐密文」——原行为会把
    `dpapi:xxxx` 当成 API Key 发出去，用户看到的是莫名其妙的 401（故障提示错位）。
    返回空串后调用方走既有的「未配置 Key」提示路径，用户能直接知道要重填 Key。
    """
    if not value or not value.startswith(_DPAPI_PREFIX):
        return value
    if not _dpapi_available():
        _set_security_warn("decrypt_failed")
        _errs.record("config._unprotect", "DPAPI 不可用（非 Windows/缺依赖），密文无法解开")
        return ""
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
    except Exception as e:  # noqa: BLE001
        _errs.record("config._unprotect", "DPAPI 解密抛错，密文无法解开", e=e)
        _set_security_warn("decrypt_failed")
        return ""
    # S3-12（R8+W）：走到这里 = CryptUnprotectData 返回 0（密文损坏/换了机器或用户）
    # 或 base64 非法——**一律返回空串**，绝不把密文原样当 Key 回吐。
    _set_security_warn("decrypt_failed")
    _errs.record("config._unprotect", "DPAPI 解密失败（密文损坏或非本机加密），已按未配置处理")
    return ""


def resolve_key(value: str) -> str:
    """取真实明文密钥（兼容明文迁移期）。

    SEC-REDACT ③（R8+W）：顺手把明文登记到 `errors`，让日志/回显侧的 `redact`
    能精确掩码**本机实际使用的**密钥（正则只认已知前缀，抓不住自定义形态）。
    """
    plain = _unprotect(value)
    if plain:
        from . import errors as _errs
        _errs.register_secret(plain)
    return plain


def encrypt_for_save(value: str) -> str:
    """保存时调用：非空且未加密 → 加密；否则原样（保留 dpapi 或空）。"""
    if value and not value.startswith(_DPAPI_PREFIX):
        return _protect(value)
    return value


# ---------------------------------------------------------------- 读写
LAST_LOAD_CORRUPT = False  # 最近一次 load() 是否因配置损坏回退（前端提示用）


def load() -> dict[str, Any]:
    global LAST_LOAD_CORRUPT
    LAST_LOAD_CORRUPT = False
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
            LAST_LOAD_CORRUPT = True
            try:
                bak = CONFIG_FILE.with_name(
                    f"{CONFIG_FILE.name}.corrupt-{int(time.time())}.bak")
                bak.write_bytes(CONFIG_FILE.read_bytes())
                logger.warning("config.json 解析失败，已备份至 %s，回退默认值：%s", bak, e)
            except Exception as e:  # noqa: BLE001
                _errs.record("config.load", "静默容错（U-15 留痕）", e=e)
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
    # R4-07：统一 write_json_atomic（唯一临时名 + Windows 共享冲突重试），与仓库状态文件口径一致
    write_json_atomic(CONFIG_FILE, cfg)


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) < 12:
        # R4-18：短 Key 只露前 2 后 2（9~11 位旧逻辑只藏 1~3 位，中段几乎全露）
        if len(key) <= 4:
            return key[:2] + "*" * (len(key) - 2)
        return key[:2] + "*" * (len(key) - 4) + key[-2:]
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
