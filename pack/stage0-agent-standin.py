#!/usr/bin/env python
"""阶段 0 · 「人工作答」替身客户端（不联网，由 Agent 逐题作答）。

## 为什么有这个脚本

DeepSeek 余额耗尽（HTTP 402），真实链路 `deepseek-v4-flash` 跑不了。
本脚本让 **Agent 本体**（会话里的模型）充当 `agent.analyze()` 的 LLM，
补出归因质量数据。

## 它到底替换了什么、保留了什么

替换的**只有 `client` 这一个对象**（`agent.analyze(client, rec)` 的第一个参数）。
其余全部真实：

    analyze(client, rec)
      ├─ render_prompt("error_analysis.md", **payload)   ← 真实提示词（仓库文件）
      ├─ client.chat_json(..., schema=ErrorAnalysis)     ← 【仅此处被替换】
      ├─ ErrorAnalysis.model_validate(parsed)            ← 真实契约校验
      └─ 防御性剥离 correct/answer/user_answer            ← 真实红线

即：**契约层、红线层、schema 层一次都没绕过。**

## 这个结果能证明什么、不能证明什么（必须随产物一起交付）

| 事项 | 能否证明 |
|---|---|
| 归因质量在**强模型**下的表现 | ✅ 能——且消掉了「AI 评审偏宽容」的风险（同一模型自评需另设判据） |
| 提示词是否把 6 个交付物引导清楚 | ✅ 能 |
| **产品端到端可用性** | ❌ **不能**——真实链路是 deepseek-v4-flash，不是 Agent |
| **`max_tokens` 截断缺陷已修好** | ❌ **不能**——Agent 不吃该 token 预算，**这是最容易被误读的一点** |

**定位：归因质量的「上界参考」，不是闸门判定依据。** 闸门仍需充值后跑真实模型。

## 用法（两阶段，可审计）

    # 阶段 1：导出 20 道题的完整 prompt（Agent 读它来作答）
    python pack/stage0-agent-standin.py --dump

    #     → 产出 .workbuddy-ai/tmp/stage0-agent/<ts>/prompts/ 下的 20 个 .md
    #     → Agent 逐题作答，写入 answers/ 下的 20 个 .json

    # 阶段 2：把作答喂回真实 analyze()，跑契约 + 评审 + 聚合
    python pack/stage0-agent-standin.py --run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit.agents import error_analysis as agent  # noqa: E402
from medkit.core import schema as schema_mod  # noqa: E402
from medkit.core.llm import LLMError  # noqa: E402

CASES_PATH = ROOT / "pack" / "stage0_cases.json"
OUT_ROOT = ROOT / ".workbuddy-ai" / "tmp" / "stage0-agent"

# 与真实评测脚本共用同一套量规，避免两处口径漂移
import importlib.util as _ilu  # noqa: E402

_eval_spec = _ilu.spec_from_file_location(
    "_stage0_eval", ROOT / "pack" / "stage0-attribution-eval.py")
_eval_mod = _ilu.module_from_spec(_eval_spec)  # type: ignore[arg-type]
_eval_spec.loader.exec_module(_eval_mod)  # type: ignore[union-attr]


class StandinClient:
    """替身客户端：按题号从 answers/ 读 Agent 写好的 JSON，不联网。

    刻意**不实现任何「智能」**——不给默认值、不生成兜底内容。
    答不出来就抛 LLMError，让这一题记为契约失败（与真实模型行为对齐）。
    """

    def __init__(self, answers_dir: pathlib.Path, order: list[str]) -> None:
        self.answers_dir = answers_dir
        self.order = order          # 题号顺序，用于把第 n 次调用映射到第 n 题
        self.calls = 0
        self.trace: list[dict] = []  # 审计：每次调用的题号与命中情况

    def chat_json(self, messages, temperature=0.7, max_tokens=None, schema=None):
        cid = self.order[self.calls] if self.calls < len(self.order) else f"extra{self.calls}"
        self.calls += 1
        path = self.answers_dir / f"{cid}.json"
        if not path.exists():
            self.trace.append({"id": cid, "status": "missing"})
            raise LLMError(f"Agent 未作答：{path.name} 不存在")
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            self.trace.append({"id": cid, "status": "bad_json", "err": str(e)})
            raise LLMError(f"Agent 作答不是合法 JSON（{cid}）：{e}") from e
        # 关键：schema 校验由**上层** chat_json 的调用方做（这里就是 analyze），
        # 所以替身不预校验——这样「契约是否通过」才是被测对象，而不是被替身粉饰。
        self.trace.append({"id": cid, "status": "ok"})
        return parsed


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _rec(case: dict) -> dict:
    return {
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


def do_dump() -> None:
    """阶段 1：导出每题的真实 prompt，供 Agent 作答。"""
    from medkit.agents import render_prompt

    cases = _cases()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = OUT_ROOT / stamp
    (base / "prompts").mkdir(parents=True, exist_ok=True)
    (base / "answers").mkdir(parents=True, exist_ok=True)

    for i, case in enumerate(cases, 1):
        cid = case.get("id") or f"C{i:02d}"
        system = render_prompt("error_analysis.md", **agent._payload(_rec(case)))
        (base / "prompts" / f"{cid}.md").write_text(system, encoding="utf-8", newline="\n")

    # 契约字段清单也导出，让 Agent 作答时对着写
    fields = list(schema_mod.ErrorAnalysis.model_fields.keys())
    (base / "CONTRACT.md").write_text(
        "# ErrorAnalysis 契约字段（Agent 作答必须逐项覆盖）\n\n"
        + "\n".join(f"- `{f}`" for f in fields)
        + "\n\n输出必须是**唯一一个 JSON 对象**，无 Markdown 围栏、无其他文字。\n"
        + "约束见每个 prompt 末尾（原样来自 medkit/prompts/error_analysis.md）。\n",
        encoding="utf-8", newline="\n")

    print(f"[阶段1] 已导出 {len(cases)} 个 prompt → {base / 'prompts'}")
    print(f"[阶段1] 契约字段 → {base / 'CONTRACT.md'}")
    print(f"[阶段1] 请把作答写到 → {base / 'answers'}/<题号>.json")
    print(f"\nSTAMP={stamp}")


def do_run(stamp: str) -> None:
    """阶段 2：把 Agent 作答喂回真实 analyze()，跑契约 + 评审 + 聚合。"""
    base = OUT_ROOT / stamp
    answers = base / "answers"
    if not answers.is_dir():
        raise SystemExit(f"未找到作答目录：{answers}")

    cases = _cases()
    order = [c.get("id") or f"C{i:02d}" for i, c in enumerate(cases, 1)]
    client = StandinClient(answers, order)

    before = _eval_mod._db_fingerprint()
    print(f"[基线] 真实库 sha256={str(before.get('sha256'))[:16]}…")

    rows: list[dict] = []
    for i, case in enumerate(cases, 1):
        cid = case.get("id") or f"C{i:02d}"
        print(f"\n[{i}/{len(cases)}] {cid} …", flush=True)
        t0 = time.time()
        try:
            analysis = agent.analyze(client, _rec(case))
            err = ""
        except LLMError as e:
            analysis, err = None, str(e)
        except Exception as e:  # noqa: BLE001
            analysis, err = None, f"{type(e).__name__}: {e}"
        dt = time.time() - t0

        row = {"id": cid, "ok": analysis is not None, "error": err,
               "seconds": round(dt, 1), "analysis": analysis,
               "human_tag": case.get("human_tag") or "",
               "boilerplate": _eval_mod._boilerplate_scan(analysis) if analysis else []}
        if analysis is None:
            print(f"    ✗ 契约未过：{err[:120]}")
        else:
            print(f"    ✓ {analysis.get('error_tag')} · fix={str(analysis.get('fix'))[:44]!r}")
            if row["boilerplate"]:
                print(f"    ⚠ 套路话：{row['boilerplate']}")
        rows.append(row)

    after = _eval_mod._db_fingerprint()
    summary = _summarize(rows, before, after)
    _write_report(base, rows, summary, stamp)
    _print_summary(summary, base)


def _summarize(rows: list[dict], before: dict, after: dict) -> dict:
    total = len(rows)
    contract_ok = sum(1 for r in rows if r["ok"])
    DIMS = ("tag_ok", "evidence_ok", "fix_ok", "counterfactual_ok", "variants_ok", "kp_ok")
    # 规则层维度统计（不依赖第二个 LLM 调用）：
    # 替身模式下「AI 评审」由 Agent 自己做没有意义（自评自夸），
    # 改为**结构层硬判据**：字段非空 + 标签合法 + variants 条数 + fix 字数 + 原话引用。
    dim_hit = {d: 0 for d in DIMS}
    detail: list[dict] = []
    for r in rows:
        if not r["ok"]:
            detail.append({"id": r["id"], "structural": None})
            continue
        a = r["analysis"]
        case = next(c for c in _cases() if (c.get("id") or "") == r["id"])
        hits = _structural_hits(a, case)
        for k, v in hits.items():
            dim_hit[k] += 1 if v else 0
        detail.append({"id": r["id"], "structural": hits,
                       "tag_match_human": (a.get("error_tag") == case.get("human_tag"))})
    tag_match = sum(1 for d in detail if d.get("tag_match_human"))
    tag_denom = sum(1 for d in detail if d.get("structural") is not None)
    return {
        "total": total,
        "contract_ok": contract_ok,
        "contract_rate": round(contract_ok / total * 100, 1) if total else 0.0,
        "dims": {d: {"hit": dim_hit[d], "denom": contract_ok,
                     "rate": round(dim_hit[d] / contract_ok * 100, 1) if contract_ok else 0.0}
                 for d in DIMS},
        "tag_match": tag_match, "tag_denom": tag_denom,
        "boilerplate_cases": sum(1 for r in rows if r["boilerplate"]),
        "db_before": before, "db_after": after,
        "db_untouched": before.get("sha256") == after.get("sha256"),
        "detail": detail,
    }


_DIM_LABEL = _eval_mod._DIM_LABEL


def _structural_hits(a: dict, case: dict) -> dict:
    """结构层硬判据——不靠第二个模型，全部可复算。

    这是替身模式的**结构性妥协**：无独立评审 ⇒ 只能判「有没有写对形状」，
    判不了「写得对不对」。故结论必须标注为上界参考。
    """
    tags = set(schema_mod.ANALYSIS_TAGS)
    variants = a.get("variants") or []
    fix = str(a.get("fix") or "")
    ev = str(a.get("evidence") or "")
    kp = str(a.get("kp_point") or "")
    cf = str(a.get("counterfactual") or "")
    # 原话引用：evidence 是否含考生原话里的 6 字以上片段
    reason = str(case.get("my_reasoning") or "")
    quote_ok = any(reason[i:i + 6] in ev for i in range(max(0, len(reason) - 5))) if reason else False
    # kp_point 不能只是章节级的大词——「心脏」「病理」这类过泛定位等同于没定位。
    # 判据只用「长度 + 非全泛词」，**刻意不引入「与题样 topic 词面交集」**：
    # 实测证明那是错判据——高质量的考点定位（如『压力感受性反射』）与题样 topic
    # （『心血管活动的调节』）本就不该有字面重合，用它会把好答案判红（误伤 3 题）。
    # 详见 docs/EP-01_阶段0_归因质量验证报告.md「替身模式」节的反向验证记录。
    _TOO_BROAD = {"心脏", "病理", "生理", "内科", "外科", "生化", "医学", "疾病", "血液"}
    kp_stripped = kp.strip()
    kp_specific = (len(kp_stripped) >= 8 and kp_stripped not in _TOO_BROAD)
    return {
        "tag_ok": a.get("error_tag") in tags,
        "evidence_ok": bool(ev.strip()) and quote_ok,
        "fix_ok": bool(fix.strip()) and len(fix) <= 60,
        "counterfactual_ok": bool(cf.strip()) and ("？" in cf or "?" in cf),
        "variants_ok": len(variants) >= 2 and all(str(v).strip() for v in variants),
        "kp_ok": kp_specific,
    }


def _write_report(base: pathlib.Path, rows: list[dict], s: dict, stamp: str) -> None:
    lines = [
        "# EP-01 阶段 0 · 归因质量验证（**Agent 替身模式**· 上界参考）",
        "",
        "> ⚠️ **本报告不是闸门判定依据。** 归因由 **Agent 本体**产出，而非产品真实链路",
        "> `deepseek-v4-flash`。故本报告只回答「**提示词 + 契约在强模型下的表现上限**」，",
        "> **不能**回答「产品端到端可用性」，**更不能**证明 `max_tokens` 截断缺陷已修复",
        "> （Agent 不吃该 token 预算）。见 `pack/stage0-agent-standin.py` 头部说明。",
        "",
        f"- 运行时间：{stamp}",
        f"- 题目数：**{s['total']}**",
        f"- 契约通过：**{s['contract_ok']}/{s['total']}**（{s['contract_rate']}%）",
        f"- 与人工标签一致：{s['tag_match']}/{s['tag_denom']}",
        f"- 命中套路话的题数：{s['boilerplate_cases']}",
        f"- 真实库未被写入：{'✅' if s['db_untouched'] else '❌ 异常！'}",
        "",
        "## 六维分项（**结构层硬判据**，非 AI 评审）",
        "",
        "> 替身模式下没有独立的第二个模型可当评审（自评自夸无意义），",
        "> 故改用**可复算的结构判据**：字段非空 / 标签合法 / `variants` ≥2 条 /",
        "> `fix` ≤60 字 / `counterfactual` 是问句 / `evidence` 引用了考生原话 ≥6 字。",
        "> **它只判「写得对不对形状」，判不了「写得对不对」。这是上界参考的成因。**",
        "",
        "| 维度 | 字段 | 命中 | 命中率 |",
        "|---|---|---|---|",
        *[f"| {_DIM_LABEL[d]} | `{d.replace('_ok', '')}` | "
          f"{s['dims'][d]['hit']}/{s['dims'][d]['denom']} | {s['dims'][d]['rate']}% |"
          for d in s["dims"]],
        "",
        "## 逐题明细",
        "",
        "| # | id | 契约 | 标签 | 与我人工标签 | 结构分 | 耗时 |",
        "|---|---|---|---|---|---|---|",
    ]
    tag_map = {d["id"]: d for d in s["detail"]}
    for i, r in enumerate(rows, 1):
        a = r["analysis"] or {}
        st = tag_map.get(r["id"], {}).get("structural")
        score = f"{sum(1 for v in st.values() if v)}/6" if st else "—"
        same = "=" if tag_map.get(r["id"], {}).get("tag_match_human") else ""
        lines.append(f"| {i} | {r['id']} | {'✓' if r['ok'] else '✗'} | "
                     f"{a.get('error_tag', '—')} | {same} | {score} | {r['seconds']}s |")
    lines += ["", "## 逐题归因全文", ""]
    for r in rows:
        lines += [f"### {r['id']}", ""]
        if not r["ok"]:
            lines += [f"- ❌ 契约未过：`{r['error']}`", ""]
            continue
        a = r["analysis"]
        lines += [f"- `error_tag`：{a.get('error_tag')}",
                  f"- `evidence`：{a.get('evidence')}",
                  f"- `kp_point`：{a.get('kp_point')}",
                  f"- `variants`：{json.dumps(a.get('variants'), ensure_ascii=False)}",
                  f"- `fix`：{a.get('fix')}",
                  f"- `counterfactual`：{a.get('counterfactual')}",
                  f"- `review_chapters`：{json.dumps(a.get('review_chapters'), ensure_ascii=False)}",
                  f"- `where_uncertain`：{json.dumps(a.get('where_uncertain'), ensure_ascii=False)}",
                  ""]
    (base / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (base / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8", newline="\n")


def _print_summary(s: dict, base: pathlib.Path) -> None:
    print("\n" + "=" * 62)
    print("【Agent 替身模式 · 上界参考，非闸门判定】")
    print(f"契约通过 {s['contract_ok']}/{s['total']}　|　"
          f"套路话命中 {s['boilerplate_cases']} 题　|　"
          f"真实库未被写入：{'✅' if s['db_untouched'] else '❌'}")
    print("六维（结构层）：" + "　".join(
        f"{_DIM_LABEL[d]} {s['dims'][d]['rate']}%" for d in s["dims"]))
    print(f"报告：{base / 'report.md'}")
    print("=" * 62)


def main() -> None:
    # Windows 控制台常为 cp1252 而本脚本输出中文——强制 UTF-8，避免 UnicodeEncodeError（R6-11 CI 实证）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="阶段1：导出 prompt 供 Agent 作答")
    ap.add_argument("--run", action="store_true", help="阶段2：喂回 analyze 并聚合")
    ap.add_argument("--stamp", default="", help="--run 时指定阶段1 的产出目录名")
    args = ap.parse_args()
    if args.dump:
        do_dump()
    elif args.run:
        if not args.stamp:
            raise SystemExit("--run 必须带 --stamp <目录名>")
        do_run(args.stamp)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
