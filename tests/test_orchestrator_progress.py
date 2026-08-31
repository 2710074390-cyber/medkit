"""WP-2：出题进度模型（progress.json 阶段 + 子步骤字段）单元测试。"""

import json


def test_set_progress_writes_sub_fields(tmp_path):
    from medkit.core.orchestrator import _set_progress

    _set_progress(tmp_path, "gate1", 1, 3, "第 1 轮",
                  sub="选项校验", sub_done=1, sub_total=4)
    data = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert data["stage"] == "gate1"
    assert data["done"] == 1 and data["total"] == 3
    assert data["sub"] == "选项校验"
    assert data["sub_done"] == 1 and data["sub_total"] == 4
    assert data["pct"] == 33


def test_set_progress_zero_total_not_done_is_zero(tmp_path):
    from medkit.core.orchestrator import _set_progress

    _set_progress(tmp_path, "gate1", 0, 0, "准备中", sub="检查", sub_done=0, sub_total=0)
    data = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert data["pct"] == 0
    assert data["sub_done"] == 0 and data["sub_total"] == 0


def test_pipeline_stages_order():
    from medkit.core.orchestrator import PIPELINE_STAGES

    assert PIPELINE_STAGES[0] == "websearch"
    assert PIPELINE_STAGES.index("gate1") < PIPELINE_STAGES.index("qc") < PIPELINE_STAGES.index("rendering")
    assert PIPELINE_STAGES[-1] == "done"
    assert len(PIPELINE_STAGES) == len(set(PIPELINE_STAGES)), "阶段不应重复"
