"""V-13：把「零引用的常量」从死代码变成单一真相（single source of truth）。

**背景**：用「定义了但没人调」审计（本轮据此挖出 `db.import_from_json()` 零调用方 → 两个 P0）
复扫出 15 个零引用定义，其中多数是**双份真相**：常量放在 A 处，而实际生效的值以字面量
硬编码在 B 处 —— 于是「改常量不生效」，比单纯死代码更危险。

本文件守两类：
1. **接线生效**：常量被真正的执行路径引用（结构守卫 + 行为断言）；
2. **不再回退**：关键字面量不得重新出现在本该用常量的地方。
"""
from __future__ import annotations

import ast
import inspect

from medkit.agents import medexplain
from medkit.core import explain as expl
from medkit.core import orchestrator as orch
from medkit.core import realexams, tutor
from medkit.core import review as rev
from medkit.core import scheduler as sched

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
#
# 现判据一律走 AST；且**判据本身必须能区分「正当消费」与「写死」**——
# 详见 R12 第二课（下方 `test_review_stats_*` 的注释）：
# 「不得出现某个字面量」这类**排除式**判据，在有合法用法时会变成假红。
# ---------------------------------------------------------------------------


def _static_number(node: ast.AST) -> float | None:
    """把 `240` / `2*120` / `4` / `0x4` 这类纯数值表达式静态求值；其它返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add, ast.Sub)):
        left, right = _static_number(node.left), _static_number(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        return left - right
    return None


def _slice_bound_literals(src: str) -> list[float]:
    """所有切片下标里的**数字字面量**（`x[:240]` / `x[0:240]` / `x[:2*120]`…）。

    只取「能被静态求值为纯数字」的表达式，所以 `x[:limit]`（变量下标）**不命中**。
    """
    out: list[float] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        parts = [sl.lower, sl.upper, sl.step] if isinstance(sl, ast.Slice) else [sl]
        for part in parts:
            if part is None:
                continue
            val = _static_number(part)
            if val is not None:
                out.append(val)
    return out


def _literal_strings(src: str) -> set[str]:
    """源码里出现的全部字符串字面量（AST 口径）。

    比子串匹配强的地方：**注释与 docstring 天然被排除**，所以
    `# 非 bocha 后端…` 这类注释不会把 orchestrator 判成「写死」
    （实测：`test_orchestrator_uses_backend_sets` 依赖这一点）。
    """
    return {
        n.value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _numeric_call_args(src: str, func_name: str) -> set[float]:
    """调 `func_name(...)` 时**数字字面量实参**的取值集合（跳过第 0 参）。

    用于 `_web_digest(web_materials, 4)`：第 0 参是要渲染的素材，之后才是上限。
    """
    out: set[float] = set()
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == func_name):
            continue
        for arg in node.args[1:]:
            val = _static_number(arg)
            if val is not None:
                out.add(val)
    return out


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _derived_bucket_names(src: str, const_name: str) -> set[str]:
    """找出「由 `const_name` 派生的字典」赋给了哪几个变量名。

    真身形态：`by_state = {s: … for s in CARD_STATES}` → 返回 `{"by_state"}`。
    这是「常量确实是真相源」的结构证据（比「字面量没出现」更贴近语义）。
    """
    tree = ast.parse(src)
    parents = _parent_map(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.DictComp):
            continue
        if not any(isinstance(g.iter, ast.Name) and g.iter.id == const_name
                   for g in node.generators):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 \
                and isinstance(parent.targets[0], ast.Name):
            names.add(parent.targets[0].id)
    return names


def _state_compared_to_literal(src: str, states: set[str]) -> list[str]:
    """找 `c.get("state") == "learning"` / `… in ("learning","relearning")`。

    这才是**真正的「档位写死」**：拿字面量当「卡片状态有哪些」的真相源。
    与之相对，`by_state["learning"]`（从派生结果里取值）是**正当消费**，不在此列。
    这个区分是 R12 的关键教训：**排除式判据会把正当消费误判成回归（假红）**。
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
                and left.func.attr == "get" and left.args
                and isinstance(left.args[0], ast.Constant)
                and left.args[0].value == "state"):
            continue
        for comp in node.comparators:
            vals: list[str] = []
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                vals = [comp.value]
            elif isinstance(comp, (ast.Tuple, ast.Set, ast.List)):
                vals = [e.value for e in comp.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            hits.extend(v for v in vals if v in states)
    return sorted(set(hits))


def test_medexplain_uses_explain_limits():
    """联网素材上限/单条字数：必须引用 `core.explain` 的常量，而不是再写 4 / 240。

    ## 判据是 AST 而非文本子串（2026-09-29 R12 改）
    旧版用 `"[:240]" not in src`，实测 `[: 240]` / `[0:240]` / `[:2*120]` 等
    **9 种等价写法全部绕过**（详见文件头注释）。现改为：
    「切片里不得出现与常量同值的**数字字面量**」——与书写格式无关，
    且 `materials[:limit]`（变量下标）不会被误伤。
    """
    src = inspect.getsource(medexplain)
    assert "WEB_MATERIALS_LIMIT" in src and "WEB_SNIPPET_LIMIT" in src

    # 1) 切片下标不得写死成常量的值（[:240] / [0:240] / [:2*120] … 一律命中）
    bounds = _slice_bound_literals(src)
    assert float(expl.WEB_SNIPPET_LIMIT) not in bounds, (
        "切片里写死了 WEB_SNIPPET_LIMIT 的值（%r），应引用常量；"
        "命中下标值：%r" % (expl.WEB_SNIPPET_LIMIT, sorted(set(bounds)))
    )

    # 干扰项自证：`materials[:limit]` 用的是变量下标，不得进入命中集合，
    # 否则说明判据把「正当的变量下标」也当成字面量了（判据过宽）。
    assert "[:limit]" in src, "源文件形态变了，本条干扰项断言失效，需重写"
    assert all(v != float(expl.WEB_SNIPPET_LIMIT) for v in bounds)

    # 2) `_web_digest` 的条数实参不得写死成常量的值（含 `4` / `0x4` / `2*2`）
    bad_args = _numeric_call_args(src, "_web_digest")
    assert float(expl.WEB_MATERIALS_LIMIT) not in bad_args, (
        "_web_digest 的条数实参写死成 %r（= WEB_MATERIALS_LIMIT），应引用常量"
        % expl.WEB_MATERIALS_LIMIT
    )

    # 3) 常量本身可被调参（同一对象）
    assert medexplain.WEB_SNIPPET_LIMIT is expl.WEB_SNIPPET_LIMIT
    assert medexplain.WEB_MATERIALS_LIMIT is expl.WEB_MATERIALS_LIMIT


def test_medexplain_limit_guard_is_not_vacuous():
    """元守卫：证明上面的 AST 判据真的能抓住「回退」（否则它就是恒绿）。

    对**内存里构造的回退版源码**跑同一套判据，必须命中。
    —— 这是「扫描面不为空」的自检，防止 AST 判据因写法变化而静默失效。
    """
    real = inspect.getsource(medexplain)
    # 切片回退：一切与 `[:WEB_SNIPPET_LIMIT]` 语义等价但写死数字的形态
    slice_fallbacks = {
        "[:240]": "[:240]",
        "[: 240]": "[: 240]",
        "[0:240]": "[0:240]",
        "[0: 240]": "[0: 240]",
        "[:2*120]": "[:2*120]",
        "[:_240]": "[:2*120]".replace("2*120", "2*120"),
    }
    for label, repl in slice_fallbacks.items():
        mutated = real.replace("[:WEB_SNIPPET_LIMIT]", repl, 1)
        assert mutated != real, "注入 %s 没匹配到源文件（探针本身失效）" % label
        assert float(expl.WEB_SNIPPET_LIMIT) in _slice_bound_literals(mutated), (
            "回退写法 %s 未被 AST 判据抓住 —— 这条守卫是假绿" % label
        )

    # 实参回退：`_web_digest(web_materials, WEB_MATERIALS_LIMIT)` → 写死 4 / 0x4
    for label, repl in {"4": "4)", "0x4": "0x4)"}.items():
        mutated = real.replace("WEB_MATERIALS_LIMIT)", repl, 1)
        assert mutated != real, "注入实参 %s 没匹配到源文件（探针本身失效）" % label
        assert float(expl.WEB_MATERIALS_LIMIT) in _numeric_call_args(mutated, "_web_digest"), (
            "_web_digest 实参回退 %s 未被 AST 判据抓住 —— 这条守卫是假绿" % label
        )

    # 反向自证：真身**不得**被判据命中（否则判据本身就偏红，等于永远失败）
    assert float(expl.WEB_SNIPPET_LIMIT) not in _slice_bound_literals(real)
    assert float(expl.WEB_MATERIALS_LIMIT) not in _numeric_call_args(real, "_web_digest")


def test_web_digest_respects_limits():
    """行为断言：条数上限真的生效（取 min(limit, len)）。"""
    mats = [{"title": f"t{i}", "url": f"http://x/{i}", "snippet": "s" * 500}
            for i in range(10)]
    text = medexplain._web_digest(mats, medexplain.WEB_MATERIALS_LIMIT)
    assert text.count("http://x/") == medexplain.WEB_MATERIALS_LIMIT
    assert "s" * (medexplain.WEB_SNIPPET_LIMIT + 1) not in text, "单条未按上限截断"


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


def test_review_stats_derives_from_card_states():
    """卡片状态分档必须由 CARD_STATES 派生（原先各档写死字面量）。

    ## 为什么不能用「档位名字面量不得出现」这种排除式判据（R12 实测教训）
    `stats()` 的语义要求把 learning 与 relearning **分成两个桶再相加**
    （`"in_progress" = by_state["learning"] + by_state["relearning"]`），
    所以「下标里出现 learning/relearning」在**真身里不可避免**。
    我用 AST 全字面量集合试过，真身直接被打成假红（`['learning','relearning','review']`）——
    **该判据不是「太严」，而是「问错了问题」**：
    它问的是「这个字符串出现过吗」，而该问的是
    「这个值是**常量派生来的**，还是**被当成真相源写死的**」。

    ## 现判据（两条，都必须是结构证据）
    1. 存在由 `CARD_STATES` 推导出来的分档字典（如 `by_state = {… for s in CARD_STATES}`）；
    2. 没有任何地方拿档位名字面量与 `卡片.get("state")` 直接比较 —— 那才是写死。
    真身：`by_state["learning"]`（从派生结果取值 = 正当消费）→ 全绿；
    回退：`c.get("state") == "learning"`（字面量当真相源）→ 命中。
    """
    src = inspect.getsource(rev.stats)
    assert "CARD_STATES" in src, "stats() 未引用 CARD_STATES"

    buckets = _derived_bucket_names(src, "CARD_STATES")
    assert buckets, (
        "stats() 里没有由 CARD_STATES 派生的分档字典（原先的 `by_state = {s: … for s in "
        "CARD_STATES}` 形态消失了）—— 常量可能已退回零引用"
    )

    leaked = _state_compared_to_literal(src, set(rev.CARD_STATES))
    assert not leaked, (
        "stats() 拿档位名字面量当真相源直接比较卡片状态 %r —— 应由 CARD_STATES 派生" % leaked
    )


def test_review_stats_guard_is_not_vacuous():
    """元守卫：证明上面的判据能抓住「各档写死」的回退（否则它是恒绿）。"""
    real = inspect.getsource(rev.stats)
    fallback = '''def stats(subject: str = "") -> dict[str, int]:
    cards = list_cards(subject)
    today = _today().isoformat()
    return {
        "total": len(cards),
        "new": sum(1 for c in cards if c.get("state") == "new"),
        "due": sum(1 for c in cards if (c.get("due") or "") <= today),
        "in_progress": sum(1 for c in cards if c.get("state") in ("learning", "relearning")),
        "review": sum(1 for c in cards if c.get("state") == "review"),
    }
'''
    # 判据 1：回退版没有派生字典
    assert not _derived_bucket_names(fallback, "CARD_STATES"), (
        "回退版被判成「有派生字典」—— 判据 1 失效"
    )
    # 判据 2：回退版的写死字面量必须被命中
    leaked = _state_compared_to_literal(fallback, set(rev.CARD_STATES))
    assert set(leaked) == {"new", "learning", "relearning", "review"}, (
        "回退版写死字面量未被判据 2 全部命中（实际 %r）—— 这条守卫是假绿" % leaked
    )
    # 反向自证：真身不得被任一条判据判红（否则是假红）
    assert _derived_bucket_names(real, "CARD_STATES")
    assert not _state_compared_to_literal(real, set(rev.CARD_STATES))


def test_scheduler_validates_sched_name():
    """未知名调度器：回退默认 + 留痕（不再静默），且合法名照常。"""
    assert isinstance(sched.make_scheduler("sm2"), sched.Sm2Scheduler)
    assert isinstance(sched.make_scheduler("fsrs"), sched.FsrsScheduler)
    assert isinstance(sched.make_scheduler(""), sched.FsrsScheduler)
    unknown = sched.make_scheduler("bogus")
    assert isinstance(unknown, sched.FsrsScheduler), "未知名应回退默认"
    snap = __import__("medkit.core.errors", fromlist=["errors"]).snapshot()
    assert any("bogus" in str(it) for it in snap.get("recent", [])), \
        "未知名回退必须留痕（U-15 口径：静默必留痕）"


def test_pure_dead_constants_removed():
    """纯死代码（应用/测试/前端三方零引用）已删除——不得悄悄回流。"""
    assert not hasattr(tutor, "QUESTION_LABELS")
    assert not hasattr(medexplain, "WEB_TIMEOUT")
    from medkit.core import providers
    from medkit.render import pagechrome
    assert not hasattr(providers, "PRICE_NOTES")
    assert not hasattr(pagechrome, "PRINT_BASE")


def test_realexams_analyze_llm_is_documented_unwired():
    """`analyze_llm` 零调用方：必须显式留档「待产品决策」，不得无说明地悬空。"""
    doc = realexams.analyze_llm.__doc__ or ""
    assert "零调用方" in doc and "待定" in doc, "未接线的功能必须写明现状与待定项"
