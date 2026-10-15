"""LLM 结构化输出契约层（ADR-003）。

每类 LLM JSON 输出对应一个 Pydantic 模型，用 ``model_validate`` 落地「字段缺失 / 类型错 /
多余键 / 业务不变式」校验；配合 :func:`validate_or_repair` 实现
「校验失败 → 带错误重发 1 次修复 → 仍失败 → 返回 None（调用方走人工复核清单）」。

本批次（IMP-03）覆盖的契约：
- MedGen 单题 :class:`QuestionItem`
- MedQC 质检报告 :class:`QcVerdict`
- MedFix 修复题 :class:`FixPatch`
- MedExplain 讲解文档 :class:`ExplainDoc`
- MedTutor 判分回合 :class:`TutorTurn`
- 真题考频归一 :class:`RealexamNorm`
- 大纲结构化抽取（K3/IMP-13）:class:`SyllabusOutline`
- 医学记忆卡（WP-05/NX-04）:class:`CardDraft` / :class:`CardDrafts`

字段名与形态一律以实际提示词（medkit/prompts/*.md）与解析代码
（medkit/agents/*.py、medkit/core/*.py）为准，不臆造。
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

__all__ = [
    "ALLOWED_TYPES",
    "ALLOWED_BLOOM",
    "QC_DECISIONS",
    "QuestionItem",
    "QcIssue",
    "QcVerdict",
    "FixPatch",
    "RealexamNormItem",
    "RealexamNorm",
    "OutlineChapter",
    "OutlineSubject",
    "SyllabusOutline",
    "CARD_KINDS",
    "CARD_KIND_LABELS",
    "validate_or_repair",
]

# 与 medkit/gates/options_check.py 的 ALLOWED_* 保持一致（单源：提示词 HC 规则）。
ALLOWED_TYPES = {"A1", "A2", "X", "B1", "A3", "A4"}
ALLOWED_BLOOM = {"记忆", "理解", "应用", "创造"}
# medqc.md 判定规则：BLOCKED / PASS_WITH_FIXES / PASS。
QC_DECISIONS = {"BLOCKED", "PASS_WITH_FIXES", "PASS"}
# medtutor.md 提问类型。
X_ANSWER_LETTERS = "ABCDE"
X_ANSWER_MIN = 2
X_ANSWER_MAX = 4


def _answer_letters(answer: Optional[str]) -> str:
    """提取答案中的选项字母（A–E，忽略空格/逗号/分隔符等其余字符）。"""
    return "".join(ch for ch in (answer or "").upper() if ch in X_ANSWER_LETTERS)


# --------------------------------------------------------------------------- QuestionItem
class QuestionItem(BaseModel):
    """MedGen 单题输出契约。

    字段名与 medgen.md 输出格式 / ``medgen._parse_questions`` 一致：
    - 题干字段是 ``question``（非 stem）；
    - B1 组题的共享 5 个选项在 ``group.options``（自身 ``options`` 可为空）；
    - ``image_ref`` / ``data_table`` 可选（图 / 表题，WP-04）；
    - X 型答案必须按选项标号升序（HC-1：如 BDE）。
    """

    model_config = ConfigDict(extra="forbid")

    # 作为契约主键的核心内容字段：题干必填，其余可缺省（由 _parse_questions 兜底补齐）。
    question: str
    type: str = "A1"
    bloom: str = ""
    subtopic: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""
    # S3：案例 / 选项组字段（扁平 + 冗余 case_stem；不引入嵌套）。
    case_id: str = ""
    case_order: int = 0
    case_stem: str = ""
    group_kind: str = ""
    group: Optional[dict[str, Any]] = None
    # WP-04：图 / 表题可选字段。
    image_ref: str = ""
    data_table: str = ""
    # v0.8.1 真题标注（PRD 6.3.2）：管线收尾/渲染层按已确认考频条目标注，缺省为空（不破坏旧产物）。
    source_type: str = ""
    source_year: str = ""

    @field_validator("options", mode="before")
    @classmethod
    def _word_options(cls, v: Any) -> Any:
        """options 容错：显式 None / 非数组视为缺失；数组内保留字符串/数字（其余过滤，与解析一致）。"""
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [o for o in v if isinstance(o, (str, int, float))]
        raise ValueError("options 必须为数组")

    @field_validator("case_order", mode="before")
    @classmethod
    def _word_case_order(cls, v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    @field_validator("type", "bloom", "subtopic", "case_id", "case_stem", "group_kind",
                     "image_ref", "data_table", "source_type", "source_year", mode="before")
    @classmethod
    def _word_str(cls, v: Any) -> str:
        return str(v or "")

    @model_validator(mode="after")
    def _answer_invariants(self) -> "QuestionItem":
        """答案不变式：X 型字母升序且 2~4 个；非 X 型单选单字母。"""
        letters = _answer_letters(self.answer)
        if self.type == "X":
            if not letters:
                raise ValueError("X 型题必须给出答案字母")
            seen = list(dict.fromkeys(letters))
            if list(letters) != seen:
                raise ValueError("X 型答案字母不能重复")
            if not (X_ANSWER_MIN <= len(seen) <= X_ANSWER_MAX):
                raise ValueError("X 型答案应为 2~4 个正确选项")
            if letters != "".join(sorted(seen)):
                raise ValueError("X 型答案必须按选项标号升序（如 BDE）")
        else:
            if len(letters) > 1:
                raise ValueError("单选 / 案例 / 组题答案应为单个选项字母")
        return self


# --------------------------------------------------------------------------- QcVerdict
class QcIssue(BaseModel):
    """MedQC 单条 issue（medqc.md：q_id + code + severity + reason + suggest）。"""

    model_config = ConfigDict(extra="ignore")

    q_id: str = ""
    code: str = ""
    severity: str = "warn"
    reason: str = ""
    suggest: str = ""


class QcVerdict(BaseModel):
    """MedQC 质检报告（medqc.md 输出格式）。

    score 保留浮点容错语义：``float`` 可接受 int / 数字字符串；``None`` 交给调用方
    ``_coerce_score`` 兜底（回退 50 + warn）。gate_decision 归一为三个判定值之一。
    """

    model_config = ConfigDict(extra="ignore")

    score: Optional[float] = None
    gate_decision: str = "PASS_WITH_FIXES"
    issues: list[QcIssue] = Field(default_factory=list)
    summary: str = ""

    @field_validator("gate_decision", mode="before")
    @classmethod
    def _word_gate(cls, v: Any) -> str:
        if v is None:
            return "PASS_WITH_FIXES"
        v = str(v).strip().upper()
        return v if v in QC_DECISIONS else "PASS_WITH_FIXES"


# --------------------------------------------------------------------------- FixPatch
class FixPatch(BaseModel):
    """MedFix 修复后的单题（medfix.md 输出格式；只返回被修复题，字段完整）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str = "A1"
    bloom: str = ""
    subtopic: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""


# --------------------------------------------------------------------------- RealexamNorm
class RealexamNormItem(BaseModel):
    """真题考频归一单条（对应 realexam_freq 的 subject / chapter / item / freq）。"""

    model_config = ConfigDict(extra="forbid")

    subject: str = ""
    chapter: str = ""
    item: str
    freq: int = 1

    @field_validator("freq", mode="before")
    @classmethod
    def _word_freq(cls, v: Any) -> int:
        try:
            return max(int(float(v)), 1)
        except (TypeError, ValueError):
            return 1


class RealexamNorm(BaseModel):
    """真题考频归一（LLM 归一开关路径：整批输出 ``{"items": [...]}``）。

    与 ``realexams.analyze()`` 的 drafts 形状一致，供 LLM 归一增强使用（默认关）。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[RealexamNormItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- SyllabusOutline（K3/IMP-13 大纲抽取契约）
class OutlineChapter(BaseModel):
    """大纲一章：「章标题 + 条目列表」（条目为原文句子，去编号、去句尾句号）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    items: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _word_name(cls, v: Any) -> str:
        return str(v or "").strip(" \u3000\n\t。-")

    @field_validator("items", mode="before")
    @classmethod
    def _word_items(cls, v: Any) -> list[str]:
        if not isinstance(v, (list, tuple)):
            return []
        out: list[str] = []
        for it in v:
            if isinstance(it, (str, int, float)):
                s = str(it).strip(" \u3000\n\t。")
                if s:
                    out.append(s)
        return out


class OutlineSubject(BaseModel):
    """大纲一科目：「科目名 + 章列表」（章为空的一律丢弃；科目名必填）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    chapters: list[OutlineChapter] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _word_name(cls, v: Any) -> str:
        return str(v or "").strip(" \u3000\n\t。-")

    @model_validator(mode="after")
    def _drop_empty_chapters(self) -> "OutlineSubject":
        if not self.name:
            raise ValueError("科目名不能为空")
        self.chapters = [c for c in self.chapters if c.items]
        return self


class SyllabusOutline(BaseModel):
    """大纲抽取总契约（K3/IMP-13：md → chat_json 结构化；字段与 syllabus_seed 一致）。"""

    model_config = ConfigDict(extra="ignore")

    exam: str = ""
    subjects: list[OutlineSubject] = Field(default_factory=list)

    @model_validator(mode="after")
    def _drop_empty_subjects(self) -> "SyllabusOutline":
        self.subjects = [s for s in self.subjects if s.name and s.chapters]
        return self


# --------------------------------------------------------------------------- 医学记忆卡类别（`render/apkg.py` 导出 Anki 时用作标签）
CARD_KINDS = ("value", "mnemonic", "contrast", "concept")
CARD_KIND_LABELS = {"value": "数值卡", "mnemonic": "口诀卡", "contrast": "鉴别卡", "concept": "概念卡"}


# --------------------------------------------------------------------------- validate_or_repair
_M = TypeVar("_M", bound=BaseModel)


def validate_or_repair(
    raw: Any,
    model: type[_M],
    repair_fn: Optional[Callable[[Any, ValidationError], Any]] = None,
) -> Optional[_M]:
    """契约校验 + 自动修复（ADR-003）。

    1. ``model.model_validate(raw)`` 一次；
    2. 失败 → 把错误信息（``raw`` 与 ``ValidationError``）交给 ``repair_fn`` 获得新的 ``raw``；
    3. 新 ``raw`` 再校验一次；
    4. 仍失败（或未提供 ``repair_fn``）→ 返回 ``None``，调用方走人工复核清单。

    ``repair_fn(raw, exc)`` 应返回一个可 ``model_validate`` 的 dict，或返回 ``None`` 表示无法修复。

    返回类型是**泛型** ``Optional[_M]``（2026-09-29）：此前写死 ``Optional[BaseModel]``，
    导致调用方拿到的静态类型是基类，``obj.score`` 这类字段访问被 mypy 判为
    「BaseModel has no attribute ...」——运行时明明是对的，静态却报错。
    改成 ``type[_M]`` + ``Optional[_M]`` 后，``validate_or_repair(raw, QcVerdict)``
    静态类型即 ``Optional[QcVerdict]``，字段可见。**类型注解要贴着真实契约写。**
    """
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        if repair_fn is None:
            return None
        new_raw = repair_fn(raw, exc)
        if new_raw is None:
            return None
        try:
            return model.model_validate(new_raw)
        except ValidationError:
            return None
