"""Agent 层：MedGen 出题 / MedQC 质检 / MedFix 修复 / MedReview 复习手册。

每个 agent 接受注入的 LLMClient（便于测试离线跑通管线）；
默认经 get_client(role) 从配置构建：生成/修复/复习用 model_gen，质检用 model_qc。
"""

import re
from pathlib import Path

from ..core import config as cfg
from ..core.config import resolve_key
from ..core.llm import LLMClient

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# 占位符单源替换：一次遍历替换全部 {var}，杜绝链式 .replace 的二次注入
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def load_prompt(name: str) -> str:
    """提示词唯一入口（可玩性 3A 影子副本：~/.medkit/prompts/ 优先，零安装写）。"""
    user = cfg.PROMPTS_DIR_USER / name
    if user.exists():
        return user.read_text(encoding="utf-8")
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render_prompt(name: str, **parts: object) -> str:
    """渲染提示词：加载（含影子副本）并单次正则替换全部占位符。

    未提供的变量原样保留（便于调试）；替换值不再被二次扫描，天然防注入。
    """
    return _PLACEHOLDER.sub(lambda m: str(parts.get(m.group(1), m.group(0))),
                            load_prompt(name))


def get_client(role: str = "gen") -> LLMClient:
    c = cfg.load()
    model = c.get("model_qc") if role == "qc" else c.get("model_gen")
    return LLMClient(c.get("base_url", ""), resolve_key(c.get("api_key", "")), model or "")
