"""Markdown 复盘笔记渲染（EP-01 输出层 · 原方案 N12）。

与 `qbank_html.py` 并列的**输出层出口**：把 `core/metacog.py` 的五组统计
渲染成一篇可读、可打印、可长期留档的复盘笔记。

## 这份产物是什么、不是什么

- **是**：一份**元认知复盘**——「我的确信准不准」「我总在哪类错因上栽」
  「同一个知识点换了几轮还是没改」「这周可以不排什么」。数据来自 `errorpipe.analyze()`。
- **不是**：错题本，也不是解析。**刻意不列题目、不给答案**——题目本体在错题本里，
  而"复盘"要回答的是「我的方法哪儿不对」，不是「这道题选什么」。
  这样也天然回避了「产物里夹带答案」的问题（与苏格拉底视图同一取向）。

## 三条硬约束（都可证伪）

1. **零 LLM**：本文**不含任何自由文本建议**，所有句子要么是**常量映射**
   （`metacog._TAG_ADVICE` 那套，只在占比 ≥60% 且样本足够时才出），
   要么是**统计量本身的直述**。理由：复盘笔记最容易退化成
   「要扎实基础」类废话——那是本项目专门有守卫防的东西。
2. **纯函数、零 IO**：入参就是 `analyze()` 的返回体，故可用**黄金值**逐字锁定输出
   （不含时间戳的部分），不必起库、不必联网。
3. **空数据要有可读说明**，不产出空壳（每个小节都有空态文案），
   且**如实标注口径**（哪些条目被排除、频次缺失等），不假装有数据。
"""

from __future__ import annotations

from typing import Any

# 表格单元格的转义复用 qbank_html 的同名实现（单源：HTML 实体那半部分只写一次，
# 否则两份转义规则会各自漂移）。本模块只补表格结构字符那一半。
from .qbank_html import _esc_md

NOTEBOOK_TITLE = "错题复盘笔记"

# 「本周优先」最多列几条：复盘笔记是拿来执行的，不是拿来堆的
PRIORITY_LIMIT = 5


def _cell(v: Any) -> str:
    """表格单元格：HTML 实体转义（同源）→ 表格结构字符 → 压平换行。"""
    t = _esc_md(v).replace("|", r"\|")
    return " ".join(t.split()) or "—"


def _pct(v: Any) -> str:
    """比率 → 整数百分比。`None` 一律显示 `—`（**不显示 0%**，那会把"没数据"说成"零正确率"）。"""
    if v is None:
        return "—"
    try:
        return f"{round(float(v) * 100)}%"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):          # bool 是 int 子类，别让它渲染成 1/0
        return "是" if v else "否"
    if isinstance(v, int):
        return str(v)
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return _cell(v)


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    """GFM 表格。空 `rows` 由调用方给空态文案，这里不产出只有表头的空壳。"""
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join([" --- "] * len(headers)) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


# --------------------------------------------------------------------- 各小节
def _section_calibration(cal: dict[str, Any]) -> list[str]:
    lines = ["## 一、校准：我的「确信」准不准", ""]
    rated = int(cal.get("rated") or 0)
    if not rated:
        lines += ["暂无有效样本。需要同时有「把握程度」和「是否答对」的错题——"
                  "这两项必须**看答案前**填，事后补不了。", ""]
        return lines
    lines.append(f"- 有效样本 **{rated}** 道")
    if cal.get("brier") is not None:
        lines.append(f"- Brier 分数 **{_num(cal['brier'], 4)}**（越低越准；"
                     "0.25 相当于全程抛硬币）")
    if cal.get("jol_bias") is not None:
        bias = float(cal["jol_bias"])
        direction = "偏高（偏乐观）" if bias > 0 else "偏低（偏保守）"
        lines.append(f"- 自评与实际的整体偏差 **{'+' if bias > 0 else ''}{_pct(bias)}**（{direction}）")
    lines.append("")
    rows = []
    for b in cal.get("buckets") or []:
        if not b.get("n"):
            continue
        rows.append([b.get("confidence"), b.get("n"), b.get("correct"),
                     _pct(b.get("accuracy")), _pct(b.get("expected")),
                     _pct(b.get("gap"))])
    if rows:
        lines += ["| 自评 | 题数 | 答对 | 实际正确率 | 该档预期 | 偏差 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
        lines += ["", "> 偏差为负 = 实际比自评差（**过度自信**）；为正 = 过度保守。", ""]
    if cal.get("alert") and cal.get("alert_msg"):
        lines += [f"> ⚠ **过度自信预警**：{cal['alert_msg']}", ""]
    skipped = []
    if cal.get("unrated"):
        skipped.append(f"{cal['unrated']} 道未填把握程度")
    if cal.get("unknown_result"):
        skipped.append(f"{cal['unknown_result']} 道没有作答结果")
    if skipped:
        lines += [f"另有 {('、'.join(skipped))}，**未计入**上述统计。", ""]
    return lines


def _section_heatmap(heat: dict[str, Any]) -> list[str]:
    lines = ["## 二、错因分布：我总在哪类错误上栽", ""]
    rows = heat.get("rows") or []
    if not rows:
        lines += ["暂无带标签的错题。录入时选一个「错误类型」，或跑一次 AI 归因。", ""]
        return lines
    tags = list(heat.get("tags") or [])
    field = str(heat.get("field") or "")
    field_name = {"ai_error_tag": "只看 AI 归因", "error_tag": "只看你自评的标签"}.get(
        field, "你自评优先、AI 兜底")
    lines += [f"口径：**{field_name}**。", ""]
    head = ["科目", "错题数"] + tags + ["主导错因", "占比"]
    body = []
    for r in rows:
        counts = r.get("counts") or {}
        body.append([r.get("subject"), r.get("total")]
                    + [counts.get(t, 0) or "" for t in tags]
                    + [r.get("top_tag") or "—", _pct(r.get("top_ratio"))])
    lines += _table(head, body)
    advices = [r for r in rows if r.get("advice")]
    if advices:
        lines += ["", "### 按错因给的动作（仅当某类占比 ≥60% 且样本足够时给出）", ""]
        for r in advices:
            lines.append(f"- **{_cell(r.get('subject'))}**：{_cell(r.get('advice'))}")
    if heat.get("untagged"):
        lines += ["", f"另有 **{heat['untagged']}** 道未归类，未计入。"]
    lines.append("")
    return lines


def _section_migration(mig: dict[str, Any]) -> list[str]:
    lines = ["## 三、跨轮次迁移：改了没有", ""]
    chains = mig.get("chains") or []
    if not chains:
        lines += ["暂无跨轮次数据。需要同一知识点在**两个及以上轮次**都有错题记录"
                  "（按流水算，不按快照）。", ""]
        return lines
    rounds = list(mig.get("rounds") or [])
    lines += _table(["知识点", *rounds, "判定", "说明"],
                    [[c.get("kp_id"), *[(c.get("tags") or {}).get(r, "—") for r in rounds],
                      c.get("verdict"), c.get("msg")] for c in chains])
    stuck = mig.get("stuck") or []
    if stuck:
        lines += ["", f"> ⚠ **{len(stuck)} 个知识点跨轮次没有改善**——"
                      "说明原来的修补方法无效，换角度重学（如从机制图入手而非背诵）：", ""]
        for c in stuck[:PRIORITY_LIMIT]:
            tags = "、".join(f"{k}:{v}" for k, v in (c.get("tags") or {}).items())
            lines.append(f"> - `{_cell(c.get('kp_id'))}` —— {_cell(tags)}")
    lines.append("")
    return lines


def _section_subtract(sub: dict[str, Any]) -> list[str]:
    lines = ["## 四、本周减法：可以不排什么", ""]
    rows = sub.get("rows") or []
    if not rows:
        lines += ["暂无足够数据。需要带「科目 / 章节」与作答结果的错题。", ""]
        return lines
    skip = sub.get("skip") or []
    if not skip:
        lines += ["本章节数据显示没有明显可以砍掉的部分——继续保持。", ""]
    else:
        lines.append("以下章节正确率高、题量少，**本周不排**，把时间让给薄弱项：")
        lines.append("")
        for r in skip:
            lines.append(f"- {_cell(r.get('subject'))} · {_cell(r.get('chapter'))}"
                         f"（{r.get('n')} 道，正确率 {_pct(r.get('accuracy'))}）")
        lines.append("")
    lines.append(f"共 {len(rows)} 个章节参与评估，其中 **{len(skip)}** 个建议本周不排"
                 f"（cut 阈值 {_num(sub.get('cut'), 2)}）。")
    if sub.get("freq_missing"):
        lines += ["", "> 未导入真题考频，当前只按「题量 × 正确率」排序。"
                      "导入真题后会把近三年考频一并计入（高频章节即使正确率高也不会被砍）。"]
    lines.append("")
    return lines


def _section_priority(heat: dict[str, Any], mig: dict[str, Any]) -> list[str]:
    """本周优先——**全部由统计派生**，不写自由文本（避免套路话）。"""
    lines = ["## 五、本周优先", ""]
    items: list[str] = []
    for r in (mig.get("stuck") or [])[:PRIORITY_LIMIT]:
        tags = "、".join(f"{k}:{v}" for k, v in (r.get("tags") or {}).items())
        items.append(f"- **滞留知识点** `{_cell(r.get('kp_id'))}`：{_cell(tags)}"
                     " —— 同一错因跨轮次未变，换方法")
    for r in [x for x in (heat.get("rows") or []) if x.get("advice")][:PRIORITY_LIMIT]:
        items.append(f"- **{_cell(r.get('subject'))}**：{_cell(r.get('advice'))}")
    if not items:
        lines += ["暂无足够证据给出优先项（需要跨轮次数据，或某类错因占比 ≥60% 且样本足够）。",
                  ""]
        return lines
    lines += items
    lines += ["", "> 本节每一条都由上面的统计直接推出，**没有模型生成的内容**——"
                  "证据不足时宁可不给建议，也不写「要扎实基础」这类无指向的话。", ""]
    return lines


def render_notebook(stats: dict[str, Any], *, subject: str = "",
                    generated_at: str = "") -> str:
    """把 `errorpipe.analyze()` 的返回体渲染成 Markdown 复盘笔记。

    `generated_at` 由调用方传入（**不在本函数里取时间**）——纯函数才好做黄金值测试。
    """
    stats = stats or {}
    counts = stats.get("counts") or {}
    cal = stats.get("calibration") or {}
    heat = stats.get("heatmap") or {}
    mig = stats.get("migration") or {}
    sub = stats.get("subtract") or {}
    agree = stats.get("agreement") or {}

    scope = f"科目：{subject}" if subject else "全部科目"
    lines = [
        f"# {NOTEBOOK_TITLE}",
        "",
        f"> {scope} · 错题 **{counts.get('cards', 0)}** 道 · "
        f"知识点 **{counts.get('kp_ids', 0)}** 个 · 追踪流水 **{counts.get('events', 0)}** 条"
        + (f" · 生成于 {generated_at}" if generated_at else ""),
        ">",
        "> 本文由 MedKit **在本机生成、零 LLM 调用**：所有结论要么来自你自己的录入，",
        "> 要么是统计量的直述。**不列题目、不给答案**——那属于错题本，不属于复盘。",
        "",
    ]
    lines += _section_calibration(cal)
    lines += _section_heatmap(heat)
    lines += _section_migration(mig)
    lines += _section_subtract(sub)

    compared = int(agree.get("compared") or 0)
    lines += ["## 附：人工 vs AI 归因一致率", ""]
    if compared:
        lines += [f"- 可比对 **{compared}** 道，一致 **{agree.get('agreed', 0)}** 道，"
                  f"一致率 **{_pct(agree.get('rate'))}**",
                  "",
                  "> **不一致的那些题价值最高**——那是自我认知与模型判断分歧的地方。"
                  "它们就在错题本里（`tag_match=0`）。", ""]
    else:
        lines += ["暂无「人工标签 + AI 归因」都齐全的错题。", ""]

    lines += _section_priority(heat, mig)
    lines += [
        "---",
        "",
        "## 口径说明（数字怎么来的，可复核）",
        "",
        "- **校准**只用「把握程度 + 是否答对」都齐全的错题；两者缺一即被排除，排除条数在上文如实标出。",
        "- **错因分布**的标签取自 6 类固定枚举（知识盲区 / 记忆偏差 / 机制混淆 / 概念偷换 / 审题失误 / 推理跳步），"
        "自造标签在录入时就会被清空。",
        "- **迁移矩阵按流水算，不按快照**——快照会被编辑，流水不会。",
        "- **减法清单**：`题量 × 历史正确率 × 近三年考频`，只看**已确认**的真题频次。",
        "",
    ]
    return "\n".join(lines)


__all__ = ["NOTEBOOK_TITLE", "PRIORITY_LIMIT", "render_notebook"]
