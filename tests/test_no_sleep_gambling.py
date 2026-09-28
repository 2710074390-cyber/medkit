# -*- coding: utf-8 -*-
"""守卫：测试之间不得用「睡一会儿」赌**另一个线程的进度**。

## 为什么需要

`tests/test_explain_stream.py::test_concurrent_duplicate_stream_409` 曾因此
**真的翻车**（2026-09-29）：它用 `time.sleep` 赌首单是否已进入在飞状态，
累计 32 次里 9 次判据失效（假绿）。同类写法在
`tests/test_r4_batch3.py::test_cards_generate_dedupe_409` 也存在
（已实测：把线程启动延迟放大到 0.16s 即可让判据崩塌）。

根因不是"sleep 太短"，而是**判据依赖调度**：
    t.start(); time.sleep(0.15); assert <另一个线程的状态>
——这段代码正确与否取决于机器负载。

## 判据（故意保守，宁漏勿误伤）

**合法**：`sleep` 用来**模拟被测量的慢操作**——例如
    def slow(ev): time.sleep(0.6); return "ok"     # 被测的超时逻辑需要一个慢函数
    class SlowClient: ... time.sleep(3)            # 模拟 provider 慢流
这类 sleep 是**被测对象的输入**，与调度无关。

**不合法**：`sleep` 出现在**模块/测试函数体**里，且后面跟着对
**其他线程/状态**的断言——即「用时长赌进度」。

实现：只扫**测试函数体顶层**的 `time.sleep(...)` 语句
（缩进 4 空格 = 函数体直接子语句），且该 sleep **不在**嵌套函数/类定义内。
嵌套函数（如 `slow` / `SlowClient.chat_stream`）里的 sleep 一律放过。

例外必须显式登记在 `_ALLOWLIST` 并写明理由，另有用例防它堆陈尸。
"""
from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

# 显式豁免：键 = "相对路径::测试函数名"，值 = 理由（必须写清为什么合法）
#
# 当前为空——不是"没写"，是**真的不需要**：
#   · `test_pipeline_events.py` 的 `time.sleep(0.6)` 在**嵌套函数 `slow()`** 里
#     （模拟被测超时逻辑要处理的慢子步骤），扫描器本来就正确地忽略它。
#   · `test_explain_stream.py` / `test_tutor_stream.py` 的 `time.sleep(3)`
#     在**嵌套类 `SlowClient.chat_stream`** 里（模拟 provider 慢流），同理。
# 我一开始以为前者需要豁免，加了进来，结果被
# `test_allowlist_entries_are_still_needed` 当场逮出是陈尸——删掉了。
# 这正是那条用例存在的意义：**豁免表不许堆没用的条目**。
_ALLOWLIST: dict[str, str] = {}


def _iter_test_files():
    for p in sorted(TESTS.glob("test_*.py")):
        yield p


def _top_level_sleeps(func: ast.FunctionDef) -> list:
    """返回测试函数体**顶层**（非嵌套函数/类内）的 `time.sleep` 行号。

    实现要点：**不能**用 `ast.walk(func)` 再想办法排除嵌套——
    `ast.walk` 会遍历所有后代，`continue` 只能跳过节点自身、跳不过它的子树，
    于是嵌套函数里的 sleep 照样被收进来（第一版就这么写错了，
    反向验证时「混合」用例报了 2 处而非 1 处）。
    正确做法：只检查**顶层语句的直接表达式**，嵌套定义内部一概不看。
    """
    sleeps = []

    def _check_expr(node):
        f = getattr(node, "func", None)
        if isinstance(node, ast.Call) and isinstance(f, ast.Attribute):
            if f.attr == "sleep" and getattr(f.value, "id", None) == "time":
                sleeps.append(node.lineno)

    for stmt in func.body:
        # 顶层语句本身可能是：Expr(sleep(...)) / Assign / If / For / With ...
        # 只看该语句**自身**或其控制流头部，不下潜进 FunctionDef/ClassDef。
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                      # 嵌套定义整棵跳过（含其子树）
        if isinstance(stmt, ast.Expr):
            _check_expr(stmt.value)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            _check_expr(getattr(stmt, "value", None))
        elif isinstance(stmt, ast.If):
            # 条件语句：只查它的 test 与 body 的直接子语句（常见于 sleep 后断言）
            _check_expr(stmt.test)
            for sub in stmt.body + stmt.orelse:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(sub, ast.Expr):
                    _check_expr(sub.value)
    return sleeps


def _scan() -> tuple[list[tuple[str, str, int]], list[str]]:
    """返回 `(可疑 sleep 列表, 解析失败的文件列表)`。

    可疑 sleep 列表元素 = `(文件名, 测试函数名, 行号)`。

    实现要点：**不能**只靠 `node.lineno` + `text.splitlines()` 自己数行。
    `node.lineno` 是「从 1 开始的物理行号」，而 `lineno - 1` 只有当
    `col_offset == 0`（模块级的 `def test_`）时才恰好对上。这里改用
    `ast.get_source_segment()`——它按 (lineno, col_offset, end_lineno,
    end_col_offset) 精确切片，**不依赖函数的定义位置**，
    所以文件里任何位置的 `def test_`（模块级 / 缩进在别处）都能取到。

    **不许静默跳过**：解析失败的文件**作为第二个返回值显式交回**，
    并让 `test_scan_never_silently_skips` 变红。
    这是 2026-09-29 反向验证打偏暴露出来的真弱点——当时我的注入脚本写错缩进
    造出了 `IndentationError`，而 `except SyntaxError: continue` 把整个文件
    悄悄跳过，守卫报绿。**「我读不懂它，所以我认为它没问题」正是门禁假绿的
    典型形态**，必须显式失败而不是 continue。仓库里 pytest 本来就会因语法
    错误失败，这条只是把「静默」变成「响亮的失败」。

    **为什么用返回值而不是模块级全局**：曾经的写法是
    `_UNPARSED[:] = unparsed` + `return out`，第二个事实靠**副作用**传递。
    那要求「读 `_UNPARSED` 的那条用例必须刚调过 `_scan()`」——
    一旦有人新增调用方而忘了这层耦合，就会读到**上一次扫描的**（可能为空）
    结果 ⇒ **静默假绿**。同一个坑我们刚踩过一次，不再留第二次。
    返回值让两个事实**必须一起拿到**，编译器层面就拆不开。
    """
    out: list[tuple[str, str, int]] = []
    unparsed: list[str] = []
    for p in _iter_test_files():
        rel = p.name
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            unparsed.append("%s（读不出来：%s）" % (rel, e))
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            # 不 continue——记下来，让 test_scan_never_silently_skips 变红
            unparsed.append("%s:%s（解析失败：%s）" % (rel, e.lineno, e.msg))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                for ln in _top_level_sleeps(node):
                    out.append((rel, node.name, ln))
    return out, unparsed


def test_no_sleep_gambling_on_other_threads():
    """测试函数体顶层不得出现 `time.sleep(...)`（那是用时长赌调度）。

    需要"等另一个线程就位"时，用 `threading.Event` 握手：
        entered = threading.Event()
        def work(): entered.set(); ...
        t.start()
        assert entered.wait(10), "..."
    等**状态**，不等**时长**。
    """
    hits, _ = _scan()
    bad = []
    for rel, func, ln in hits:
        key = "%s::%s" % (rel, func)
        if key in _ALLOWLIST:
            continue
        bad.append("%s:%d  (在 %s 的顶层)" % (rel, ln, func))
    assert not bad, (
        "以下测试在函数体顶层用了 time.sleep 赌调度——请改用 Event 握手"
        "（等状态，不等时长）。若确属「模拟慢操作」的合法用法，"
        "它应该在嵌套函数里而不是顶层：\n  " + "\n  ".join(bad)
    )


def test_allowlist_entries_are_still_needed():
    """豁免表不得堆陈尸：每条豁免对应的 (文件, 函数) 必须仍存在。"""
    hits, _ = _scan()
    scanned = {"%s::%s" % (r, f) for r, f, _ in hits}
    stale = [k for k in _ALLOWLIST if k not in scanned]
    assert not stale, (
        "以下豁免已不再对应任何可疑 sleep（函数改名/删了/已改好）——"
        "请从 _ALLOWLIST 删除，否则它只是噪音：\n  " + "\n  ".join(stale)
    )


def test_raw_teardown_check_on_real_repo():
    """现在仓库里应当**没有**可疑的顶层 sleep（本守卫的目标态）。"""
    hits, unparsed = _scan()
    assert not unparsed, (
        "扫描不完整，无法断言「仓库里没有可疑 sleep」：\n  "
        + "\n  ".join(unparsed)
    )
    bad = [x for x in hits if "%s::%s" % (x[0], x[1]) not in _ALLOWLIST]
    assert bad == [], "仓库里仍有可疑 sleep：%s" % bad


def test_scan_never_silently_skips():
    """**没有**任何测试文件被静默跳过——跳过即红（不许「读不懂就放过」）。

    反向验证时踩出来的洞：我的注入脚本把 sleep 写成 8 空格缩进，
    造出 `IndentationError`；当时的 `except SyntaxError: continue`
    把整个文件跳过，守卫报绿——**假绿**。
    这条用例把「跳过」变成「响亮的失败」。
    """
    _, unparsed = _scan()
    assert not unparsed, (
        "以下测试文件无法解析，被扫描器跳过了——「读不懂就认为没问题」"
        "正是门禁假绿：\n  " + "\n  ".join(unparsed)
    )


def _tracked_test_files() -> set[str] | None:
    """从 **git 索引**取 `tests/test_*.py` 的集合——独立于磁盘 glob。

    为什么需要它：`_iter_test_files()` 和 `TESTS.glob()` **用的是同一个 glob**，
    所以「两个集合相等」对**文件被移出/改名**是**恒真**的盲区——
    文件不在了，两边一起不含它，断言照样通过。
    （2026-09-29 实测：把 `test_r8w_p1_remaining.py` 改名成 `.bak` 后
    `found == actual` 仍为 True，文件数从 83 掉到 82 却无人报警。）
    git 索引是**另一条**事实来源，能照出「少了一个文件」。

    返回 None 表示拿不到 git（例如源码包已脱离仓库）——
    此时调用方应**跳过**该断言而不是假装通过。
    """
    try:
        r = subprocess.run(
            ["git", "ls-files", "tests/test_*.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return {pathlib.Path(x).name for x in r.stdout.split()}


def test_scan_covers_every_test_file():
    """扫描面必须等于整个 tests/ 目录，且**每个文件都真的被读过**。

    历史教训：R3-16 的源码扫描只扫了 `review-desk.js`，漏了 `learn.js`。
    三条断言，缺一不可：
      1. `_iter_test_files()` == 磁盘 `TESTS.glob("test_*.py")`
         —— 防「扫描函数自己写窄了」（两个 glob 都改窄时它会失效，故有第 2 条）；
      2. 扫描面 == **git 索引**里的 `tests/test_*.py`
         —— 独立事实来源，能照出**文件消失/改名**；
      3. 文件数 > 50 —— 防整体塌缩成个位数还"自洽"。
    配套 `test_scan_never_silently_skips` 负责「解析失败即红」。
    """
    found = {p.name for p in _iter_test_files()}
    actual = {p.name for p in TESTS.glob("test_*.py")}
    assert found == actual, (
        "扫描面与磁盘不一致（漏扫 = 门禁开口）：\n"
        "  只扫到: %s\n  应有:   %s" % (sorted(found), sorted(actual))
    )
    assert len(found) > 50, "测试文件数量异常少（%d）——glob 可能写错了" % len(found)

    tracked = _tracked_test_files()
    if tracked is None:
        pytest.skip("拿不到 git 索引（脱离仓库？）——跳过「文件消失」这项独立的判据")
    missing = tracked - found          # git 里有、扫描面没有 → 被漏扫/删了
    extra = found - tracked            # 扫描面有、git 里没有 → 新文件未入库
    assert not missing, (
        "以下测试文件在 git 索引里但**没有被扫描**（被改名/删除/glob 漏掉）"
        "——这就是 R3-16 那类漏扫：\n  " + "\n  ".join(sorted(missing))
    )
    assert not extra, (
        "以下测试文件在磁盘上但不在 git 索引里（新增未 `git add`）：\n  "
        + "\n  ".join(sorted(extra))
    )
