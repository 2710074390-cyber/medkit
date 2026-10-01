"""B8 回归（R8+W）：P2 数据可靠性四项。

- **S3-19**：素材会话必须**原子写**（原裸 `write_text`，崩溃留半截 JSON）。
- **S2-28**：`import_from_json` 的 JSON 改名必须在 **commit 成功之后**（原在 tx 块内 →
  回滚时 DB 回滚了但文件已改名，双轨分叉）。
- **S3-21**：`meta.json` 的读-改-写必须持锁且**只改指定字段**（原各写各的 → lost update）。
- **S3-23**：`substeps.jsonl` 裁剪必须**原子**（原裸 `write_text` 覆盖 → 崩溃留截断 jsonl）。
"""

import ast
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import (
    db,  # noqa: E402
    sessions,  # noqa: E402
)
from medkit.core import orchestrator as orch  # noqa: E402

# ---------------------------------------------------------------- S3-19

def test_save_session_is_atomic(tmp_path, monkeypatch):
    """会话落盘后：内容可解析、且不留临时文件（原子写的直接证据）。"""
    monkeypatch.setattr(sessions, "_dir", lambda: tmp_path)
    meta = sessions.save_session("素材A", "textbook",
                                [{"sid": "S001", "title": "第一章", "text": "内容"}])
    files = list(tmp_path.iterdir())
    assert len(files) == 1, f"应只有一个会话文件，实际 {[f.name for f in files]}"
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["id"] == meta["id"] and data["slice_count"] == 1
    assert not [f for f in files if f.suffix == ".tmp" or ".tmp" in f.name], "残留临时文件"


def test_save_session_source_uses_atomic_writer():
    """源码级守卫：会话写盘不得回退成裸 write_text。

    ## 旧判据是假绿（2026-09-29 R20 修）

    旧版：

        assert "write_json_atomic" in src
        assert ").write_text(" not in src.replace("write_json_atomic", "")

    两个毛病：
    ① `"write_json_atomic" in src` 是**纯文本子串** —— 真身 `sessions.py:63`
       的**注释**里就有这个词（「统一走 fsutil.write_json_atomic」），
       所以把真调用（`:65`）整行删掉、注释留着，断言**照样通过**（已实测）。
    ② 负向那句用 `src.replace("write_json_atomic", "")` 把名字**全局抹掉**再找
       `.write_text(` —— 既误伤了正常代码，又没检查调用形态。

    现改为 AST：`save_session` 函数体里必须有**真实的** `write_json_atomic(...)`
    调用（`ast.Call`），且**整个模块**不得有 `.write_text(...)` 调用。
    这样注释/docstring 里的词不算数。
    """
    import ast

    src = (ROOT / "medkit" / "core" / "sessions.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "save_session"), None)
    assert fn is not None, "sessions.py 里没有 save_session 函数"
    called = [
        n.func.attr if isinstance(n.func, ast.Attribute) else
        (n.func.id if isinstance(n.func, ast.Name) else None)
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    ]
    assert "write_json_atomic" in called, (
        "save_session 里没有真实的 write_json_atomic(...) 调用"
        "（注释里提过不算——旧判据就是被注释骗过的）")

    # 全模块不得再出现 .write_text(...) 调用
    bad = [n for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "write_text"]
    assert not bad, (
        f"sessions.py 里仍有裸 write_text 落盘（第 {[n.lineno for n in bad]} 行）："
        "崩溃/断电会留下半截 JSON")


# ---------------------------------------------------------------- S2-28

@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "medkit.db")
    db.reset_conn()
    return tmp_path


def test_import_rename_happens_after_commit(iso_db, monkeypatch):
    """tx 内失败 → JSON 必须**保持原样**（不得已改名）。"""
    json_path = iso_db / "mistakes.json"
    json_path.write_text(json.dumps([{"id": "m1", "subject": "儿科"}]), encoding="utf-8")

    def _boom(cur, table, rec, cols):
        raise RuntimeError("模拟导入期故障")

    monkeypatch.setattr(db, "put_row", _boom)
    with pytest.raises(RuntimeError):
        db.import_from_json()
    assert json_path.exists(), "tx 失败却已改名 = 双轨分叉（S2-28 回归）"
    assert not list(iso_db.glob("mistakes.json.pre-db-import-*.bak"))


def test_import_rename_happens_on_success(iso_db):
    """对照组：成功路径仍照常改名留档。"""
    json_path = iso_db / "mistakes.json"
    json_path.write_text(json.dumps([{"id": "m1", "subject": "儿科"}]), encoding="utf-8")
    db.migrate()
    db.import_from_json()
    assert not json_path.exists()
    assert list(iso_db.glob("mistakes.json.pre-db-import-*.bak")), "成功后应改名留档"


# ---------------------------------------------------------------- S3-21

def test_update_meta_preserves_other_fields(tmp_path):
    """只改指定字段：连续两次更新不得互相覆盖（原整份覆盖 → lost update）。"""
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps({"pid": "p", "stage": "quota"}), encoding="utf-8")
    orch._update_meta(mp, stage="generating")
    orch._update_meta(mp, contract_warnings=3)
    got = json.loads(mp.read_text(encoding="utf-8"))
    assert got == {"pid": "p", "stage": "generating", "contract_warnings": 3}


def test_update_meta_acquires_lock(tmp_path, monkeypatch):
    """接线级：读-改-写必须在 `_META_LOCK` 内完成（用 spy 锁直接观测持有状态）。"""
    class SpyLock:
        def __init__(self):
            self.entries = 0
            self.held = False

        def __enter__(self):
            self.entries += 1
            self.held = True
            return self

        def __exit__(self, *a):
            self.held = False
            return False

    spy = SpyLock()
    monkeypatch.setattr(orch, "_META_LOCK", spy)
    mp = tmp_path / "meta.json"
    mp.write_text("{}", encoding="utf-8")
    seen = {}
    real = orch._write_json_atomic

    def _spy_write(path, data):
        seen["held_during_write"] = spy.held
        return real(path, data)

    monkeypatch.setattr(orch, "_write_json_atomic", _spy_write)
    orch._update_meta(mp, stage="done")
    assert spy.entries == 1, "读-改-写必须持锁（S3-21 回归）"
    assert seen["held_during_write"] is True, "写盘必须发生在锁内"
    assert spy.held is False


def test_concurrent_meta_updates_lose_nothing(tmp_path):
    """并发压测：两线程各写自己的字段，最终两者都在（持锁后必成立）。"""
    mp = tmp_path / "meta.json"
    mp.write_text("{}", encoding="utf-8")

    def _worker(key, n):
        for i in range(n):
            orch._update_meta(mp, **{key: i})

    ts = [threading.Thread(target=_worker, args=("a", 30)),
          threading.Thread(target=_worker, args=("b", 30))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = json.loads(mp.read_text(encoding="utf-8"))
    assert "a" in got and "b" in got, f"并发更新丢字段：{got}"


# ---------------------------------------------------------------- S3-23

def test_substep_trim_is_atomic(tmp_path):
    """超过 KEEP 后裁剪：不留 .trim 临时文件，且文件仍是完整 JSONL。"""
    base = tmp_path / "proj"
    base.mkdir()
    for i in range(orch._SUBSTEP_KEEP + 20):
        orch._substep(base, "generating", f"s{i}", f"步骤{i}", "running")
    path = base / "substeps.jsonl"
    assert path.exists()
    assert not list(base.glob("*.trim")), "残留临时文件 = 裁剪非原子"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) <= orch._SUBSTEP_KEEP
    for ln in lines:
        json.loads(ln)          # 每行都必须是完整 JSON（无截断）


def test_substep_source_uses_atomic_replace():
    """源码级守卫：裁剪不得回退成裸 `write_text` 覆盖。

    ## 为什么改成 AST（2026-10-01，C3 门禁加固）

    旧版是**位置切段 + 子串在场**：

    ```python
    seg = src[src.index("def _substep("):src.index("def _substeps_terminate(")]
    assert ".replace(path)" in seg
    ```

    注入实测（`.workbuddy-ai/tmp/diag_substep_guard.py`）**三个方向都坏**：

    | 注入 | 旧判据 | 说明 |
    |---|---|---|
    | `_tmp.replace( path )`（只加空格） | ❌ 假红 | 绑死书写格式 |
    | `def _substep (`（只加空格） | ❌ 假红 | 切段锚点绑死签名文本 |
    | 删掉真调用、**注释里留 `.replace(path)` 字样** | ❌ **假绿（P0）** | 注释骗过子串断言——正是它要防的「回退成裸覆盖」被放过 |

    现按**结构**判：找到 `_substep` 的函数体，取出 `.replace(X)` 调用，
    要求「**先写临时文件、再 replace 过去**」：

    - 存在 `.replace(...)` 调用（原子替换的正面证据）；
    - 其**接收者**（临时文件）必须被 `write_text` 写过；
    - 其**实参**（最终文件）**不得**被 `write_text` 直接写过（那正是要防的裸覆盖）。

    变量名、空格、注释一律无关；注释也不再能冒充调用。
    """
    tree = ast.parse((ROOT / "medkit" / "core" / "orchestrator.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_substep"), None)
    assert fn is not None, "找不到 `_substep` 函数——改名了？"

    def _calls(attr: str) -> list[ast.Call]:
        return [c for c in ast.walk(fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == attr]

    repl = [c for c in _calls("replace") if len(c.args) == 1]
    assert repl, "`_substep` 里没有 `.replace(...)` 调用——裁剪不再是原子的"
    written = {ast.unparse(c.func.value) for c in _calls("write_text")}
    for c in repl:
        tmp = ast.unparse(c.func.value)     # 临时文件
        final = ast.unparse(c.args[0])      # 最终文件
        assert tmp in written, (
            f"`.replace` 的接收者 {tmp!r} 没有被 `write_text` 写过——"
            "不是「先写临时文件、再原子替换」"
        )
        assert final not in written, (
            f"裁剪直接 `write_text` 覆盖了最终文件 {final!r}——非原子（崩溃留截断 jsonl）"
        )
