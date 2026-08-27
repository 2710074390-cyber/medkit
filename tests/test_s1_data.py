"""S1-C 过时数据刷新回归测试（对照 2026-08 官方源）：

智谱默认模型换代（glm-4.6 → glm-5.3）+ 新价目（8/28，缓存命中 2）/
删除「qwen-max 不支持联网搜索」过时说法 / 智谱检索默认模型同步 /
MinerU v4 页限 200 → 600 / 默认模型 deepseek-chat → deepseek-v4-flash + 旧值自动迁移 /
DeepSeek 检索模型非 v4 回退防御 / 检索后端注册表 note。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import websearch as ws
from medkit.core.mineru import V4_PAGE_LIMIT  # noqa: E402
from medkit.core.providers import get_provider  # noqa: E402


def test_zhipu_default_model_and_price_refreshed():
    z = get_provider("zhipu")
    assert z["default_model"] == "glm-5.3", "智谱默认模型应保持最新代际"
    assert z["price"]["input"] == 8.0 and z["price"]["output"] == 28.0, "2026-08 官方价 8/28"
    assert z["price"].get("cache_hit") == 2.0, "缓存命中 2 元/1M"
    # 用户政策（AI 迭代快）：注记不固化代际/版本，改为「以官方最新为准」稳定引导
    assert "以官方最新为准" in z["note"], "注记应引导以官方最新为准（不写死代际）"
    assert "GLM-5.3" not in z["note"] and "4.7" not in z["note"], "注记不应固化版本断言"


def test_qwen_note_no_stale_claim():
    z = get_provider("qwen")
    assert "qwen-max" not in (z["note"] or "") and "Qwen3.8" not in (z["note"] or ""), \
        "时效断言应删除（qwen3-max 系列/Qwen3.8 均不写死）"
    assert "以官方最新为准" in (z["note"] or ""), "注记应引导以官方最新为准"
    bs = next(b for b in ws.BACKENDS if b["id"] == "qwen_tool")
    assert "qwen-max" not in bs["note"]


def test_zhipu_search_default_model_synced():
    import inspect

    assert inspect.signature(ws.search_zhipu).parameters["model"].default == "glm-5.3", \
        "search_zhipu 默认模型应同步 glm-5.3"


def test_deepseek_search_model_fallback():
    # 非 v4（旧默认 deepseek-chat）→ 回退 v4-flash（修「默认配置下联网检索必 400」）
    assert ws._normalize_deepseek_model("deepseek-chat") == "deepseek-v4-flash"
    assert ws._normalize_deepseek_model("") == "deepseek-v4-flash"
    assert ws._normalize_deepseek_model(None) == "deepseek-v4-flash"
    assert ws._normalize_deepseek_model("deepseek-v4-pro") == "deepseek-v4-pro"
    assert ws._normalize_deepseek_model("deepseek-v4-flash-vision-exp") == "deepseek-v4-flash-vision-exp"


def test_mineru_v4_page_limit_refreshed():
    assert V4_PAGE_LIMIT == 600, "MinerU 官方现行单文件 ≤600 页（旧值 200 过时）"


def test_config_default_model_refreshed():
    assert cfgmod.DEFAULTS["model_gen"] == "deepseek-v4-flash", "默认模型应换代 v4-flash"
    assert cfgmod.DEFAULTS["model_qc"] == "deepseek-v4-flash"


def test_config_legacy_model_migration(tmp_path, monkeypatch):
    """旧配置 deepseek-chat → load() 自动迁移为 v4-flash（且不污染 DEFAULTS）。"""
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"provider": "deepseek", "model_gen": "deepseek-chat",
                                "model_qc": "deepseek-chat"}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", conf)
    cfg = cfgmod.load()
    assert cfg["model_gen"] == "deepseek-v4-flash", "旧值应自动迁移"
    assert cfg["model_qc"] == "deepseek-v4-flash"
    assert cfgmod.DEFAULTS["model_gen"] == "deepseek-v4-flash"
    # 新值（v4 系列）不应被改动
    conf.write_text(json.dumps({"provider": "deepseek", "model_gen": "deepseek-v4-pro",
                                "model_qc": "deepseek-v4-flash"}), encoding="utf-8")
    cfg2 = cfgmod.load()
    assert cfg2["model_gen"] == "deepseek-v4-pro"
