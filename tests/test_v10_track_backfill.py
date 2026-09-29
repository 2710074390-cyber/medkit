"""V-10：JSON 轨 → SQL 轨切换时的数据补导（P0 数据可见性守卫）。

**缺陷（实测必现）**：`_store_is_sql()` 只判断「`medkit.db` 是否存在」，而该 db 由**别的域**
（大纲管理 / 真题考频 / 记忆卡）按需 `migrate()` 建立；库域自己从不建库、也从不补导。
于是：

    新装 → 用错题本（JSON 轨）→ 去点一次「大纲管理」（建库）
         → 库域改判 SQL 轨 → 错题本/掌握度读空库 = 界面全空

数据没丢（仍在 `mistakes.json`），但界面完全不可见；此后新写入进 DB，形成真正的双轨分叉。

`db.import_from_json()`（按 id 幂等 + 导入后原 JSON 改名留档）本就是为这一步准备的，
只是从未被接线。本文件把「切换即补导」钉成回归网。
"""
from __future__ import annotations

import json

import pytest

from medkit.core import db as dbs
from medkit.core import library as lib


@pytest.fixture
def json_then_db(tmp_path, monkeypatch):
    """先把库域跑在 JSON 轨，再建库——复现真实的轨切换时序。"""
    monkeypatch.setattr(dbs, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(lib, "MISTAKES_FILE", tmp_path / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", tmp_path / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    dbs.reset_conn()
    return tmp_path


def _write_json_track(n: int = 3) -> None:
    for i in range(n):
        lib.add_mistake({"subject": "内科学", "chapter": "第1章",
                         "question": f"JSON 轨错题 {i}", "answer": "A",
                         "know_tags": [f"JSON轨知识点{i}"]})


def test_json_track_data_visible_after_db_created(json_then_db):
    """核心守卫：JSON 轨写下的数据，在别处建库之后**必须仍然可见**。"""
    assert not lib._store_is_sql(lib.MISTAKES_FILE), "前置：应处于 JSON 轨"
    _write_json_track(3)
    assert len(lib.list_mistakes()) == 3
    assert len(lib.list_knowledge()) == 3

    dbs.migrate()          # 模拟用户去点了「大纲管理 / 真题考频 / 记忆卡」
    assert lib._store_is_sql(lib.MISTAKES_FILE), "前置：建库后应切到 SQL 轨"

    assert len(lib.list_mistakes()) == 3, "切轨后 JSON 轨旧数据不可见（V-10 回归）"
    assert len(lib.list_knowledge()) == 3, "切轨后派生知识点不可见（V-10 回归）"
    assert lib.count_mistakes() == 3, "count_mistakes 也必须先补导再计数"


def test_backfill_is_idempotent(json_then_db):
    """反复访问不得重复导入（幂等由 id 键保证）。"""
    _write_json_track(2)
    dbs.migrate()
    for _ in range(3):
        assert len(lib.list_mistakes()) == 2
    with dbs.tx(write=False) as cur:
        assert cur.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 2


def test_backfill_keeps_json_as_backup(json_then_db):
    """补导成功后原 JSON 改名留档（内容原样保留，可回滚）。"""
    _write_json_track(2)
    dbs.migrate()
    lib.list_mistakes()          # 触发补导

    assert not (json_then_db / "mistakes.json").exists(), "原 JSON 应已改名"
    baks = list(json_then_db.glob("mistakes.json.pre-db-import-*.bak"))
    assert baks, f"未留下导入备份：{list(json_then_db.iterdir())}"
    assert len(json.loads(baks[0].read_text(encoding="utf-8"))) == 2, "备份内容应完整"


def test_backfill_failure_falls_back_to_json_track(json_then_db, monkeypatch):
    """补导失败时**退回 JSON 轨**——宁可暂时双轨，也不能让用户数据隐身。"""
    _write_json_track(2)
    dbs.migrate()

    def boom():
        raise RuntimeError("模拟补导失败")

    monkeypatch.setattr(dbs, "import_from_json", boom)
    assert len(lib.list_mistakes()) == 2, "补导失败后数据仍应可见（退回 JSON 轨）"
    assert not lib._sql_ready(lib.MISTAKES_FILE), "补导失败时不应判定为可走 SQL 轨"


def test_no_backfill_when_no_json(json_then_db):
    """无 JSON 轨数据时补导是零开销 no-op（新装/已补导后的常态路径）。"""
    dbs.migrate()
    assert lib._backfill_json_once() is True
    lib.add_mistake({"subject": "内科学", "question": "SQL 轨新题", "answer": "A",
                     "know_tags": ["SQL轨知识点"]})
    assert len(lib.list_mistakes()) == 1
    assert len(list(json_then_db.glob("*.json"))) == 0, "SQL 轨不应产生 JSON 活数据"


def test_wiring_guard():
    """结构守卫：库域的轨判定必须统一走 `_sql_ready`（不得再裸用 `_store_is_sql` 判定读写）。

    ## 补导调用改用 AST 判据（2026-09-29 R15 改）

    旧版末条：

        assert "import_from_json" in inspect.getsource(lib._backfill_json_once)

    实测**假绿**：`import_from_json` 在该函数里出现 **2 次**——
    一次在 **docstring**（"`db.import_from_json()` 本就是为这一步准备的…"），
    一次是真调用 `dbs.import_from_json()`。**把真实调用那一行整行删掉，断言仍然通过**
    （docstring 里那个词还在），于是「补导从未被接线」这个 P0 缺陷可以无声回归。

    判据改为 AST：必须存在 `Call(func=Attribute(attr='import_from_json'))`
    ——docstring 是 `ast.Constant`，天然不计入。
    """
    import ast
    import inspect

    for fn in (lib._load, lib._save, lib._store, lib.count_mistakes):
        # 2026-09-29 R20：改用 AST 查真实调用 —— 旧版 `"_sql_ready(" in src` 是
        # 纯文本子串，注释里写一句「这里本来该走 _sql_ready」就能骗过它。
        fn_tree = ast.parse("\n".join(inspect.getsource(fn).splitlines()))
        hit = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_sql_ready"
            for n in ast.walk(fn_tree))
        assert hit, f"{fn.__name__} 未**调用** _sql_ready（注释里提到不算，切轨会漏补导）"

    src = inspect.getsource(lib._backfill_json_once)
    tree = ast.parse("\n".join(src.splitlines()))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "import_from_json"
    ]
    assert calls, (
        "_backfill_json_once 里没有对 import_from_json 的**真实调用**"
        "（docstring 里提到不算）——补导未接线会让切轨后旧数据读空"
    )
    # 且该调用必须处在 try 块里（失败要能退回 JSON 轨，不能裸抛）
    try_calls: set[int] = set()
    for t in ast.walk(tree):
        if isinstance(t, ast.Try):
            for c in ast.walk(t):
                if isinstance(c, ast.Call):
                    try_calls.add(id(c))
    assert any(id(c) in try_calls for c in calls), (
        "补导调用不在 try 块里——失败会裸抛，无法退回 JSON 轨")


def test_backfill_wiring_guard_is_not_vacuous():
    """元守卫：证明 AST 判据抓得住「删掉真实调用、只在 docstring 里留词」。"""
    import ast
    import inspect

    src = inspect.getsource(lib._backfill_json_once)

    def _calls(text: str) -> int:
        tree = ast.parse("\n".join(text.splitlines()))
        return len([n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "import_from_json"])

    # 真身绿
    assert _calls(src) == 1
    # 文本子串（旧判据）在真身里命中 2 次——说明它包含 docstring 那次
    assert src.count("import_from_json") == 2, (
        "若真身只剩 1 次出现，本条证伪用例的前提已变，需同步更新")

    # 证伪：删掉真实调用那一行，docstring 原样保留
    kept = [ln for ln in src.splitlines(keepends=True)
            if "dbs.import_from_json()" not in ln]
    mutated = "".join(kept)
    assert "import_from_json" in mutated, "docstring 里的词应仍在（这正是旧判据的漏洞）"
    assert _calls(mutated) == 0, (
        "删掉真实调用后 AST 判据仍说『有调用』 —— 这条守卫是假绿")
