"""题库身份与解析覆盖率（2026-10-29 P0）。

守三条：
1. 回填**只补空值**，不改已有考点（用户手改比推断更可信）；
2. 默认 dry-run，且 dry-run 不写库；
3. 覆盖率**逐字段独立**统计——「有选项理由」不等于「有原文引句」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from medkit import main as m
from medkit.core import bank as banklib
from medkit.core import insights


def _item(i: str, **kw):
    base = {
        "question": f"题干{i}",
        "options": ["A. 甲选项", "B. 乙选项", "C. 丙选项", "D. 丁选项", "E. 戊选项"],
        "answer": "A", "analysis": "解析", "type": "A1", "bloom": "理解",
        "subtopic": "", "origin_ref": {},
    }
    base.update(kw)
    return base


def _bank(items):
    b = banklib.create_bank("覆盖用例库", subject="外科学")
    banklib.add_items(b["id"], items, dedupe="new")
    return b["id"]


def test_backfill_uses_origin_module_and_keeps_existing():
    bid = _bank([
        _item("1", origin_ref={"module": "胸部损伤"}),
        _item("2", subtopic="人工考点"),
        _item("3"),
    ])
    out = insights.backfill_topics(bank_id=bid, dry_run=True)
    assert out["filled"] == 1, "只有空 subtopic 且能推断的那条可补"
    assert out["by_source"] == {"origin_ref.module": 1}
    # dry-run 不写库
    items = {i["question"]: i for i in banklib.list_items(bid)}
    assert items["题干1"]["subtopic"] == ""
    assert items["题干2"]["subtopic"] == "人工考点"


def test_backfill_writes_only_when_not_dry_run():
    bid = _bank([_item("1", origin_ref={"module": "胸部损伤"}),
                 _item("2", subtopic="人工考点")])
    out = insights.backfill_topics(bank_id=bid, dry_run=False)
    assert out["filled"] == 1
    items = {i["question"]: i for i in banklib.list_items(bid)}
    assert items["题干1"]["subtopic"] == "胸部损伤"
    assert items["题干2"]["subtopic"] == "人工考点", "已有考点不许被覆盖"


def test_backfill_does_not_use_subject_as_topic():
    """科目不是考点：拿「外科学」当考点会让整库只有一个知识点，比空着更误导。"""
    bid = _bank([_item("1")])
    out = insights.backfill_topics(bank_id=bid, dry_run=True)
    assert out["filled"] == 0
    assert out["skipped"] == 1


def test_coverage_counts_each_field_independently():
    bid = _bank([
        _item("1", subtopic="胸部损伤", option_rationales={"A": "甲对"}),
        _item("2", subtopic="胸部损伤", source_quote="原文一句",
              difficulty=3, misconceptions={"B": "误以为乙"}),
        _item("3"),
    ])
    cov = insights.coverage(bank_id=bid)
    t = cov["total"]
    assert t["items"] == 3
    assert t["topic"] == 2
    assert t["rationales"] == 1, "只有第 1 题有逐选项理由"
    assert t["source_quote"] == 1, "「有理由」不能算成「有引句」"
    assert t["misconceptions"] == 1
    assert t["difficulty"] == 1


def test_insights_endpoints():
    bid = _bank([_item("1", origin_ref={"module": "胸部损伤"})])
    client = TestClient(m.app, base_url="http://127.0.0.1")
    r = client.get("/api/bank/insights/coverage", params={"bank_id": bid})
    assert r.status_code == 200
    assert r.json()["total"]["items"] == 1

    r = client.post("/api/bank/insights/backfill-topic", json={"bank_id": bid})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and r.json()["filled"] == 1

    r = client.post("/api/bank/insights/backfill-topic",
                    json={"bank_id": bid, "dry_run": False})
    assert r.status_code == 200 and r.json()["filled"] == 1
    assert banklib.list_items(bid)[0]["subtopic"] == "胸部损伤"

    r = client.post("/api/bank/insights/backfill-topic", json={"limit": -1})
    assert r.status_code == 400
