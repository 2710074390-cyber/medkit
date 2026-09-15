"""U 批次 1 修复守卫测试（2026-09-15 三报告整合 · P0/P1 项）。

覆盖（每条对应 `docs/reviews/优化清单与执行记录_2026-09-15.md` 的 U-xx）：
- U-02 前端调用与后端路由方法一致性（「铺卡」405 的直接防线）
- U-03 FEATURES 判断不再依赖 window（记忆卡入口不可见的直接防线）
- U-08 产物页 #qreset 不再被 setCounts 写成 null
- U-18 题库页头部题数与筛选（按卡）计数口径一致
- U-19 押题卷回流错题缺省错因 = unknown（不再伪造「推理断链」）
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "medkit" / "web" / "js"


def _route_methods() -> dict[str, set[str]]:
    from medkit.main import app
    m: dict[str, set[str]] = {}
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        m.setdefault(path, set()).update(methods)
    return m


def _iter_api_calls(text: str):
    """产出 (行号, 路径, 完整调用文本)：按括号配平截取 `api(...)` 整段（跨行）。"""
    for mo in re.finditer(r"\bapi\s*\(", text):
        i = mo.end() - 1  # 指向 '('
        depth, j = 0, i
        while j < len(text):
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        call = text[i:j + 1]
        pm = re.match(r"\(\s*[\"'`](/api/[^\"'`?]+)", call)
        if pm:
            yield text.count("\n", 0, mo.start()) + 1, pm.group(1), call


def test_u02_frontend_api_method_matches_backend():
    """U-02：POST-only 路由的前端调用必须显式 method:"POST"（防 405 回归）。"""
    routes = _route_methods()
    problems: list[str] = []
    for js in sorted(JS_DIR.glob("*.js")):
        for lineno, path, call in _iter_api_calls(js.read_text(encoding="utf-8")):
            methods = routes.get(path)
            # 仅在「POST-only」时要求显式 method；同路径兼有 GET（列表端点）时按 GET 调用合法
            if not methods or "POST" not in methods or "GET" in methods:
                continue
            if not re.search(r"method\s*:\s*[\"']POST[\"']", call):
                problems.append(f"{js.name}:{lineno} → {path}（后端 POST-only，前端未指定 method）")
    assert not problems, "前端调用与后端方法不一致：\n" + "\n".join(problems)


def test_u03_no_window_features_guard():
    """U-03：FEATURES 为 const（不挂 window），判断必须直接用 FEATURES。"""
    for js in sorted(JS_DIR.glob("*.js")):
        assert "window.FEATURES" not in js.read_text(encoding="utf-8"), (
            f"{js.name} 仍在用 window.FEATURES 判断"
            "（const 不挂 window → 入口永久不渲染）")
    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    assert re.search(r"^const FEATURES\s*=", app_js, re.M), "FEATURES 应在 app.js 顶层声明"


B1_GROUP = {"options": ["支原体", "肺炎链球菌", "腺病毒", "呼吸道合胞病毒", "金黄色葡萄球菌"]}


def _b1(i: int) -> dict:
    return {"id": f"Q01{i}", "type": "B1", "bloom": "记忆", "subtopic": "病原体",
            "question": f"上呼吸道感染最常见病原（{i}）？", "options": [],
            "answer": "B", "analysis": "解析", "group_kind": "option_group",
            "group": dict(B1_GROUP)}


def test_u08_qreset_not_null():
    """U-08：筛选区「重置」按钮不得被 setCounts 写成 null。"""
    from medkit.render.qbank_html import export_html
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
           "question": "肺通气？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    h = export_html(qs, "题库")
    assert 'id="qreset"' in h
    # setCounts 必须限定带 data-t 的按钮（否则 #qreset 的 data-label 为 null → 文本 "null"）
    assert "document.querySelectorAll('.filters button[data-t]').forEach" in h
    assert "b.textContent=(b.getAttribute('data-label')||'')" in h
    # 反例守卫：旧写法（不加 data-t、不做 label 兜底）不得回归
    assert "b.textContent=b.getAttribute('data-label')+(c[k]" not in h


def test_u18_header_count_matches_card_count():
    """U-18：头部题数与筛选（按卡）计数口径一致。"""
    from medkit.render.qbank_html import export_html
    qs = [_b1(1), _b1(2), _b1(3),
          {"id": "Q020", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
           "question": "单题？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    h = export_html(qs, "题库")
    # 4 题 → 2 张卡（B1 组 1 张 + 单题 1 张）
    assert "共 4 题（2 张卡，其中组内子题 2 道）" in h


def test_u07_dup_badge_rendered():
    """U-07：查重未通过的题在产物页显示「⚠ 疑似重复」标记（不剔除、可见）。"""
    from medkit.render.qbank_html import export_html
    q = {"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
         "question": "肺通气？", "options": ["a", "b", "c", "d", "e"],
         "answer": "A", "analysis": "解析", "_dup_warn": "与 Q002 题干高度相似"}
    h = export_html([q], "题库")
    assert "⚠ 疑似重复" in h
    assert "与 Q002 题干高度相似" in h


def test_u19_sync_default_error_reason_unknown(monkeypatch):
    """U-19：押题卷回流错题缺省错因 = unknown（不再伪造「推理断链」）。"""
    from medkit.core import library as lib
    captured: list[dict] = []
    monkeypatch.setattr(lib, "batch_add",
                        lambda rows: (captured.extend(rows), len(rows))[1])
    lib.sync_from_paper([{"id": "Q1", "question": "题干甲（U-19 守卫）",
                          "options": ["a", "b"], "answer": "A",
                          "user_answer": "B", "subtopic": "呼吸"}])
    assert captured, "应产出一条错题记录"
    assert captured[0]["error_reason"] == "unknown"
