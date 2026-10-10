"""题库导入解析（IQ = import quality）：md/csv/json/txt → 结构化题目。

## 核心原则：**导入格式必须先兼容自己的导出格式**

`render/qbank_html.py::export_md()` 产出的是：

```markdown
### {id} · {TYPE_LABELS[type]} · {bloom}
**{subtopic}**
{question}
- A. {opt}
**✅ 答案：{answer}**
💡 {analysis}
```

即「自己导出的 md 必须能原样导回来」——这是零成本拿到的一个正确性判据（往返测试）。
`library.parse_import_text()` 的 md 分支此前**读不回**这个格式（详见
`docs/题库刷题功能_调研与实现方案_2026-10-02.md` §5.7），本模块补上：
① 按标题/题号切块（`### q1 · …` 里的 id 不是 `\\d+`，旧切块器不认）；
② 剥前导列表符（`- A. 吸烟`）——这条同时修在 `library.parse_question_text` 的调用点，
错题导入一并受益。

## 题型推断

`options` 有 N 项且 `answer` 单字母 → A1；`answer` 多字母 → X。**推断结果让用户在前端确认**
（MedKit 一贯红线：机器不替用户拍板）。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

# 题干起始标记（三种形态：md 标题 / 中文题号 / 【题 N】）
# ⚠️ 一级标题 `# 题库` 是**文档标题**，不是题目起始——`export_md()` 的产物正是这种形态，
#    把它当题会凭空多出一道「题干=文档标题、无答案」的题（实测踩过）。
_Q_START_RE = re.compile(
    r"^\s*(?:"
    r"(?P<doc>#)\s+\S.*"
    r"|#{2,6}\s+(?P<h>.*)"
    r"|(?P<n>\d+)\s*[、.．)]\s*(?P<nb>.*)"
    r"|第\s*\d+\s*题\s*[:：.、]?\s*(?P<cn>.*)"
    r"|【题\s*\d+\s*】\s*(?P<bn>.*)"
    r")\s*$"
)
# 行内独立加粗行（`**慢阻肺的诊断**`）——MedKit 导出格式里紧跟标题的就是 subtopic
_BOLD_ONLY_RE = re.compile(r"^\*\*(.+?)\*\*$")
# 组头（案例 `## 📋 案例 c1` / 选项组 `## 🧩 选项组（B1 共享选项）`）——自身不是题目
_GROUP_HEAD_RE = re.compile(r"^[📋🧩]")
# 选项行样态（用于排除「加粗行其实是选项」）
_OPT_LIKE = re.compile(r"^\s*[（(]?[A-Ha-h][)）、.．\s]")
# 前导列表符（与 `library._LIST_MARK_RE` 同口径；此处只用于组头共享选项的归一）
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# 元数据标题里题干与字段的分隔符（`q1 · A1 型 · 单选 · 理解`）
_META_SEP = " · "

# 题型标签 → 代码（与 render/qbank_html.py:17 TYPE_LABELS 的取值同源，取首段）
_TYPE_BY_LABEL = {"A1": "A1", "A2": "A2", "A3": "A3", "A4": "A4",
                  "B1": "B1", "X": "X", "B": "B1"}


def _norm_q(text: Any) -> str:
    """题干归一（去空白/标点/大小写），用于去重键。"""
    return re.sub(r"[\s·、/（）()\-—.,，。；;：:【】\[\]“”\"'`*#]+", "", str(text or "")).lower()


def strict_key(item: dict[str, Any]) -> str:
    """① 严格签名：完全同一题（题干全文）。"""
    return hashlib.sha1(_norm_q(item.get("question")).encode("utf-8")).hexdigest()


def loose_key(item: dict[str, Any]) -> str:
    """② 宽松签名：题干措辞微改（取前 80 字归一）。"""
    return hashlib.sha1(_norm_q(str(item.get("question") or "")[:80]).encode("utf-8")).hexdigest()


def contains_hit(question: Any, others: list[dict[str, Any]]) -> Optional[str]:
    """③ 包含检测：新题干的归一串被已有题干的归一串**包含**（题干被加/删前缀）。

    命中**只提示不自动处理**（MedKit 红线：机器不替用户拍板）。返回命中的已有题 id 或 None。
    """
    q = _norm_q(question)
    if len(q) < 8:
        return None
    for o in others or []:
        oq = _norm_q(o.get("question"))
        if len(oq) >= 8 and (q in oq or oq in q):
            return str(o.get("id") or "")
    return None


def infer_type(answer: Any, options: Optional[list[Any]] = None) -> str:
    """按答案字母数推断题型：多字母 → X，单字母 → A1（前端可改）。"""
    letters = "".join(ch for ch in str(answer or "").upper() if "A" <= ch <= "J")
    if len(letters) >= 2:
        return "X"
    return "A1"


def _split_blocks(text: str) -> list[tuple[Optional[str], str]]:
    """按「题干起始行」把 md 切成 [(标题内容或 None, 正文)]。

    第一个标题之前的说明文字不产出块（文档标题 `# 题库` 不该变成一道题）。
    """
    blocks: list[tuple[Optional[str], str]] = []
    head: Optional[str] = None
    body: list[str] = []
    started = False
    for line in (text or "").splitlines():
        m = _Q_START_RE.match(line)
        if m:
            if m.group("doc") is not None:
                continue          # 一级标题 = 文档标题，丢弃（不并入正文、不开启新块）
            if started:
                blocks.append((head, "\n".join(body)))
            started = True
            head = (m.group("h") or m.group("nb") or m.group("cn") or m.group("bn") or "").strip()
            body = []
        else:
            body.append(line)
    if started:
        blocks.append((head, "\n".join(body)))
    if not blocks:
        # 一个「题干起始标记」都没有（纯裸题干 + 选项行）→ 整篇作为一个块。
        # 与旧 `library.parse_import_text` 的 md 兜底（`if len(blocks) < 2: blocks = [text]`）同精神。
        return [(None, text or "")]
    return blocks


def _split_meta(head: str) -> tuple[Optional[str], Optional[str], str]:
    """拆元数据标题 → (题目id, 题型, 剩余题干文本)。

    MedKit 导出格式的标题形如 `q1 · A1 型 · 单选 · 理解`（含 ` · ` 分隔）。
    不含 ` · ` 的标题视为「标题即题干」（如 `### 1. 慢阻肺最主要的病因是`）。
    """
    if _META_SEP not in (head or ""):
        return None, None, (head or "").strip()
    parts = [p.strip() for p in head.split(_META_SEP)]
    qid = parts[0] or None
    qtype = None
    if len(parts) >= 2 and parts[1]:
        qtype = _TYPE_BY_LABEL.get(parts[1].split()[0])
    # 元数据标题里不含题干（题干在标题下方一行）
    return qid, qtype, ""


def parse_bank_md(text: str) -> list[dict[str, Any]]:
    """md/txt → 结构化题目列表（本地规则，零 LLM）。

    逐块：标题元数据（id/题型）→ 独立加粗行（subtopic）→ 余下正文走
    `library.parse_question_text`（题干/选项/答案/解析）。

    选项组（B1 共享选项）的组头选项**顺延给后续子题**，否则 B1 子题会没有选项。
    """
    from . import library as lib

    rows: list[dict[str, Any]] = []
    carry_options: list[str] = []
    for head, body in _split_blocks(text.lstrip("\ufeff")):
        if head and _GROUP_HEAD_RE.match(head):
            # 组头块：把其中的选项作为「共享选项」顺延给后续子题。
            # ⚠️ 必须先剥前导列表符再判 `_OPT_LIKE` —— `- A. 甲` 的行首是 `-`，不剥不命中。
            opts = [_LIST_MARK_RE.sub("", ln.strip())
                    for ln in body.splitlines()
                    if _OPT_LIKE.match(_LIST_MARK_RE.sub("", ln.strip()))]
            if opts:
                carry_options = opts
            continue
        qid, qtype, head_q = _split_meta(head or "")
        lines = body.splitlines()
        # 独立加粗行 = subtopic（只在标题是元数据时才认，避免误吞用户正文里的加粗行）
        subtopic = ""
        if head_q == "" and head:
            for i, ln in enumerate(lines):
                s = ln.strip()
                if not s:
                    continue
                m = _BOLD_ONLY_RE.match(s)
                if m and not _OPT_LIKE.match(m.group(1)):
                    subtopic = m.group(1).strip()
                    lines = lines[:i] + lines[i + 1:]
                break
        payload = "\n".join(lines).strip()
        if head_q:
            payload = (head_q + "\n" + payload).strip()
        if len(payload) < 4:
            continue
        parsed = lib.parse_question_text(payload)
        question = (parsed.get("question") or "").strip()
        if not question:
            continue
        options = list(parsed.get("options") or [])
        if not options and carry_options:
            options = list(carry_options)
        row: dict[str, Any] = {
            "question": question,
            "options": options,
            "answer": (parsed.get("answer") or "").strip(),
            "analysis": (parsed.get("analysis") or "").strip(),
            "subtopic": subtopic,
        }
        if qid:
            row["origin_ref"] = {"source_id": qid}
        if qtype:
            row["type"] = qtype
        rows.append(row)
    return rows


def parse_bank_text(text: str, fmt: str = "md") -> list[dict[str, Any]]:
    """统一入口：md/txt → `parse_bank_md`；json/csv → 复用 `library.parse_import_text`。"""
    fmt = (fmt or "md").lower()
    if fmt in ("md", "markdown", "txt", "text"):
        return parse_bank_md(text)
    from . import library as lib

    return lib.parse_import_text(text, "json" if fmt == "json" else "csv")


def normalize_item(raw: dict[str, Any], *, bid: str, origin: str,
                   now: str) -> Optional[dict[str, Any]]:
    """原始解析结果 → 可落库的题库条目（含题库域独有字段）。

    只对**空题干**拒绝（返回 None）；其余字段一律尽力归一。
    `schema.QuestionItem` 的校验用于**给出告警**（如 X 型答案未升序），不用于拒收——
    第三方题库的写法千奇百怪，静默丢题比带着告警收下更糟（判分口径是集合比较，与题型无关）。
    """
    from ..render.qbank_html import _strip_letter
    from . import bank as bank_mod
    from . import schema

    question = str(raw.get("question") or "").strip()
    if not question:
        return None

    def _s(key: str) -> str:
        return str(raw.get(key) or "").strip()

    def _i(key: str) -> int:
        try:
            return int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    # 选项**剥掉字母前缀**入库：字母是渲染层的呈现职责（`LETTERS[i]`），不是数据的一部分。
    # 不剥的话「导出的 md 再导回来」会得到 `A. 吸烟`，而渲染层又补一次字母 → `A. A. 吸烟`。
    options = [_strip_letter(o) for o in (raw.get("options") or []) if str(o).strip()]
    answer = "".join(ch for ch in str(raw.get("answer") or "").upper() if "A" <= ch <= "J")
    qtype = str(raw.get("type") or "").strip() or infer_type(answer, options)
    rec: dict[str, Any] = {
        "id": raw.get("id") or bank_mod._new_id("qi"),
        "bank_id": bid,
        "question": question,
        "type": qtype,
        "bloom": _s("bloom"),
        "subtopic": _s("subtopic"),
        "options": options,
        "answer": answer,
        "analysis": str(raw.get("analysis") or "").strip(),
        # 案例 / 选项组 / 图 / 表 / 溯源：**原样带过来**。
        # 从项目导入（F11）的题目就带这些字段；不搬运等于导入即降级
        # （案例题干丢、共享选项丢、图题变普通题）。
        "case_id": _s("case_id"), "case_order": _i("case_order"), "case_stem": _s("case_stem"),
        "group_kind": _s("group_kind"),
        "group": raw.get("group") if isinstance(raw.get("group"), dict) else None,
        "image_ref": _s("image_ref"), "data_table": _s("data_table"),
        "source_type": _s("source_type"), "source_year": _s("source_year"),
        "origin": str(origin or "md_import"),
        "origin_ref": raw.get("origin_ref") or {},
        # 题库域独有：收藏（星标）与个人笔记。**只存在 data JSON 里，不加查询列**——
        # 「只看收藏」在题量级（千题以下）用 Python 过滤足够，加列要动 schema（v10），
        # 而新增列等于多一个「列与 data 漂移」的失效面。
        "starred": bool(raw.get("starred", False)),
        "note": str(raw.get("note") or "").strip(),
        "created_at": now, "updated_at": now,
    }
    payload = {k: rec[k] for k in ("question", "type", "bloom", "subtopic", "options",
                                   "answer", "analysis", "case_id", "case_order",
                                   "case_stem", "group_kind", "group", "image_ref",
                                   "data_table", "source_type", "source_year")}
    try:
        schema.QuestionItem.model_validate(payload)
    except Exception as e:  # noqa: BLE001  契约不合 → 收下并标注（前端可提示人工核验）
        rec["_warn"] = f"契约校验未通过：{e}"[:200]
    return rec
