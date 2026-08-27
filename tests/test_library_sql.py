"""library.py SQL 模式端到端（S0·方案 §2.3）：db 建立后 _load/_save 路由到行级事务。

验证：① 公共 API 在 SQL 模式下行为与 JSON 完全一致；② 并发 record_quiz/add_mistake
不再丢失更新（K5 修复闭环）；③ JSON→SQLite 导入后 library 直接读库；④ SQL 模式乱码修复。

隔离：monkeypatch core.db 与 core.library 的全部存储常量到临时目录 + reset_conn。
"""
from __future__ import annotations

import json
import threading

import pytest

from medkit.core import db as dbs
from medkit.core import library as lib

KP = "kp_sql"


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def sql_iso(tmp_path, monkeypatch):
    monkeypatch.setattr(dbs, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "MISTAKES_FILE", tmp_path / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", tmp_path / "knowledge.json")
    dbs.reset_conn()
    dbs.migrate()
    assert lib._store_is_sql(lib.MISTAKES_FILE)
    return tmp_path


def _seed(sql_iso):
    return lib.add_mistake({
        "question": "SQL 模式测试：患儿 3 岁发热？", "options": ["A．是", "B．否"],
        "answer": "A．是", "subject": "儿科", "chapter": "呼吸系统", "topic": "肺炎",
        "know_tags": [KP],
    })


def test_sql_add_and_list_roundtrip(sql_iso):
    rec = _seed(sql_iso)
    mistakes = lib.list_mistakes()
    assert len(mistakes) == 1 and mistakes[0]["id"] == rec["id"]
    kps = lib.list_knowledge()
    assert kps[0]["name"] == KP and kps[0]["state"] == "weak"
    # 再入一条 → 列表/知识点联动
    lib.add_mistake({"question": "第二题？", "answer": "B", "know_tags": [KP]})
    assert len(lib.list_mistakes()) == 2
    assert next(k for k in lib.list_knowledge() if k["name"] == KP)["attempts"] == 2


def test_sql_record_quiz_and_flowback(sql_iso):
    _seed(sql_iso)
    assert lib.record_quiz(KP, 2)          # ≥2 → 答对
    kp = next(k for k in lib.list_knowledge() if k["name"] == KP)
    assert kp["attempts"] == 2 and kp["correct"] == 1
    assert lib.record_quiz(KP, 0) is not None   # 答错
    kp = next(k for k in lib.list_knowledge() if k["name"] == KP)
    # attempts=3（seed+2 次作答）；miss=2（seed 记为 miss + 本次答错）；score 随失败回落
    assert kp["attempts"] == 3 and kp["miss"] == 2 and kp["score"] < 1


def test_sql_concurrent_quiz_no_lost_update(sql_iso):
    """K5 修复闭环：两线程各 50 次 record_quiz（BEGIN IMMEDIATE 串行）→ 无一丢失。"""
    _seed(sql_iso)
    barrier = threading.Barrier(2)

    def flood():
        barrier.wait()
        for _ in range(50):
            lib.record_quiz(KP, 1)

    ts = [threading.Thread(target=flood) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    kp = next(k for k in lib.list_knowledge() if k["name"] == KP)
    assert kp["attempts"] == 101           # 1(seed) + 100，无丢失


def test_sql_concurrent_add_no_lost_update(sql_iso):
    barrier = threading.Barrier(2)

    def flood(tag: int):
        barrier.wait()
        for j in range(50):
            lib.add_mistake({"id": f"m_{tag}_{j}", "question": f"Q{tag}_{j}？",
                             "options": ["A"], "answer": "A", "know_tags": [KP]})

    ts = [threading.Thread(target=flood, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(lib.list_mistakes()) == 100


def test_sql_import_then_read(sql_iso, tmp_path):
    """JSON→SQLite 导入后，library 公共 API 直接读库（导入源不复用，防回灌）。"""
    recs = [{"id": "m1", "question": "导入题一？", "options": ["A"], "answer": "A",
             "know_tags": ["导入kp"]}]
    _write_json(tmp_path / "mistakes.json", recs)
    _write_json(tmp_path / "knowledge.json",
                [{"id": "kp1", "name": "导入kp", "score": 0.3}])
    result = dbs.import_from_json()
    assert result["mistakes"].startswith("imported 1")
    assert lib.list_mistakes()[0]["id"] == "m1"
    assert lib.list_knowledge()[0]["name"] == "导入kp"
    # 导入后 JSON 已改名 → 不会回灌覆盖
    assert not (tmp_path / "mistakes.json").exists()
    lib.add_mistake({"id": "m2", "question": "新题", "options": ["A"], "answer": "A"})
    assert len(lib.list_mistakes()) == 2


def test_sql_heal_encoding(sql_iso, tmp_path):
    moji = "患儿肺炎".encode("utf-8").decode("cp1252")   # cp1252 误读（可逆；字节避开未定义区）
    lib.add_mistake({"id": "m1", "question": "题？", "answer": "A",
                     "subject": moji, "know_tags": [moji]})
    res = lib.heal_encoding()
    assert res["healed"] >= 1
    assert any(b.endswith(".bak") for b in res["backups"])       # 备份了 library（含 db）
    rec = next(m for m in lib.list_mistakes() if m["id"] == "m1")
    assert "患儿" in rec["subject"]
    kp_names = [k["name"] for k in lib.list_knowledge()]
    assert any("患儿" in n for n in kp_names)
