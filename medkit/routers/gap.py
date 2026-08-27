"""routers：缺陷驱动智能组卷（WP-03）——一键刷薄弱 → 复用课题通道 → 成本前置。

命名空间 /api/library/gap-paper。零 LLM（配题纯本地）；卷面标注「薄弱点专项」；
判错回流复用既有 sync-paper（零新代码）。
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..core import gap as gap_mod

router = APIRouter()


class GapBody(BaseModel):
    subject: str = ""
    source_pid: str = ""
    question_count: int = 50
    w_freq: float = 15.0      # 真题考频权重 0~30（%，默认 0 → 纯薄弱/掌握度逻辑）


@router.post("/api/library/gap-paper")
def gap_paper(body: GapBody) -> dict[str, Any]:
    count = max(10, min(int(body.question_count or 50), 500))
    w_freq = max(0.0, min(float(body.w_freq or 0), 30.0)) / 100.0
    return gap_mod.create_gap_project(subject=body.subject.strip(),
                                      count=count, w_freq=w_freq,
                                      source_pid=body.source_pid.strip())
