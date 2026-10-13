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

# 解析器版本：**解析逻辑有实质改动时递增**。
# 用途：题库域保存「导入时用的版本」（见 `db.bank_sources.parser_version`），
# 使解析器改进后能对**已入库的题库**一键重解析——否则解析修好了，已入库数据仍是旧产物
# （2026-10-13 实测：用户库里那份 231 题旧解析产物，改代码后不重导就一直是脏的）。
PARSER_VERSION = "R13"

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

# ---- R13（2026-10-13）：第三方 md 的「标题 / 题干 / 元数据」识别 ----
# 实测样本：`ALL_questions_FIXED.md`（急诊与灾难医学 batch035）——
#   `## M1（8题）`                        ← 分节标题，**不是题目**
#   `### 8. [X型题｜理解] batch035-M1-X-001` ← 题号标题（题型/难度/id），题干在下方
#   `> 模块：M1｜页码：教材P1`              ← 溯源元数据，**不是题干**
#   `**我国急救医疗服务体系（EMSS）的组成部分包括**` ← 题干（整行加粗）
#   `- **A. 院前急救** ✅`                  ← 选项（加粗 + 正确性对勾）
# 旧实现把前三者全并进题干，并把分节标题当题 ⇒ 231 题里 11 题是空壳、选项错位。
# 分节标题判据：标题里带「（N题）」计数。
_SECTION_HEAD_RE = re.compile(r"^.{0,80}?[（(]\s*\d+\s*题\s*[)）]")
# 第三方题号标题：`8. [X型题｜理解] batch035-M1-X-001`
_TAG_HEAD_RE = re.compile(
    r"^\s*\d+\s*[、.．)）]\s*[【\[]\s*(?P<label>[^】\]]{0,30}?)\s*[】\]]\s*(?P<id>\S.*)$")
# 标签里的题型（`X型题` / `A1型题` / `B1型题`）——⚠️ 必须含 X（多选），
# 而 `[A-H]` 覆盖不到 X（实测漏判 30 道 X 型题，题型全落成 None）
_TYPE_IN_LABEL_RE = re.compile(r"([A-Ha-hXx]{1,2}\d?)\s*型")
# Bloom 层取值（从标题标签里认难度）
_BLOOM_SET = ("记忆", "理解", "应用", "分析", "评价", "创造")
# 元数据引用行（`> 模块：M1｜页码：教材P1`）——溯源信息，不是题干。
# ⚠️ 必须放行 `> 解析：…` / `> 答案：…`：它们是解析/答案。
# 否定前瞻里要自带 `\s*`——否则外层 `\s*` 回溯到「零空格」就能绕开前瞻，
# 把 `> 解析：…` 当元数据剥掉（实测：解析整段丢失）。
_META_QUOTE_RE = re.compile(r"^\s*>\s*(?!\s*(?:解析|答案|💡))[^:：]{1,10}\s*[:：]")
# 水平分隔线（题目之间的 `---`）
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
# 标题本身「像题目」：`1. xxx` / `第3题` / `【题 5】`（用于区分「标题即题干」与「章节标题」）
_HEAD_LOOKS_LIKE_Q_RE = re.compile(r"^\s*(?:\d+\s*[、.．)）]|第\s*\d+\s*题|【题)")
# 标题里的**题号**（`### 1. 题干`）——题号是编号，不是题干内容，须剥。
# `(?!\d)` 是防误伤：`### 2.5 mg/kg 的剂量是` 里的 `2.` 不是题号（小数点后紧跟数字）。
_NUM_HEAD_RE = re.compile(r"^\s*\d+\s*[、.．)）](?!\d)\s*(?P<q>.+)$")

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
    """② 宽松签名：题干前 80 字 **+ 选项**（均归一）——判「题干措辞微改」的重复。

    ⚠️ **必须把选项并进键**：只取题干前 80 字会在**案例组题**（A3/A4）上误判——
    同一案例的子题共用同一段案例题干，前 80 字完全相同，只有后面的**提问与选项**不同。
    实测 batch035：4 组 9 题被当成重复 ⇒ `dedupe="update"` **覆盖丢失 5 题**
    （220 题导入后只剩 215）。并进选项后子题各成一体，而「措辞微改」的重复
    （题干微调、选项不变）仍能命中——这正是本键的用途。
    """
    base = _norm_q(str(item.get("question") or "")[:80])
    opts = "|".join(_norm_q(o) for o in (item.get("options") or []))
    return hashlib.sha1((base + "#" + opts).encode("utf-8")).hexdigest()


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


def _split_meta(head: str) -> tuple[Optional[str], Optional[str], str, str]:
    """拆元数据标题 → (题目id, 题型, bloom, 剩余题干文本)。

    支持的标题形态：
    ① MedKit 导出：`q1 · A1 型 · 单选 · 理解`（题干在标题下方一行）；
    ② 第三方：`8. [X型题｜理解] batch035-M1-X-001`（题型/bloom/id 都在标题里，题干在下方）；
    ③ 标题即题干：`### 1. 慢阻肺最主要的病因是`（无元数据分隔符，题干就是标题）。
    ①② 返回的题干文本为空串（题干在标题下方），③ 返回标题本身。
    """
    h = (head or "").strip()
    m = _TAG_HEAD_RE.match(h)
    if m:
        label = m.group("label") or ""
        tm = _TYPE_IN_LABEL_RE.search(label)
        qtype = _TYPE_BY_LABEL.get(tm.group(1).upper()) if tm else None
        bloom = next((p.strip() for p in re.split(r"[｜|·/]", label)
                      if p.strip() in _BLOOM_SET), "")
        return (m.group("id").strip() or None), qtype, bloom, ""
    m = _NUM_HEAD_RE.match(h)
    if m:
        # `### 1. 慢阻肺最主要的病因是` —— 题号是编号，不是题干内容
        return None, None, "", m.group("q").strip()
    if _META_SEP not in h:
        return None, None, "", h
    parts = [p.strip() for p in h.split(_META_SEP)]
    qid = parts[0] or None
    qtype = None
    if len(parts) >= 2 and parts[1]:
        qtype = _TYPE_BY_LABEL.get(parts[1].split()[0])
    bloom = next((p for p in parts[2:] if p in _BLOOM_SET), "")
    # 元数据标题里不含题干（题干在标题下方一行）
    return qid, qtype, bloom, ""


def _is_option_line(s: str) -> bool:
    """该行是不是选项行（剥前导列表符 + 加粗/斜体标记后判 `_OPT_LIKE`）。

    ⚠️ 必须连加粗标记一起剥：`- **A. 院前急救** ✅` 的行首是 `**`，
    只剥 `- ` 不命中 ⇒ 会被误判成「题干行」。
    """
    probe = _LIST_MARK_RE.sub("", (s or "").strip())
    probe = re.sub(r"^(?:\*{1,2}|_{1,2})\s*", "", probe)
    return bool(_OPT_LIKE.match(probe))


def _has_stem_after(lines: list[str], idx: int) -> bool:
    """`lines[idx]` 之后、**第一个选项行之前**是否还有真正的题干行。

    题干总在选项之前（两种格式都如此）⇒ 只看选项段之前的内容：
      · MedKit 导出：`**subtopic**` + 题干行 + 选项 ⇒ True（加粗行是 **subtopic**）；
      · 第三方：`**题干**` + 选项               ⇒ False（加粗行**就是题干**）。

    ⚠️ 不能只看「后面有没有非选项行」——`**答案：B**` / `> 解析：…` 也不是选项行，
    但它们是答案/解析，不是题干；按那个判据会把加粗题干误当 subtopic 摘掉，
    题干随即回退成「选项 + 答案残留」整块（实测踩到）。
    """
    for ln in lines[idx + 1:]:
        s = ln.strip()
        if not s or s.startswith(">") or _HR_RE.match(s):
            continue
        if _is_option_line(s):
            return False      # 已进入选项段 ⇒ 中间没有题干行
        return True           # 选项之前有实质文本行 ⇒ 那就是题干
    return False


def _parse_meta_quote(line: str) -> dict[str, str]:
    """`> 模块：M1｜页码：教材P1` → `{"模块": "M1", "页码": "教材P1"}`。"""
    s = (line or "").lstrip().lstrip(">").strip()
    out: dict[str, str] = {}
    for seg in re.split(r"[｜|]", s):
        m = re.match(r"^\s*([^:：]{1,10})\s*[:：]\s*(.*)$", seg)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


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
            # ⚠️ 必须先剥前导列表符再判选项 —— `- A. 甲` 的行首是 `-`，不剥不命中。
            opts = [_LIST_MARK_RE.sub("", ln.strip())
                    for ln in body.splitlines()
                    if _is_option_line(ln)]
            if opts:
                carry_options = opts
            continue
        if head and _SECTION_HEAD_RE.match(head):
            # R13：带「（N题）」计数的分节标题（`## M1（8题）`）——章节，不是题目
            continue
        qid, qtype, bloom, head_q = _split_meta(head or "")
        lines = body.splitlines()
        # R13：元数据引用行（`> 模块：M1｜页码：教材P1`）摘出来做溯源，不混进题干
        meta: dict[str, str] = {}
        kept: list[str] = []
        for ln in lines:
            if _META_QUOTE_RE.match(ln):
                meta.update(_parse_meta_quote(ln))
                continue
            kept.append(ln)
        lines = kept
        if not any(ln.strip() for ln in lines) and not _HEAD_LOOKS_LIKE_Q_RE.match(head or ""):
            # R13：光秃秃的标题块（无正文、标题也不像题号）——分节 / 装饰，不是题目
            continue
        # 独立加粗行 = subtopic —— **仅当其后还有真正的题干行**时才认（`_has_stem_after`）。
        # 否则该加粗行本身就是题干（第三方写法 `**急诊医学的核心内涵是**` 直接接选项）。
        subtopic = ""
        if head_q == "" and head:
            first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
            if first is not None:
                m = _BOLD_ONLY_RE.match(lines[first].strip())
                if m and not _is_option_line(m.group(1)) and _has_stem_after(lines, first):
                    subtopic = m.group(1).strip()
                    lines = lines[:first] + lines[first + 1:]
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
            ref: dict[str, Any] = {"source_id": qid}
            if meta.get("模块"):
                ref["module"] = meta["模块"]
            if meta.get("页码"):
                ref["page"] = meta["页码"]
            row["origin_ref"] = ref
        if qtype:
            row["type"] = qtype
        if bloom:
            row["bloom"] = bloom
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
