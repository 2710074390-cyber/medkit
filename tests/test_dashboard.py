"""D：学习闭环驾驶舱聚合 dashboard.summary()。

隔离：monkeypatch lib/expl/rev/tut 的存储文件到临时目录；不发起真实 LLM。
验证三闭环（掌握度 / 复习 SM-2 / 提问式 MedTutor）聚合计数、按科目过滤与闭环流转环节。
掌握度晋升由 library 状态机负责（已有独立测试），此处用白盒注入高分数据定式验证 mastered 计数。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.dashboard as dash  # noqa: E402
import medkit.core.explain as expl  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.core.review as rev  # noqa: E402
import medkit.core.tutor as tut  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    for mod in (lib, tut, rev, expl):
        monkeypatch.setattr(mod, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", libd / "tutor_sessions.json")
    monkeypatch.setattr(rev, "REVIEW_QUEUE_FILE", libd / "review_queue.json")
    monkeypatch.setattr(expl, "EXPLAINS_FILE", libd / "explains.json")
    return tmp_path


def _seed(isolated, subject="儿科学"):
    # 错题（派生知识点：儿科学=支气管肺炎、解剖学=颅骨，均为 weak/shaky）
    lib.add_mistake({"subject": subject, "chapter": "呼吸", "question": "题1",
                     "options": ["a", "b", "c", "d", "e"], "answer": "B", "know_tags": ["支气管肺炎"]})
    lib.add_mistake({"subject": "解剖学", "chapter": "骨", "question": "骨题",
                     "options": ["a", "b", "c", "d", "e"], "answer": "A", "know_tags": ["颅骨"]})
    lib.record_quiz("支气管肺炎", 3)          # 儿科学升到 shaky
    # 复习卡 + 提问会话（均挂在儿科学）
    rev.enqueue("支气管肺炎", subject=subject)
    tut.start_session(subject, "支气管肺炎")
    return subject


def test_review_stats_has_review_state(isolated):
    lib.add_mistake({"subject": "儿科学", "question": "q", "options": ["a", "b", "c", "d", "e"],
                     "answer": "A", "know_tags": ["x"]})
    rev.enqueue("x", subject="儿科学")
    st = rev.stats("儿科学")
    assert st["total"] == 1
    for key in ("new", "due", "in_progress", "review"):
        assert key in st, f"stats 应含 {key} 状态计数"
    assert st["new"] == 1


def test_dashboard_summary_aggregates(isolated):
    _seed(isolated)
    d = dash.summary("儿科学")

    assert d["subject"] == "儿科学" and d["subject_label"] == "儿科学"
    assert d["mastery"]["total_knowledge"] == 1          # 儿科学只含支气管肺炎
    assert d["mastery"]["total_mistakes"] == 1           # 只统计儿科学的错题
    assert d["mastery"]["miss_kps"] == 1 and d["mastery"]["miss_count"] == 1

    assert d["review"]["total"] == 1
    assert d["review"]["done"] == d["review"]["total"] - d["review"]["due"]

    assert d["tutor"]["total"] == 1
    assert d["tutor"]["in_progress"] == 1               # 会话未到 mastered
    assert d["tutor"]["by_state"]["shaky"] == 1          # 会话初始状态=知识点当前(shaky)

    # 闭环流转环节
    assert d["loop"]["mistakes"] == 1
    assert d["loop"]["explains"] == 0
    assert d["loop"]["tutor"] == 1
    assert d["loop"]["review"] == 1
    assert d["loop"]["mastered"] == 0


def test_dashboard_subject_filter_and_global(isolated):
    _seed(isolated)
    g = dash.summary("")                                # 全部科目
    assert g["mastery"]["total_knowledge"] == 2         # 儿科学 + 解剖学
    assert g["mastery"]["total_mistakes"] == 2
    sub = dash.summary("解剖学")
    assert sub["mastery"]["total_knowledge"] == 1       # 解剖学 1 个
    assert sub["mastery"]["total_mistakes"] == 1
    assert sub["tutor"]["total"] == 0                   # 会话在儿科学


def test_dashboard_mastered_tally(isolated):
    """白盒定式验证：注入高分知识点后 mastered 计数与掌握率正确。"""
    _seed(isolated)
    kps = lib.list_knowledge()
    rec = next(k for k in kps if k["subject"] == "儿科学")
    rec.update({"score": 0.99, "correct": 5, "attempts": 6})   # 使其计算为 mastered
    lib._save(lib.KNOWLEDGE_FILE, kps)
    d = dash.summary("儿科学")
    assert d["mastery"]["mastered"] == 1
    assert d["mastery"]["mastered_rate"] == 100          # solid?solid + mastered = 1/1
    assert d["loop"]["mastered"] == 1


def test_dashboard_router_endpoint(isolated):
    _seed(isolated)
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/library/dashboard?subject=儿科学")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "儿科学"
    assert body["mastery"]["total_knowledge"] == 1
    assert body["tutor"]["total"] == 1
    assert "loop" in body
    assert "contract_warnings" in body   # NX-03：dashboard 端点携带契约告警计数


def test_dashboard_contract_warnings(isolated, monkeypatch, tmp_path):
    """NX-03：项目 meta contract_warnings 聚合（按科目可选过滤）。"""
    import json as _json

    projs = tmp_path / "projs"
    a = projs / "p1"
    a.mkdir(parents=True)
    (a / "meta.json").write_text(_json.dumps({"subject": "内科学", "contract_warnings": 3}),
                                 encoding="utf-8")
    b = projs / "p2"
    b.mkdir()
    (b / "meta.json").write_text(_json.dumps({"subject": "儿科学", "contract_warnings": 5}),
                                 encoding="utf-8")
    # 无计数项目 / 未生成过 meta 的项目应被忽略
    monkeypatch.setattr(expl, "_PROJ_ROOT", projs)
    s = dash.summary()
    assert s["contract_warnings"] == {"total": 8, "by_subject": {"内科学": 3, "儿科学": 5}}
    assert dash.summary(subject="内科学")["contract_warnings"]["total"] == 3
    assert dash.summary(subject="外科学")["contract_warnings"]["total"] == 0
