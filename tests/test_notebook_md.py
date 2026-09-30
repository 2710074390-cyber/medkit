"""EP-01 输出层：Markdown 复盘笔记渲染（原方案 N12）测试。

覆盖三层：
1. **渲染层**（纯函数、零 IO）：固定 stats → 断言**具体数字/表格行/空态**（语义断言，
   不是"有输出就算过"）；
2. **红线**：产物里**不得出现题干与答案**——复盘是元认知文档，题目本体属于错题本
   （这也天然回避了"产物夹带答案"）；
3. **结构守卫**：渲染模块**不得**依赖 LLM/agents（"零 LLM"是可证伪的声明，不是口号）。

反向验证见 `.workbuddy-ai/tmp/diag_notebook.py`（删小节 / 塞题干 ⇒ 必红）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402
from medkit.render import notebook_md  # noqa: E402

NOTEBOOK_SRC = ROOT / "medkit" / "render" / "notebook_md.py"

# 与 `errorpipe.analyze()` 返回体逐字段对齐的确定性样本
STATS: dict[str, Any] = {
    "calibration": {
        "buckets": [
            {"confidence": 5, "n": 3, "correct": 1, "accuracy": 0.3333,
             "expected": 1.0, "gap": -0.6667, "low_sample": False},
            {"confidence": 4, "n": 3, "correct": 2, "accuracy": 0.6667,
             "expected": 0.8, "gap": -0.1333, "low_sample": False},
            {"confidence": 1, "n": 0, "correct": 0, "accuracy": 0.0,
             "expected": 0.2, "gap": 0.0, "low_sample": True},
        ],
        "brier": 0.3733, "jol_bias": 0.0167, "rated": 6, "unrated": 2,
        "unknown_result": 1, "alert": True,
        "alert_msg": "自评 5 的题正确率仅 33%（<90%）：在凭直觉做题且不自知。",
    },
    "heatmap": {
        "tags": ["知识盲区", "记忆偏差", "机制混淆", "概念偷换", "审题失误", "推理跳步"],
        "subjects": ["生理学"],
        "rows": [{"subject": "生理学",
                  "counts": {"知识盲区": 2, "记忆偏差": 2, "机制混淆": 3,
                             "概念偷换": 1, "审题失误": 2, "推理跳步": 1},
                  "total": 11, "top_tag": "机制混淆", "top_ratio": 0.2727,
                  "advice": "因果链错了 → 重讲机制并做「机制→病理→临床」串联"}],
        "untagged": 3, "field": "auto",
    },
    "migration": {
        "chains": [{"kp_id": "kp1_ed0410bae453564e",
                    "tags": {"早鸟轮": "机制混淆", "跟课轮": "机制混淆", "强化轮": "机制混淆"},
                    "verdict": "未变", "msg": "跨 3 轮仍是「机制混淆」——修补动作无效，换方法"}],
        "stuck": [{"kp_id": "kp1_ed0410bae453564e",
                   "tags": {"早鸟轮": "机制混淆", "跟课轮": "机制混淆", "强化轮": "机制混淆"},
                   "verdict": "未变", "msg": "跨 3 轮仍是「机制混淆」——修补动作无效，换方法"}],
        "stuck_count": 1, "rounds": ["早鸟轮", "跟课轮", "强化轮"],
    },
    "agreement": {"compared": 9, "agreed": 7, "rate": 0.7778, "mismatches": []},
    "subtract": {
        "rows": [{"key": "生理学|消化", "subject": "生理学", "chapter": "消化", "n": 2,
                  "rated": 2, "accuracy": 1.0, "freq": 1.0, "score": 2.0,
                  "skip_this_week": True}],
        "skip": [{"key": "生理学|消化", "subject": "生理学", "chapter": "消化", "n": 2,
                  "rated": 2, "accuracy": 1.0, "freq": 1.0, "score": 2.0,
                  "skip_this_week": True}],
        "freq_missing": True, "cut": 1,
    },
    "counts": {"cards": 14, "events": 12, "gated": 14, "kp_ids": 11},
}

SECTIONS = ("一、校准", "二、错因分布", "三、跨轮次迁移", "四、本周减法", "五、本周优先")


def _md(stats: dict[str, Any] | None = None, **kw) -> str:
    return notebook_md.render_notebook(stats if stats is not None else STATS,
                                       generated_at="2026-09-30 22:00", **kw)


# ==================================================================== 1. 渲染
def test_all_sections_present_and_ordered():
    """五个小节都必须在场且**按序**（复盘笔记的阅读顺序是设计的一部分）。"""
    md = _md()
    pos = [md.index(f"## {s}") for s in SECTIONS]
    assert pos == sorted(pos), "小节顺序被打乱"
    assert md.startswith(f"# {notebook_md.NOTEBOOK_TITLE}")


def test_header_reports_counts_and_generation_time():
    md = _md()
    head = md.split("\n")[2]
    assert "错题 **14** 道" in head
    assert "知识点 **11** 个" in head
    assert "追踪流水 **12** 条" in head
    assert "生成于 2026-09-30 22:00" in head


def test_calibration_numbers_are_the_input_numbers():
    """语义断言：表里的数字必须**等于输入**，不是"看起来像百分比"。"""
    md = _md()
    assert "有效样本 **6** 道" in md
    assert "**0.3733**" in md, "Brier 分数应原样展示（4 位小数）"
    assert "**+2%**" in md, "jol_bias 0.0167 → +2%（且带正号）"
    # 自评 5 那一行：3 题 / 对 1 / 实际 33% / 预期 100% / 偏差 -67%
    assert "| 5 | 3 | 1 | 33% | 100% | -67% |" in md
    # 无样本的档位（conf=1, n=0）**不出现在表里**（空行会让人误读成"0% 正确率"）。
    # ⚠️ 判据必须是「没有以 `| 1 |` 开头的**行**」——`"| 1 |" in md` 是错的：
    # 上一行的 `| 5 | 3 | 1 | 33% …` 里就含这个子串（子串断言绑了无关上下文）。
    assert not any(ln.startswith("| 1 |") for ln in md.splitlines()), "n=0 的档位不该出表"
    assert "过度自信预警" in md and "33%" in md


def test_calibration_reports_excluded_counts():
    """被排除的条目要**如实标出**（不静默丢），且区分两种排除原因。"""
    md = _md()
    assert "2 道未填把握程度" in md
    assert "1 道没有作答结果" in md


def test_heatmap_table_and_advice():
    md = _md()
    assert "| 生理学 | 11 |" in md
    assert "| 机制混淆 | 27% |" in md, "主导错因与占比应成对出现"
    assert "因果链错了" in md
    assert "另有 **3** 道未归类" in md
    assert "你自评优先、AI 兜底" in md, "口径要写出来"


def test_migration_and_stuck_alert():
    md = _md()
    assert "| kp1_ed0410bae453564e | 机制混淆 | 机制混淆 | 机制混淆 | 未变 |" in md
    assert "**1 个知识点跨轮次没有改善**" in md


def test_subtract_list_and_freq_missing_hint():
    md = _md()
    assert "生理学 · 消化（2 道，正确率 100%）" in md
    assert "共 1 个章节参与评估" in md
    assert "未导入真题考频" in md, "freq_missing 必须说明（不假装有数据）"


def test_agreement_section():
    md = _md()
    assert "一致率 **78%**" in md
    assert "价值最高" in md


def test_priority_section_is_derived_only():
    """「本周优先」只由统计派生：滞留知识点 + 有建议的科目，且**不带模型文本**。"""
    md = _md()
    tail = md[md.index("## 五、本周优先"):]
    assert "滞留知识点" in tail and "kp1_ed0410bae453564e" in tail
    assert "生理学" in tail
    assert "没有模型生成的内容" in tail


@pytest.mark.parametrize("empty_stats", [
    {},
    {"counts": {"cards": 0, "events": 0, "gated": 0, "kp_ids": 0}},
])
def test_empty_data_renders_readable_empty_states(empty_stats):
    """空数据：每个小节都要有可读说明，**不产出空壳**、不抛异常。"""
    md = _md(empty_stats)
    for s in SECTIONS:
        assert f"## {s}" in md
    for kw in ("暂无有效样本", "暂无带标签的错题", "暂无跨轮次数据",
               "暂无足够数据", "暂无足够证据"):
        assert kw in md, f"空态缺少「{kw}」"
    assert "| --- " not in md, "空数据不该留下只有表头的空表"


def test_cells_escape_table_structure_and_html():
    """用户内容里的 `|` / `<` 必须转义，否则撑破表格或变成标签。"""
    stats = {
        "heatmap": {
            "tags": ["知识盲区"], "subjects": ["内科|外科"],
            "rows": [{"subject": "内科|外科<script>", "counts": {"知识盲区": 1},
                      "total": 1, "top_tag": "知识盲区", "top_ratio": 1.0,
                      "advice": "a|b <b>x</b>"}],
            "untagged": 0, "field": "auto",
        },
        "counts": {"cards": 1, "events": 1, "kp_ids": 1},
    }
    md = _md(stats)
    assert r"内科\|外科&lt;script&gt;" in md, "表格里的 | 与 HTML 都要转义"
    assert "<script>" not in md
    assert r"a\|b &lt;b&gt;x&lt;/b&gt;" in md


def test_no_answer_or_stem_field_is_referenced_by_renderer():
    """**结构守卫**：渲染模块不得引用 `answer` / `user_answer` / `question` 字段。

    复盘笔记是元认知文档——题目本体属于错题本。这条把"不列题目不给答案"变成机械可查的性质
    （AST 找 `fields` 里出现这些名字的下标/`get`，而不是文本子串——注释里提到不算）。
    """
    tree = ast.parse(NOTEBOOK_SRC.read_text(encoding="utf-8"))
    forbidden = {"answer", "user_answer", "question", "analysis", "correct_answer"}
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value in forbidden:
            hits.append(str(node.slice.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value in forbidden:
            hits.append(str(node.args[0].value))
    assert hits == [], f"渲染模块读取了题目/答案字段：{hits}"


def test_renderer_does_not_import_llm_or_agents():
    """**结构守卫**：「零 LLM」是声明，得可证伪 —— 模块不得依赖 llm/agents。

    用 AST 查 import 而不是 `"llm" not in src`：后者会被注释/字符串骗过（两个方向都骗）。
    """
    tree = ast.parse(NOTEBOOK_SRC.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "llm" in mod or "agents" in mod or "websearch" in mod:
                bad.append(mod)
        elif isinstance(node, ast.Import):
            bad += [a.name for a in node.names if "llm" in a.name or "agents" in a.name]
    assert bad == [], f"渲染模块引入了外部依赖模块：{bad}"


# ==================================================================== 2. 端点
@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    for mod in (lib, dbs, kpid, ev):
        monkeypatch.setattr(mod, "LIBRARY_DIR", libd, raising=False)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json", raising=False)
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json", raising=False)
    dbs.reset_conn()
    yield tmp_path
    dbs.reset_conn()


@pytest.fixture()
def client(isolated):
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_endpoint_returns_markdown(client):
    r = client.get("/api/errors/export/notebook")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["filename"].startswith("复盘笔记_") and body["filename"].endswith(".md")
    assert body["markdown"].startswith(f"# {notebook_md.NOTEBOOK_TITLE}")
    assert body["count"] == 0


def test_endpoint_never_leaks_stem_or_answer(client):
    """**红线（端到端）**：产物里不得出现题干与答案。

    它们就在库里（错题本读得到），但复盘笔记**不该**把它们带出来——
    产物会被打印、会被放进笔记软件、会长期留档。
    """
    stem = "患儿男六岁发热咳嗽五天双肺闻及中细湿啰音最可能的诊断是"
    answer = "支气管肺炎"
    client.post("/api/errors/intake", json={
        "question": stem, "answer": answer, "user_answer": "急性支气管炎",
        "subject": "儿科学", "chapter": "呼吸系统", "confidence": 4,
        "my_reasoning": "只记得湿啰音", "error_tag": "机制混淆"})
    r = client.get("/api/errors/export/notebook")
    md = r.json()["markdown"]
    assert r.json()["count"] == 1, "错题应被计入统计"
    assert stem not in md, "复盘笔记里出现了题干"
    assert answer not in md, "复盘笔记里出现了答案"
    assert "急性支气管炎" not in md, "复盘笔记里出现了考生作答"


def test_endpoint_subject_filter_changes_scope(client):
    client.post("/api/errors/intake", json={
        "question": "q1", "answer": "A", "subject": "生理学", "chapter": "循环",
        "confidence": 3, "my_reasoning": "x"})
    md_all = client.get("/api/errors/export/notebook").json()["markdown"]
    md_sub = client.get("/api/errors/export/notebook?subject=生理学").json()["markdown"]
    assert "全部科目" in md_all
    assert "科目：生理学" in md_sub
    assert md_all != md_sub
