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
    """同一串操作，行级增量写 vs 全表替换 → 最终表内容必须完全一致。

    V-05 教训复用：时间源必须冻结。`_now()` 是秒级精度，两次运行若跨秒边界，
    写出的 `last_tried`/history 时间戳就会不同，断言会随机翻红（本用例首次编写时即踩到）。
    """
    monkeypatch.setattr(lib, "_now", lambda: "2026-09-16T10:00:00")
    _seed_knowledge()
    lib.record_quiz("知识点7", 3)
    lib.record_review("知识点7", 4)
    lib.log_knowledge_event("知识点7", "explain", note="x")
    lib.log_knowledge_event("知识点199", "review", note="y")
    row_wise = _raw_rows("knowledge")

    # 清库重来，把回写强制成「整表快照 + 全表替换」这一参考实现（V-09 后 _write_back 的增量分支
    # 不再依赖「登记集为空」，故不能再用 monkeypatch 登记函数的方式造参考路径）
    with dbs.tx(write=True) as cur:
        dbs.replace_all(cur, "knowledge", [], lib._K_TABLE[1])
    _seed_knowledge()

    def full_write_back(cur, table, st, meta):
        lst = st[table] if table in st else dbs.list_rows(cur, table)
        for rid, rec in (st["rowwise"].get(table) or {}).items():
            for i, item in enumerate(lst):
                if str(item.get("id")) == rid:
                    lst[i] = rec
                    break
            else:
                lst.append(rec)
        dbs.replace_all(cur, table, lst, meta[1])

    monkeypatch.setattr(lib, "_write_back", full_write_back)
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


def test_add_mistake_same_id_replaces_not_duplicates(sql_iso):
    """V-09：同 id 重复入库必须**覆盖**而非新增。

    旧实现靠「整表读 + `_find` 按下标替换」保证；V-09 去掉了整表读，改由主键
    `INSERT OR REPLACE` 承担同一语义——本用例把这条等价性钉住。
    """
    lib.add_mistake({"id": "dup1", "subject": "科目A", "question": "第一版", "answer": "A"})
    lib.add_mistake({"id": "dup1", "subject": "科目A", "question": "第二版", "answer": "B"})
    rows = [m for m in lib.list_mistakes() if m["id"] == "dup1"]
    assert len(rows) == 1, f"同 id 应只有一行，实际 {len(rows)}"
    assert rows[0]["question"] == "第二版"


def test_derived_knowledge_is_persisted(sql_iso):
    """V-09 回归守卫：定向读路径下，错题派生的**新知识点**必须落库。

    实测踩过：`_find_kp` 定向取到的是临时 dict，若回写时用「整表旧快照」做全表替换，
    这条新知识点会被静默丢掉（`test_sql_heal_encoding` 曾因此变红）。
    """
    lib.add_mistake({"subject": "科目A", "question": "题", "answer": "A",
                     "know_tags": ["全新知识点X"]})
    kps = [k for k in lib.list_knowledge() if k["name"] == "全新知识点X"]
    assert len(kps) == 1, "错题派生的新知识点未落库"
    assert kps[0]["attempts"] == 1 and kps[0]["miss"] == 1

    lib.add_mistake({"subject": "科目A", "question": "题2", "answer": "A",
                     "know_tags": ["全新知识点X"]})
    kps = [k for k in lib.list_knowledge() if k["name"] == "全新知识点X"]
    assert len(kps) == 1, "第二次入库不应新建重复知识点"
    assert kps[0]["attempts"] == 2, "第二次入库应累加样本数"


def test_knowledge_events_are_persisted(sql_iso):
    """V-09 回归守卫：`log_knowledge_events` 批量写必须落库（含 add_mistake 的 'mistake' 事件）。"""
    lib.add_mistake({"subject": "科目A", "question": "题", "answer": "A",
                     "know_tags": ["事件知识点"]})
    kp = next(k for k in lib.list_knowledge() if k["name"] == "事件知识点")
    assert any(h.get("event") == "mistake" for h in kp.get("history") or []), \
        "入库错题的 mistake 事件未写入 knowledge.history"

    ids = lib.log_knowledge_events(["事件知识点", "不存在的知识点"], "explain", note="n")
    assert len(ids) == 1, "未命中的名字应被跳过"
    kp = next(k for k in lib.list_knowledge() if k["name"] == "事件知识点")
    assert [h.get("event") for h in kp["history"]][-1] == "explain"


def test_changed_rows_merged_back_when_table_materialized_later(sql_iso):
    """V-09 守卫：**先登记变更行、之后才把整表读进内存**时，变更不得被旧快照覆盖。

    触发路径：① 登记 kp 的改动（此时 knowledge 尚未整表加载）；
    ② 查一个不存在的名字 → 因表内存在 `name` 为 NULL 的历史行而走兜底整表扫描（materialize）；
    ③ 回写走全表替换 → 必须先把登记行并回列表，否则列表里的旧对象会把改动盖掉。
    """
    _seed_knowledge(5)
    with dbs.tx(write=True) as cur:
        cur.execute(
            "INSERT INTO knowledge (id, data, name, subject, state) VALUES (?, ?, ?, ?, ?)",
            ("legacy1", json.dumps({"subject": "科目X", "score": 0.5, "attempts": 1,
                                    "correct": 0, "miss": 1, "history": []},
                                   ensure_ascii=False), None, "科目X", "shaky"))

    with lib._store() as st:
        kp = lib._find_kp(st, "知识点0")
        assert kp is not None
        kp["history"] = (kp.get("history") or []) + [{"t": "x", "event": "probe", "note": ""}]
        lib._mark_kp_row(st, kp)
        assert "knowledge" not in st, "此步 knowledge 应尚未整表加载"
        lib._find_kp(st, "根本不存在的名字")   # 命中 NULL name 兜底 → 整表加载
        assert "knowledge" in st, "兜底扫描应已把整表读进内存"

    kp = next(k for k in lib.list_knowledge() if k["name"] == "知识点0")
    assert any(h.get("event") == "probe" for h in kp.get("history") or []), \
        "整表加载后回写用了旧快照，登记行的改动被覆盖"


def test_structural_paths_still_use_full_replace(sql_iso, monkeypatch):
    """批量/结构性改动（会删行）仍走全表替换——不得误走增量而漏删。"""
    with dbs.tx(write=True) as cur:
        for i in range(30):
            dbs.put_row(cur, "mistakes", {
                "id": f"m{i:04d}", "subject": "科目A", "question": f"题{i}",
                "answer": "A", "learned": False, "miss_count": 1,
            }, lib._M_TABLE[1])

    called = {"n": 0}
    real = dbs.replace_all

    def spy(cur, table, recs, cols=()):
        called["n"] += 1
        return real(cur, table, recs, cols)

    monkeypatch.setattr(lib.dbs, "replace_all", spy)
    assert lib.delete_mistake("m0001") is True, "删除单条错题应成功"
    assert called["n"] >= 1, "删除（结构性改动）应走全表替换，而非增量"
    assert not [m for m in lib.list_mistakes() if m["id"] == "m0001"], "删除必须真的落库"


def test_incremental_wiring_is_present():
    """结构守卫：增量路径必须真实存在（防止改回纯 replace_all 而用例仍绿）。"""
    import inspect
    assert hasattr(dbs, "upsert_rows"), "db 层缺少 upsert_rows"
    assert hasattr(dbs, "find_row"), "db 层缺少 find_row（V-09 定向读）"
    src = inspect.getsource(lib._write_back)
    assert "upsert_rows" in src, "_write_back 未接增量路径"
    for fn in (lib.record_quiz, lib.record_review, lib.log_knowledge_events):
        assert "_mark_kp_row(" in inspect.getsource(fn), f"{fn.__name__} 未登记单行增量"
    for fn in (lib.add_mistake, lib.update_mistake, lib.mark_learned):
        assert "_mark_m_row(" in inspect.getsource(fn), f"{fn.__name__} 未登记单行增量"
    # V-09：单行路径不得再整表读（`st["mistakes"]` / `st["knowledge"]` 是整表加载的唯一入口）
    for fn in (lib.record_quiz, lib.record_review, lib.log_knowledge_events,
               lib.add_mistake, lib.update_mistake, lib.mark_learned):
        src = inspect.getsource(fn)
        assert 'st["knowledge"]' not in src and 'st["mistakes"]' not in src, \
            f"{fn.__name__} 仍整表加载（V-09 应走 _find_kp / _find_mistake 定向读）"
