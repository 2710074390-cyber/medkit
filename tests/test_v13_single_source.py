"""V-13：把「零引用的常量」从死代码变成单一真相（single source of truth）。

**背景**：用「定义了但没人调」审计（本轮据此挖出 `db.import_from_json()` 零调用方 → 两个 P0）
复扫出 15 个零引用定义，其中多数是**双份真相**：常量放在 A 处，而实际生效的值以字面量
硬编码在 B 处 —— 于是「改常量不生效」，比单纯死代码更危险。

本文件守两类：
1. **接线生效**：常量被真正的执行路径引用（结构守卫 + 行为断言）；
2. **不再回退**：关键字面量不得重新出现在本该用常量的地方。

2026-10-15（奥卡姆精简 T2）：讲解 / 提问 / 复习卡 / 调度器四域零使用被删，
本文件随之去掉它们的单一真相守卫（`medexplain` 上限常量、`review.CARD_STATES`、
`scheduler.make_scheduler` 的未知调度器回退），保留仍有效的两类：
**orchestrator 后端集合** 与 **纯死常量不得回流**。
"""
from __future__ import annotations

import ast
import inspect

from medkit.core import orchestrator as orch
from medkit.core import realexams

# ---------------------------------------------------------------------------
# 为什么这里用 AST 而不是「字面量子串 not in src」
#
# 2026-09-29（R12）反向验证实测：原先三条负向断言写成
#     assert "[:240]" not in src
#     assert "_web_digest(web_materials, 4)" not in src
#     assert '"learning", "relearning"' not in src
# **9 种语义等价的「回退」写法全部绕过**（断言仍绿）：
#     [: 240] / [:240 ] / [0:240] / [0: 240] / [:2*120] / [:_240]
#     _web_digest(web_materials,4) / ,  4) / , 0x4) / len(mats[:4])
#     ("learning","relearning") / 'learning', 'relearning'
# 根因：**文本子串匹配不是结构断言**——它绑的是「当时的书写格式」，
# 而不是「这里是否写死了魔法值」。凡断言「某处不得出现硬编码值」，
# 必须问「这个值在语法树上长什么样」。
# ---------------------------------------------------------------------------


def _literal_strings(src: str) -> set[str]:
    """源码里**出现在语法树上**的字符串字面量（注释/文档串天然被排除）。"""
    out: set[str] = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def test_orchestrator_uses_backend_sets():
    """后端分类必须走 websearch 的集合常量（原先写死 "bocha"/"manual"）。

    判据用 AST 字符串字面量集合，而非 `'backend == "bocha"' not in src`：
    后者对**换引号**（`'bocha'`）会漏。AST 口径另有两个附带好处：
    - 注释天然被排除（本模块 L646 注释里就有 `bocha` 一词，子串扫描会误红）；
    - 与空格、引号风格、是否包在元组里全部无关。
    """
    src = inspect.getsource(orch)
    assert "ws.EXTERNAL_BACKENDS" in src and "ws.NO_SEARCH_BACKENDS" in src
    strings = _literal_strings(src)
    for magic in ("bocha", "manual"):
        assert magic not in strings, (
            "后端名以字符串字面量写死在 orchestrator 里（%r）——"
            "应走 ws.EXTERNAL_BACKENDS / ws.NO_SEARCH_BACKENDS" % magic
        )


def test_orchestrator_backend_guard_is_not_vacuous():
    """元守卫：证明上面的集合判据能抓住写死回退（否则它是恒绿）。"""
    real = inspect.getsource(orch)
    fallbacks = {
        'backend == "bocha"': ('backend in ws.EXTERNAL_BACKENDS', 'backend == "bocha"'),
        "backend != 'manual'": ('backend not in ws.NO_SEARCH_BACKENDS', "backend != 'manual'"),
        "backend in ('bocha','manual')": (
            "backend in ws.NO_SEARCH_BACKENDS", "backend in ('bocha', 'manual')"),
    }
    for label, (old, new) in fallbacks.items():
        mutated = real.replace(old, new, 1)
        assert mutated != real, "注入 %s 没匹配到源文件（探针本身失效）" % label
        strings = _literal_strings(mutated)
        assert any(m in strings for m in ("bocha", "manual")), (
            "回退写法 %s 未被 AST 字面量判据抓住 —— 这条守卫是假绿" % label
        )
    # 反向自证：真身源码里的注释含 bocha，但 AST 口径不看注释
    assert "bocha" in real and "bocha" not in _literal_strings(real), (
        "真身注释里的 bocha 被 AST 判据误命中 —— 判据过宽"
    )


def test_pure_dead_constants_removed():
    """纯死代码（应用/测试/前端三方零引用）已删除——不得悄悄回流。"""
    from medkit.core import providers
    from medkit.render import pagechrome

    assert not hasattr(providers, "PRICE_NOTES")
    assert not hasattr(pagechrome, "PRINT_BASE")


def test_realexams_analyze_llm_is_documented_unwired():
    """`analyze_llm` 零调用方：必须显式留档「待产品决策」，不得无说明地悬空。"""
    doc = realexams.analyze_llm.__doc__ or ""
    assert "零调用方" in doc and "待定" in doc, "未接线的功能必须写明现状与待定项"
