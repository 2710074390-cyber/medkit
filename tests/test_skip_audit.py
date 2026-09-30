"""**全仓 skip 审计**（R30）：每一处 skip 都必须显式登记，否则红。

## 为什么需要这条守卫

项目长期声明「**现已全仓 0 skip**」，但那是**一次性扫描的结论**——没有守卫背书
⇒ 之后任何人加一个 `@pytest.mark.skipif(...)` 都无人拦。
本仓已有的 `test_no_silent_skip_in_doc_guards` **只管 `test_docs_coverage.py` 一个文件**，
覆盖不到别处（本轮实测：`test_render_markdown.py` 的 node 探测、
`test_sign_release.py` 的 PowerShell 探测都是**无条件 skipif**——本机缺依赖时
那几条用例会**静默消失**，而 README 仍声称覆盖）。

## 判据（三条腿，缺一不可）

1. **位点必须显式登记**（`(文件, 形态) → (出现次数, 理由)`）：新增未登记 ⇒ 红；
   同一文件多写一个同类 skip ⇒ 次数不符 ⇒ 红。
2. **登记不得腐烂**：登记了但代码里已没有 ⇒ 红（否则白名单只增不减，
   久而久之变成"什么都放行"）。
3. **「仅 CI 时跳过」的位点必须真的带 CI 条件**：登记里标 `require_ci=True` 的，
   其**所在函数源码**必须出现 `CI`——否则等于退化成无条件 skip（这正是本轮的病灶）。

扫描面用 `git ls-files tests`（独立事实来源），并配非空 + 数量下限 + 与索引核对，
防扫描面塌缩成空集后"自洽"。

## 允许的 skip 类型（与技能里的「skip 分级」表一致）

| 类型 | 允许 | 条件 |
|---|---|---|
| 环境缺失（产物/依赖） | ✅ | **只在 `CI=true` 时 skip**，本地缺即红 |
| 显式开关（`SKIP_BROWSER=1`） | ✅ | 开关本身另有守卫（`test_lint_gate` 审计） |
| 独立判据无法成立（拿不到 git 索引） | ✅ | 仅限**降级某一条独立判据**，且其余判据仍在跑 |
| 解析/格式 | ❌ | 直接 assert |
| 子进程失败 | ❌ | `assert returncode == 0` |
| 平台 | ❌ | 先问「依赖内核行为还是可替换接口」，后者 patch 掉 |
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TESTS_DIR = ROOT / "tests"
# 扫描面下限：本仓 tests/ 下远超这个数（防扫描面塌缩后"自洽"）
MIN_SCAN_FILES = 60

_KINDS = ("skip", "skipif", "xfail", "importorskip")

# (相对路径, 形态) → (出现次数, 理由, 是否必须带 CI 条件)
REGISTERED: dict[tuple[str, str], tuple[int, str, bool]] = {
    ("tests/browser/conftest.py", "skip"): (
        3,
        "浏览器层可跳过是 verify.cmd 的既定设计：SKIP_BROWSER=1 是**显式开关**"
        "（开关本身由 test_lint_gate 审计），另两处是缺 playwright / chromium 的环境探测。"
        "浏览器层与单测必须分进程，故它本就独立于总闸。",
        False,
    ),
    ("tests/test_no_sleep_gambling.py", "skip"): (
        1,
        "拿不到 git 索引时，「文件消失」这条**独立判据**无法成立——技能明确认可此处 skip"
        "（不要用 `assert not None` 假装通过）。同用例的非空 + 数量下限两条腿仍在跑。",
        False,
    ),
    ("tests/test_release_artifacts.py", "skip"): (
        1,
        "**仅 CI=true 时**跳过（发布产物不进仓库）；本地缺产物即红——"
        "因为「产物不存在」恰是该用例要防的情形之一。",
        True,
    ),
    ("tests/test_render_markdown.py", "skip"): (
        1,
        "**仅 CI=true 时**跳过（CI 未预装 node）；本地缺 node 即红（fail）。"
        "原为无条件 skipif ⇒ 本地没装 node 时富文本/XSS 用例静默消失。",
        True,
    ),
    ("tests/test_s2_refactor.py", "skipif"): (
        1,
        "**仅 CI 时**跳过（发布态 / 产物只在本地发布流程里存在）。",
        True,
    ),
}


def _root_name(node: ast.AST) -> str:
    """`pytest.mark.skipif` 这类嵌套属性取到根名（`pytest`）。"""
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else ""


def _tracked_test_files() -> list[Path]:
    """`git ls-files tests` 里的测试文件（独立事实来源）。"""
    out = subprocess.run(["git", "ls-files", "tests"], cwd=str(ROOT),
                         capture_output=True, text=True)
    names = [n for n in (out.stdout or "").splitlines() if n.strip()]
    return [ROOT / n for n in names
            if n.endswith(".py") and ("/test_" in n or n.endswith("conftest.py"))]


def _enclosing_node(tree: ast.Module, lineno: int) -> ast.AST:
    """取包含该行的**最内层函数**节点；不在任何函数里则返回模块节点。"""
    best: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            if node.lineno <= lineno <= end:
                if best is None or node.lineno >= getattr(best, "lineno", 0):
                    best = node
    return best if best is not None else tree


def _has_ci_reference(node: ast.AST) -> bool:
    """该作用域是否**真的在读 CI**：`x.get("CI")` / `x["CI"]` / 含 `CI` 的标识符。

    ⚠️ 两级收紧都是被注入逼出来的：
    ① 首版用 `"CI" in 源码片段` —— 被 **docstring 里的「CI」**满足（方向 G）；
    ② 二版改成「任意字符串常量含 `CI` 即算」—— 被**注入载荷自己的说明文本**满足
       （我写的 `pytest.skip("注入：不再带 CI 条件")` 里就有 "CI"，判据照样绿）。
    ⇒ 只有**环境变量读取**这一种形态才算数：`environ.get("CI")` / `environ["CI"]`
       / 含 `CI` 的标识符（如模块级 `_CI`）。**判据要描述"在做什么"，不是"提到了什么"。**
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and "CI" in n.id:
            return True
        if isinstance(n, ast.Attribute) and "CI" in n.attr:
            return True
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str) and "CI" in n.args[0].value):
            return True
        if (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str) and "CI" in n.slice.value):
            return True
    return False


def _scan() -> dict[tuple[str, str], list[tuple[int, ast.AST]]]:
    """扫描全部测试文件，返回 `{(relpath, kind): [(lineno, 所在函数/模块节点)]}`。"""
    found: dict[tuple[str, str], list[tuple[int, ast.AST]]] = {}
    for path in _tracked_test_files():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if _root_name(node.func) != "pytest" or node.func.attr not in _KINDS:
                continue
            kind = node.func.attr
            found.setdefault((rel, kind), []).append(
                (node.lineno, _enclosing_node(tree, node.lineno)))
    return found


def test_scan_face_is_not_empty():
    """元守卫（非参数化）：扫描面不能塌缩——否则下面两条断言在空集上恒真。"""
    files = _tracked_test_files()
    assert files, "扫描面为空（git ls-files tests 拿不到？）"
    assert len(files) >= MIN_SCAN_FILES, f"扫描面只有 {len(files)} 个文件（下限 {MIN_SCAN_FILES}）"
    on_disk = {p for p in TESTS_DIR.rglob("*.py")
               if "test_" in p.name or p.name == "conftest.py"}
    missing = {p for p in on_disk} - set(files)
    assert not missing, f"磁盘上有但 git 索引里没有（新增未 git add）：{sorted(missing)}"


def test_every_skip_site_is_registered():
    """**新增 skip 未登记即红**（含「同文件多写一个同类 skip」——按次数核对）。"""
    found = _scan()
    unregistered = sorted(k for k in found if k not in REGISTERED)
    assert not unregistered, (
        "以下 skip 位点未登记——skip 会让用例**静默消失**（CI 里 skip 与 pass 同退出码），"
        "必须显式登记并写明理由：\n  "
        + "\n  ".join(f"{rel} :: {kind} × {len(found[(rel, kind)])}" for rel, kind in unregistered)
        + "\n（登记表见本文件 REGISTERED；按项目的 skip 分级表判断该不该允许。）"
    )
    for key, (want_n, _reason, _need_ci) in REGISTERED.items():
        if key in found:
            got_n = len(found[key])
            assert got_n == want_n, (
                f"{key[0]} 的 {key[1]} 出现 {got_n} 次，登记为 {want_n} 次——"
                "新增/删除 skip 都要同步登记（次数对不上说明白名单已与代码脱钩）")


def test_registered_entries_are_not_rotted():
    """**登记不得腐烂**：登记了但代码里已没有 ⇒ 红（否则白名单只增不减，最终什么都放行）。"""
    found = _scan()
    rotten = sorted(k for k in REGISTERED if k not in found)
    assert not rotten, f"以下登记项在代码里已不存在，请从 REGISTERED 里删掉：{rotten}"


def test_ci_only_skips_really_check_ci():
    """标了「仅 CI」的 skip **必须真的带 CI 条件**——否则退化成无条件 skip（本轮病灶）。

    ⚠️ 判据用 **AST 找 CI 引用**，不是 `"CI" in 源码`：后者会被 **docstring 里的「CI」**
    满足（实测：删掉 `os.environ.get("CI")` 条件后，docstring 里那半句「CI 里才允许 skip」
    让子串判据照样绿）。同方向 G「docstring 里的词也算数」。
    """
    found = _scan()
    bad: list[str] = []
    for (rel, kind), (_n, _reason, need_ci) in REGISTERED.items():
        if not need_ci:
            continue
        for lineno, enclosing in found.get((rel, kind), []):
            if not _has_ci_reference(enclosing):
                bad.append(f"{rel}:{lineno}（{kind}）所在作用域里没有 CI 引用"
                           "（只在 docstring/注释里提到不算）")
    assert not bad, (
        "以下 skip 声称「仅 CI 时跳过」，但所在作用域里找不到 CI 引用 —— "
        "本地缺依赖时会静默跳过（「检查没跑」被当成「检查通过」）：\n  " + "\n  ".join(bad)
    )


def test_no_unconditional_skip_outside_registry():
    """登记表里**没有**任何一项允许「无条件」跳过本地环境缺失（除显式开关那类）。

    这条是上一轮修 `test_render_markdown` / `test_sign_release` 的直接产物：
    它们原本是 `skipif(which("node") is None)` / `skipif(_PWSH is None)`，
    即**本地缺依赖就静默跳过**。现在这两处都不在登记表里（已改成 fail），
    若有人把它们改回去，`test_every_skip_site_is_registered` 会先红。
    """
    found = _scan()
    # 显式开关类（浏览器层）是唯一允许不带 CI 条件的 skip 位点
    allowed_without_ci = {("tests/browser/conftest.py", "skip"),
                          ("tests/test_no_sleep_gambling.py", "skip")}
    for key in found:
        if key not in REGISTERED:
            continue          # 未登记由上一条用例负责报错
        if REGISTERED[key][2]:   # require_ci=True 的由 test_ci_only_skips_really_check_ci 管
            continue
        assert key in allowed_without_ci, (
            f"{key} 不在「允许不带 CI 条件」的白名单里——"
            "本地环境缺失必须红，不能静默跳过（见项目的 skip 分级表）")
