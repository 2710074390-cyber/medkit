#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 题样预检（跑真实模型**之前**的体检，只读、不调 API、不烧钱）。

为什么要先预检
--------------
一旦充值，每跑一次题样都要花真金白银。若题样本身有问题（答案字母越界、
选项与答案对不上、题干重复），跑出来的分数**无意义且要重跑**。
本脚本把这些在**本地、零成本**地挡住。

与 `stage0-cases-expand-30.py --check` 的区别
-------------------------------------------
那个脚本的校验含**构造约束**（`my_reasoning` 长度 28~45 字、
`confidence ∈ {2,3}`）——那是我模仿既有题样风格时定的，**真实错题不该受它约束**
（你的推理可能 15 字也可能 200 字，置信度可能是 1 也可能是 5）。
本脚本只保留**真判据**：

| 判据 | 类别 | 不满足的后果 |
|---|---|---|
| 必备字段齐全 | 硬 | 评测脚本 KeyError 或写入空值 |
| `options` 字母前缀连续（A. B. C. …） | 硬 | 答案字母映射不到选项文本 |
| `answer` / `user_answer` 是合法字母且在选项范围内 | 硬 | 同上 |
| `answer != user_answer` | 硬 | 混进答对的题 → **分母污染** |
| `human_tag` ∈ `ANALYSIS_TAGS`（6 类） | 硬 | 「与人工标签一致率」无法计算 |
| `my_reasoning` 非空 | 软（警告） | 归因只能靠答案偏离方向猜，质量不同 |
| id 唯一 | 硬 | 结果表串行、定位错题 |
| 题干重复检测 | 软（警告） | 重复题会让某一维的权重被放大 |
| `confidence` ∈ 1..5 或 None | 软（警告） | 超出区间的自评在 prompt 里显示异常 |
| 题目数 vs §3.4 的 30 题口径 | 提示 | 样本量不足时结论要写明 |

用法
----
    python pack/stage0-cases-check.py                          # 默认查 pack/stage0_cases.json
    python pack/stage0-cases-check.py --cases pack/stage0_cases_real.json
    python pack/stage0-cases-check.py --strict                 # 软问题也当失败（CI 用）

退出码：0 = 通过（可能有警告）；1 = 有硬问题；2 = 用法/文件错误。
"""
import argparse
import json
import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "pack" / "stage0_cases.json"

REQUIRED = ("id", "subject", "chapter", "topic", "question", "options",
            "answer", "user_answer", "confidence", "my_reasoning", "human_tag")
LETTERS = "ABCDEFGH"
# 《总纲》§3.4 的题量口径
TARGET_N = 30
# prompt 模板里会出现的占位符
PROMPT_LIMITS = {"STEM_MAX_CHARS": 3000, "REASONING_MAX_CHARS": 200}


def _tags() -> set:
    """从 schema 取权威的 6 类标签，避免手写字面量漂移。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from medkit.core import schema as schema_mod

    return set(schema_mod.ANALYSIS_TAGS)


def _norm(s: str) -> str:
    """题干归一：去空白、去标点差异、全角转半角，用于查重。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(ch for ch in s if not ch.isspace())


def check_case(c: dict, tags: set) -> tuple:
    """返回 (hard, soft)。hard 非空 = 该题不可用；soft 非空 = 可用但有隐患。"""
    hard, soft = [], []

    for k in REQUIRED:
        if k not in c:
            hard.append("缺字段 %s" % k)
    if hard:
        return hard, soft   # 字段都不全，后续检查无意义

    opts = list(c.get("options") or [])
    if not opts:
        hard.append("options 为空")
    else:
        want = LETTERS[: len(opts)]
        for i, o in enumerate(opts):
            o = str(o)
            if not o.startswith(want[i] + "."):
                hard.append("选项 %d 前缀不符（期望 %s.）: %r"
                            % (i + 1, want[i], o[:12]))
        # 选项文本（去掉前缀）是否为空
        for i, o in enumerate(opts):
            body = str(o).split(".", 1)[-1].strip() if "." in str(o) else ""
            if not body:
                hard.append("选项 %s 无文本内容" % want[i])

    ans = str(c.get("answer") or "").strip()
    ua = str(c.get("user_answer") or "").strip()
    if not ans:
        hard.append("缺正确答案")
    if not ua:
        hard.append("缺考生答案")
    if ans and ua and ans == ua:
        hard.append("answer == user_answer（这是错题集，会污染分母）")
    if ans and ans not in LETTERS[: len(opts)]:
        hard.append("答案 %r 不在选项字母范围 %s" % (ans, LETTERS[: len(opts)]))
    if ua and ua not in LETTERS[: len(opts)]:
        hard.append("考生答案 %r 不在选项字母范围 %s" % (ua, LETTERS[: len(opts)]))

    q = str(c.get("question") or "").strip()
    if not q:
        hard.append("题干为空")
    elif len(q) > PROMPT_LIMITS["STEM_MAX_CHARS"]:
        soft.append("题干 %d 字 > 模板上限 %d，会被截断"
                    % (len(q), PROMPT_LIMITS["STEM_MAX_CHARS"]))

    tag = str(c.get("human_tag") or "").strip()
    if not tag:
        hard.append("缺人工归因 human_tag（无法判 AI 对不对）")
    elif tag not in tags:
        hard.append("human_tag %r 不在权威 6 类 %s" % (tag, sorted(tags)))

    reason = str(c.get("my_reasoning") or "").strip()
    if not reason:
        soft.append("my_reasoning 为空（归因只能靠答案偏离方向推断，质量不同）")
    elif len(reason) > PROMPT_LIMITS["REASONING_MAX_CHARS"]:
        soft.append("my_reasoning %d 字 > 模板上限 %d，会被截断"
                    % (len(reason), PROMPT_LIMITS["REASONING_MAX_CHARS"]))

    conf = c.get("confidence")
    if conf is not None:
        try:
            ci = int(conf)
            if not (1 <= ci <= 5):
                soft.append("confidence=%r 不在 1~5" % conf)
        except (TypeError, ValueError):
            soft.append("confidence=%r 不是数值" % conf)

    return hard, soft


def main(argv=None) -> int:
    # Windows 控制台常为 cp1252 而本脚本输出中文——强制 UTF-8，避免 UnicodeEncodeError（R6-11 CI 实证）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(description="阶段 0 题样预检（只读、零成本）")
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--strict", action="store_true",
                    help="软问题也视为失败（适合放进 CI）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.cases)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        print("[用法错误] 题样文件不存在：%s" % path, file=sys.stderr)
        return 2
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print("[用法错误] 不是合法 JSON：%s" % e, file=sys.stderr)
        return 2
    if not isinstance(cases, list):
        print("[用法错误] 顶层应为数组", file=sys.stderr)
        return 2

    tags = _tags()
    print("=" * 62)
    print("阶段 0 题样预检（本地、零成本，不调 API）")
    print("=" * 62)
    print("文件：%s" % path)
    print("题数：%d" % len(cases))
    print("权威标签：%s" % " / ".join(sorted(tags)))
    print()

    n_hard = n_soft = 0
    seen_ids, seen_stems = {}, {}

    for i, c in enumerate(cases, 1):
        cid = c.get("id") or "（无 id）"
        if cid in seen_ids:
            print("[FAIL] 第 %d 题 id=%r 与第 %d 题重复" % (i, cid, seen_ids[cid]))
            n_hard += 1
        else:
            seen_ids[cid] = i

        hard, soft = check_case(c, tags)
        for msg in hard:
            print("[FAIL] %s: %s" % (cid, msg))
            n_hard += 1
        for msg in soft:
            print("[WARN] %s: %s" % (cid, msg))
            n_soft += 1

        stem = _norm(c.get("question"))
        if stem:
            if stem in seen_stems:
                print("[WARN] %s: 题干与 %s 重复（重复题会放大该考点的权重）"
                      % (cid, seen_stems[stem]))
                n_soft += 1
            else:
                seen_stems[stem] = cid

    # 口径提示
    print()
    if len(cases) >= TARGET_N:
        print("[OK] 题量 %d ≥ %d（《总纲》§3.4 口径）。" % (len(cases), TARGET_N))
    elif len(cases) >= TARGET_N * 0.6:
        print("[提示] 题量 %d < %d。可跑，但样本量偏少，结论须写明。"
              % (len(cases), TARGET_N))
    else:
        print("[提示] 题量 %d 明显不足（< %d 的 60%%）。建议先补题样再烧模型额度。"
              % (len(cases), TARGET_N))

    print()
    print("-" * 62)
    print("硬问题 %d 处　软警告 %d 处" % (n_hard, n_soft))
    if n_hard:
        print("结论：**不可用**，先修硬问题；修完再跑模型。")
        return 1
    if n_soft and args.strict:
        print("结论：--strict 下软警告也算失败。")
        return 1
    if n_soft:
        print("结论：可用（有软警告，跑之前看一眼是否要处理）。")
        return 0
    print("结论：可用，无问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
