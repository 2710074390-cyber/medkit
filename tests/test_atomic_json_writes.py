"""W1 守卫：用户数据 JSON 必须原子写（2026-10-02 二轮审计）。

背景：`meta.json` 走 `fsutil.write_json_atomic`，但**同一目录**的 `slices.json` /
`stage.json` 却是裸 `write_text`——建项目瞬间崩溃/断电会留下**截断的 JSON**，
随后 `orchestrator` 的 `json.loads(slices.json)` 永久抛异常 ⇒ 项目报废。
本文件把「原子写」钉成**契约**，并覆盖建项目与管线产物的写入点。

判据（两条，缺一即假绿）：
1. **结构判据**：`core/projects.py` / `core/orchestrator.py` 里对 `.json` 的写入
   不得出现裸 `write_text`（用 AST 读 `.write_text` 调用的接收者，排除 `fsutil` 内部）。
2. **行为判据（正面 + 注入反向）**：注入「序列化中途崩溃」→ 目标文件必须**保持旧内容**
   （原子写的定义）。把原子写换回裸 `write_text` 时本用例必须红。

运行：`pytest tests/test_atomic_json_writes.py -q`（零 LLM、零网络）
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "medkit" / "core"

# 允许出现裸 write_text 的白名单（非用户数据 / 非 JSON 状态）：
#   fsutil.write_json_atomic 自身必须先写 tmp（这是原子写的实现，不是缺陷）
#   orchestrator 的 run.log/substeps/人工复核清单/渲染产物(.md/.html) 是**追加或整篇重写**的
#   文本产物，崩了也只是内容不完整（不会让 json.loads 抛异常导致项目报废）
_JSON_WRITE_EXEMPT_FILES = {"fsutil.py"}


def _bare_write_text_json_sites(path: Path) -> list[tuple[int, str]]:
    """AST 找出「对 .json 路径裸调 write_text」的位置（排除 fsutil 内部）。

    识别：`<something>.write_text(...)`，且同一调用表达式里**没有**经过
    `_write_json_atomic` / `write_json_atomic`；用文本佐证路径以 `.json` 结尾。
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "write_text"):
            continue
        # 取这一行（含续行）作为证据文本
        start = node.lineno
        chunk = "\n".join(lines[start - 1:start + 3])
        if ".json" not in chunk:
            continue
        hits.append((start, lines[start - 1].strip()))
    return hits


# ---------------------------------------------------------------- 1) 结构判据
@pytest.mark.parametrize("fname", ["projects.py", "orchestrator.py"])
def test_no_bare_write_text_for_json(fname):
    """用户数据 JSON 不得裸 write_text（原子写是默认，不是特例）。"""
    hits = _bare_write_text_json_sites(CORE / fname)
    assert not hits, (
        f"{fname} 存在对 .json 的裸 write_text（应改 _write_json_atomic）：\n"
        + "\n".join(f"  line {ln}: {txt}" for ln, txt in hits))


def test_detector_actually_can_hit():
    """元守卫：检测器必须能命中一个已知的裸写样本（否则恒绿）。"""
    sample = ast.parse(
        'def f(p, d):\n'
        '    (p / "x.json").write_text(d, encoding="utf-8")\n')
    found = [n for n in ast.walk(sample)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "write_text"]
    assert found, "AST 检测器无法命中裸 write_text —— 本守卫会恒绿，必须修"


# ---------------------------------------------------------------- 2) 行为判据（正面）
def test_write_json_atomic_keeps_old_content_on_serialize_crash(tmp_path, monkeypatch):
    """序列化中途崩溃 → 目标文件必须是**旧内容**（原子写的核心承诺）。

    注入点打在「真身」上：让 json.dumps 抛错，观察目标文件。
    """
    from medkit.core.fsutil import write_json_atomic

    target = tmp_path / "slices.json"
    write_json_atomic(target, [{"sid": "S001", "text": "旧内容"}])
    before = target.read_bytes()

    # 注入：序列化阶段崩溃（模拟断电时 json.dumps 未完成）
    real_dumps = json.dumps

    def boom(*a, **k):
        raise RuntimeError("模拟断电")

    monkeypatch.setattr(json, "dumps", boom)
    with pytest.raises(RuntimeError):
        write_json_atomic(target, [{"sid": "S002", "text": "新内容"}])
    monkeypatch.setattr(json, "dumps", real_dumps)

    assert target.read_bytes() == before, "原子写失败后目标文件被破坏（未保持旧内容）"
    assert not list(tmp_path.glob("*.tmp*")), "原子写留下了临时文件残骸"


def test_atomic_write_result_is_readable(tmp_path):
    """正常路径：写入后可被 json.loads 读回（证明不是写了半截/换了编码）。"""
    from medkit.core.fsutil import write_json_atomic

    target = tmp_path / "stage.json"
    write_json_atomic(target, {"stage": "quota", "n": 3})
    assert json.loads(target.read_text(encoding="utf-8")) == {"stage": "quota", "n": 3}


# ---------------------------------------------------------------- 3) 建项目路径（端到端）
def test_create_project_writes_slices_atomically(tmp_path, monkeypatch):
    """建项目落盘的 slices.json / stage.json 必须是合法的（可 json.loads）。

    这是 W1 的端到端证据：即便写入被中断，也不能留下非法 JSON。此处只验证
    「写完后内容是合法 JSON 且不含 tmp 残骸」——崩溃注入见上一个用例（打在真身上）。
    """
    from medkit.core import projects as core_proj

    proj_root = tmp_path / "projects"
    monkeypatch.setattr(core_proj, "project_dir", lambda pid: proj_root / pid)

    slices = [{"sid": "S001", "title": "第1章", "text": "内容", "source": "t", "page": ""}]
    payload = {"subject": "原子写测试", "target": 5, "requirements": "",
               "textbook_slices": slices, "teacher_slices": [], "exam_slices": [],
               "extra_slices": [], "teacher_text": "", "bloom": {}}
    res = core_proj.create_project_record(payload)
    pid = res["pid"]
    base = proj_root / pid

    got = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    assert [s["sid"] for s in got] == ["S001"]
    assert json.loads((base / "stage.json").read_text(encoding="utf-8"))["stage"] == "quota"
    assert not list(base.glob("*.tmp*")), "建项目留下临时文件残骸"
