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


def _search_error_hint(e: Exception) -> str:
    """WP-6：检索测试失败 → 可操作的中文原因（缺 Key / 无权 / 网络 / 参数）。"""
    s = str(e or "").lower()
    if "未配置" in str(e):
        return str(e)
    if "401" in s or "unauthorized" in s or "invalid api key" in s or "authentication" in s:
        return "API Key 无效或无权限（401）——请检查 Key 与账户余额"
    if "403" in s or "forbidden" in s:
        return "无权限（403）——请检查 Key 权限/账户状态"
    if "timeout" in s or "timed out" in s:
        return "连接超时——网络不可达或后端响应慢，请稍后重试"
    if "connection" in s or "getaddrinfo" in s or "connect" in s or "network" in s:
        return "网络不可达——请检查本机网络或后端地址"
    if "400" in s:
        return "请求参数不被后端接受（模型/工具版本可能过时）"
    return f"测试失败：{e}"


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
        if results:
            msg = f"{ws.BACKEND_LABELS.get(backend, backend)} 连通（{len(results)} 条结果）"
        else:
            # 2026-09-01：服务端已响应但未提取到链接 → 明确提示（此前「连通（0 条）」易被当作失败）
            msg = (f"{ws.BACKEND_LABELS.get(backend, backend)} 已连通，但本次未提取到结果"
                   f"——可重试、换关键词，或改选其它后端")
        return {"ok": True, "backend": backend, "count": len(results),
                "samples": results[:3], "msg": msg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": backend, "msg": _search_error_hint(e)}
