# -*- coding: utf-8 -*-
"""WP-01 大纲种子构建（只取 GoldenSet + 知识库素材，不取其生成产物）。

来源：
  1. 知识库素材/chunks_metadata/*_chunks.jsonl —— 教材 chunks 的 chapter/section 元数据
     （内科学 v10 / 外科学 / 儿科学 / 神经病学 / 精神病学 / 中医学 / 医患沟通 …）
  2. GoldenSet/structured/GS_*.json —— 真题 1994-2025（subject 维度，供考频权重）

输出：medkit/data/syllabus_seed_306.json
  {version, exam, generated_at, sources, subjects: [{code, name, chapters: [{name, items[]}]}]}
usage: python docs/spikes/build_syllabus_seed.py
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\38063\Desktop\MedAgentWork")
OUT = Path(r"C:\Users\38063\Desktop\medkit\data\syllabus_seed_306.json")

CHUNKS = ROOT / "知识库素材" / "chunks_metadata"

# subject_code → 对外科目名（chunks 里 subject 字段为准，这里做映射兜底）
SUBJECT_NAMES = {
    "internal-med": "内科学", "internal-med-exercise": "内科学(习题)",
    "surgery": "外科学", "surgery-exercise": "外科学(习题)",
    "pediatrics": "儿科学", "neurology": "神经病学",
    "psychiatry": "精神病学", "psychiatry-exercise": "精神病学(习题)",
    "tcm": "中医学", "tcm-psychology": "中医心理学",
    "doctor-patient": "医患沟通", "dermatology": "皮肤性病学",
    "cognitive-neuroscience": "认知神经科学", "zhaozhao-part1": "昭昭题眼(上)",
    "zhaozhao-part2": "昭昭题眼(下)",
}

_PREFIX = re.compile(
    r"^(?:"
    r"第\s*[0-9一二三四五六七八九十百零]+\s*[章节篇节]?\s*[、。:：| ]*"  # 第x章/节/篇
    r"|\[?[0-9一二三四五六七八九十百零]+\]?[、.．\s\-－]+"                # 1. 1、 12-
    r")")
_JUNK = re.compile(r"(目录|插图|序|前言|参考文献|索引|封底|版权|页|笔记|作业|考试|附录|致谢|练习)")


def clean_chapter(s: str) -> str | None:
    s = (s or "").replace("\u2002", " ").replace("\u2003", " ").replace("|", " ")
    s = _PREFIX.sub("", s).strip(" \u3000|·-—")
    s = re.sub(r"\s{2,}", " ", s)
    if len(s) < 2 or len(s) > 30:
        return None
    if _JUNK.search(s):
        return None
    return s


def clean_item(s: str) -> str | None:
    s = (s or "").replace("\u2002", " ").replace("\u2003", " ").replace("|", " ")
    s = _PREFIX.sub("", s).strip(" \u3000|·-—")
    # 去尾部噪声：页码（"不寐 140"）、省略号残留（"…… 188"）
    s = re.sub(r"(…|\.{2,}|，|、|\s)*\s*\d+\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" \u3000|·-—…")
    if len(s) < 2 or len(s) > 40:
        return None
    if _JUNK.search(s) and len(s) < 6:
        return None
    return s


def is_noisy(code: str) -> bool:
    """排除噪声源：习题集（OCR 章名脏）、无目录的题眼 OCR（无章元数据）。"""
    return code.endswith("-exercise") or code.startswith("zhaozhao")


def scan_subjects() -> OrderedDict[str, dict]:
    """按 subject_code 聚合 章 → 节（保序去重）。"""
    out: OrderedDict[str, dict] = OrderedDict()
    for f in sorted(CHUNKS.glob("*_chunks.jsonl")):
        code = f.name.replace("_chunks.jsonl", "")
        if is_noisy(code):
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                subj = r.get("subject_code") or code
                chapter = clean_chapter(r.get("chapter"))
                if not chapter:
                    continue
                item = clean_item(r.get("section"))
                bucket = out.setdefault(subj, {"subject": r.get("subject") or SUBJECT_NAMES.get(code, code),
                                               "textbooks": OrderedDict(),
                                               "chapters": OrderedDict()})
                if r.get("textbook"):
                    bucket["textbooks"][r["textbook"]] = True
                ch = bucket["chapters"].setdefault(chapter, OrderedDict())
                if item:
                    ch[item] = True
    return out


def gs_subject_counts() -> dict[str, int]:
    """GoldenSet 真题（上/下册）按 subject 计数 → 考频权重的真实依据。"""
    counts: dict[str, int] = {}
    for f in ("GS_上册_2024.json", "GS_下册_2025_1994.json"):
        p = ROOT / "GoldenSet" / "structured" / f
        if not p.exists():
            continue
        data = json.load(open(p, encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for rec in data:
            s = rec.get("subject") or ""
            if s:
                counts[s] = counts.get(s, 0) + 1
    return counts


def main() -> None:
    subs = scan_subjects()
    gs = gs_subject_counts()
    subjects = []
    for code, b in subs.items():
        chapters = [{"name": name, "items": list(items.keys())}
                    for name, items in b["chapters"].items()]
        chapters.sort(key=lambda c: (-len(c["items"]), c["name"]))
        subject = {
            "code": code,
            "name": b["subject"] or SUBJECT_NAMES.get(code, code),
            "textbooks": sorted(b["textbooks"].keys()),
            "chapters": chapters,
        }
        # GS 真题计数附在科目级（考频权重种子，WP-02 细化）
        gs_n = gs.get(b["subject"] or "")
        if gs_n:
            subject["gs_questions"] = gs_n
        subjects.append(subject)
    subjects.sort(key=lambda s: (-s["chapters"].__len__(), s["name"]))
    seed = {
        "version": 1,
        "exam": "306-西综-本科期末（用户自有）",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": ["知识库素材/chunks_metadata/*_chunks.jsonl",
                    "GoldenSet/structured/GS_*.json（subject 计数）"],
        "note": "章/节来自教材 chunks 元数据（去重清洗）；条目级可由「粘贴大纲」进一步订正；"
                "生理/生化/病理无本地教材 → 待粘贴/后续补充。",
        "gs_subject_counts": gs,
        "subjects": subjects,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"subjects={len(subjects)}")
    for s in subjects:
        print(f"  {s['name']}: textbooks={s.get('textbooks')} chapters={len(s['chapters'])} "
              f"items={sum(len(c['items']) for c in s['chapters'])} gs={s.get('gs_questions')}")
    print("gs subject counts:", json.dumps(gs, ensure_ascii=False))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
