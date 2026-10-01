"""**全仓零断言用例审计**（R30）：`test_*` 函数必须含实质断言，否则红。

## 为什么需要这条守卫

pytest 会把**零断言**的 `test_*` 函数当用例收集并判「通过」——它虚增「N 项 pytest」计数，
并让「无断言用例」这一形态在后续扫描里越藏越深（R23 就是这个问题）。
R23 的修复是**逐文件**做的，没有全仓守卫；本仓已有的 `test_no_silent_skip_in_doc_guards`
**只管 `test_docs_coverage.py` 一个文件**。本文件把它推广到全仓。

## 「实质断言」的口径（R23 的三个坑都避开）

认这四种形态，且**必须 `ast.walk` 整个函数体**（只看顶层语句会漏掉**循环体内**的 assert）：
- `assert <任意表达式>`（**裸 `assert <Name>` 也算**——它可证伪）
- `raise AssertionError(...)`（合规断言形态，尤其在「前提分流」里用：CI 跳过 / 本地红）
- `pytest.raises(...)` / `pytest.fail(...)` / `pytest.xfail(...)`

不认：docstring、纯调用、`assert True`（后者虽形态合法但**恒真**——由 `assert True`
单独列出，因为它是「写了等于没写」）。

## 允许的例外：**登记制**

唯一允许的形态是「断言在**同文件共用的辅助函数**里」——此时扫描器看不见，
必须显式登记（并防腐烂）。这与 `test_skip_audit` 同一套形态：
**新增未登记即红 · 登记不得腐烂**。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TESTS_DIR = ROOT / "tests"
MIN_SCAN_FILES = 60

# (相对路径, 函数名) → 理由。**新增未登记即红；登记了但代码里没有也红。**
REGISTERED: dict[tuple[str, str], str] = {
    ("tests/browser/test_learn_views.py", "test_learn_view_remembered_after_reload"):
        "断言在**同文件共用的辅助** `_assert_active()` 里（扫描器只看用例自身函数体，看不见它）",
}


def _is_assertion_like(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        # `assert True` 恒真 = 写了等于没写；裸 Name / 其它表达式都算实质断言
        return not (isinstance(node.test, ast.Constant) and node.test.value is True)
    if isinstance(node, ast.Raise):
        e = node.exc
        return (isinstance(e, ast.Call) and isinstance(e.func, ast.Name)
                and e.func.id == "AssertionError")
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            return f.attr in ("raises", "fail", "xfail")
        if isinstance(f, ast.Name):
            return f.id in ("raises", "fail")
    return False


def _has_real_assertion(fn: ast.AST) -> bool:
    """函数体（**整个子树**，不只顶层）里是否存在实质断言。"""
    return any(_is_assertion_like(n) for n in ast.walk(fn))


def _tracked_test_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "tests"], cwd=str(ROOT),
                         capture_output=True, text=True)
    names = [n for n in (out.stdout or "").splitlines() if n.strip()]
    return [ROOT / n for n in names
            if n.endswith(".py") and ("/test_" in n or n.endswith("conftest.py"))]


def _scan() -> set[tuple[str, str]]:
    """全仓扫出**零实质断言**的 `test_*` 函数：`{(relpath, 函数名)}`。"""
    found: set[tuple[str, str]] = set()
    for path in _tracked_test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if not _has_real_assertion(node):
                found.add((rel, node.name))
    return found


def test_detector_really_detects():
    """**元守卫**：检测器必须真的能命中——用**内存构造**的样本自证。

    不这样做的话，一个"永远返回空集"的检测器会让下面两条断言在空集上恒真
    （方向 D：元守卫不得依赖真身当前内容，必须是自造模板串）。
    """
    samples = {
        "只有 docstring": 'def test_x():\n    """只有文档。"""\n',
        "只有调用": "def test_x():\n    do_something()\n",
        "assert True": "def test_x():\n    assert True\n",
        "空函数体": "def test_x():\n    pass\n",
    }
    for label, code in samples.items():
        fn = next(n for n in ast.walk(ast.parse(code))
                  if isinstance(n, ast.FunctionDef))
        assert not _has_real_assertion(fn), f"检测器漏报：{label}"

    ok_samples = {
        "assert 表达式": "def test_x():\n    assert a == 1\n",
        "裸 assert Name": "def test_x():\n    assert hits\n",
        "循环体内的 assert": "def test_x():\n    for i in xs:\n        assert i\n",
        "pytest.raises": "def test_x():\n    with pytest.raises(ValueError):\n        f()\n",
        "pytest.fail": "def test_x():\n    pytest.fail('nope')\n",
        "raise AssertionError": "def test_x():\n    raise AssertionError('nope')\n",
    }
    for label, code in ok_samples.items():
        fn = next(n for n in ast.walk(ast.parse(code))
                  if isinstance(n, ast.FunctionDef))
        assert _has_real_assertion(fn), f"检测器误报：{label}"


def test_scan_face_is_not_empty():
    """元守卫：扫描面不能塌缩（否则下面两条在空集上恒真）。"""
    files = _tracked_test_files()
    assert files, "扫描面为空（git ls-files tests 拿不到？）"
    assert len(files) >= MIN_SCAN_FILES, f"扫描面只有 {len(files)} 个文件（下限 {MIN_SCAN_FILES}）"
    on_disk = {p for p in TESTS_DIR.rglob("*.py")
               if "test_" in p.name or p.name == "conftest.py"}
    assert not (on_disk - set(files)), \
        f"磁盘上有但 git 索引里没有（新增未 git add）：{sorted(on_disk - set(files))}"


def test_every_assertion_free_test_is_registered():
    """**新增零断言用例即红**（pytest 会把它当用例收集并判「通过」，虚增计数）。"""
    found = _scan()
    unregistered = sorted(found - set(REGISTERED))
    assert not unregistered, (
        "以下 `test_*` 函数**没有任何实质断言**——pytest 会判它「通过」，"
        "虚增计数且让这类形态越藏越深：\n  "
        + "\n  ".join(f"{rel}::{name}" for rel, name in unregistered)
        + "\n（断言在同文件辅助函数里时，请登记进本文件 REGISTERED 并写明理由。）")


def test_registered_entries_are_not_rotted():
    """**登记不得腐烂**：登记了但已不再是零断言 ⇒ 红（否则白名单只增不减）。"""
    found = _scan()
    rotten = sorted(set(REGISTERED) - found)
    assert not rotten, (
        "以下登记项现在**已经有实质断言**（或函数已改名/删除），请从 REGISTERED 里删掉："
        f"{rotten}")
