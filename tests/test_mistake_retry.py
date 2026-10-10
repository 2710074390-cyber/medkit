# -*- coding: utf-8 -*-
"""错题重练（`core.library.retry_mistake` + `POST /api/library/mistakes/{mid}/retry`）。

口径要点（都钉住，避免以后被「顺手改回去」）：

- **判分与题库/押题卷同源**（`core.bank.answers_equal`，集合比较）——不复制一份规则；
- 判对 → 归档「已掌握」；判错 → 保持未掌握；
- 掌握度经 `record_quiz` 记一次真实提取（与提问判分同一条通道），**不另造计数**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def _seed(client, *, question="慢阻肺最主要的病因是", answer="A", tags=("慢阻肺的诊断",),
          options=("吸烟", "感染", "空气污染"), chapter="呼吸系统"):
    r = client.post("/api/library/mistakes", json={
        "subject": "内科学", "chapter": chapter, "question": question,
        "options": list(options), "answer": answer, "analysis": "吸烟是主要危险因素",
        "know_tags": list(tags)})
    assert r.status_code == 200, r.text
    return r.json()["mistake"]["id"]


def _kp(name="慢阻肺的诊断"):
    return next(k for k in lib.list_knowledge() if k["name"] == name)


def test_retry_correct_archives_and_counts_success(client):
    mid = _seed(client)
    assert _kp()["correct"] == 0 and _kp()["miss"] == 1     # 录入时记了一次 miss

    r = client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "A"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["correct"] is True
    assert j["learned"] is True
    assert j["answer"] == "A" and "吸烟" in j["analysis"], "重练入口同时充当「查看解析」"

    mk = next(x for x in lib.list_mistakes() if x["id"] == mid)
    assert mk["learned"] is True, "判对必须真的归档（落库，不只是返回值）"
    kp = _kp()
    assert kp["correct"] == 1 and kp["attempts"] == 2, kp
    assert kp["state"] != "weak" or kp["score"] > 0


def test_retry_wrong_keeps_unlearned_and_counts_miss(client):
    mid = _seed(client)
    j = client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "B"}).json()
    assert j["correct"] is False and j["learned"] is False
    mk = next(x for x in lib.list_mistakes() if x["id"] == mid)
    assert mk["learned"] is False
    kp = _kp()
    assert kp["miss"] == 2 and kp["attempts"] == 2, kp   # 录入一次 + 重练一次
    # 空作答不算对
    assert client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": ""}).json()["correct"] is False


@pytest.mark.parametrize("user,correct", [
    ("AC", True), ("A,C", True), ("CA", True), ("A", False), ("ABC", False), ("", False),
])
def test_retry_uses_set_semantics_for_multi_choice(client, user, correct):
    mid = _seed(client, question="治疗要点包括", answer="AC", tags=("心衰治疗",),
                options=("限盐", "利尿", "绝对卧床"))
    j = client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": user}).json()
    assert j["correct"] is correct, (user, j)


def test_retry_counts_toward_daily_goal(client):
    """错题重练**也是练习**——每日目标的今日计数必须把它算进去。

    证伪：去掉 `routers/library.retry_mistake` 里的 `record_attempt` → 本用例必红
    （用户设了目标、重练一整晚，进度条还是 0）。
    """
    mid = _seed(client)
    assert client.get("/api/bank/today").json()["attempts"] == 0
    client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "A"})
    client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "B"})
    today = client.get("/api/bank/today").json()
    assert today["attempts"] == 2, today
    assert today["correct"] == 1
    assert today["items"] == 1, "同一道题重练两次 → 去重题数仍为 1"


def test_retry_unknown_mistake_is_404(client):
    assert client.post("/api/library/mistakes/nope/retry", json={"user_answer": "A"}).status_code == 404


def test_retry_only_touches_its_own_knowledge_point(client):
    mid_a = _seed(client, question="题甲", tags=("考点甲",))
    _seed(client, question="题乙", tags=("考点乙",))
    client.post(f"/api/library/mistakes/{mid_a}/retry", json={"user_answer": "A"})
    assert _kp("考点乙")["attempts"] == 1, "重练不得碰到别的知识点"
    assert _kp("考点甲")["attempts"] == 2


def test_retry_without_any_kp_key_does_not_crash(client):
    """无标签 / 无章节的错题（`_kp_key` 返回空）→ 只归档，不碰掌握度。"""
    mid = _seed(client, question="孤立题", tags=(), chapter="", options=("甲", "乙"))
    j = client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "A"}).json()
    assert j["correct"] is True and j["learned"] is True
    assert lib.list_knowledge() == []
