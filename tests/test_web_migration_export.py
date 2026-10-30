# -*- coding: utf-8 -*-
"""网页版迁移包（`core/library.export_web_package` + `GET /api/library/migrate/export`）。

契约对象是 medkit-web-contest 的 `js/store.js::importPackage`——它只落位、**不重算 id**，
所以「作答 id 必须在导出侧算成 `科目名#序号`」这条只能在这里钉。三条底线：
  ① 引用不存在题目的作答被剔除并如实计数（桌面端重解析/删题后老作答对不上是常态，
     静默保留会让「练了」指向另一道题）；
  ② 错题随身携带用户自己写的字（my_reasoning / understanding / doubts）——丢字比丢字段严重；
  ③ vault 缺失/损坏时迁移包仍要能出（错题降级为空并如实计数），不能 500。
隔离：`tests/conftest.py` 的 autouse fixture 已把存储重定向到 tmp_path，不触碰真实 ~/.medkit。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.core.bank as bank  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


_MD = """# 呼吸系统题库
### q1 · A1 型 · 单选 · 理解
**慢阻肺的诊断**
慢阻肺最主要的病因是
- A. 吸烟
- B. 感染
**✅ 答案：A**
💡 吸烟是主要危险因素

### q2 · X 型 · 多选 · 应用
**心衰治疗**
下列属于治疗要点的是
- A. 限盐
- B. 利尿
- C. 绝对卧床
**✅ 答案：AC**
💡 限盐与利尿是基石
"""


def _make_bank(client) -> tuple[str, list[dict]]:
    r = client.post("/api/bank/import/preview", json={"text": _MD, "fmt": "md"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    r = client.post("/api/bank/import/commit", json={"name": "呼吸系统", "subject": "内科学", "items": items})
    assert r.status_code == 200, r.text
    bid = r.json()["bank_id"]
    detail = client.get(f"/api/bank/banks/{bid}").json()
    return bid, detail["items"]


# ---------------------------------------------------------------- 包形状与 id 映射
def test_export_maps_ids_and_carries_content(client):
    bid, items = _make_bank(client)
    # 答错第 1 题 → 判错回流错题本（source_ref 指向 bank item）
    r = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": items[0]["id"], "user_answer": "B", "mode": "practice"})
    assert r.status_code == 200 and r.json()["correct"] is False

    resp = client.get("/api/library/migrate/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers.get("content-disposition", "")
    pkg = json.loads(resp.content.decode("utf-8"))

    assert pkg["app"] == "medkit-web" and pkg["kind"] == "migration"
    assert len(pkg["banks"]) == 1
    b = pkg["banks"][0]
    assert b["name"] == "呼吸系统" and b["subject"] == "内科学" and len(b["items"]) == 2
    # 网页版字段形状：answer 大写、options 是纯文本数组
    it0 = b["items"][0]
    assert it0["answer"] == "A" and isinstance(it0["options"], list)

    # 作答 id 在导出侧算好，网页端只落位
    atts = pkg["data"]["mk_web_attempts"]
    assert [a["id"] for a in atts] == ["呼吸系统#0"]
    assert atts[0]["correct"] is False and atts[0]["bid"] == "呼吸系统"
    assert pkg["stats"]["attempts_dropped"] == 0

    # 错题挂到网页 id；due 是 epoch 毫秒
    ms = pkg["data"]["mk_web_mistakes"]
    assert len(ms) == 1
    assert ms[0]["itemId"] == "呼吸系统#0" and ms[0]["id"] == "M:呼吸系统#0"
    assert isinstance(ms[0]["due"], int) and ms[0]["due"] > 0

    # 每日学习量从作答流水合成（网页版 streak/last7 的数据源）
    assert sum(d["count"] for d in pkg["data"]["mk_web_daily"].values()) == 1


def test_export_drops_orphan_attempts_and_counts(client):
    bid, items = _make_bank(client)
    bank.record_attempt(bid, items[1]["id"], user_answer="AC", correct=True)
    # 孤儿作答：record_attempt 不校验题目存在（历史版本/手工数据可能留下这种行），
    # 导出必须剔除并计数，而不是让「练了」指向一道不存在的题。
    bank.record_attempt(bid, "qi_deleted_elsewhere", user_answer="A", correct=True)
    pkg = lib.export_web_package()
    assert pkg["stats"]["attempts"] == 1
    assert pkg["stats"]["attempts_dropped"] == 1
    assert [a["id"] for a in pkg["data"]["mk_web_attempts"]] == ["呼吸系统#1"]


# ---------------------------------------------------------------- 用户自写文字必须随身携带
def test_export_keeps_user_words_and_due(client):
    bid, items = _make_bank(client)
    rec = lib.add_mistake({
        "id": "m_test_1", "source": "bank", "subject": "内科学",
        "question": "慢阻肺最主要的病因是", "options": ["吸烟", "感染"], "answer": "A",
        "analysis": "吸烟是主要危险因素", "miss_count": 2,
        "source_ref": {"question_id": items[0]["id"]},
        "my_reasoning": "我把慢阻肺和哮喘混了",
        "understanding": "气流受限不可逆才是慢阻肺",
        "doubts": "和哮喘的鉴别点到底背哪条",
        "due": "2026-11-02",
    })
    assert rec.get("id")
    # understanding/doubts/due 只活在 vault md（add_mistake 的白名单不含它们——
    # 用户在错题本里手写、错题助手代笔，都在文件层）。模拟真实写入路径：
    import medkit.core.vault as vault
    vp = next(vault.iter_mistake_files())
    vrec = vault.read_record(vp)
    vrec["my_reasoning"] = "后来复盘：气流受限不可逆才是慢阻肺"   # md「我的理解」段落
    vrec["doubts"] = "和哮喘的鉴别点到底背哪条"
    vrec["due"] = "2026-11-02"
    vault.write_record(vrec)
    pkg = lib.export_web_package()
    ms = {m["itemId"]: m for m in pkg["data"]["mk_web_mistakes"]}
    m0 = ms["呼吸系统#0"]
    # vault（真身）的复盘文字盖过 mistakes 表的录入快照
    assert m0["my_reasoning"] == "后来复盘：气流受限不可逆才是慢阻肺"
    assert m0["doubts"] == "和哮喘的鉴别点到底背哪条"
    assert m0["wrong"] == 2
    # due 日期原样保留（复习队列的时间顺序不丢；SM-2 三参数桌面端已迁 FSRS，回默认值）
    assert m0["due"] > 0


# ---------------------------------------------------------------- 降级路径
def test_export_empty_store_and_vault_gone(client):
    pkg = lib.export_web_package()
    assert pkg["kind"] == "migration"
    assert pkg["banks"] == []
    assert pkg["stats"]["items"] == 0 and pkg["stats"]["attempts"] == 0

    # vault 目录整个没了（换机器 / 未初始化）：迁移包仍要能出，错题降级为空
    import medkit.core.vault as vault
    orig = vault.vault_root
    try:
        vault.vault_root = lambda: Path(tempfile.mkdtemp()) / "not-a-vault"  # type: ignore[assignment]
        pkg2 = lib.export_web_package()
        assert pkg2["data"]["mk_web_mistakes"] == []
        assert pkg2["stats"]["banks"] == 0
    finally:
        vault.vault_root = orig  # type: ignore[assignment]
