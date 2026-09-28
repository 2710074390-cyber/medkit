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
    "TUTOR_NEXT_TYPES",
    "QuestionItem",
    "QcIssue",
    "QcVerdict",
    "FixPatch",
    "ExplainSource",
    "ExplainDoc",
    "TutorTurn",
    "RealexamNormItem",
    "RealexamNorm",
    "OutlineChapter",
    "OutlineSubject",
    "SyllabusOutline",
    "CARD_KINDS",
    "CARD_KIND_LABELS",
    "CardDraft",
    "CardDrafts",
    "ANALYSIS_TAGS",
    "FIX_MAX_CHARS",
    "ErrorAnalysis",
    "SOCRATIC_SCORE_MIN",
    "SOCRATIC_SCORE_MAX",
    "SocraticScore",
    "validate_or_repair",
]

# 与 medkit/gates/options_check.py 的 ALLOWED_* 保持一致（单源：提示词 HC 规则）。
ALLOWED_TYPES = {"A1", "A2", "X", "B1", "A3", "A4"}
ALLOWED_BLOOM = {"记忆", "理解", "应用", "创造"}
# medqc.md 判定规则：BLOCKED / PASS_WITH_FIXES / PASS。
QC_DECISIONS = {"BLOCKED", "PASS_WITH_FIXES", "PASS"}
# medtutor.md 提问类型。
TUTOR_NEXT_TYPES = {"explain", "apply", "contrast", "predict", "trace"}
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


# --------------------------------------------------------------------------- ExplainDoc
class ExplainSource(BaseModel):
    """MedExplain 溯源条目（正文来源标注）。"""

    model_config = ConfigDict(extra="ignore")

    kind: str = "textbook"
    title: str = ""
    url: str = ""


class ExplainDoc(BaseModel):
    """MedExplain 讲解文档输出。

    medexplain 的 LLM 输出为四段 Markdown（结论先行 / 机制 / 鉴别 / 记忆锚点），
    非 JSON；本模型对其结构化包装 ``explain_knowledge()`` 的返回做契约化
    （content / sources / via_web / web_materials / grounded）。本批次未接线，仅作契约定义。
    grounded=False 表示未命中教材切片原文（无原文回退：网络素材 + 模型知识输出）。
    """

    model_config = ConfigDict(extra="ignore")

    content: str = ""
    sources: list[ExplainSource] = Field(default_factory=list)
    via_web: bool = False
    web_materials: list[dict[str, Any]] = Field(default_factory=list)
    grounded: bool = True


# --------------------------------------------------------------------------- TutorTurn
class TutorTurn(BaseModel):
    """MedTutor 判分回合（medtutor.md 当 task=score 的 JSON 输出）。"""

    model_config = ConfigDict(extra="forbid")

    score: int = 0
    gap: str = ""
    next_question: str = ""
    next_type: str = "explain"

    @field_validator("next_type", mode="before")
    @classmethod
    def _word_next_type(cls, v: Any) -> str:
        v = str(v or "").strip()
        return v if v in TUTOR_NEXT_TYPES else "explain"

    @field_validator("score", mode="before")
    @classmethod
    def _word_score(cls, v: Any) -> int:
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return -1

    @model_validator(mode="after")
    def _clamp_score(self) -> "TutorTurn":
        # 0~3 为有效判分；-1 表示无法判定（不计分重答），由调用方启发式兜底。
        if self.score not in (0, 1, 2, 3, -1):
            raise ValueError("score 应为 0~3（-1 表示无法判定）")
        return self


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


# --------------------------------------------------------------------------- CardDraft（WP-05/NX-04 医学记忆卡）
CARD_KINDS = ("value", "mnemonic", "contrast", "concept")
CARD_KIND_LABELS = {"value": "数值卡", "mnemonic": "口诀卡", "contrast": "鉴别卡", "concept": "概念卡"}


class CardDraft(BaseModel):
    """MedCards 单张记忆卡（medcards.md 输出；kind ∈ value|mnemonic|contrast|concept）。

    - value：数值/指标/阈值（如 3.25kg、120/80）
    - mnemonic：口诀/记忆锚点
    - contrast：鉴别/对比（最多 3 项鉴别点）
    - concept：概念/机制
    front 为正面线索（≤500 字），back 为背面答案（≤1200 字）；两者均必填。
    """

    model_config = ConfigDict(extra="ignore")

    kind: str = "concept"
    front: str = ""
    back: str = ""

    @field_validator("kind", mode="before")
    @classmethod
    def _word_kind(cls, v: Any) -> str:
        v = str(v or "").strip().lower()
        return v if v in CARD_KINDS else "concept"

    @field_validator("front", "back", mode="before")
    @classmethod
    def _word_text(cls, v: Any) -> str:
        return str(v or "").strip()

    @model_validator(mode="after")
    def _require_text(self) -> "CardDraft":
        if not self.front or not self.back:
            raise ValueError("记忆卡 front/back 均不能为空")
        if len(self.front) > 500:
            raise ValueError("front 超过 500 字限制")
        if len(self.back) > 1200:
            raise ValueError("back 超过 1200 字限制")
        return self


class CardDrafts(BaseModel):
    """MedCards 输出根契约（{cards: [...]}；空卡组一律拒绝——调用方走人工复核语义）。"""

    model_config = ConfigDict(extra="ignore")

    cards: list[CardDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_cards(self) -> "CardDrafts":
        # 按「数值/口诀/鉴别/概念」稳定排序去重（同 front 只保留一张）；上限 10 张（防超发）
        seen: set[str] = set()
        order = {k: i for i, k in enumerate(CARD_KINDS)}
        keep: list[CardDraft] = []
        for c in sorted(self.cards, key=lambda x: (order.get(x.kind, 0), x.front)):
            if c.front in seen:
                continue
            seen.add(c.front)
            keep.append(c)
        self.cards = keep[:10]
        if not self.cards:
            raise ValueError("未生成任何有效记忆卡")
        return self


# --------------------------------------------------------------------------- ErrorAnalysis
# EP-01：错题归因流水线的 LLM 输出契约。字段与 prompts/error_analysis.md 的
# 「归因/考点定位/修正陈述/反事实问题/建议回看的章节」五段结构一一对应。
#
# 红线（《总纲》§3.2）：**正确答案必须由用户提供，AI 只负责解释和定位**。
# 故本契约里**没有 correct / answer 字段**——不是漏了，是不允许模型回写正确答案。
# 若模型在输出里塞了正确答案类的键，`extra="ignore"` 会丢弃（不给它改答案的通道）。
ANALYSIS_TAGS = {"知识盲区", "记忆偏差", "机制混淆", "概念偷换", "审题失误", "推理跳步"}
# 修正陈述上限：提示词写「不超过 25 字」，契约留 2 倍余量容忍模型轻微超发，
# 但硬顶 60 字——超过就是又写成了一段解析，那正是要避免的"泛泛而谈"。
FIX_MAX_CHARS = 60


class ErrorAnalysis(BaseModel):
    """错题归因输出契约（prompts/error_analysis.md）。

    - `error_tag`：模型给的归因，**必须**落在 6 类里（自造标签一律判非法 → 走修复/人工）；
    - `evidence`：判断依据，**要求引用用户原话**——这是让归因可证伪的关键字段；
    - `fix`：一句可执行的修正陈述（≤60 字）；
    - `counterfactual`：反事实问题——改一个条件就会选另一项的那种问法；
    - `where_uncertain`：模型自认不确定处（对应提示词的【待查】/【答案待核实】标记）。
    """

    model_config = ConfigDict(extra="ignore")

    error_tag: str = ""
    evidence: str = ""
    kp_point: str = ""
    variants: list[str] = Field(default_factory=list)
    fix: str = ""
    counterfactual: str = ""
    review_chapters: list[str] = Field(default_factory=list)
    where_uncertain: list[str] = Field(default_factory=list)

    @field_validator("error_tag", mode="before")
    @classmethod
    def _word_tag(cls, v: Any) -> str:
        """标签归一：剥空白后必须精确命中 6 类之一；未命中留空（→ model_validator 抛错）。"""
        return str(v or "").strip()

    @field_validator("kp_point", "fix", "counterfactual", mode="before")
    @classmethod
    def _word_str(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("variants", "review_chapters", "where_uncertain", mode="before")
    @classmethod
    def _word_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x or "").strip()]
        # 模型偶尔给单个字符串而不是数组 → 视为一项（容错，不以此判非法）
        return [str(v).strip()] if str(v or "").strip() else []

    @model_validator(mode="after")
    def _invariants(self) -> "ErrorAnalysis":
        if self.error_tag not in ANALYSIS_TAGS:
            raise ValueError(f"error_tag 必须是 6 类之一，收到 {self.error_tag!r}")
        if not self.evidence:
            raise ValueError("evidence（判断依据）不能为空——归因必须有出处可复核")
        if not self.fix:
            raise ValueError("fix（修正陈述）不能为空——没有可执行结论的分析等于没做")
        if len(self.fix) > FIX_MAX_CHARS:
            raise ValueError(f"fix 超过 {FIX_MAX_CHARS} 字（提示词要求 ≤25 字，超发=又写成了泛泛解析）")
        if len(self.variants) > 3:
            raise ValueError("variants 最多 2~3 条常见变形考法")
        return self


# EP-01 阶段 3：苏格拉底式错题复习的判分契约（prompts/socratic_review.md 当 task=score）。
#
# 与 medtutor.md 的 MEDTutor 判分**不同**：那里的判分对象是「知识点掌握度」，
# 这里的判分对象是「有没有回到当初那个错误岔路口」——故多一个 `hit_crossroad` 布尔。
#
# 红线（《总纲》§3.2）：**本契约没有 answer / correct 字段**。
# 提示词要求模型"看得到答案但不得转述"，契约层面再堵一道口子：
# 模型即便把答案塞进 `next_question` 之外的键，`extra="ignore"` 也会丢弃。
# `next_question` / `gap` 是自由文本，故另有 `test_socratic_never_leaks_answer`
# 端到端把关（用真答案串做注入，断言不出现在任何返回文本里）。
SOCRATIC_SCORE_MIN = 0
SOCRATIC_SCORE_MAX = 3


class SocraticScore(BaseModel):
    """苏格拉底复习单轮判分（prompts/socratic_review.md 当 task=score 的 JSON 输出）。

    - `score`：0~3 整数，禁止鼓励分（直接进掌握度状态机，虚高 = 骗自己）；
    - `gap`：一句具体差距，必须点到"这一步推理缺的环节"；
    - `next_question`：下一问（**不得含正确答案**，由端到端守卫把关）；
    - `hit_crossroad`：本轮是否真的回到并说清当初那个错误岔路口——本管线最关键的信号，
      为 False 时下一问必须继续追同一岔路口（不得换档）；
    - `where_uncertain`：模型自认不确定处（【待查】/【答案待核实】）。
    """

    model_config = ConfigDict(extra="ignore")

    score: int = -1           # -1 = 无法判定（与本项目既有判分口径一致，见 medtutor.score_answer）
    gap: str = ""
    next_question: str = ""
    hit_crossroad: bool = False
    where_uncertain: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _int_score(cls, v: Any) -> int:
        """归一到 [-1, 3]。非整数/越界/小数 → -1（**不夹取、不截断**）。

        截断与夹取都会把"模型填错了"伪装成"模型判了 0/2 分"，而分数会直接进
        掌握度状态机 —— 这是与 `errorpipe._coerce_meta` 同一条铁律：
        **宁可判不了（-1）走重试，不可编一个分出来**。
        """
        # bool 是 int 子类：True 不是"1 分"，是模型把字段填错了 → 拒绝
        if isinstance(v, bool) or v is None:
            return -1
        if isinstance(v, float) and not v.is_integer():
            return -1      # 2.5 分不存在；int(2.5)==2 是静默截断（EP-01 §7.3 同款）
        try:
            n = int(v)
        except (TypeError, ValueError):
            return -1
        if n < SOCRATIC_SCORE_MIN or n > SOCRATIC_SCORE_MAX:
            return -1
        return n

    @field_validator("gap", "next_question", mode="before")
    @classmethod
    def _word_str(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("hit_crossroad", mode="before")
    @classmethod
    def _bool_hit(cls, v: Any) -> bool:
        """只有明确的真值才算 True——字符串 "false" 不能因为非空就变 True。"""
        if isinstance(v, bool):
            return v
        return str(v or "").strip().lower() in ("true", "1", "yes", "是")

    @field_validator("where_uncertain", mode="before")
    @classmethod
    def _word_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x or "").strip()]
        return [str(v).strip()] if str(v or "").strip() else []

    @model_validator(mode="after")
    def _invariants(self) -> "SocraticScore":
        if self.score < 0:
            # 无法判定时不要求 gap/next_question（调用方走 retry 分支，不计轮次）
            return self
        if not self.gap:
            raise ValueError("gap 不能为空——判了分却不说差距，等于给个数字就走")
        if self.score < SOCRATIC_SCORE_MAX and not self.next_question:
            raise ValueError("未满分的轮次必须给 next_question（继续追问，而非放行）")
        return self


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
    改成 ``type[_M]`` + ``Optional[_M]`` 后，``validate_or_repair(raw, SocraticScore)``
    静态类型即 ``Optional[SocraticScore]``，字段可见。**类型注解要贴着真实契约写。**
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
