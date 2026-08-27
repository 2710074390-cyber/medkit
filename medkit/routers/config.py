"""routers：服务商配置 / 健康检查 / LLM 工具（连接测试·模型列表）。"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import __version__
from ..core import config as cfg
from ..core.config import resolve_key
from ..core.llm import LLMClient
from ..core.providers import PROVIDERS, get_provider

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "stage": "ready"}


@router.get("/api/providers")
def providers() -> dict[str, Any]:
    return {"providers": PROVIDERS}


@router.get("/api/config")
def get_config() -> dict[str, Any]:
    out = cfg.public_view(cfg.load())
    # 配置曾损坏（已备份 + 回退默认）→ 标识给前端提示（用户需要重新配置服务商）
    out["config_corrupt"] = cfg.LAST_LOAD_CORRUPT
    return out


class ConfigBody(BaseModel):
    provider: str
    base_url: str
    api_key: str = ""
    model_gen: str = ""
    model_qc: str = ""
    web_search_enabled: bool = False
    web_search_api_key: str = ""
    web_search_backend: str = "auto"
    mineru_api_key: str = ""
    mineru_auto_ocr: bool = True


@router.put("/api/config")
def put_config(body: ConfigBody) -> dict[str, Any]:
    """保存配置。约定：api_key / mineru_api_key 传空串 = 保留已保存值（防静默清除）。
    S2：新键入 Key 一律 DPAPI 加密落盘；旧明文在保存时自动升级为密文。
    v0.5.1：多服务商 Key 存档——保存时把旧服务商的 Key 归档到 provider_keys；
    切换服务商且未提供新 Key 时自动提升该服务商的存档 Key（仿 Cherry Studio 的服务商独立配置）。
    v0.5.2：同服务商重复保存时，归档同步为本次保存的端点/模型（此前滞留上一次的旧值，
    导致「切走再切回」还原出过时配置）。
    """
    prov = get_provider(body.provider)
    if prov is None:
        raise HTTPException(400, "未知服务商: " + body.provider)
    saved = cfg.load()
    pkeys = dict(saved.get("provider_keys", {}) or {})
    old_provider = saved.get("provider", "")
    same = old_provider == body.provider

    # 本次生效的端点/模型（同服务商归档取同一组值，保证「切走再切回」还原的就是本次配置）
    base_url = body.base_url or prov.get("base_url", saved.get("base_url", ""))
    model_gen = body.model_gen or prov.get("default_model", saved.get("model_gen", ""))
    model_qc = body.model_qc or model_gen or saved.get("model_qc", "")
    if not model_gen:
        raise HTTPException(400, "请填写生成模型（如 deepseek-v4-flash）")

    # 1) 归档旧服务商（含 Key 的最新加密值 + 端点 + 模型）
    old_key = body.api_key if (same and body.api_key) else saved.get("api_key", "")
    if old_provider and old_key:
        pkeys[old_provider] = {
            "api_key": old_key if old_key.startswith("dpapi:") else cfg.encrypt_for_save(old_key),
            "base_url": base_url if same else saved.get("base_url", ""),
            "model_gen": model_gen if same else saved.get("model_gen", ""),
            "model_qc": model_qc if same else saved.get("model_qc", ""),
        }

    # 2) 切换服务商且未提供新 Key → 提升存档（无存档则空，等待用户填写）
    if not same and not body.api_key:
        api_key = (pkeys.get(body.provider) or {}).get("api_key", "")
    else:
        api_key = body.api_key or saved.get("api_key", "")  # 空 = 保留

    mineru_api_key = body.mineru_api_key or (saved.get("mineru", {}) or {}).get("api_key", "")
    ws_api_key = body.web_search_api_key or (saved.get("web_search", {}) or {}).get("api_key", "")

    new_cfg = {
        "provider": body.provider,
        "base_url": base_url,
        "api_key": cfg.encrypt_for_save(api_key),
        "model_gen": model_gen,
        "model_qc": model_qc,
        "web_search": {
            "enabled": body.web_search_enabled,
            "backend": body.web_search_backend or "auto",
            "api_key": cfg.encrypt_for_save(ws_api_key),
        },
        "mineru": {"api_key": cfg.encrypt_for_save(mineru_api_key),
                   "auto_ocr": body.mineru_auto_ocr},
        "projects_dir": saved.get("projects_dir", cfg.DEFAULTS["projects_dir"]),
        "provider_keys": pkeys,
        "features": saved.get("features", {}),   # IMP-02：前端 PUT 不改 features 节，原样保留
    }
    cfg.save(new_cfg)
    v = cfg.public_view(new_cfg)
    v["key_encrypted"] = str(new_cfg.get("api_key", "")).startswith(cfg._DPAPI_PREFIX)
    return v


@router.get("/api/keys")
def list_keys() -> dict[str, Any]:
    """多服务商 Key 存档列表（掩码）。当前生效配置单独给出。"""
    saved = cfg.load()
    pkeys = saved.get("provider_keys", {}) or {}
    active_pid = saved.get("provider", "")
    rows = []
    for p in PROVIDERS:
        pid = p["id"]
        prof = dict(pkeys.get(pid) or {})
        # 当前生效服务商：归档里没有时，用主配置的 Key/端点/模型补齐视图（保持列表完整）
        if pid == active_pid and not prof.get("api_key") and saved.get("api_key"):
            prof = {"api_key": saved["api_key"], "base_url": saved.get("base_url", ""),
                    "model_gen": saved.get("model_gen", ""), "model_qc": saved.get("model_qc", "")}
        has = bool(prof.get("api_key"))
        rows.append({
            "id": pid, "name": p["name"],
            "saved": has,
            "key_masked": cfg.mask_api_key(cfg.resolve_key(prof.get("api_key", ""))) if has else "",
            "base_url": prof.get("base_url") or p["base_url"],
            "model_gen": prof.get("model_gen") or p.get("default_model", ""),
            "model_qc": prof.get("model_qc") or "",
            "active": active_pid == pid,
        })
    return {"keys": rows, "active_provider": active_pid}


@router.delete("/api/keys/{pid}")
def delete_key(pid: str) -> dict[str, Any]:
    """删除某服务商的 Key 存档（不影响当前生效配置）。"""
    if get_provider(pid) is None:
        raise HTTPException(404, "未知服务商: " + pid)
    saved = cfg.load()
    pkeys = dict(saved.get("provider_keys", {}) or {})
    if pid not in pkeys:
        raise HTTPException(404, "该服务商没有已保存的 Key")
    del pkeys[pid]
    saved["provider_keys"] = pkeys
    cfg.save(saved)
    return {"ok": True}


# ---------------------------------------------------------------- LLM 工具
class TestBody(BaseModel):
    base_url: str
    api_key: str = ""
    model: str


@router.post("/api/llm/test")
def llm_test(body: TestBody) -> dict[str, Any]:
    key = body.api_key or resolve_key(cfg.load().get("api_key", ""))
    try:
        client = LLMClient(body.base_url, key, body.model, timeout=30)
        ok, msg = client.test()
        return {"ok": ok, "msg": msg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": str(e)}


class ModelsBody(BaseModel):
    base_url: str
    api_key: str = ""


@router.post("/api/llm/models")
def llm_models(body: ModelsBody) -> dict[str, Any]:
    """POST + JSON body：Key 不进 URL（避免日志记录）。失败时返回真实原因（Key 错/网络/端点不支持）。"""
    key = body.api_key or resolve_key(cfg.load().get("api_key", ""))
    try:
        client = LLMClient(body.base_url, key, "x", timeout=20)
        models = client.list_models(raise_on_error=True)
        return {"ok": True, "models": models}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "msg": str(e)}
