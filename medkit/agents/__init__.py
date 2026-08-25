"""Agent 层：MedGen 出题 / MedQC 质检 / MedFix 修复 / MedReview 复习手册。

每个 agent 接受注入的 LLMClient（便于测试离线跑通管线）；
默认经 get_client(role) 从配置构建：生成/修复/复习用 model_gen，质检用 model_qc。
"""

from pathlib import Path

from ..core import config as cfg
from ..core.config import resolve_key
from ..core.llm import LLMClient

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(name: str) -> str:
    """提示词唯一入口（可玩性 3A 影子副本：~/.medkit/prompts/ 优先，零安装写）。"""
    user = cfg.PROMPTS_DIR_USER / name
    if user.exists():
        return user.read_text(encoding="utf-8")
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def get_client(role: str = "gen") -> LLMClient:
    c = cfg.load()
    model = c.get("model_qc") if role == "qc" else c.get("model_gen")
    return LLMClient(c.get("base_url", ""), resolve_key(c.get("api_key", "")), model or "")
