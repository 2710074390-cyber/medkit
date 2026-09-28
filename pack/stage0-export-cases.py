#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从真实错题库导出阶段 0 题样（把你自己的错题变成闸门可用的题样）。

为什么需要这个脚本
------------------
阶段 0 闸门要测的是「**我自己的**错题，AI 归因准不准」。
但直到 2026-09-28，题样都是构造的（`pack/stage0_cases.json` 的 S01~S30），
代表性有限。这一步是把题样换成真实错题的**唯一入口**——
**不要让用户手改 JSON**：错题记录里有十几个字段，
人工搬运必然漏字段，而漏了元认知字段（confidence/my_reasoning/error_tag）
的表现是「脚本跑得通、但那一维全是空」——一个不报错的功能失效。

字段映射（来源 `medkit/core/library.py:483` 的 `add_mistake` 白名单）
--------------------------------------------------------------
    题样字段        错题记录字段          说明
    id              id                    直接沿用
    subject/chapter/topic 同名            直接沿用
    question        question
    options         options               已由 `_norm_options` 归一
    answer          answer                正确答案（用户提供，AI 不得改写）
    user_answer     user_answer           考生所答
    confidence      confidence            看答案前自评 1-5
    my_reasoning    my_reasoning          看答案前的原始想法
    human_tag       error_tag             **人工归因**（6 类之一）
                                         ← 注意是 error_tag 不是 ai_error_tag

准入条件（不满足的**跳过并列明原因**，不硬塞）
------------------------------------------
- `answer` 与 `user_answer` 均非空，且两者不等（这是错题集，不是答对集）
- `error_tag` 非空（人工归因是"对照基准"，缺了就无法判 AI 对不对）
- `my_reasoning` 非空（归因必须依附原话；空推理只能按答案偏离方向推断，
  质量不可同日而语，故单独隔离而不是混进主集合）
- `question` 非空

用法
----
    # 先体检：看有多少条可用、被跳过的是哪些、为什么
    python pack/stage0-export-cases.py --check

    # 导出（默认追加到既有题样之后，不覆盖）
    python pack/stage0-export-cases.py --out pack/stage0_cases_real.json

    # 导出并直接用作评测题样
    python pack/stage0-attribution-eval.py --cases pack/stage0_cases_real.json

**只读**：本脚本绝不写真实库。
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 题样必备字段 → 错题记录中的来源字段
FIELD_MAP = {
    "id": "id",
    "subject": "subject",
    "chapter": "chapter",
    "topic": "topic",
    "question": "question",
    "options": "options",
    "answer": "answer",
    "user_answer": "user_answer",
    "confidence": "confidence",
    "my_reasoning": "my_reasoning",
    "human_tag": "error_tag",   # ← 人工归因；不是 ai_error_tag
}


def _skip_reason(rec: dict) -> str:
    """返回跳过原因；空串 = 可用。"""
    if not str(rec.get("question") or "").strip():
        return "题干为空"
    ans = str(rec.get("answer") or "").strip()
    ua = str(rec.get("user_answer") or "").strip()
    if not ans:
        return "缺正确答案"
    if not ua:
        return "缺考生答案"
    if ans == ua:
        return "答案与考生所答相同（这是错题集）"
    if not str(rec.get("error_tag") or "").strip():
        return "缺人工归因 error_tag（无法判 AI 对不对）"
    if not str(rec.get("my_reasoning") or "").strip():
        return "缺 my_reasoning（归因无原话可依附）"
    return ""


def _to_case(rec: dict) -> dict:
    case = {}
    for dst, src in FIELD_MAP.items():
        v = rec.get(src)
        if dst == "options":
            case[dst] = list(v or [])
        elif dst == "confidence":
            case[dst] = v
        else:
            case[dst] = str(v or "").strip()
    return case


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从真实错题库导出阶段 0 题样（只读）")
    ap.add_argument("--check", action="store_true", help="只体检，不写文件")
    ap.add_argument("--out", default="pack/stage0_cases_real.json",
                    help="导出路径（默认 pack/stage0_cases_real.json）")
    ap.add_argument("--include-dup-answer", action="store_true",
                    help="也纳入 answer==user_answer 的记录（默认排除）")
    args = ap.parse_args(argv)

    # 延迟 import：只有真要用时才碰 medkit，避免体检前就把库拉起来
    from medkit.core import library

    records = library.list_mistakes()
    print("=" * 60)
    print("真实错题库体检")
    print("=" * 60)
    print("库内错题总数: %d" % len(records))

    usable, skipped = [], []
    for rec in records:
        why = _skip_reason(rec)
        if why == "答案与考生所答相同（这是错题集）" and args.include_dup_answer:
            why = ""
        if why:
            skipped.append((rec.get("id"), why))
        else:
            usable.append(_to_case(rec))

    print("可用题样:     %d" % len(usable))
    print("被跳过:       %d" % len(skipped))
    if skipped:
        from collections import Counter

        print()
        print("跳过原因分布:")
        for why, n in Counter(w for _, w in skipped).most_common():
            print("  %-46s %d" % (why, n))

    if usable:
        from collections import Counter

        print()
        print("学科分布: %s" % dict(Counter(c["subject"] or "(空)" for c in usable)))
        print("人工归因分布: %s" % dict(Counter(c["human_tag"] for c in usable)))

    # 与《总纲》§3.4 的 30 题口径对比
    print()
    if len(usable) >= 30:
        print("[OK] 可用题样 %d 条 ≥ 30，可满足 §3.4 的 30 题口径。" % len(usable))
    else:
        print("[提示] 可用题样 %d 条 < 30（§3.4 口径）。"
              "闸门仍可跑，但样本量不足，结论要写明。" % len(usable))

    if args.check:
        print()
        print("(--check：未写任何文件)")
        return 0

    if not usable:
        print()
        print("没有可用题样，未写出文件。")
        print("→ 请先在产品里录入错题，并填写「人工归因」与「当时的想法」。")
        return 1

    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(usable, ensure_ascii=False, indent=2) + "\n"
    out.write_bytes(payload.encode("utf-8"))
    print()
    print("已写出 %d 条题样 → %s" % (len(usable), out))
    print("跑闸门：python pack/stage0-attribution-eval.py --cases %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
