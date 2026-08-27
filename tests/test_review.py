"""M5 复习调度测试：SM-2 纯逻辑 + 队列持久化 + 路由 TestClient。

隔离：monkeypatch lib/rev 的存储路径到临时目录；纯本地算法，无需 LLM。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.library as lib  # noqa: E402
import medkit.core.review as rev  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(rev, "REVIEW_QUEUE_FILE", libd / "review_queue.json")
    return tmp_path


# ---------------------------------------------------------------- SM-2 纯逻辑
def test_next_intervals_growth():
    # 连记：1 → 6 → 前隔×ease
    i1, r1 = rev.next_intervals(2.5, 0, 0)
    i2, r2 = rev.next_intervals(2.5, r1, i1)
    i3, r3 = rev.next_intervals(2.6, r2, i2)
    assert (i1, r1) == (1, 1)
    assert (i2, r2) == (6, 2)
    assert i3 >= i2 and r3 == 3      # 第二次后按 ease 增长，绝不回缩


def test_update_ease_ranges():
    assert rev.update_ease(2.5, 5, True) > 2.5
    assert rev.update_ease(2.5, 1, True) < 2.5          # 通过但质量低 → 仍小幅降
    assert rev.update_ease(2.5, 1, True) > rev.MIN_EASE  # 不越下限
    assert rev.update_ease(rev.MIN_EASE, 0, False) == pytest.approx(rev.MIN_EASE)
    assert rev.update_ease(2.5, 0, False) == pytest.approx(2.3)  # 失败 −0.2，不越下限
    # 质量越高，ease 越正向：q=5 > q=4 > q=3
    assert rev.update_ease(2.5, 5, True) > rev.update_ease(2.5, 4, True)


def test_grade_card_progression_and_fail(isolated):
    card = rev.enqueue("支气管肺炎首选治疗", "儿科学")
    assert card["state"] == "new" and card["interval"] == 0
    c1 = rev.grade_card(card, 4)                    # 记住
    assert c1["state"] == "learning" and c1["interval"] == 1
    c2 = rev.grade_card(c1, 5)                      # 再记住 → review
    assert c2["state"] == "review" and c2["interval"] == 6
    assert len(c2["review_log"]) == 2
    c3 = rev.grade_card(c2, 1)                      # 忘了 → relearning，间隔归零
    assert c3["state"] == "relearning" and c3["interval"] == 0
    assert c3["lapses"] == 1 and c3["reps"] == 0


def test_enqueue_idempotent(isolated):
    a = rev.enqueue("x", "儿科学", "kp_1")
    b = rev.enqueue("x", "儿科学", "kp_1")
    assert a["id"] == b["id"] and len(rev.list_cards()) == 1
    # 不同 kp_id 同名称仍可入队（按 kp_id 判重，其余按名称）
    rev.enqueue("y", "儿科学", "kp_2")
    assert len(rev.list_cards()) == 2


def test_today_and_stats(isolated):
    rev.enqueue("a", "儿科学")
    rev.enqueue("b", "儿科学")
    rev.enqueue("c", "儿科学")
    cards = rev.list_cards()
    # 全部 due=今天 → 都到期
    assert len(rev.today_cards()) == 3
    assert rev.stats()["due"] == 3
    # 记住 b（interval=1 → 明天）后 b 不在今日
    b = next(c for c in cards if c["kp_name"] == "b")
    rev.grade(b["id"], 4)
    assert len(rev.today_cards()) == 2


# ---------------------------------------------------------------- 路由（TestClient，纯本地）
def test_router_review_flow(isolated):
    lib.add_mistake({"question": "q",
                     "know_tags": ["支气管肺炎首选治疗"], "subject": "儿科学"})
    c = TestClient(m.app, base_url="http://127.0.0.1")

    q = c.post("/api/library/review/queue", json={"subject": "儿科学",
                                                  "kp_name": "支气管肺炎首选治疗"})
    assert q.status_code == 200, q.text
    cid = q.json()["card"]["id"]

    t = c.get("/api/library/review/today")
    assert t.status_code == 200 and t.json()["total"] == 1
    assert t.json()["stats"]["due"] == 1

    g = c.post("/api/library/review/grade", json={"card_id": cid, "quality": 4})
    assert g.status_code == 200, g.text
    assert g.json()["card"]["state"] == "learning"
    assert g.json()["card"]["interval"] == 1

    # 掌握度历史已回写
    kp = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    assert any(h["event"] == "review" for h in kp["history"])

    # 删除
    assert c.delete(f"/api/library/review/{cid}").status_code == 200
    assert c.delete(f"/api/library/review/{cid}").status_code == 404
    assert c.post("/api/library/review/grade",
                  json={"card_id": "nope", "quality": 3}).status_code == 404


def test_router_review_grade_writes_back_mastery(isolated):
    lib.add_mistake({"question": "q",
                     "know_tags": ["支气管肺炎首选治疗"], "subject": "儿科学"})
    kp0 = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    assert (kp0["attempts"], kp0["correct"], kp0["miss"]) == (1, 0, 1)
    c = TestClient(m.app, base_url="http://127.0.0.1")
    q = c.post("/api/library/review/queue", json={"subject": "儿科学",
                                                  "kp_name": "支气管肺炎首选治疗"})
    assert q.status_code == 200, q.text
    cid = q.json()["card"]["id"]
    # quality=4（pass）→ 复习回流：记一次 correct + 刷新 last_reviewed
    g = c.post("/api/library/review/grade", json={"card_id": cid, "quality": 4})
    assert g.status_code == 200, g.text
    kp = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (2, 1, 1)
    assert kp.get("last_reviewed")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
