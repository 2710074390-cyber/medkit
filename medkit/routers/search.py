"""routers：网络检索（后端注册表 / 连通性测试）。"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..core import config as cfg
from ..core import websearch as ws
from ..core.config import resolve_key

router = APIRouter()


class SearchTestBody(BaseModel):
    backend: str = "bocha"
    api_key: str = ""
    query: str = "儿科学 儿童生长发育 考试大纲"


@router.get("/api/search/backends")
def search_backends() -> dict[str, Any]:
    """检索后端注册表（含「自带 / 需外部搜索」能力标注，供 UI 动态渲染）。
    2026-08 官方核查：DeepSeek（Responses API web_search）/ 智谱（Web Search API）/ 千问（enable_search）
    三家均自带；自定义 OpenAI 兼容端点多数不带联网工具 → 需外部（博查）或手动。"""
    return {"backends": ws.BACKENDS,
            "note": ("自带网络搜索（2026-08 官方核查）：DeepSeek（Responses API web_search，服务端托管）· "
                     "智谱 GLM（专用 Web Search API）· 通义千问（enable_search，"
                     "qwen3-max 系列已支持，现行代际至 Qwen3.8 Max/Plus/Flash）。"
                     "自定义 OpenAI 兼容端点：多数不含联网工具，"
                     "必须搭配「博查」外部搜索或「手动粘贴」。"),
            "builtin_backend_by_provider": ws.BUILTIN_BACKEND_BY_PROVIDER}


@router.post("/api/search/test")
def search_test(body: SearchTestBody) -> dict[str, Any]:
    """单次检索连通性测试（不记账、不落盘）。manual 不支持在线测试。"""
    backend = ws.resolve_backend(body.backend, cfg.load().get("provider", "deepseek"),
                                 body.api_key or resolve_key(
                                     (cfg.load().get("web_search", {}) or {}).get("api_key", "")))
    if backend == "manual":
        return {"ok": False, "msg": "手动粘贴模式不需要测试；直接在「② 新建课题」粘贴素材即可"}
    try:
        fn = ws.build_backend_fn(backend, body.api_key or resolve_key(
            (cfg.load().get("web_search", {}) or {}).get("api_key", "")),
            cfg.load().get("model_gen", ""))
        results = fn(body.query[:60])
        return {"ok": True, "backend": backend, "count": len(results),
                "samples": results[:3],
                "msg": f"{ws.BACKEND_LABELS.get(backend, backend)} 连通（{len(results)} 条）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": backend, "msg": f"测试失败：{e}"}
