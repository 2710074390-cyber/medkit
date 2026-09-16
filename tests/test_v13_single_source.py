"""V-13：把「零引用的常量」从死代码变成单一真相（single source of truth）。

**背景**：用「定义了但没人调」审计（本轮据此挖出 `db.import_from_json()` 零调用方 → 两个 P0）
复扫出 15 个零引用定义，其中多数是**双份真相**：常量放在 A 处，而实际生效的值以字面量
硬编码在 B 处 —— 于是「改常量不生效」，比单纯死代码更危险。

本文件守两类：
1. **接线生效**：常量被真正的执行路径引用（结构守卫 + 行为断言）；
2. **不再回退**：关键字面量不得重新出现在本该用常量的地方。
"""
from __future__ import annotations

import inspect

from medkit.agents import medexplain
from medkit.core import explain as expl
from medkit.core import orchestrator as orch
from medkit.core import realexams, tutor
from medkit.core import review as rev
from medkit.core import scheduler as sched


def test_medexplain_uses_explain_limits():
    """联网素材上限/单条字数：必须引用 `core.explain` 的常量，而不是再写 4 / 240。"""
    src = inspect.getsource(medexplain)
    assert "WEB_MATERIALS_LIMIT" in src and "WEB_SNIPPET_LIMIT" in src
    assert "[:240]" not in src, "单条字数又写死成 240（应走 WEB_SNIPPET_LIMIT）"
    assert "_web_digest(web_materials, 4)" not in src, "素材条数又写死成 4"
    # 常量本身可被调参（同一对象）
    assert medexplain.WEB_SNIPPET_LIMIT is expl.WEB_SNIPPET_LIMIT
    assert medexplain.WEB_MATERIALS_LIMIT is expl.WEB_MATERIALS_LIMIT


def test_web_digest_respects_limits():
    """行为断言：条数上限真的生效（取 min(limit, len)）。"""
    mats = [{"title": f"t{i}", "url": f"http://x/{i}", "snippet": "s" * 500}
            for i in range(10)]
    text = medexplain._web_digest(mats, medexplain.WEB_MATERIALS_LIMIT)
    assert text.count("http://x/") == medexplain.WEB_MATERIALS_LIMIT
    assert "s" * (medexplain.WEB_SNIPPET_LIMIT + 1) not in text, "单条未按上限截断"


def test_orchestrator_uses_backend_sets():
    """后端分类必须走 websearch 的集合常量（原先写死 "bocha"/"manual"）。"""
    src = inspect.getsource(orch)
    assert "ws.EXTERNAL_BACKENDS" in src and "ws.NO_SEARCH_BACKENDS" in src
    assert 'backend == "bocha"' not in src
    assert 'backend != "manual"' not in src
    assert 'backend == "manual"' not in src


def test_review_stats_derives_from_card_states():
    """卡片状态分档必须由 CARD_STATES 派生（原先各档写死字面量）。"""
    src = inspect.getsource(rev.stats)
    assert "CARD_STATES" in src
    assert '"learning", "relearning"' not in src


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
