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
    return {"ok": True, "version": __version__, "stage": "v0.5-S2"}


@router.get("/api/providers")
def providers() -> dict[str, Any]:
    return {"providers": PROVIDERS}


@router.get("/api/config")
def get_config() -> dict[str, Any]:
    return cfg.public_view(cfg.load())


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
    """
    prov = get_provider(body.provider)
    if prov is None:
        raise HTTPException(400, "未知服务商: " + body.provider)
    saved = cfg.load()
    base_url = body.base_url or prov.get("base_url", saved.get("base_url", ""))
    model_gen = body.model_gen or prov.get("default_model", saved.get("model_gen", ""))
    model_qc = body.model_qc or model_gen or saved.get("model_qc", "")
    if not model_gen:
        raise HTTPException(400, "请填写生成模型（如 deepseek-v4-flash）")

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
    }
    cfg.save(new_cfg)
    return cfg.public_view(new_cfg)


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
    """POST + JSON body：Key 不进 URL（避免日志记录）。"""
    key = body.api_key or resolve_key(cfg.load().get("api_key", ""))
    try:
        client = LLMClient(body.base_url, key, "x", timeout=20)
        return {"ok": True, "models": client.list_models()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "msg": str(e)}
