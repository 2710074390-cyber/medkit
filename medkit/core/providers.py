"""预置服务商注册表（BYOK 核心）。

设计参照 LobeChat provider schema：每个 provider = {id, name, base_url, default_model, ...}，
用户只在设置页填 Key；支持预置 + 自定义端点。
（2026-08 修订：按用户要求移除「本机 Ollama」选项；register_url 用于设置页跳转。）
"""

from typing import Any, Optional

# 参考单价（元 / 1M token；2026-08 官方源核查，仅用于成本预估，以官网实时价格为准）
# deepseek：官方 api-docs.deepseek.com/zh-cn/quick_start/pricing（高峰时段，空闲减半）
# zhipu：open.bigmodel.cn 官方（GLM-5.3 输入 8 / 输出 28；缓存命中 2）
# qwen：阿里云百炼华北2北京官方价（qwen-max 页面）；qwen3.x 系列以控制台为准
# kimi：platform.kimi.com 定价页（kimi-k2-thinking：缓存命中 1 / 输入 4 / 输出 16）
PRICE_NOTES = {"deepseek": "官网 https://platform.deepseek.com（峰谷时段价，见实时页面）",
               "zhipu": "官网 https://open.bigmodel.cn（美元价×汇率估算）",
               "qwen": "官网 https://bailian.console.aliyun.com",
               "kimi": "官网 https://platform.moonshot.cn（K2 系列价格见文档定价页）"}

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "register_url": "https://platform.deepseek.com",
        "note": "国内直连 · OpenAI/Anthropic 兼容；模型与能力以官方最新为准（点「获取模型列表」自动拉取）",
        "search_support": True,
        "search_tool": "deepseek_tool",
        "price": {"input": 3.0, "output": 9.0, "unit": "元/1M token（高峰时段；空闲减半）"},
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.3",
        "register_url": "https://open.bigmodel.cn",
        "note": "国内直连 · OpenAI 兼容；模型与能力以官方最新为准（支持官方 Web Search 接口）",
        "search_support": True,
        "search_tool": "zhipu_tool",
        "price": {"input": 8.0, "output": 28.0, "cache_hit": 2.0,
                  "unit": "元/1M token（缓存命中 2 元/1M；以官网实时价为准）"},
    },
    {
        "id": "qwen",
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "register_url": "https://bailian.console.aliyun.com",
        "note": "阿里云百炼 · 国内直连；模型与能力以官方最新为准（支持官方 Web Search 接口）",
        "search_support": True,
        "search_tool": "qwen_tool",
        "price": {"input": 2.4, "output": 9.6, "unit": "元/1M token（百炼华北2北京）"},
    },
    {
        "id": "kimi",
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2-thinking",
        "register_url": "https://platform.moonshot.cn",
        "note": "国内直连 · OpenAI 兼容（可配境外端点）；模型与上下文能力以官方最新为准",
        "search_support": False,
        "price": {"input": 4.0, "output": 16.0, "cache_hit": 1.0,
                  "unit": "元/1M token（kimi-k2-thinking；缓存命中 1 元/1M）"},
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
