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
            "note": ("检索后端概览（能力随时随服务商官方接口演进，以「可选后端」列表标注为准，"
                     "不锁定代际）：DeepSeek / 智谱 GLM / 通义千问 官方自带联网搜索（与你选择的"
                     "服务商同一 Key 即可用）；「博查」需单独 Key；自定义 OpenAI 兼容端点多数"
                     "不含联网工具 → 用博查或手动粘贴。"),
            "builtin_backend_by_provider": ws.BUILTIN_BACKEND_BY_PROVIDER}


@router.post("/api/search/test")
def search_test(body: SearchTestBody) -> dict[str, Any]:
    """单次检索连通性测试（不记账、不落盘）。manual 不支持在线测试。

    Key 解析（修复：内置后端复用服务商 LLM Key）：
    - 内置（deepseek_tool/zhipu_tool/qwen_tool）：与 LLM 同一账户 → 用服务商已存 Key；
    - 博查：用 web_search.api_key（界面「博查 API Key」）；未配置 → 明确报缺 Key。
    """
    web_cfg = cfg.load().get("web_search", {}) or {}
    provider_key = resolve_key(cfg.load().get("api_key", ""))          # 服务商 LLM Key
    bocha_key = (body.api_key or "").strip() or resolve_key(web_cfg.get("api_key", ""))
    backend = ws.resolve_backend(body.backend, cfg.load().get("provider", "deepseek"),
                                 bocha_key)
    if backend == "manual":
        return {"ok": False, "msg": "手动粘贴模式不需要测试；直接在「② 新建课题」粘贴素材即可"}
    if backend == "bocha" and not bocha_key:
        return {"ok": False, "backend": backend,
                "msg": "未配置博查 API Key——请在上方「博查 API Key」填入（或改选「自动匹配/内置后端」）"}
    key = provider_key if backend != "bocha" else bocha_key
    try:
        fn = ws.build_backend_fn(backend, key, cfg.load().get("model_gen", ""))
        results = fn(body.query[:60])
        return {"ok": True, "backend": backend, "count": len(results),
                "samples": results[:3],
                "msg": f"{ws.BACKEND_LABELS.get(backend, backend)} 连通（{len(results)} 条）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": backend, "msg": f"测试失败：{e}"}
