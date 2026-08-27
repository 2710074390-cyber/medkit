# -*- coding: utf-8 -*-
"""K3 SPIKE：306 官方大纲 → 结构化种子素材（IMP-13 闭环）。

流水线（与指南 IMP-13 一致）：
  1. 输入：docs/spikes/k3_out/306大纲_2026.md（用户 PDF → 文本，OCR 抽样已核一致性）
  2. 抽取：medkit.core.syllabus.extract_outline —— chat_json + OutlineSubject 契约
     （按「第四部分 考查内容」锚点切分 6 科，逐科调用；deepseek 配置来自 ~/.medkit/config.json）
  3. 核验：脚本内置「独立结构化解析」作为真值（不复用 core 解析器，避免同源循环论证）
     —— 逐科计算条目 recall / precision（归一化子串匹配）+ 章名命中率
  4. 抽样：按科目分层取 10 条，输出 原文 ↔ 抽取结果 对照（供人核 ≥80%）
  5. 产物：docs/spikes/k3_out/K3_report_<ts>.json / .md
          docs/spikes/k3_out/syllabus_306_official.json（抽取原始结果）

usage: python docs/spikes/K3_syllabus_extract.py [md_path]
"""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MD = Path(r"docs/spikes/k3_out/306大纲_2026.md")
if len(sys.argv) > 1:
    MD = Path(sys.argv[1])
MD = MD if MD.is_absolute() else ROOT / MD
OUT_DIR = ROOT / "docs" / "spikes" / "k3_out"

_PART_ANCHOR = re.compile(r"第四部分\s*考查内容")
_SUBJECT_RE = re.compile(r"^\s*(?:#{1,4}\s*)?([一二三四五六七八九十]+)、\s*(.+?)\s*$")
_CHAPTER_RE = re.compile(r"^\s*(?:#{1,4}\s*)?[（(]([一二三四五六七八九十]+)[）)]\s*(.+?)\s*$")
_NUM_PREFIX = re.compile(r"^\s*(?:[（(]?\d+[）)]?|\d+[、.．]|[①②③④⑤⑥⑦⑧⑨⑩])\s*")


def norm(s: str) -> str:
    return re.sub(r"[\s·、,，。；;：:（）()\-—/\]\[《》]+", "", s or "")


def parse_truth(text: str) -> dict[str, dict]:
    """独立结构化解析（仅作核验真值）：科目 → {chapters: {name: [items]}, items: [..]}。

    规则：锚点后按「中文数字、」分科；「（x）」分章；条目 = 去编号后的行
    （含标点/较长 ≥6 字；不含标点且后随编号行、≤14 字的纯中文短行视为子标题，不作条目）。
    """
    m = _PART_ANCHOR.search(text)
    body = text[m.end():] if m else text
    out: dict[str, dict] = {}
    cur_subj: str | None = None
    cur_chap: str | None = None
    cur_chap_items: list[str] = []
    subj_items: list[str] = []
    subj_chapters: dict[str, list[str]] = {}
    lines = body.splitlines()
    for li, raw in enumerate(lines):
        line = re.sub(r"^#+\s*", "", raw).strip().strip("\u3000")
        if not line:
            continue
        ms = _SUBJECT_RE.match(raw)
        if ms:
            if cur_subj:
                out[cur_subj] = {"chapters": subj_chapters, "items": subj_items}
            cur_subj = ms.group(2).strip()
            cur_chap, cur_chap_items = None, []
            subj_items, subj_chapters = [], {}
            continue
        if not cur_subj:
            continue
        mc = _CHAPTER_RE.match(raw)
        if mc:
            cur_chap, cur_chap_items = mc.group(2).strip(), []
            subj_chapters[cur_chap] = cur_chap_items
            continue
        item = _NUM_PREFIX.sub("", line).strip("。 \u3000")
        if not item:
            continue
        if _SUBJECT_RE.match(line) or _CHAPTER_RE.match(line):
            continue
        # 无编号子标题排除：短、无句号、且下一非空行为 (1)… 编号条目 → 视为「章级标题」
        nxt = next((ln.strip() for ln in lines[li + 1:] if ln.strip()), "")
        if len(item) <= 14 and "。" not in item and re.match(r"^\s*[（(]?\s*\d+\s*[）)]?[、.．]?\s", nxt):
            if item not in subj_chapters:
                subj_chapters[item] = []
            continue
        if len(item) >= 6 or re.search(r"[、，。；：（）]", item):
            subj_items.append(item + "。")
            if cur_chap:
                cur_chap_items.append(item + "。")
    if cur_subj:
        out[cur_subj] = {"chapters": subj_chapters, "items": subj_items}
    return out


def match_score(items_a: list[str], items_b: list[str]) -> tuple[int, int, int, int]:
    """(a_recall_hits, len_a, b_precision_hits, len_b)：归一化子串双向匹配。"""
    na = [norm(x) for x in items_a]
    nb = [norm(x) for x in items_b]
    ahits = sum(1 for x in na if any(x and y and (x in y or y in x) for y in nb))
    bhits = sum(1 for y in nb if any(x and y and (x in y or y in x) for x in na))
    return ahits, len(na), bhits, len(nb)


def main() -> None:
    text = MD.read_text(encoding="utf-8")
    truth = parse_truth(text)
    print(f"== K3 key: 真值科目 {len(truth)} 科，条目合计 "
          f"{sum(len(v['items']) for v in truth.values())} 条")

    # ---- 注入 medkit 提取器（真实配置 LLM）----
    sys.path.insert(0, str(ROOT))
    from medkit.core import syllabus as syl

    outline = syl.extract_outline(text)
    if not outline:
        print("!! extract_outline 返回 None（LLM 不可用？）")
        sys.exit(2)
    ex_subjects = {s["name"]: s for s in outline["subjects"]}
    print(f"== LLM 抽取 {len(ex_subjects)} 科（errors: {outline.get('errors')}）")
    print("   names:", sorted(ex_subjects.keys()))

    def find_subject(name: str) -> dict | None:
        """真值科目名 → 抽取科目（归一前缀双向匹配，容错括号注释/简写）。"""
        ns = norm(name)
        for k, v in ex_subjects.items():
            nk = norm(k)
            if ns and nk and (ns in nk or nk in ns):
                return v
        return None

    # ---- 全量核验 ----
    per_subject: dict[str, dict] = {}
    tot = {"tl_a": 0, "tl_b": 0, "ar": 0, "bp": 0, "chap_ok": 0, "chap_n": 0}
    for name, t in truth.items():
        items_t, items_e = t["items"], []
        subj_ex = find_subject(name)
        if subj_ex:
            items_e = [it for c in subj_ex["chapters"] for it in c["items"]]
            ch_e = [c["name"] for c in subj_ex["chapters"]]
        else:
            ch_e = []
        ar, la, bp, lb = match_score(items_t, items_e)
        chs_t = list(t["chapters"].keys())
        chap_ok = sum(1 for ct in chs_t
                      if any(ct == ce or norm(ct) in norm(ce) or norm(ce) in norm(ct)
                             for ce in ch_e))
        per_subject[name] = {"source_items": la, "extracted_items": lb,
                             "recall": round(ar / la, 3) if la else 1.0,
                             "precision": round(bp / lb, 3) if lb else 1.0,
                             "source_chapters": chs_t, "extracted_chapters": ch_e,
                             "chapter_hit": f"{chap_ok}/{len(chs_t)}"}
        tot["tl_a"] += la
        tot["tl_b"] += lb
        tot["ar"] += ar
        tot["bp"] += bp
        tot["chap_ok"] += chap_ok
        tot["chap_n"] += len(chs_t)

    overall = {"items": tot["tl_a"], "extracted": tot["tl_b"],
               "recall": round(tot["ar"] / tot["tl_a"], 3) if tot["tl_a"] else 1.0,
               "precision": round(tot["bp"] / tot["tl_b"], 3) if tot["tl_b"] else 1.0,
               "chapter_hit": f"{tot['chap_ok']}/{tot['chap_n']}"}
    gate = overall["recall"] >= 0.8 and overall["precision"] >= 0.8

    # ---- 10 条分层抽样人核清单 ----
    rng = random.Random(20260827)
    samples: list[dict] = []
    names = sorted(truth.keys())
    for _ in range(10):
        name = names[rng.randrange(len(names))]
        t = truth[name]
        idx = rng.randrange(len(t["items"]))
        src = t["items"][idx]
        ex = "〔缺失〕"
        subj_ex = find_subject(name)
        if subj_ex:
            # 偏好「抽取条目 ⊆ 原文」（整条覆盖）最长者；否则「原文 ⊆ 抽取条目」最长者
            cands = []
            for c in subj_ex["chapters"]:
                for it in c["items"]:
                    x, y = norm(src), norm(it)
                    if x and y and (x in y or y in x):
                        cands.append((len(y), y in x, it))
            if cands:
                cands.sort(key=lambda v: (v[1], v[0]))
                ex = cands[-1][2] + "。"
        samples.append({"subject": name, "source": src, "extracted": ex,
                        "ok": ex != "〔缺失〕"})

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"md": str(MD), "run_at": ts, "per_subject": per_subject,
              "overall": overall, "gate_pass": gate, "samples": samples,
              "errors": outline.get("errors", [])}
    (OUT_DIR / "syllabus_306_official.json").write_text(
        json.dumps({"exam": outline.get("exam", ""), "subjects": outline["subjects"],
                    "errors": outline.get("errors", [])},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / f"K3_report_{ts}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# K3 核验报告（{ts}）", "",
             f"- 源文件：{MD}", "- 抽取：chat_json + OutlineSubject（逐科 6 次调用，max_tokens=16000）", ""]
    lines.append("## 全量核验（真值 = 独立结构化解析）")
    lines.append(f"- **条目 recall {overall['recall']:.1%}**"
                 f" · **precision {overall['precision']:.1%}** · "
                 f"真值条目 {overall['items']} / 抽取 {overall['extracted']}")
    lines.append(f"- **章名命中 {overall['chapter_hit']}**")
    lines.append("")
    for k in sorted(per_subject):
        v = per_subject[k]
        lines.append(f"### {k}：recall {v['recall']:.1%} / precision {v['precision']:.1%} · "
                     f"{v['source_items']}条 → {v['extracted_items']}条 · 章 {v['chapter_hit']}")
    lines.append("")
    lines.append("## 10 条分层抽样（人核）")
    lines.append("| # | 科目 | 原文 | 抽取结果 | 一致 |")
    lines.append("| - | - | - | - | - |")
    for i, s in enumerate(samples, 1):
        lines.append(f"| {i} | {s['subject']} | {s['source']} | {s['extracted']} | "
                     f"{'✅' if s['ok'] else '❌'} |")
    lines.append("")
    lines.append(f"**结论：{'✅ 通过（≥80%）→ 已接 /api/syllabus/seed 文件输入路径' if gate else '❌ 未通过 → 更新 docs/spikes/ 记录'}**")
    (OUT_DIR / f"K3_report_{ts}.md").write_text("\n".join(lines), encoding="utf-8")

    for line in lines:
        print(line)
    print(f"\nsaved: {OUT_DIR}")


if __name__ == "__main__":
    main()
