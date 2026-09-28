# -*- coding: utf-8 -*-
"""`pack/rollback-json-track.py` 的安全不变量守卫。

## 为什么需要（2026-09-29 发现）

这是**第二个直接动用户真实库**的运维脚本（SQLite → JSON 轨回滚），
此前零测试覆盖。它的破坏性动作是「把 `medkit.db` 移走」——
DB 文件一走，域模块立刻按 `_store_is_sql()` 回落 JSON 轨。
设计本身是对的（默认 dry-run、`--yes` 才动手、用 `rename` 不用删除、
绝不覆盖已存在的活文件），但**这些性质没有东西钉住**。

## 本文件断言什么

1. **默认必须是 dry-run**（不带 `--yes` 时零改动）——这是最重要的安全默认；
2. **移走 ≠ 删除**：用 `rename`，且落点保留 `.rollback-<ts>` 可反悔；
3. **绝不覆盖**已存在的活文件（计划期 + 执行期双重检查）；
4. **`_live_name()` 解析**正确（只认自己认识的备份形态，防误把无关文件当恢复源）；
5. **同一活文件有多个备份时取最早那个**（确定性，不随 glob 顺序漂移）。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pack" / "rollback-json-track.py"


def _load():
    spec = importlib.util.spec_from_file_location("rollback_json_track", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 默认安全

def test_default_is_dry_run(tmp_path, monkeypatch, capsys):
    """**不带 `--yes` 时必须零改动**——这是本脚本最重要的安全默认。

    判据不是"返回 0"，而是**目录内容逐字节未变**。
    """
    m = _load()
    lib = tmp_path / ".medkit" / "library"
    lib.mkdir(parents=True)
    (lib / "medkit.db").write_bytes(b"FAKE-DB")
    (lib / "medkit.db-wal").write_bytes(b"WAL")
    (lib / "mistakes.json.pre-db-20260827-201247.bak").write_text(
        '[{"id":"m_1"}]', encoding="utf-8")

    before = {p.name: p.read_bytes() for p in lib.iterdir()}

    monkeypatch.setattr(sys, "argv", ["prog", "--home", str(tmp_path / ".medkit")])
    assert m.main() == 0

    after = {p.name: p.read_bytes() for p in lib.iterdir()}
    assert after == before, "未加 --yes 却改动了文件（默认必须是 dry-run）"


def test_yes_renames_db_and_never_deletes(tmp_path, monkeypatch):
    """`--yes` 时：db 文件被**改名保留**（不是删除），且备份被恢复成活文件。"""
    m = _load()
    lib = tmp_path / ".medkit" / "library"
    lib.mkdir(parents=True)
    (lib / "medkit.db").write_bytes(b"FAKE-DB")
    (lib / "mistakes.json.pre-db-20260827-201247.bak").write_text(
        '[{"id":"m_1"}]', encoding="utf-8")

    monkeypatch.setattr(sys, "argv",
                        ["prog", "--home", str(tmp_path / ".medkit"), "--yes"])
    m.main()

    names = {p.name for p in lib.iterdir()}
    assert "medkit.db" not in names, "db 文件必须被移走（_store_is_sql 才会回落 JSON）"
    assert any(n.startswith("medkit.db.rollback-") for n in names), \
        "db 文件未被保留为 .rollback-<ts>——删除就不可反悔了"
    assert "mistakes.json" in names, "备份未恢复成活文件名"
    assert (lib / "mistakes.json").read_text(encoding="utf-8") == '[{"id":"m_1"}]'


def test_never_overwrites_existing_live_file(tmp_path, monkeypatch):
    """已存在的活文件**绝不被覆盖**——用户当前数据优先于旧备份。

    ## 这道保护有两层，都要在（2026-09-29 反向验证发现）

    脚本在**两处**独立检查活文件是否已存在：
    - 计划期（line 83）：`if target.exists(): continue`（不列入恢复计划）
    - 执行期（line 103）：`if dst.exists(): continue`（复查，防计划后有并发写入）

    反向验证时我只删掉了计划期那处 → **用例仍然绿**，
    因为执行期那处把活文件护住了。这不是守卫的洞（是防御纵深在生效），
    但说明**只测一层会漏掉"另一层被删"**。

    故本用例在**两个层次**各做一次注入式判据（见下方两段注释）。
    """
    m = _load()
    lib = tmp_path / ".medkit" / "library"
    lib.mkdir(parents=True)
    (lib / "mistakes.json").write_text('[{"id":"CURRENT"}]', encoding="utf-8")
    (lib / "mistakes.json.pre-db-20260827.bak").write_text(
        '[{"id":"OLD-BAK"}]', encoding="utf-8")

    monkeypatch.setattr(sys, "argv",
                        ["prog", "--home", str(tmp_path / ".medkit"), "--yes"])
    m.main()

    got = (lib / "mistakes.json").read_text(encoding="utf-8")
    assert got == '[{"id":"CURRENT"}]', "活文件被备份覆盖了（不可接受）"


def test_overwrite_protection_exists_at_both_phases():
    """两层覆盖保护都必须**仍存在于源码里**（结构性判据）。

    上一条用例只证明"行为正确"，无法区分是**两层都在**还是**恰好剩一层**。
    本用例把两层分别钉住——任何一层被删都会红，
    这样"删掉一层但另一层兜住"不会伪装成"保护完好"。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "if target.exists():" in src, (
        "计划期的覆盖保护消失（`if target.exists()`）——"
        "恢复计划会把已存在的活文件列进去"
    )
    assert "if dst.exists():" in src, (
        "执行期的覆盖复查消失（`if dst.exists()`）——"
        "计划期与执行期之间若有并发写入，活文件会被覆盖"
    )
    # 执行期必须有"复查"语义的注释/行为，防止被改成无条件 copy
    tail = src.split("for src, dst in restores:", 1)
    assert len(tail) == 2, "恢复循环不见了"
    assert "continue" in tail[1][:400], "执行期复查没有 continue（会无条件覆盖）"


# ------------------------------------------------------------------ 纯函数

def test_live_name_parsing():
    """`_live_name` 只认自己认识的备份形态，其余返回 None。

    防的是把无关的 `.bak` 当恢复源——那会把不相关的数据写成活文件。
    """
    m = _load()
    P = Path
    assert m._live_name(P("mistakes.json.pre-db-20260827-201247.bak")) == "mistakes.json"
    assert m._live_name(P("knowledge.json.pre-db-import-20260827-201705-577e.bak")) \
        == "knowledge.json"
    # 不认识的一律 None
    assert m._live_name(P("random.bak")) is None
    assert m._live_name(P("mistakes.json.bak")) is None
    assert m._live_name(P(".pre-db-x.bak")) is None       # 前缀位置在 0 → 无活文件名
    assert m._live_name(P("mistakes.json.pre-db-20260827")) is None   # 不以 .bak 结尾


def test_earliest_backup_wins_on_duplicates(tmp_path, monkeypatch):
    """同一活文件有多个备份时，取**排序最早**那个（确定性）。

    glob 顺序不保证稳定；若取"碰到的第一个"，同一份库在不同机器上
    可能恢复出不同内容——这会让人无法复现问题。
    """
    m = _load()
    lib = tmp_path / ".medkit" / "library"
    lib.mkdir(parents=True)
    (lib / "medkit.db").write_bytes(b"DB")
    (lib / "mistakes.json.pre-db-20260827-201247.bak").write_text(
        '[{"id":"EARLIEST"}]', encoding="utf-8")
    (lib / "mistakes.json.pre-db-20260828-004033.bak").write_text(
        '[{"id":"LATER"}]', encoding="utf-8")

    monkeypatch.setattr(sys, "argv",
                        ["prog", "--home", str(tmp_path / ".medkit"), "--yes"])
    m.main()

    got = (lib / "mistakes.json").read_text(encoding="utf-8")
    assert got == '[{"id":"EARLIEST"}]', "未取最早的备份作恢复源（结果不确定）"


def test_missing_library_dir_is_noop(tmp_path, monkeypatch):
    """库目录不存在 → 返回 0 且不动作（不得报错/不得创建目录）。"""
    m = _load()
    home = tmp_path / "empty"
    home.mkdir()
    monkeypatch.setattr(sys, "argv", ["prog", "--home", str(home)])
    assert m.main() == 0
    assert list(home.iterdir()) == [], "库目录不存在时竟然创建了内容"
