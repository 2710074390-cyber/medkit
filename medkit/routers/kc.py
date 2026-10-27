"""知识点图谱与学习者模型路由（v0.22 阶段 2）。

## 端点族

- `GET  /api/kc/items` —— 题目列表 + 映射状态（供映射确认台分页）
- `POST /api/kc/items/{item_id}/map` —— **唯一写入口**：人工确认映射（覆盖式）
- `POST /api/kc/backfill` —— 规则回填（幂等、可重入；默认 dry_run）
- `GET  /api/kc/state` —— 知识点状态（掌握概率 + 优先级）
- `GET  /api/kc/calibration` —— 自评置信度 → 实际正确率 + Brier
- `GET  /api/kc/overview` —— 总览（覆盖度 / 状态分布 / 校准）
- `GET  /api/kc/edges` —— 知识点之间的关系

## 契约铁律

- 全部返回 `{"ok": true, ...}`；错误走 `HTTPException`（统一错误体含 `error_code`）。
- **字段名全小写下划线，后端 JSON 与前端 JS 完全同名，前端不做任何重命名映射。**
- 新增字段一律可选；前端按「存在才渲染」处理。
- 本组端点**不做用户级鉴权**（服务只绑 127.0.0.1 + Host/Origin 白名单 + 零入站鉴权，
  与既有全部端点一致）——**不新增任何网络暴露面**。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..core import bank as banklib
from ..core import kc as kclib
from ._common import require_flag

router = APIRouter(tags=["kc"])

# 分页上限：防止一次拉全量（`/api/kc/items` 是本组最重的读端点）
_MAX_LIMIT = 200


@router.get("/api/kc/items")
def kc_items(bank_id: str = "", subject: str = "", q: str = "",
              status: str = "all", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """题目列表 + 映射状态（供映射确认台分页）。

    `status`：`all` / `unmapped`（无任何映射）/ `pending`（有候选但未确认）/ `mapped`（有确认）。
    """
    require_flag("kc")
    if status not in ("all", "unmapped", "pending", "mapped"):
        raise HTTPException(400, "status 只能是 all / unmapped / pending / mapped")
    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise HTTPException(400, "limit / offset 必须是整数")
    if limit < 1 or limit > _MAX_LIMIT:
        raise HTTPException(400, f"limit 必须在 1~{_MAX_LIMIT} 之间")
    if offset < 0:
        raise HTTPException(400, "offset 不能为负")

    items = banklib.list_items(bank_id) if bank_id else _all_items()
    if subject:
        items = [it for it in items
                 if str(it.get("subject") or "") == subject]
    if q:
        ql = q.strip().lower()
        items = [it for it in items
                 if ql in str(it.get("question") or "").lower()
                 or ql in str(it.get("topic") or it.get("subtopic") or "").lower()]

    pool = kclib.kc_pool(subject=subject)
    rows: list[dict[str, Any]] = []
    for it in items:
        iid = str(it.get("id") or "")
        mapped = kclib.mappings_for_item(iid)
        confirmed = [m for m in mapped if m.get("confirmed")]
        topic = str(it.get("topic") or it.get("subtopic") or "")
        cands = kclib.rule_candidates(topic, pool) if topic else []
        if confirmed:
            st = "mapped"
        elif cands:
            st = "pending"
        else:
            st = "unmapped"
        if status != "all" and st != status:
            continue
        rows.append({
            "item_id": iid,
            "bank_id": str(it.get("bank_id") or ""),
            "question": str(it.get("question") or ""),
            "topic": topic,
            "subject": str(it.get("subject") or ""),
            "status": st,
            "mapped": confirmed,
            "candidates": cands[:8],
        })
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"ok": True, "total": total, "limit": limit, "offset": offset, "items": page}


def _all_items() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in banklib.list_banks():
        out.extend(banklib.list_items(str(b.get("id") or "")))
    return out


@router.post("/api/kc/items/{item_id}/map")
def kc_map(item_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """人工确认映射（**唯一写入口**）。

    覆盖语义：本次 `kc_ids` 即该题最终的全部**已确认**映射；空数组 = 取消该题全部确认。
    """
    require_flag("kc")
    if not item_id:
        raise HTTPException(400, "缺少题目 id")
    raw = body.get("kc_ids")
    if raw is None or not isinstance(raw, list):
        raise HTTPException(400, "kc_ids 必须是数组")
    kc_ids = []
    for k in raw:
        if not isinstance(k, str) or not k.strip():
            raise HTTPException(400, "kc_ids 的元素必须是非空字符串")
        kc_ids.append(k.strip())
    item = banklib.get_item(item_id)
    if item is None:
        raise HTTPException(404, "题目不存在")
    before = {m["kc_id"] for m in kclib.mappings_for_item(item_id, only_confirmed=True)}
    rows = kclib.set_mappings(item_id, kc_ids, source="human")
    after = {m["kc_id"] for m in rows}
    return {"ok": True, "item_id": item_id, "mapped": rows,
            "removed": sorted(before - after)}


@router.post("/api/kc/backfill")
def kc_backfill(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """规则回填（幂等、可重入）。默认 `dry_run=true`——先看报告，再正式执行。"""
    require_flag("kc")
    method = str(body.get("method") or "rule").strip().lower()
    if method not in ("rule", "llm"):
        raise HTTPException(400, "method 只能是 rule 或 llm")
    try:
        min_score = float(body.get("min_score", 0.45))
    except (TypeError, ValueError):
        raise HTTPException(400, "min_score 必须是 0~1 的数字")
    if not 0.0 <= min_score <= 1.0:
        raise HTTPException(400, "min_score 必须在 0~1 之间")
    try:
        res = kclib.backfill(bank_id=str(body.get("bank_id") or ""),
                             subject=str(body.get("subject") or ""),
                             method=method, min_score=min_score,
                             dry_run=bool(body.get("dry_run", True)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **res}


@router.get("/api/kc/state")
def kc_state(subject: str = "", order: str = "priority", limit: int = 100,
             offset: int = 0) -> dict[str, Any]:
    """知识点状态（掌握概率 + 优先级 + 支架等级 + 到期）。"""
    require_flag("kc")
    if order not in ("priority", "mastery", "due"):
        raise HTTPException(400, "order 只能是 priority / mastery / due")
    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise HTTPException(400, "limit / offset 必须是整数")
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit 必须在 1~500 之间")
    if offset < 0:
        raise HTTPException(400, "offset 不能为负")
    total, rows = kclib.list_states(subject=subject, order=order,
                                    limit=limit, offset=offset)
    return {"ok": True, "total": total, "limit": limit, "offset": offset,
            "items": [kclib.state_with_priority(r) for r in rows]}


@router.get("/api/kc/calibration")
def kc_calibration(subject: str = "", days: int = 30) -> dict[str, Any]:
    """自评置信度 → 实际正确率 + Brier 校准误差。

    `insufficient=true`（样本 < 10）时前端应显示「样本不足」而不是数字。
    """
    require_flag("kc")
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise HTTPException(400, "days 必须是整数")
    if days < 1 or days > 365:
        raise HTTPException(400, "days 必须在 1~365 之间")
    pairs = _confidence_pairs(subject=subject, days=days)
    res = kclib.calibration_curve(pairs)
    return {"ok": True, **res}


def _confidence_pairs(subject: str = "", days: int = 30) -> list[tuple[int, bool]]:
    """自评置信度 → 是否答对（SQL 与事务边界在 core/kc.py：U-09 守卫）。"""
    return kclib.confidence_pairs(subject=subject, days=days)


@router.get("/api/kc/overview")
def kc_overview(subject: str = "") -> dict[str, Any]:
    """总览：覆盖度 / 状态分布 / 今日到期 / 校准。"""
    require_flag("kc")
    total, rows = kclib.list_states(subject=subject, order="priority", limit=500)
    by_state = {"weak": 0, "shaky": 0, "solid": 0, "mastered": 0}
    due_today = 0
    today = date.today().isoformat()
    for r in rows:
        by_state[kclib.state_with_priority(r)["state"]] += 1
        if str(r.get("due") or "") and str(r.get("due")) <= today:
            due_today += 1
    pool = kclib.kc_pool(subject=subject)
    # confirmed_mappings() 返回 {item_id: [kc_id, ...]} ⇒ 展平后去重才是「被映射的知识点数」
    mapped_kcs = len({kc for kcs in kclib.confirmed_mappings().values() for kc in kcs})
    cal = kclib.calibration_curve(_confidence_pairs(subject=subject))
    return {
        "ok": True,
        "total_kcs": len(pool),
        "mapped_kcs": mapped_kcs,
        "tracked_kcs": total,
        "by_state": by_state,
        "due_today": due_today,
        "avg_brier": cal["brier"] if cal["n"] >= 10 else None,
        "coverage_ratio": round(mapped_kcs / len(pool), 4) if pool else 0.0,
        "calibration_n": cal["n"],
    }


@router.get("/api/kc/edges")
def kc_edges(kc_id: str = "", kind: str = "") -> dict[str, Any]:
    """知识点之间的关系（前置 / 关联 / 易混）。"""
    require_flag("kc")
    if kind and kind not in ("prerequisite", "related", "contrast"):
        raise HTTPException(400, "kind 只能是 prerequisite / related / contrast")
    return {"ok": True, "items": kclib.list_edges(kc_id=kc_id, kind=kind)}
