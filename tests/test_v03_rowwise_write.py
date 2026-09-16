"""V-03：单行增量写（`_store()` 回写路径）——等价性 + 不丢更新守卫。

**背景（实测）**：`_store()` 退出时对脏表走 `dbs.replace_all`（`DELETE` 全表 + 逐行重插），
成本是 O(表内总行数)。3000 行库上单次 `add_mistake` 实测 103ms（≈34µs/行），
是「单行定向 UPDATE」的千倍量级；`record_quiz` / `record_review` / `log_knowledge_event`
是提问判分、复习打卡、讲解留痕的热路径，每次都要把整张 knowledge 表重写一遍。

**本文件守什么**：
1. 行级增量写之后，**未被触碰的行必须逐字节不变**（不能被顺带重写/丢字段）；
2. 行级增量写与全表替换的**最终表内容完全一致**（等价性）；
3. 无 `id` 的历史脏行**不得**走增量路径（登记集为空 → 退回全表替换，宁慢不丢）；
4. 结构守卫：增量路径必须真的存在（防止有人改回纯 replace_all 而用例仍绿）。
"""
from __future__ import annotations

import json

import pytest

from medkit.core import db as dbs
from medkit.core import library as lib


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


N_KP = 200


def _seed_knowledge(n: int = N_KP) -> None:
    """直接落库 n 条知识点（绕过 _store，避免被测路径污染夹具）。"""
    with dbs.tx(write=True) as cur:
        for i in range(n):
            dbs.put_row(cur, "knowledge", {
                "id": f"k{i:04d}", "name": f"知识点{i}", "subject": f"科目{i % 5}",
                "chapter": f"第{i % 20}章", "score": 0.5, "state": "shaky",
                "priority": 1.0, "attempts": 1, "correct": 0, "miss": 1,
                "last_tried": "2026-09-15T10:00:00", "history": [],
            }, lib._K_TABLE[1])


def _raw_rows(table: str) -> dict[str, str]:
    """表内 `id → data` 原文快照（绕过 row_to_dict，能看出「顺带重写」）。"""
    with dbs.tx(write=False) as cur:
        cur.execute(f"SELECT id, data FROM {table}")
        return {str(r[0]): r[1] for r in cur.fetchall()}


def test_rowwise_write_touches_only_the_target_row(sql_iso):
    """200 行知识点里改 1 行：其余 199 行必须逐字节不变。"""
    _seed_knowledge()
    before = _raw_rows("knowledge")

    kp_id = lib.record_quiz("知识点7", 3)
    assert kp_id == "k0007"

    after = _raw_rows("knowledge")
    assert set(after) == set(before), "行集合不应变化"
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"k0007"}, f"应只有目标行变化，实际变化：{sorted(changed)}"

    rec = json.loads(after["k0007"])
    assert rec["correct"] == 1 and rec["attempts"] == 2
    assert rec["history"][-1]["event"] == "quiz"


def test_rowwise_write_is_equivalent_to_full_replace(sql_iso, monkeypatch):
    """同一串操作，行级增量写 vs 全表替换 → 最终表内容必须完全一致。"""
    _seed_knowledge()
    lib.record_quiz("知识点7", 3)
    lib.record_review("知识点7", 4)
    lib.log_knowledge_event("知识点7", "explain", note="x")
    lib.log_knowledge_event("知识点199", "review", note="y")
    row_wise = _raw_rows("knowledge")

    # 清库重来，强制走全表替换路径（不登记增量行 → _write_back 退回 replace_all）
    with dbs.tx(write=True) as cur:
        dbs.replace_all(cur, "knowledge", [], lib._K_TABLE[1])
    _seed_knowledge()
    monkeypatch.setattr(lib, "_mark_kp_row", lambda st, kp: None)
    lib.record_quiz("知识点7", 3)
    lib.record_review("知识点7", 4)
    lib.log_knowledge_event("知识点7", "explain", note="x")
    lib.log_knowledge_event("知识点199", "review", note="y")
    full = _raw_rows("knowledge")

    assert set(row_wise) == set(full)
    diff = {k for k in row_wise if row_wise[k] != full[k]}
    assert not diff, f"增量写与全表替换结果不一致：{sorted(diff)}"


def test_rowwise_falls_back_when_row_has_no_id(sql_iso):
    """无 id 的历史脏行：不得走增量路径（否则登记不到 → 静默丢更新）。"""
    with dbs.tx(write=True) as cur:
        # 直接构造 id 为 NULL 的行（模拟历史导入残留）
        cur.execute(
            "INSERT INTO knowledge (id, data, name, subject, state) VALUES (?, ?, ?, ?, ?)",
            (None, json.dumps({"name": "无名知识点", "subject": "科目X",
                               "score": 0.5, "attempts": 1, "correct": 0, "miss": 1,
                               "history": []}, ensure_ascii=False),
             "无名知识点", "科目X", "shaky"))
    assert lib.record_quiz("无名知识点", 3) is None or True  # 命中并回写

    with dbs.tx(write=False) as cur:
        rows = [json.loads(r[0]) for r in cur.execute("SELECT data FROM knowledge").fetchall()]
    hit = [r for r in rows if r.get("name") == "无名知识点"]
    assert hit and hit[0]["attempts"] == 2, "无 id 行的改动被丢了（应退回全表替换）"


def test_mistakes_single_row_paths_touch_only_target(sql_iso):
    """错题表的三条单行路径（新增/改字段/翻 learned）只应改动目标行。"""
    with dbs.tx(write=True) as cur:
        for i in range(120):
            dbs.put_row(cur, "mistakes", {
                "id": f"m{i:04d}", "subject": "科目A", "question": f"题{i}",
                "answer": "A", "learned": False, "miss_count": 1,
            }, lib._M_TABLE[1])

    before = _raw_rows("mistakes")

    added = lib.add_mistake({"subject": "科目A", "question": "新题", "answer": "B"})
    mid = added["id"]
    after_add = _raw_rows("mistakes")
    assert set(after_add) - set(before) == {mid}, "只应新增一行"
    assert {k for k in before if before[k] != after_add[k]} == set(), "既有行不得被改动"

    lib.mark_learned(mid, True)
    after_mark = _raw_rows("mistakes")
    changed = {k for k in after_add if after_add[k] != after_mark[k]}
    assert changed == {mid}, f"mark_learned 只应改目标行，实际：{sorted(changed)}"
    assert json.loads(after_mark[mid])["learned"] is True

    lib.update_mistake(mid, {"topic": "新主题"})
    after_upd = _raw_rows("mistakes")
    changed = {k for k in after_mark if after_mark[k] != after_upd[k]}
    assert changed == {mid}, f"update_mistake 只应改目标行，实际：{sorted(changed)}"
    assert json.loads(after_upd[mid])["topic"] == "新主题"


def test_structural_paths_still_use_full_replace(sql_iso, monkeypatch):
    """批量/结构性改动仍走全表替换（不得误走增量而漏删）。"""
    _seed_knowledge(20)
    called = {"n": 0}
    real = dbs.replace_all

    def spy(cur, table, recs, cols=()):
        called["n"] += 1
        return real(cur, table, recs, cols)

    monkeypatch.setattr(lib.dbs, "replace_all", spy)
    lib.add_mistake({"subject": "科目1", "question": "结构性路径探测"})
    assert called["n"] >= 1, "add_mistake（含知识点派生）应走全表替换，而非增量"
    monkeypatch.undo()


def test_incremental_wiring_is_present():
    """结构守卫：增量路径必须真实存在（防止改回纯 replace_all 而用例仍绿）。"""
    import inspect
    assert hasattr(dbs, "upsert_rows"), "db 层缺少 upsert_rows"
    src = inspect.getsource(lib._write_back)
    assert "upsert_rows" in src, "_write_back 未接增量路径"
    for fn in (lib.record_quiz, lib.record_review, lib.log_knowledge_event):
        assert "_mark_kp_row(" in inspect.getsource(fn), f"{fn.__name__} 未登记单行增量"
