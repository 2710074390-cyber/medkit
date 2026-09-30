"""Anki `.apkg` 导入解析（EP-01 录入层 · 原方案 N2 / §9.3）。

`.apkg` 就是一个 ZIP，里面有：
- `collection.anki2`（或 `collection.anki21`）：一个 **SQLite 库**，表 `notes` 存卡片字段、
  表 `col` 里有一坨 JSON 存**笔记类型（model）的字段名与顺序**；
- `media`：媒体文件名映射（本项目不处理图片，导入时如实告知）。

## 为什么必须按**字段名**而不是位置取

不同牌组的字段顺序不一样（Anki 里可以拖）。MedKit 自己导出的包用
`题干 / 选项 / 答案 / 解析 / 溯源`（见 `render/apkg.py`），但用户从别处拿来的包
可能是 `Front / Back`、`Question / Answer`、`正面 / 背面`……
故本模块先读 `col.models` 拿到 **field 名 → ord** 的映射，再按**名字**取值；
认不出的字段名回退到位置约定（第 0 个字段当题干）。

## 三条硬约束

1. **零 LLM、零新依赖**：只用 stdlib（`zipfile` / `sqlite3` / `html` / `re`）。
2. **只读打开 SQLite**（`mode=ro`）：导入一个别人的包不该有任何写副作用；
   也避免 SQLite 在临时目录留下 `-wal`/`-journal` 残骸。
3. **闸门不放宽**：本模块只负责**解析成结构化 dict**，落库与闸门判定仍走
   `errorpipe.normalize_card` + `library.add_mistake`（与 JSONL 导入同一套）——
   导入通道不能成为"绕过 confidence/my_reasoning 必填"的后门。

## 安全边界（与项目既有口径一致）

- **压缩炸弹**：解压后总大小与压缩比都设上限（复用 `_common` 的量级），
  且**先看 `ZipInfo.file_size` 再决定要不要读**——不先解压到内存再判断。
- **条数上限**：`max_notes` 兜底，超出**明确报出被截断**（不静默少导）。
- **坏包**：不是 ZIP / 缺 collection / 不是 SQLite / 表结构对不上，
  一律给**可读中文原因**，不抛裸异常。
"""

from __future__ import annotations

import html
import io
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

# 解压后总上限 / 压缩比上限（与 `routers/_common._content_guard` 同量级）
MAX_UNZIPPED_BYTES = 600 * 1024 * 1024
MAX_ZIP_RATIO = 200
# 单次导入条数上限：Anki 牌组动辄上千，但错题档案是"按轮次反复刷"的沉淀，
# 一次灌太多会稀释掉闸门与归因的价值。超出**明确报出**（不静默截断）。
MAX_NOTES = 5000

_COLLECTION_NAMES = ("collection.anki2", "collection.anki21", "collection.anki21b")

# MedKit 自己导出的字段名（`render/apkg.py`）——优先按这套取，保证**往返无损**
_MEDKIT_FIELDS = ("题干", "选项", "答案", "解析", "溯源")

# 通用 Anki 牌组的字段名线索（小写子串匹配；**不区分中英**）
_ANSWER_HINTS = ("答案", "answer", "correct")
_ANALYSIS_HINTS = ("解析", "explanation", "analysis", "note")
_OPTION_HINTS = ("选项", "option", "choice")
_STEM_HINTS = ("题干", "question", "stem", "front", "正面", "问题")

_TAG_RE = re.compile(r"<[^>]{1,300}>")
_BLOCK_END_RE = re.compile(r"</(?:div|p|li|tr|h[1-6])\s*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
# 选项行：`A. xxx` / `A、xxx` / `(A) xxx`
_OPT_LINE_RE = re.compile(r"^\s*[（(]?([A-Ha-h])[）)]?\s*[.、．,，:：)）]?\s*\S")


class ApkgError(Exception):
    """`.apkg` 解析失败（含可读中文原因）。"""


def _plain(raw: Any) -> str:
    """Anki 字段 → 纯文本。

    **顺序很关键**：先按 HTML 处理（`<br>`/块级闭合 → 换行、剥真标签），**最后**才解实体。
    反过来的话，用户原文里的字面量 `&lt;b&gt;` 会被当成标签剥掉（那是内容，不是标记）。
    """
    t = str(raw or "")
    t = _BR_RE.sub("\n", t)
    t = _BLOCK_END_RE.sub("\n", t)
    t = _TAG_RE.sub("", t)      # 此时字面量还是 &lt;b&gt;，不会被误剥
    t = html.unescape(t)
    return t.strip()


def _split_options(text: str) -> list[str]:
    """选项字段 → 选项行列表（保留 `A. ` 前缀，与库内既有格式一致）。

    只在**看起来像选项行**时才拆；否则整体作为一行（避免把解析文本切碎）。
    """
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) <= 1:
        return lines
    if all(_OPT_LINE_RE.match(ln) for ln in lines):
        return lines
    # 不全是选项行 → 只取像选项的那几行（Anki 里偶尔混了说明文字）
    opts = [ln for ln in lines if _OPT_LINE_RE.match(ln)]
    return opts if opts else lines


def _pick(fields: dict[str, str], hints: tuple[str, ...]) -> str:
    """按名字线索取字段（小写子串匹配）；取不到返回空串。"""
    for name, value in fields.items():
        low = name.lower()
        if any(h in low for h in hints):
            return value
    return ""


def _read_models(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """`col.models`（JSON）→ `{mid: {"name":…, "fields":[名按 ord 排序]}}`。"""
    import json

    try:
        row = db.execute("SELECT models FROM col LIMIT 1").fetchone()
    except sqlite3.DatabaseError as e:
        raise ApkgError(f"读取牌组元数据失败（不是有效的 Anki collection）：{e}") from e
    if not row or not row[0]:
        return {}
    try:
        models = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mid, m in (models or {}).items():
        if not isinstance(m, dict):
            continue
        flds = m.get("flds") or []
        names = []
        for f in flds:
            if isinstance(f, dict) and f.get("name"):
                names.append((int(f.get("ord") or 0), str(f["name"])))
        names.sort()
        out[str(mid)] = {"name": str(m.get("name") or ""), "fields": [n for _, n in names]}
    return out


def _note_to_item(fields: dict[str, str], model_name: str, tags: str) -> Optional[dict[str, Any]]:
    """一条 note → 错题 dict；**不是题目**的返回 None（调用方计入 skipped 并说明）。"""
    stem = _pick(fields, _STEM_HINTS)
    if not stem and fields:
        # 认不出字段名 → 回退位置约定（Anki 的"正面"通常是第 0 个字段）
        stem = next(iter(fields.values()))
    if not stem:
        return None

    question = stem
    case_stem = ""
    # MedKit 案例题：`_fields()` 写成「【案例】题干<br>子题」（见 render/apkg.py）
    if question.startswith("【案例】"):
        head, _, rest = question.partition("\n")
        case_stem = head[len("【案例】"):].strip()
        question = rest.strip() or head[len("【案例】"):].strip()

    answer = _pick(fields, _ANSWER_HINTS)
    analysis = _pick(fields, _ANALYSIS_HINTS)
    opts_raw = _pick(fields, _OPTION_HINTS)
    options = _split_options(opts_raw)

    # 模型名带「记忆卡」= 正面/背面 型，不是题目（MedKit 的记忆卡包就是这种）
    if not answer and not options and ("记忆" in model_name or "正面" in fields):
        return None

    tag_list = [t for t in str(tags or "").split() if t]
    return {
        "source": "anki",
        "source_ref": {"model": model_name, "tags": tag_list},
        "question": question,
        "options": options,
        "answer": answer,
        "analysis": analysis,
        "case_stem": case_stem,
        # 科目/章节**不臆造**：Anki 字段里没有可靠来源，交给录入者或 kp 对齐补
        "subject": "",
        "chapter": "",
        "topic": "",
        "know_tags": tag_list,
    }


def parse_apkg(data: bytes, *, max_notes: int = MAX_NOTES) -> dict[str, Any]:
    """`.apkg` 字节 → `{ok, items, skipped, error, meta}`。

    `items` 的形状 = `library.add_mistake` 能直接吃的 dict；
    `skipped` 是**逐条原因**（不静默丢），`meta` 记包级信息（牌组名、模型名、字段映射）。
    """
    if not data:
        return {"ok": False, "items": [], "skipped": [], "error": "文件为空（0 字节）", "meta": {}}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"ok": False, "items": [], "skipped": [],
                "error": "不是有效的 .apkg（ZIP 结构损坏）——请确认是 Anki 导出的文件", "meta": {}}

    with zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > MAX_UNZIPPED_BYTES:
            return {"ok": False, "items": [], "skipped": [],
                    "error": f"解压后体积过大（约 {total // (1024 * 1024)} MB，"
                             f"上限 {MAX_UNZIPPED_BYTES // (1024 * 1024)} MB）——疑似压缩炸弹，已拒绝",
                    "meta": {}}
        ratio = total / max(len(data), 1)
        if total > 20 * 1024 * 1024 and ratio > MAX_ZIP_RATIO:
            return {"ok": False, "items": [], "skipped": [],
                    "error": f"压缩比异常（约 {ratio:.0f}:1）——疑似压缩炸弹，已拒绝", "meta": {}}

        name = next((n for n in _COLLECTION_NAMES if n in zf.namelist()), "")
        if not name:
            return {"ok": False, "items": [], "skipped": [],
                    "error": "包内没有 collection.anki2（可能是「导出为文本」或媒体包）——"
                             "请在 Anki 里用「导出 → Anki 牌组包(.apkg)」重新导出",
                    "meta": {}}

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".anki2")
        try:
            tmp.write(zf.read(name))
            tmp.close()
            return _parse_collection(Path(tmp.name), max_notes=max_notes)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def _parse_collection(path: Path, *, max_notes: int) -> dict[str, Any]:
    """只读打开 collection，读出 notes 并映射成 items。"""
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return {"ok": False, "items": [], "skipped": [],
                "error": f"打开 collection 失败：{e}", "meta": {}}
    try:
        models = _read_models(db)
        deck = ""
        try:
            row = db.execute("SELECT decks FROM col LIMIT 1").fetchone()
            if row and row[0]:
                import json
                decks = json.loads(row[0])
                if isinstance(decks, dict) and decks:
                    deck = str(next(iter(decks.values())).get("name", "")) or ""
        except (sqlite3.DatabaseError, TypeError, ValueError, AttributeError):
            deck = ""      # 牌组名只是展示信息，取不到不影响导入
        try:
            rows = db.execute("SELECT id, mid, flds, tags FROM notes").fetchall()
        except sqlite3.DatabaseError as e:
            return {"ok": False, "items": [], "skipped": [],
                    "error": f"读取 notes 表失败（表结构不认识）：{e}", "meta": {}}
    finally:
        db.close()

    items: list[dict[str, Any]] = []
    skipped: list[str] = []
    mapping: dict[str, list[str]] = {}
    for nid, mid, flds, tags in rows:
        m = models.get(str(mid)) or {}
        names = list(m.get("fields") or [])
        values = str(flds or "").split("\x1f")
        if not names:
            names = [f"字段{i + 1}" for i in range(len(values))]
        fields = {names[i]: _plain(values[i]) for i in range(min(len(names), len(values)))}
        mapping.setdefault(str(m.get("name") or f"mid={mid}"), names)
        item = _note_to_item(fields, str(m.get("name") or ""), str(tags or ""))
        if item is None:
            skipped.append(f"note {nid}：不是题目（模型「{m.get('name') or mid}」）——已跳过")
            continue
        item["source_ref"]["note_id"] = str(nid)
        items.append(item)
        if len(items) >= max_notes:
            skipped.append(f"已达单次导入上限 {max_notes} 条，**其余未导入**"
                           f"（共 {len(rows)} 条）——请分牌组导入")
            break

    return {"ok": True, "items": items, "skipped": skipped, "error": "",
            "meta": {"deck": deck, "models": mapping, "notes_total": len(rows)}}


__all__ = ["MAX_NOTES", "ApkgError", "parse_apkg"]
