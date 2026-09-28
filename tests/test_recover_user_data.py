# -*- coding: utf-8 -*-
"""`pack/recover-user-data.py` 的安全不变量守卫。

## 为什么需要（2026-09-29 发现）

这是全仓**唯一直接写用户真实学习数据**的脚本（`~/.medkit/library/medkit.db`），
此前**零测试覆盖**。它写错等于不可逆地污染用户的错题/知识点/复习卡/辅导会话。

脚本本身设计是对的（先快照、`INSERT OR IGNORE`、`--dry-run`、只挑污染前的行），
但**这些性质没有任何东西钉住**——下次谁改一行，可能就把
「不覆盖」改成「覆盖」、把「先快照」挪到写入之后，而没有任何用例会红。

## 本文件断言什么（都是"改了就出事"的性质）

1. **必须先快照再写库**——快照失败必须中止（返回非零），不得继续写；
2. **必须幂等**——已存在的 id 不得被覆盖（依赖 `INSERT OR IGNORE` +
   目标表有 `id` 主键这个双重前提）；
3. **污染判定必须保守**——无时间戳/未知字段一律判为"不可信"（不恢复）；
4. **`--dry-run` 必须真的零写入**。

## 明确不测什么

不测"恢复得对不对"（那要看取证结论与真实备份目录，属人工核对）。
本文件只锁**破坏性安全**，这是零覆盖下性价比最高的部分。
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pack" / "recover-user-data.py"


def _load():
    spec = importlib.util.spec_from_file_location("recover_user_data", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 污染判定

def test_pre_pollution_requires_timestamp():
    """无时间戳 / 字段缺失一律判**不可信**（不恢复）。

    理由：真实行都带 14:34 / 18:38 这类时间戳；没有时间戳的行
    无法证明它来自污染前，**保守起见不恢复**。
    放宽这条 = 可能把测试垃圾恢复进用户库。
    """
    m = _load()
    assert m._is_pre_pollution({"id": "m_1", "created_at": ""}, "created_at") is False
    assert m._is_pre_pollution({"id": "m_1"}, "created_at") is False
    assert m._is_pre_pollution({"id": "m_1", "created_at": None}, "created_at") is False


def test_pre_pollution_boundary_is_exclusive():
    """恰好等于 `POLLUTION_START` 的时刻**不算**污染前（判据用 `<`）。

    边界必须偏保守：宁可漏恢复一条，也不要把污染窗口起点的行恢复进来。
    """
    m = _load()
    assert m._is_pre_pollution(
        {"id": "m_1", "created_at": m.POLLUTION_START}, "created_at") is False
    # 早一秒 → 可信
    assert m._is_pre_pollution(
        {"id": "m_1", "created_at": "2026-08-27T20:39:59"}, "created_at") is True


def test_pollution_id_prefix_always_rejected():
    """带污染 id 前缀的行**即使时间戳看着正常**也必须拒。

    实测背景：同一次 pytest 运行生成的 id 前缀相同（毫秒时间戳），
    光看时间戳不足以排除——前缀是第二道闸。
    """
    m = _load()
    for tid in ("m_1787834000000", "kp_1787834839551_1"):
        assert m._is_pre_pollution(
            {"id": tid, "created_at": "2026-08-27T14:34:00"}, "created_at") is False, tid


# ------------------------------------------------------------------ 幂等前提

def test_insert_or_ignore_needs_primary_key():
    """`INSERT OR IGNORE` 的"不覆盖"效果**依赖目标表有 id 主键**。

    这是本脚本最容易被误解的一点：无主键时 `OR IGNORE` 会**照插不误**
    （实测：无 PK 表两次插入同 id → 两行都在）。
    所以脚本的"已存在则跳过（不覆盖）"承诺，实际由
    「`INSERT OR IGNORE`」+「表有 `id` PRIMARY KEY」两个前提共同保证。

    本用例锁死这个认知，防止有人为了别的目的把脚本改成裸 INSERT，
    或把目标表改成无主键。
    """
    # 有主键：忽略 → 不覆盖
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id TEXT PRIMARY KEY, data TEXT)")
    c.execute("INSERT INTO t VALUES ('a','v1')")
    c.execute("INSERT OR IGNORE INTO t VALUES ('a','v2')")
    assert c.execute("SELECT data FROM t").fetchall() == [("v1",)], "有主键应忽略新值"
    # 无主键：照插 → 会重复（证明"不覆盖"不是 OR IGNORE 白送的）
    c.execute("CREATE TABLE t2 (id TEXT, data TEXT)")
    c.execute("INSERT INTO t2 VALUES ('a','v1')")
    c.execute("INSERT OR IGNORE INTO t2 VALUES ('a','v2')")
    assert len(c.execute("SELECT * FROM t2").fetchall()) == 2, "无主键应重复插入"
    c.close()


@pytest.mark.parametrize("table", ["mistakes", "knowledge", "review_cards", "tutor_sessions"])
def test_real_schema_has_id_primary_key(table):
    """真实 schema 里这四张表都必须有 `id` 主键（脚本幂等的前提）。

    真身：`medkit/core/db.py` 的 `_tbl()` 统一生成 `id TEXT PRIMARY KEY`。
    这里**按包路径 import 真模块**（而非按文件路径 load——
    `db.py` 里有 `from . import config`，按路径 load 会 `ImportError`，
    这是本文件第一版踩到的坑），再检查 `_V1_UP` 的建表语句。
    """
    sys.path.insert(0, str(ROOT))
    from medkit.core import db as dbmod

    ddl = "\n".join(dbmod._V1_UP)
    assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl, f"{table} 不在 V1 建表语句里"
    seg = ddl.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(")", 1)[0]
    assert "id TEXT PRIMARY KEY" in seg, f"{table} 缺 id 主键——脚本的幂等承诺失效"


# ------------------------------------------------------------------ 快照先于写入

def test_snapshot_happens_before_any_write_to_live_db():
    """源码顺序：快照 `copytree` 必须出现在 `live.commit()` **之前**。

    这条不能靠"代码看起来是对的"——写入是不可逆的，
    必须有个判据在顺序被调换时变红。用行号做判据（简单且够用）。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    snap_idx = src.index("shutil.copytree")
    commit_idx = src.index("live.commit()")
    assert snap_idx < commit_idx, (
        "快照（copytree）出现在 live.commit() 之后——"
        "一旦写入出错就没有回退点了。顺序必须：先快照、再写库。"
    )


def test_snapshot_failure_aborts():
    """快照失败必须**中止**（不得吞掉异常继续写库）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    # 快照的 try/except 里必须 return（中止），不能 pass
    tail = src.split("shutil.copytree", 1)[1].split('print("[2/2]', 1)[0]
    assert "except Exception" in tail, "快照没有异常保护"
    assert "return 2" in tail, "快照失败后没有中止（应当 return 非零）"
    assert "pass" not in tail.replace("shutil", ""), "快照失败被 pass 吞掉了"


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """`--dry-run` 必须真的一次写操作都不做。

    同时验证"备份目录不存在 → 返回 2 中止"这条入口守卫。

    注意夹具：`_collect_sources` 会 WAL 回放备份里的 `medkit.db` 并查
    `review_cards`，所以备份目录里得有一个**含该表**的 db；
    否则会在预检阶段 `no such table` 崩掉（本文件第一版踩到）。
    """
    m = _load()
    # 1) 备份目录不存在 ⇒ 返回 2
    monkeypatch.setattr(sys, "argv", ["prog", "--backup", str(tmp_path / "nope")])
    assert m.main() == 2

    # 2) 存在备份但 --dry-run ⇒ 返回 0 且不写库
    backup = tmp_path / "bk"
    backup.mkdir()
    (backup / "mistakes.json").write_text("[]", encoding="utf-8")
    (backup / "knowledge.json").write_text("[]", encoding="utf-8")
    # 备份库里建出脚本会查的两张表（否则 _collect_sources 崩）
    bconn = sqlite3.connect(str(backup / "medkit.db"))
    bconn.execute("CREATE TABLE review_cards (id TEXT PRIMARY KEY, data TEXT)")
    bconn.execute("CREATE TABLE knowledge (id TEXT PRIMARY KEY, data TEXT)")
    bconn.commit()
    bconn.close()

    monkeypatch.setattr(m, "LIB", tmp_path / "lib")
    m.LIB.mkdir(parents=True)
    conn = sqlite3.connect(str(m.LIB / "medkit.db"))
    conn.execute("CREATE TABLE mistakes (id TEXT PRIMARY KEY, data TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(sys, "argv",
                        ["prog", "--backup", str(backup), "--dry-run"])
    assert m.main() == 0
    # 判据 = 库文件的**字节**没变（不是"行数没变"——行数变不了但页可能被改）
    before = (m.LIB / "medkit.db").read_bytes()
    monkeypatch.setattr(sys, "argv",
                        ["prog", "--backup", str(backup), "--dry-run"])
    m.main()
    assert (m.LIB / "medkit.db").read_bytes() == before, "dry-run 竟然改了库文件"
