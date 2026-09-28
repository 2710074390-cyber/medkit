#!/usr/bin/env python
"""EP-01 阶段 0：归因质量真实验证（《总纲》§2.5 闸门）。

## 这个脚本存在的理由

《总纲》§2.5 / 方案 §7 阶段 0 要求：**先手工跑 20 道真实错题、认可率 ≥70% 再动工**。
实际是「先动了工」（阶段 1~3 已落地并随 0.10.5 发布）。本脚本补做这道闸门。

## 与「手工跑」的差异（必须说清，否则结论会被高估）

原要求是**手工**在 Kimi/Claude/DeepSeek 里逐条跑、人肉判认可率。本脚本用**本机配置的真实
LLM**（`config.json` 的 DeepSeek Key）+ **仓库里的真实提示词**跑，差异在于：

- ✅ 更严格的地方：走**真实代码路径**（`agents.error_analysis.analyze`），
  因此契约校验、`correct` 防御性剥离、标签归一**全部真实生效**——
  手工跑不会经过 `ErrorAnalysis` 契约，会**高估**输出合格率。
- ⚠️ 更宽松的地方：**认可率由 AI 判定**（另一个独立调用当评审），不是人肉判。
  AI 评审会有偏（可能比自己更宽容）。故本脚本的结论是**下界参考**，
  最终仍需人工抽查 `report.md` 里的逐条结果。

## 红线守卫

- **不写库**：只调 `analyze()`（纯计算），不碰 `library` / `errorpipe.persist`。
  验证前后对用户真实库取 SHA256 比对，不一致即报错。
- **不复制提示词**：用 `render_prompt("error_analysis.md", ...)` 从仓库读，
  避免「改了提示词但评测还用旧的」。
- **correct 只由用户提供**：题样里 `answer` 是人工写的标准答案，
  脚本不生成、不修正标准答案。

## 用法

    python pack/stage0-attribution-eval.py                 # 全量 20 题
    python pack/stage0-attribution-eval.py --limit 3       # 冒烟
    python pack/stage0-attribution-eval.py --no-judge      # 只出归因，不判认可率

产物：`.workbuddy-ai/tmp/stage0/<ts>/` 下的 `report.md` + `results.jsonl`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit.core import db as dbs  # noqa: E402
from medkit.core.llm import LLMError  # noqa: E402
from medkit.agents import error_analysis as agent  # noqa: E402
from medkit.core import metacog  # noqa: E402

CASES_PATH = ROOT / "pack" / "stage0_cases.json"
OUT_ROOT = ROOT / ".workbuddy-ai" / "tmp" / "stage0"


# ------------------------------------------------------------------ 基线取证
def _db_fingerprint() -> dict[str, object]:
    """用户真实库指纹（只读）。用于证明本次评测没写库。"""
    p = pathlib.Path(dbs.DB_PATH)
    if not p.exists():
        return {"path": str(p), "exists": False}
    return {
        "path": str(p),
        "exists": True,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "size": p.stat().st_size,
        "sidecars": sorted(x.name for x in p.parent.glob("medkit.db*")),
    }


# ------------------------------------------------------------------ 评审 prompt
_JUDGE_SYS = """你是医学考研错题分析的严格评审。我会给你一道错题的背景信息，
和一份 AI 生成的归因分析。你的任务是判断这份归因**对考生是否真的有用**。

评分标准（四项，各 0/1 分）：

1. **标签正确**：`error_tag` 是否真实反映考生的错误性质？给出你判断的标签。
   注意：考生自报的人工标签仅供参考，**可能本身就不准**——不要盲从它。
2. **证据具体**：`evidence` 是否指向了考生原话或答案偏离的**具体点**，
   而不是「基础不牢」这类空话？
3. **修正可背**：`fix` 是否是一句**可背诵、可执行**的结论（不是又一段解析、不是废话）？
4. **反事实有效**：`counterfactual` 是否真的「改一个条件就会翻转答案」？
   （如果它只是把原题重问一遍、或改的条件不影响答案，判 0）

**认可（accept）= 四项中至少 3 项为 1，且第 1 项必须为 1。**
（标签都判错的分析没有采纳价值，无论其余写得多好。）

只输出一个 JSON 对象，不要任何其他文字：
{"tag_judge": "六类标签之一", "tag_ok": 1, "evidence_ok": 1, "fix_ok": 1, "counterfactual_ok": 1,
 "accept": true, "reason": "一句话说明扣分点（全部满分则写 无扣分）"}"""


def _judge(client, case: dict, analysis: dict) -> dict:
    """AI 评审：独立于归因调用，避免自评自夸。"""
    human_tag = case.get("human_tag") or "（考生未填）"
    payload = f"""## 错题背景

题干：{case["question"]}
选项：{" / ".join(case.get("options") or [])}
考生答案：{case["user_answer"]}
正确答案：{case["answer"]}
考生把握程度：{case.get("confidence")}/5
考生自述想法：{case.get("my_reasoning") or "（未填写）"}
考生自报的归因标签（仅供参考，可能不准）：{human_tag}

## 待评审的 AI 归因

error_tag: {analysis.get("error_tag")}
evidence: {analysis.get("evidence")}
kp_point: {analysis.get("kp_point")}
variants: {json.dumps(analysis.get("variants"), ensure_ascii=False)}
fix: {analysis.get("fix")}
counterfactual: {analysis.get("counterfactual")}
review_chapters: {json.dumps(analysis.get("review_chapters"), ensure_ascii=False)}
where_uncertain: {json.dumps(analysis.get("where_uncertain"), ensure_ascii=False)}

请按评分标准输出 JSON。"""
    raw = client.chat(
        [{"role": "system", "content": _JUDGE_SYS},
         {"role": "user", "content": payload}],
        temperature=0.0, max_tokens=2000)
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return {"accept": None, "reason": f"评审输出非 JSON：{(raw or '')[:120]!r}"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"accept": None, "reason": f"评审 JSON 解析失败：{e}"}


# ------------------------------------------------------------------ 废话检测（规则层）
_BANNED = ("扎实基础", "多做题", "好好复习", "加强记忆", "要提高", "建议多看", "认真审题",
           "注意审题", "基础不牢", "回归教材", "多看几遍")


def _boilerplate_scan(analysis: dict) -> list[str]:
    """规则层兜底：AI 评审可能比人宽容，这里再查一遍已知废话套路。"""
    hits = []
    for field in ("evidence", "fix", "counterfactual"):
        txt = str(analysis.get(field) or "")
        for b in _BANNED:
            if b in txt:
                hits.append(f"{field} 含套路话『{b}』")
    return hits


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    ap.add_argument("--no-judge", action="store_true", help="跳过 AI 评审")
    ap.add_argument("--cases", default=str(CASES_PATH))
    args = ap.parse_args()

    cases = json.loads(pathlib.Path(args.cases).read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]

    before = _db_fingerprint()
    print(f"[基线] 真实库 {before['path']}")
    print(f"[基线] sha256={str(before.get('sha256'))[:16]}… size={before.get('size')}")

    client = agent.make_client()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = OUT_ROOT / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for i, case in enumerate(cases, 1):
        cid = case.get("id") or f"C{i:02d}"
        rec = {
            "question": case["question"],
            "options": case.get("options") or [],
            "user_answer": case["user_answer"],
            "answer": case["answer"],
            "confidence": case.get("confidence"),
            "my_reasoning": case.get("my_reasoning") or "",
            "subject": case.get("subject") or "",
            "chapter": case.get("chapter") or "",
            "topic": case.get("topic") or "",
        }
        print(f"\n[{i}/{len(cases)}] {cid} …", flush=True)
        t0 = time.time()
        try:
            analysis = agent.analyze(client, rec)
            err = ""
        except LLMError as e:
            analysis, err = None, str(e)
        dt = time.time() - t0

        row = {"id": cid, "ok": analysis is not None, "error": err,
               "seconds": round(dt, 1), "analysis": analysis,
               "human_tag": case.get("human_tag") or "",
               "boilerplate": _boilerplate_scan(analysis) if analysis else []}

        if analysis is None:
            print(f"    ✗ 契约未过：{err[:100]}")
        else:
            print(f"    ✓ {analysis.get('error_tag')} · {dt:.1f}s · "
                  f"fix={str(analysis.get('fix'))[:40]!r}")
            if row["boilerplate"]:
                print(f"    ⚠ 套路话：{row['boilerplate']}")
            if not args.no_judge:
                j = _judge(client, case, analysis)
                row["judge"] = j
                mark = {True: "认可", False: "不认可", None: "评审失败"}[j.get("accept")]
                print(f"    → 评审：{mark} · {j.get('reason', '')[:70]}")

        rows.append(row)

    after = _db_fingerprint()

    # ---- 聚合
    #
    # ⚠️ 口径陷阱（第一版踩过）：只把「可评审」的行放进分母，会得到 100% 的假象——
    # 契约失败的题被静默排除在分母之外了。**必须以总题数作分母**，
    # 因为「契约没过」本身就是考生要承受的失败（他拿到的是「AI 归因失败」）。
    total = len(rows)
    contract_ok = sum(1 for r in rows if r["ok"])
    judged = [r for r in rows if r.get("judge", {}).get("accept") is not None]
    accepted = [r for r in judged if r["judge"]["accept"]]
    # 口径 A：仅可评审（旧口径，只用于对照，不作结论）
    rate_judged = (len(accepted) / len(judged) * 100) if judged else 0.0
    # 口径 B：全部题目（诚实口径，等于「考生拿去能用的比例」）
    rate_total = (len(accepted) / total * 100) if total else 0.0
    tag_hit = sum(1 for r in rows if r["ok"] and r["human_tag"]
                  and r["human_tag"] == r["analysis"].get("error_tag"))
    tag_denom = sum(1 for r in rows if r["human_tag"] and r["ok"])
    boiler = sum(1 for r in rows if r["boilerplate"])

    summary = {
        "total": total,
        "contract_ok": contract_ok,
        "contract_rate": round(contract_ok / total * 100, 1) if total else 0,
        "judged": len(judged),
        "accepted": len(accepted),
        "accept_rate_judged": round(rate_judged, 1),
        "accept_rate_total": round(rate_total, 1),
        "gate_70": rate_total >= 70.0,
        "human_tag_hit": tag_hit,
        "human_tag_denom": tag_denom,
        "boilerplate_cases": boiler,
        "db_before": before,
        "db_after": after,
        "db_untouched": before.get("sha256") == after.get("sha256"),
    }

    # ---- 产物
    (outdir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8", newline="\n")

    lines = [
        "# EP-01 阶段 0 · 归因质量验证报告",
        "",
        f"- 运行时间：{stamp}",
        f"- 题目数：**{total}**",
        f"- 契约通过：**{contract_ok}/{total}**（{summary['contract_rate']}%）",
        f"- **认可率（诚实口径 = 认可数 / 总题数）：{len(accepted)}/{total} = "
        f"{summary['accept_rate_total']}%**　闸门 ≥70% → "
        f"**{'通过 ✅' if summary['gate_70'] else '未通过 ❌'}**",
        f"- 认可率（仅计可评审的 {len(judged)} 题）：{summary['accept_rate_judged']}%"
        f"　← 仅作对照：把契约失败的题排除在分母外会虚高",
        f"- 与人工标签一致：{tag_hit}/{tag_denom}",
        f"- 命中套路话的题数：{boiler}",
        f"- 真实库未被写入：{'✅' if summary['db_untouched'] else '❌ 异常！'}",
        "",
        "## 逐题明细",
        "",
        "| # | id | 契约 | 标签 | 与我人工标签 | 评审 | 扣分点 | 耗时 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        j = r.get("judge") or {}
        tag = (r["analysis"] or {}).get("error_tag", "—")
        contract = "✓" if r["ok"] else "✗"
        if j.get("accept") is True:
            verdict = "认可"
        elif j.get("accept") is False:
            verdict = "不认可"
        else:
            verdict = "—"
        same = "=" if r["human_tag"] and r["human_tag"] == tag else ""
        lines.append(
            f"| {rows.index(r) + 1} | {r['id']} | {contract} | {tag} | {same} | "
            f"{verdict} | {str(j.get('reason', r['error'] or ''))[:60]} | {r['seconds']}s |")

    lines += ["", "## 逐题完整输出", ""]
    for r in rows:
        lines.append(f"### {r['id']}")
        lines.append("")
        if not r["ok"]:
            lines.append(f"**契约未过**：{r['error']}")
            lines.append("")
            continue
        a = r["analysis"]
        lines += [
            f"- **error_tag**：{a.get('error_tag')}　（人工标签：{r['human_tag'] or '未填'}）",
            f"- **evidence**：{a.get('evidence')}",
            f"- **kp_point**：{a.get('kp_point')}",
            f"- **variants**：{json.dumps(a.get('variants'), ensure_ascii=False)}",
            f"- **fix**：{a.get('fix')}",
            f"- **counterfactual**：{a.get('counterfactual')}",
            f"- **review_chapters**：{json.dumps(a.get('review_chapters'), ensure_ascii=False)}",
            f"- **where_uncertain**：{json.dumps(a.get('where_uncertain'), ensure_ascii=False)}",
        ]
        if r["boilerplate"]:
            lines.append(f"- ⚠ **套路话命中**：{r['boilerplate']}")
        j = r.get("judge") or {}
        if j:
            lines.append(f"- **评审**：{j.get('reason', '')}")
        lines.append("")

    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    print("\n" + "=" * 62)
    print(f"契约通过 {contract_ok}/{total}　|　认可率(诚实) {summary['accept_rate_total']}% "
          f"（{len(accepted)}/{total}）　闸门 {'通过 ✅' if summary['gate_70'] else '未通过 ❌'}")
    print(f"仅计可评审 {len(judged)} 题时为 {summary['accept_rate_judged']}%（对照用，非结论）")
    print(f"套路话命中 {boiler} 题　|　真实库未被写入：{'✅' if summary['db_untouched'] else '❌'}")
    print(f"报告：{outdir / 'report.md'}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
