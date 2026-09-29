"""U-12（R6-07）「防丢断言」：断言**实现结构**而非行为。

**为什么需要这一层**：R5-02 的教训——`CHANGELOG` / `AGENT_HANDOFF` / R4 报告三处都记载
「R4-01 已落地」，而代码从未进入提交，**测试全程绿灯**。原因是既有用例用
`monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: …)` 把缺陷接缝**整个 mock 掉**，
且 mock 签名还是按「修复版」写的——于是「真的修好了」与「测试写成了通过的样子」无法区分。

**本文件的纪律**：对关键接缝，断言「源码里存在该结构」而不是「调用后行为正确」。
一旦有人把 `dedupe.end` 从 `finally` 移出、或删掉 `cancel_ev.set()`，本文件必须变红。
"""

import ast
import inspect
import textwrap

import pytest

from medkit.routers import library as lib_router


def _finally_body(src: str) -> str:
    """取最后一段 `finally:` 块的**块体源码**（本文件的断言都基于它）。

    ⚠️ 不能用 `src.rfind("finally:")` 一刀切：那样会把 `finally:` 之后
    **同一函数里其余语句**（如 `return StreamingResponse(...)`）一并截进来，
    而这些语句在语义上并不在 finally 块内。改为 AST 精确定位。
    """
    tree = ast.parse(src)
    last: ast.Try | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and node.finalbody:
            last = node
    assert last is not None, "未找到 finally 块——资源释放必须放在 finally 里"
    return "\n".join(ast.get_source_segment(src, stmt) or "" for stmt in last.finalbody)


# ---------------------------------------------------------------------------
# AST 判据工具（2026-09-29 R12-2）
#
# 为什么不用「精确文本子串」：真身那行是
#     cancel_ev = threading.Event()   # R5-03：...
# 锚点串 "cancel_ev = threading.Event()" 之所以命中，靠的是**注释写在后面**。
# 实测：把注释挪到行内别处 / 改成双空格 `cancel_ev  =  threading.Event()`
# ⇒ 断言**假绿**（源码没变语义，守卫却不再命中）。凡断言「某结构在场」，
# 必须用 AST 判定，别绑书写格式。
# ---------------------------------------------------------------------------
def _event_assign_names(src: str) -> set[str]:
    """找 `x = threading.Event()` 形式的赋值，返回被赋值的变量名集合。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        val = node.value
        if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute) \
                and val.func.attr == "Event" \
                and isinstance(val.func.value, ast.Name) and val.func.value.id == "threading":
            out.add(node.targets[0].id)
    return out


def _called_attr_names(src: str) -> set[str]:
    """所有「对某对象调用的方法名」集合（如 `dedupe.begin(...)` → `begin`）。

    `src` 可以是完整函数，也可以是 `_finally_body()` 切出的**裸 `finally:` 片段**——
    后者用 `textwrap.dedent` + 补 `try:` 头使其成为合法语句后再解析。
    """
    tree = _parse_fragment(src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
    return out


def _parse_fragment(src: str) -> ast.AST:
    """解析「完整函数源码」或「若干条语句的片段」。"""
    stripped = src.lstrip()
    if stripped.startswith(("def ", "class ", "@", "async def ")):
        return ast.parse(src)
    return ast.parse(textwrap.dedent(src))


def _def_names(src: str) -> set[str]:
    return {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}


@pytest.mark.parametrize("fn_name,key_fn", [
    ("explain_stream", "_explain_key"),
    ("tutor_start_stream", "_tutor_key"),
])
def test_stream_seam_wiring_is_present(fn_name, key_fn):
    """讲解/提问两条流式接缝：请求级去重 + 取消事件必须结构性在位。

    ## 判据改为 AST（2026-09-29 R12-2）
    旧版是精确文本子串（`"cancel_ev = threading.Event()" in src` 等）。
    实测：把该行的**行内注释挪位**、或把 `=` 两侧改成双空格，
    源码语义完全不变，断言却不再命中 ⇒ 假绿。
    现改为 AST：找「对 `threading.Event()` 的赋值」「被调用的方法名」
    「定义的函数名」，与注释、空白、书写格式全无关。
    """
    src = inspect.getsource(getattr(lib_router, fn_name))

    # ① 取消事件（R5-03）：必须存在 `x = threading.Event()` 赋值
    events = _event_assign_names(src)
    assert events, f"{fn_name} 缺少 threading.Event() 取消事件创建"

    # ② 请求级去重（R5-02）：锁在首帧之前获取，持有者是生成器而非请求守卫
    calls = _called_attr_names(src)
    assert "begin" in calls, f"{fn_name} 缺少 dedupe.begin（流内去重锁）"
    assert "gen" in _def_names(src), f"{fn_name} 应把锁获取放在生成器内（首帧之前）"

    # ③ 释放路径：dedupe.end 与 cancel_ev.set 都必须在 finally 中
    tail = _finally_body(src)
    tail_calls = _called_attr_names(tail)
    assert "end" in tail_calls, f"{fn_name} 的 dedupe.end 不在 finally 中（异常/断连会漏放锁）"
    assert "set" in tail_calls, f"{fn_name} 的 cancel_ev.set() 不在 finally 中（断连不会真停）"

    # ④ 去重键按知识点（不同知识点并行不互相阻塞）
    assert key_fn in src, f"{fn_name} 的去重键应来自 {key_fn}（按知识点隔离）"


def test_stream_seam_guard_is_not_vacuous():
    """元守卫：证明 AST 判据能抓住「删接缝」（否则它是恒绿），且不误伤等价改写。"""
    src = inspect.getsource(lib_router.tutor_start_stream)

    # 反假红 1：真身必须绿
    assert _event_assign_names(src) and "begin" in _called_attr_names(src)

    # 反假红 2：把行内注释挪位 / 改双空格 —— 语义不变，AST 判据必须仍命中
    reformatted = src.replace("cancel_ev = threading.Event()",
                              "cancel_ev  =  threading.Event()")
    assert reformatted != src, "注入锚点未命中（探针失效）"
    assert _event_assign_names(reformatted), (
        "等价改写（双空格）被误判为「取消事件缺失」—— 假红"
    )

    # 证伪 1：删掉取消事件创建 ⇒ 必须判「缺失」
    no_event = src.replace("cancel_ev = threading.Event()", "cancel_ev = object()")
    assert no_event != src, "注入锚点未命中（探针失效）"
    assert not _event_assign_names(no_event), "删掉 Event 创建却没红 —— 假绿"

    # 证伪 2：去掉 dedupe.begin ⇒ 必须判「缺失」
    no_begin = src.replace("dedupe.begin(", "dedupe_off(")
    assert no_begin != src, "注入锚点未命中（探针失效）"
    assert "begin" not in _called_attr_names(no_begin), "去掉 dedupe.begin 却没红 —— 假绿"


def test_stream_guard_rejects_duplicate_before_first_frame():
    """去重命中时应在**首帧之前**返回 error，而不是先吐 meta 再报错。"""
    src = inspect.getsource(lib_router.explain_stream)
    i_begin = src.find("dedupe.begin(")
    i_meta = src.find('yield _sse("meta"')
    assert i_begin != -1 and i_meta != -1
    assert i_begin < i_meta, "dedupe.begin 必须在首个 meta 帧之前（否则重复提交会看到半截流）"
