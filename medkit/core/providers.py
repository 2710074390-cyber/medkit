"""预置服务商注册表（BYOK 核心）。

设计参照 LobeChat provider schema：每个 provider = {id, name, base_url, default_model, ...}，
用户只在设置页填 Key；支持预置 + 自定义端点。
（2026-08 修订：按用户要求移除「本机 Ollama」选项；register_url 用于设置页跳转。）
"""

from typing import Any, Optional

# 参考单价（元 / 1M token；2026-08 官方源核查，仅用于成本预估，以官网实时价格为准）
# deepseek：官方 api-docs.deepseek.com/zh-cn/quick_start/pricing（高峰时段，空闲减半）
# zhipu：z.ai 官方 USD（$0.6/$2.2 per 1M，GLM-4.6）× ~7.2 汇率估算，以官网为准
# qwen：阿里云百炼华北2北京官方价（qwen-max 页面）；qwen3.x 系列以控制台为准
PRICE_NOTES = {"deepseek": "官网 https://platform.deepseek.com（峰谷时段价，见实时页面）",
               "zhipu": "官网 https://open.bigmodel.cn（美元价×汇率估算）",
               "qwen": "官网 https://bailian.console.aliyun.com"}

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "register_url": "https://platform.deepseek.com",
        "note": "国内直连 · 2026-08 官方：V4 系列（1M 上下文，Tool Calls + Responses API + Anthropic 兼容）",
        "search_support": True,
        "search_tool": "deepseek_tool",
        "price": {"input": 3.0, "output": 9.0, "unit": "元/1M token（高峰时段；空闲减半）"},
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.6",
        "register_url": "https://open.bigmodel.cn",
        "note": "国内直连；2026-08 官方：GLM-4.6/4.7/5.x，专用 Web Search API",
        "search_support": True,
        "search_tool": "zhipu_tool",
        "price": {"input": 4.3, "output": 15.8, "unit": "元/1M token（USD×7.2 估算）"},
    },
    {
        "id": "qwen",
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "register_url": "https://bailian.console.aliyun.com",
        "note": "阿里云百炼，国内直连；联网搜索仅 Qwen3.5~3.8 系列（qwen-max 不支持）",
        "search_support": True,
        "search_tool": "qwen_tool",
        "price": {"input": 2.4, "output": 9.6, "unit": "元/1M token（百炼华北2北京）"},
    },
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容端点）",
        "base_url": "",
        "default_model": "",
        "register_url": "",
        "note": "任意 OpenAI 兼容服务，如 SiliconFlow / OpenRouter / 公司内部网关；搜索能力看端点，建议外部搜索",
        "search_support": False,
        "price": None,
    },
]


def get_provider(pid: str) -> Optional[dict[str, Any]]:
    for p in PROVIDERS:
        if p["id"] == pid:
            return dict(p)
    return None
