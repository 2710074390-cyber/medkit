"""前后端契约同源守卫（v0.22 阶段 5）。

## 这一组在守什么

阶段 5 是唯一改前端的一步，而前端的失败方式很「安静」：字段改名后页面只是**少显示一块**，
不报错、不红，直到用户发现「掌握度没了」。所以这里用**静态 + 契约**两道判据：

1. **路径同源**：JS 里出现的每个 `/api/kc/...` 字面量，必须在 `routers/kc.py` 里真实存在；
2. **字段同源**：界面依赖的每个字段名，必须在真实响应里出现（后端改名 ⇒ 这里先红）；
3. **装载顺序**：`index.html` 必须先 `kc-api.js` 后 `learn-kc.js`，且两者都在 `app.js` 之后；
4. **耦合守卫已登记**：新分片不进 `CORE` 元组 = 不被扫描 = 静默漏检。

> 为什么不逐一翻译字段再比对：本项目**禁止前端重命名**（实施方案 §3.4 冻结契约）。
> 前端拿到 `p_mastery` 就叫 `p_mastery`——所以「后端有这个键」与「前端能用」是同一件事。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit import main as m  # noqa: E402
from medkit.core import kc as kclib  # noqa: E402

WEB = ROOT / "medkit" / "web"
KC_API_JS = (WEB / "js" / "kc-api.js").read_text(encoding="utf-8")
LEARN_KC_JS = (WEB / "js" / "learn-kc.js").read_text(encoding="utf-8")
BANK_DRILL_JS = (WEB / "js" / "bank-drill.js").read_text(encoding="utf-8")
# v0.22 阶段 5：bank-drill.js 一度 835 行（超 800 上限）⇒ 把握分与结果区三段
# 拆到 bank-drill-result.js。契约守卫要**两片一起读**（只看一片会漏判）。
BANK_DRILL_RESULT_JS = (WEB / "js" / "bank-drill-result.js").read_text(encoding="utf-8")
BANK_DRILL_ALL = BANK_DRILL_JS + BANK_DRILL_RESULT_JS
INDEX_HTML = (WEB / "index.html").read_text(encoding="utf-8")
ROUTER = (ROOT / "medkit" / "routers" / "kc.py").read_text(encoding="utf-8")


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


# ---------------------------------------------------------------- 路径同源
def test_every_js_kc_path_exists_in_router():
    """JS 里写死的 `/api/kc/...` 路径必须在路由里存在（拼错 = 404，页面只显示空）。"""
    used = set(re.findall(r'["\'`](/api/kc/[a-zA-Z0-9_/{}$.\-]*)["\'`]', KC_API_JS))
    assert used, "kc-api.js 里没找到任何 /api/kc 路径——守卫本身失效了"
    for path in used:
        # JS 模板串形如 /api/kc/items/${...}/map → 还原成路由里的 {item_id}
        norm = re.sub(r"\$\{[^}]+\}", "{item_id}", path)
        assert norm in ROUTER or norm.rstrip("/") in ROUTER, f"路由里没有 {path}"


# ---------------------------------------------------------------- 字段同源
KEYS = {
    "/api/kc/overview": ["total_kcs", "tracked_kcs", "due_today", "by_state"],
    "/api/kc/calibration": ["n", "brier", "insufficient", "buckets"],
    "/api/kc/state": ["items", "total"],
}


def test_state_and_overview_fields_used_by_ui_exist():
    """界面依赖的字段必须真的在响应里（后端改名 ⇒ 这里先红，而不是页面悄悄空掉）。"""
    kclib.ensure_ready()
    # 造一条真实状态，否则 /state 的 items 为空、字段判据形同虚设
    st = kclib.apply_attempt(None, True)
    st.update({"kc_id": "kc_contract_test", "item": "契约测试考点", "subject": "内科学"})
    kclib.upsert_state(st)

    c = _client()
    ov = c.get("/api/kc/overview").json()
    for k in KEYS["/api/kc/overview"]:
        assert k in ov, f"/api/kc/overview 缺字段 {k}"
    for k in ("weak", "shaky", "solid", "mastered"):
        assert k in ov["by_state"], f"by_state 缺 {k}"

    cal = c.get("/api/kc/calibration").json()
    for k in KEYS["/api/kc/calibration"]:
        assert k in cal, f"/api/kc/calibration 缺字段 {k}"
    assert isinstance(cal["buckets"], list)
    if cal["buckets"]:
        for k in ("confidence", "n", "accuracy"):
            assert k in cal["buckets"][0]

    st_resp = c.get("/api/kc/state").json()
    for k in KEYS["/api/kc/state"]:
        assert k in st_resp, f"/api/kc/state 缺字段 {k}"
    row = next(i for i in st_resp["items"] if i["id"] == "kc_contract_test")
    for k in ("p_mastery", "attempts", "correct", "state", "item", "priority"):
        assert k in row, f"/api/kc/state 的行缺字段 {k}"


def test_items_and_backfill_fields_used_by_console_exist():
    """映射确认台依赖的字段：`item_id/status/mapped/candidates` 与回填报告的 `would_confirm`。"""
    kclib.ensure_ready()
    c = _client()
    res = c.get("/api/kc/items", params={"status": "all", "limit": 1}).json()
    for k in ("total", "items", "limit", "offset"):
        assert k in res, f"/api/kc/items 缺 {k}"
    if res["items"]:
        for k in ("item_id", "question", "topic", "status", "mapped", "candidates"):
            assert k in res["items"][0], f"/api/kc/items 的行缺 {k}"

    bf = c.post("/api/kc/backfill", json={"dry_run": True}).json()
    for k in ("scanned", "matched", "unmatched", "would_confirm", "created", "dry_run"):
        assert k in bf, f"/api/kc/backfill 缺 {k}"
    assert bf["dry_run"] is True


def test_grade_response_carries_fields_the_drill_renders():
    """刷题结果区渲染的字段必须来自 `/grade`（否则「逐选项理由」永远不显示）。"""
    from medkit.core import bank as banklib

    kclib.ensure_ready()
    banklib.create_bank("契约测试题库", "内科学")
    bid = str(banklib.list_banks()[0]["id"])
    banklib.add_items(bid, [{
        "question": "契约测试题", "subtopic": "契约测试", "options": ["甲", "乙", "丙", "丁", "戊"],
        "answer": "A", "analysis": "解析。【源:切片S001】",
        "option_rationales": {"A": "对", "B": "错"}, "misconceptions": {"B": "误解"},
        "source_quote": "原文", "difficulty": 3,
    }])
    iid = str(banklib.list_items(bid)[0]["id"])
    body = _client().post("/api/bank/grade", json={
        "bank_id": bid, "item_id": iid, "user_answer": "A", "confidence": 4}).json()
    for k in ("kc", "option_rationales", "misconceptions", "source_quote", "difficulty"):
        assert k in body, f"/grade 缺 {k}"
    for k in ("mapped", "kcs"):
        assert k in body["kc"], f"/grade 的 kc 段缺 {k}"


# ---------------------------------------------------------------- 装载与登记
def test_index_loads_kc_scripts_in_order():
    """`kc-api.js` 必须先于 `learn-kc.js` 装载（渲染层调用接口层）。"""
    i_api = INDEX_HTML.index("/assets/js/kc-api.js")
    i_ui = INDEX_HTML.index("/assets/js/learn-kc.js")
    i_app = INDEX_HTML.index("/assets/js/app.js")
    assert i_app < i_api < i_ui, "脚本顺序不对：app.js → kc-api.js → learn-kc.js"


def test_new_js_files_are_registered_in_coupling_guard():
    """新分片必须进 `CORE` 元组——不登记 = 不被扫描 = 静默漏检（本仓已栽过）。"""
    src = (ROOT / "tests" / "test_frontend_domain_coupling.py").read_text(encoding="utf-8")
    for name in ("kc-api.js", "learn-kc.js"):
        assert f'"{name}"' in src, f"耦合守卫未登记 {name}"


def test_drill_renders_rationales_only_when_present():
    """渲染分支必须「存在才渲染」：旧题的 `option_rationales` 为空 ⇒ 不加空标题。"""
    assert "keys.length" in BANK_DRILL_ALL
    assert "_bankRationalesHTML" in BANK_DRILL_ALL
    assert "_bankKcHTML" in BANK_DRILL_ALL
    # 拆分后两个渲染函数必须在**结果分片**里（否则分片守卫与本守卫会各说各话）
    assert "_bankRationalesHTML" in BANK_DRILL_RESULT_JS
    assert "_bankConfHTML" in BANK_DRILL_RESULT_JS


@pytest.mark.parametrize("snippet", [
    "p_mastery", "attempts", "correct", "would_confirm", "option_rationales",
    "source_quote", "misconceptions", "insufficient",
])
def test_ui_reads_contract_field_names_verbatim(snippet):
    """冻结契约：前端**不重命名**，字段名必须原样出现在 JS 里。"""
    blob = KC_API_JS + LEARN_KC_JS + BANK_DRILL_ALL
    assert snippet in blob, f"前端没有按原名读取 {snippet}"
