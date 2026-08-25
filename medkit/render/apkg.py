"""Anki .apkg 真包导出（genanki 0.13.1，纯 Python 零重依赖，S3）。

设计（S3 方案）：
- model_id / deck_id **按项目名稳定哈希**：随机 id 会导致重复导入生成重复卡（genanki 最常见坑）
- 两个笔记模板：常规（A1/A2/B1）+ X 型自评卡（正面只列选项、翻面才见答案）
- 字段：题干 / 选项 / 答案 / 解析 / 溯源；标签 = 题型 / Bloom / 章节
- 案例题（A3/A4，case_stem）：题干字段带「案例题干」前缀；组内子题独立成卡
- 特殊字符：全部 HTML 转义 + 换行 → <br>（与 anki_export.txt 同口径）
"""

import hashlib
import re
from pathlib import Path
from typing import Any

import genanki

from .qbank_html import LETTERS

TYPE_LABELS = {"A1": "A1 型 · 单选", "A2": "A2 型 · 病例单选", "A3": "A3 型 · 案例多选",
               "A4": "A4 型 · 案例多选", "X": "X 型 · 多选", "B1": "B1 型 · 共用选项"}
SELF_ASSESS_TYPES = {"X"}


def stable_id(name: str) -> int:
    """按名称稳定哈希（≥32 位限制内）生成模型/牌组 id。"""
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)


def _esc_anki(s: Any) -> str:
    """字段转义：HTML 实体 + 换行/制表符（与 anki_export.txt 同口径）。"""
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;")
            .replace("\n", "<br>").replace("\t", " "))


def _source_note(q: dict[str, Any]) -> str:
    """溯源字段：模块/切片 + 解析里的 [源:...] 标记。"""
    parts = [str(q.get("module") or q.get("subtopic") or ""),
             str(q.get("sid") or "")]
    srcs = [m for m in (q.get("analysis") or "").split("【") if "源" in m]
    if srcs:
        parts.append("【" + srcs[0].rstrip())
    return " · ".join(x for x in parts if x)


def _fields(q: dict[str, Any]) -> dict[str, str]:
    stem = str(q.get("case_stem") or "")
    question = str(q.get("question") or "")
    front_question = f"【案例】{stem}<br>" + question if stem else question
    opts = "<br>".join(
        f"{LETTERS[i]}. {_esc_anki(o)}"
        for i, o in enumerate(q.get("options") or []) if isinstance(o, str))
    return {"题干": _esc_anki(front_question), "选项": opts,
            "答案": _esc_anki(q.get("answer") or ""),
            "解析": _esc_anki(q.get("analysis") or ""),
            "溯源": _esc_anki(_source_note(q))}


def _sanitize_tag(tag: str) -> str:
    """Anki 标签规则：不允许空格/逗号（否则 genanki 报错）。"""
    return re.sub(r"[\s,]+", "_", (tag or "").strip())[:30] or "未分类"


def _note_tags(q: dict[str, Any]) -> list[str]:
    tags = [_sanitize_tag(str(q.get("type") or "") or "A1"),
            _sanitize_tag(str(q.get("bloom") or "理解"))]
    chapter = str(q.get("module") or q.get("subtopic") or "")
    if chapter:
        tags.append(_sanitize_tag(chapter))
    return tags


def _model(name: str, self_assess: bool) -> genanki.Model:
    front = ("<div style='font-size:15px;line-height:1.8'>"
             "{{#题干}}{{题干}}<br><br>{{/题干}}"
             "{{选项}}"
             + ("<br><div style='color:#888'>☐ 逐一自评：先自行勾选全部正确项，再翻面核对</div>"
                if self_assess else "")
             + "</div>")
    back = ("{{FrontSide}}<hr style='border:none;border-top:1px dashed #ccc'>"
            "<div style='font-size:14px;line-height:1.8'>"
            "<b>✅ 答案：{{答案}}</b><br><br>"
            "💡 {{解析}}<br><br>"
            "<span style='color:#888;font-size:12px'>📚 溯源：{{溯源}}</span>"
            "</div>")
    return genanki.Model(
        stable_id(name),
        name,
        fields=[{"name": f} for f in ("题干", "选项", "答案", "解析", "溯源")],
        templates=[{"name": "卡片", "qfmt": front, "afmt": back}],
        css=(".card{font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
             "background:#fff;color:#12233d;padding:20px;text-align:left}"),
    )


def _models() -> dict[str, genanki.Model]:
    return {
        "normal": _model("MedKit 标准卡", self_assess=False),
        "self": _model("MedKit X 型自评卡", self_assess=True),
    }


def export_apkg(questions: list[dict[str, Any]], subject: str, project_key: str,
                out_path: Path) -> Path:
    """导出 .apkg（真 Anki 包）。project_key（如项目 pid）决定稳定 id。"""
    out_path = Path(out_path)
    models = _models()
    deck = genanki.Deck(stable_id(project_key), f"MedKit :: {subject or '题库'} ({project_key[:20]})")
    for q in sorted(questions, key=lambda x: str(x.get("id", ""))):
        fields = _fields(q)
        m = models["self"] if str(q.get("type", "")) in SELF_ASSESS_TYPES else models["normal"]
        deck.add_note(genanki.Note(model=m, fields=[fields[f] for f in ("题干", "选项", "答案", "解析", "溯源")],
                                   tags=_note_tags(q)))
    deck.write_to_file(str(out_path))
    return out_path
