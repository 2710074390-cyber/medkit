"""MedGen：按切片 + 配额出题（单切片一次调用；不足自动补齐）。

可玩性（2026-08 执行方案）：
- 1A 附加生成要求（requirements）：自由文本追加在 system 末尾（唯一注入点）
- 2A 结构化旋钮（KNOB_FRAGMENTS）：难度/解析风格/题干风格 → 预写片段同通道注入
- 2B Bloom 配比自定义：{bloom_ratios} 占位符替换（默认 30/40/25/5）
- §5.4 网络检索参考素材：web_materials 注入 + 引用配额 web_quota（默认 0）
A6（2026-08 审计）：user 消息只保留参数摘要，全文仅在 system 注入一次。
U4：返回题数 < 配额 → 最多补 2 轮。
"""

import logging
from typing import Any, Optional

from pydantic import ValidationError

from ..core.schema import QuestionItem
from . import get_client as _get_client
from . import render_prompt

logger = logging.getLogger(__name__)

DEFAULT_BLOOM = {"记忆": 30, "理解": 40, "应用": 25, "创造": 5}
TEACHER_CHAR_LIMIT = 4000  # 教师重点注入上限（S2：单源常量，管线/orchestrator/trial 共用）
SYLLABUS_TEXT_LIMIT = 800  # WP-01：大纲锚定条目注入上限
IMAGE_SECTIONS_LIMIT = 2000  # WP-04：图像素材清单注入上限
EXAM_CHAR_LIMIT = 4000    # 自备真题注入上限（v0.5.2：仅风格/考点校准，防照抄）
EXTRA_CHAR_LIMIT = 4000    # 自备资料注入上限（v0.5.2：教材的补充上下文）

# 结构化旋钮 → 预写提示词片段（2A；集中一处便于调优）
KNOB_FRAGMENTS: dict[str, dict[str, str]] = {
    "difficulty": {
        "basic": "难度定位：基础题——考点单一、直问直答，干扰项区分度中等，适合第一轮复习。",
        "clinical": "难度定位：临床综合题——多考点串联，选项需鉴别诊断，贴近执医/西综真题风格。",
        "challenge": "难度定位：挑战题——细节陷阱、数值边界、易混淆概念对比，适合查漏。",
    },
    "analysis_style": {
        "detailed": "解析风格：详尽机制型——按「考点定位 → 机制阐述 → 易错提醒」三段展开，≥80 字，适合第一轮系统学习。",
        "snappy": "解析风格：速记型——一句话点破考点 + 一行易错提醒，≤40 字。",
        "examkey": "解析风格：考点速览——固定三行：「考点：…」「关键鉴别：…」「易错：…」，适合冲刺背诵与快速自测。",
        "teaching": "解析风格：教学讲解型——结论先行，再逐项评析每个干扰项为何错、考什么，适合初学者与教研命题。",
        "compare": "解析风格：鉴别对比型——以「最易混淆对象 → 关键区别点 → 记忆锚（口诀/类比）」呈现，突出横向对比。",
    },
    "stem_style": {
        "direct": "题干风格：直问直答（A1 干净无病例）。",
        "narrative": "题干风格：病例叙事（临床资料 → 提问，A2 化）。",
        "staged": "题干风格：渐进披露——病例信息分阶段给出（首诊 → 复诊 → 辅助检查回报），考察诊疗决策的动态更新。",
        "data": "题干风格：数据判读——题干附检验/影像数值（含单位与参考范围），考察判读与临界值分析。",
    },
}


def build_extra_block(requirements: str = "", knobs: Optional[dict[str, str]] = None) -> str:
    """用户自定义 → 追加在 system 末尾。追加而非改模板：影子副本/旋钮/自由文本共用同一通道，
    且不触碰内置模板结构，避免占位符漂移。顺序：片段在前、自由文本在后。"""
    parts: list[str] = []
    if knobs:
        for key, val in knobs.items():
            frag = KNOB_FRAGMENTS.get(key, {}).get(val, "")
            if frag:
                parts.append(frag)
    req = (requirements or "").strip()
    if req:
        parts.append("## 用户附加生成要求（优先级高于默认风格；"
                     "与上方硬约束冲突时，以硬约束为准）\n" + req[:500])
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def build_web_block(web_materials: str = "", web_quota: int = 0) -> str:
    """§5.4：网络检索参考素材注入（引用配额 >0 时才生效；conflict 条目不得作答案依据）。"""
    if not web_materials or web_quota <= 0:
        return ""
    return ("\n\n## 网络检索参考素材（引用配额 ≤ %d%% —— 从下述素材选题时，"
            "analysis 末尾以 [源:网 URL] 结尾；标记【与教材冲突-勿用答案】的条目"
            "不得作为正确答案依据）\n%s") % (web_quota, web_materials)


def build_reference_block(exam_text: str = "", extra_text: str = "") -> str:
    """v0.5.2：自备真题 / 补充资料注入（追加在 system 末尾，与 web 块同通道）。

    真题只做考点/风格校准（防照抄硬约束）；资料作为教材的补充上下文（冲突以教材为准）。
    两者均为用户本机自备素材，不参与 [源:] 溯源——题目依据仍是教材切片 + 教师重点。
    """
    parts: list[str] = []
    ex = (exam_text or "").strip()
    if ex:
        parts.append(
            "## 用户自备真题参考（仅供校准，严禁照抄）\n"
            "以下是用户自备的历年真题原文，仅用于两件事：\n"
            "1. 高频考点优先：真题反复出现的考点，若出现在当前教材切片中，优先出题、加大题干深度；\n"
            "2. 风格校准：题干长度/选项结构/难度分布贴近真题风格。\n"
            "硬约束（违反即不合格）：\n"
            "- 严禁照抄、改写、翻译或换选项复述任何一道真题原题（含题干与选项）；\n"
            "- 所有题目必须出自当前教材切片，溯源仍标注 [源:切片SXXX]，不得标注或引用 [源:用户真题]；\n"
            "- 真题中出现但教材切片没有的知识点不得出题（防超纲）。\n"
            "```\n%s\n```" % ex)
    et = (extra_text or "").strip()
    if et:
        parts.append(
            "## 用户自备补充资料（课件/笔记/大纲）\n"
            "以下资料与教材切片互补，可用于补充出题细节（数值/标准/表格）与解析措辞；\n"
            "若与教材冲突，一律以教材切片为准；溯源仍标注 [源:切片SXXX]。\n"
            "```\n%s\n```" % et)
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def _bloom_ratio_str(bloom: Optional[dict[str, Any]]) -> str:
    """Bloom 配比 → 提示词字符串。

    v0.5：兼容小数/百分比混合输入（int(0.3)=0 的静默回退已修）；合计 ≠100 时归一 + warn
    （如 0.3/0.4/0.25/0.05 的比例式输入会归一为 30/40/25/5）。
    """
    raw = bloom or DEFAULT_BLOOM
    b: dict[str, float] = {}
    for k, v in raw.items():
        try:
            b[k] = float(v or 0)
        except (TypeError, ValueError):
            b[k] = 0.0
    total = sum(b.values())
    if total <= 0:
        b = {k: float(v) for k, v in DEFAULT_BLOOM.items()}
    elif abs(total - 100.0) > 0.01:
        logger.warning("Bloom 配比合计 %.2f ≠ 100，已归一化到 100%%", total)
        b = {k: round(v / total * 100.0, 1) for k, v in b.items()}
    return " / ".join(f"{b.get(k, float(d)):g}%" for k, d in DEFAULT_BLOOM.items())


def build_user_message(subject: str, exam: str, slice_: dict[str, Any],
                       count: int, ratios: dict[str, int]) -> str:
    ratios_str = ", ".join(f"{k} {v}%" for k, v in ratios.items() if v > 0)
    return (f"科目：{subject}\n目标考试：{exam}\n本切片题数：{count} 题；题型配比：{ratios_str}\n"
            f"教材切片：{slice_.get('sid')}《{slice_.get('title', '')}》\n"
            f"请输出 JSON：{{'questions': [...]}}，恰好 {count} 道题。")


def _parse_questions(data: Any, slice_: dict[str, Any],
                     contract_bad: Optional[list[int]] = None) -> list[dict[str, Any]]:
    raw = data.get("questions", []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    questions = [q for q in raw if isinstance(q, dict) and q.get("question")]
    for q in questions:
        # IMP-03：QuestionItem 契约校验（软校验——仅记录告警，不删除。
        # 与既有门禁（gate1 / 渲染前终检）分工：契约层冗余校验，避免改变
        # 现有筛选与修复行为造成零回归；坏题仍交由门禁修复循环处理。）
        try:
            QuestionItem.model_validate(q)
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            logger.warning("MedGen 输出未通过 QuestionItem 契约（question=%r）：%s",
                           str(q.get("question", ""))[:40],
                           first.get("msg", str(e)))
            # NX-03（R-2）：软校验告警计数 → 项目 meta（学习中心概览可见）
            if contract_bad is not None:
                contract_bad.append(1)
        # v0.5：显式 null / 类型异常统一兜底（setdefault 不覆盖显式 null → 下游 enumerate 崩）
        q["type"] = str(q.get("type") or "A1")
        q["bloom"] = str(q.get("bloom") or "理解")
        q["subtopic"] = str(q.get("subtopic") or slice_.get("title", "")[:12])
        opts = q.get("options")
        if isinstance(opts, list):
            q["options"] = [str(o) for o in opts if isinstance(o, (str, int, float))]
        else:
            q["options"] = []
        q["answer"] = str(q.get("answer") or "")
        q["analysis"] = str(q.get("analysis") or "")
        q["sid"] = slice_.get("sid", "")
        q["module"] = slice_.get("title", "")
        # S3：案例/选项组字段（扁平 + 冗余 case_stem；不引入嵌套）
        q["case_stem"] = str(q.get("case_stem") or "")[:1500]
        q["case_id"] = str(q.get("case_id") or "")
        # WP-04：图/表题字段（可选）：image_ref=素材切片ID（如 IMG1）；data_table=Markdown 表格
        q["image_ref"] = str(q.get("image_ref") or "")[:40]
        q["data_table"] = str(q.get("data_table") or "")[:1200]
        try:
            q["case_order"] = int(q.get("case_order") or 0)
        except (TypeError, ValueError):
            q["case_order"] = 0
        q["group_kind"] = str(q.get("group_kind") or "")
        grp = q.get("group")
        if q["group_kind"] == "option_group" and isinstance(grp, dict):
            gopts = grp.get("options")
            grp = dict(grp)
            grp["options"] = ([str(o) for o in gopts if isinstance(o, (str, int, float))]
                              if isinstance(gopts, list) else [])
            q["group"] = grp
        elif grp is not None:
            q["group"] = grp if isinstance(grp, dict) else None
    return questions


def _call_once(client: Any, system: str, subject: str, exam: str, slice_: dict[str, Any],
               count: int, ratios: dict[str, int], supplement: bool = False,
               contract_bad: Optional[list[int]] = None) -> list[dict[str, Any]]:
    msg = build_user_message(subject, exam, slice_, count, ratios)
    if supplement:
        msg = ("当前不足要求题数，请仅补充缺少的题目：\n" + msg)
    data = client.chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": msg},
    ], temperature=0.7)
    return _parse_questions(data, slice_, contract_bad)


def generate_slice(client: Any, subject: str, exam: str, slice_: dict[str, Any],
                   count: int, ratios: dict[str, int], teacher_text: str,
                   ids_start: int = 1, requirements: str = "",
                   knobs: Optional[dict[str, str]] = None,
                   bloom: Optional[dict[str, int]] = None,
                   web_materials: str = "", web_quota: int = 0,
                   exam_text: str = "", extra_text: str = "",
                   syllabus_text: str = "", image_sections: str = "",
                   contract_bad: Optional[list[int]] = None) -> tuple[list[dict[str, Any]], int]:
    """单切片出题（可能含 ≤2 次补充调用）。返回 (questions, 下一个 id 序号)。

    v0.5：占位符一次性替换（防教材文本二次替换注入）；超发题数按配额截断。
    ``contract_bad``：软校验失败计数器（list，线程安全 append；None = 不计数）。
    """
    parts = {
        "subject": subject,
        "exam": exam,
        "slice_count": str(count),
        "ratios": ", ".join(f"{k} {v}%" for k, v in ratios.items() if v > 0),
        "bloom_ratios": _bloom_ratio_str(bloom),
        "slice_text": slice_.get("text", "")[:8000],
        "teacher_text": teacher_text[:TEACHER_CHAR_LIMIT],
        "syllabus_text": syllabus_text[:SYLLABUS_TEXT_LIMIT],
        "image_sections": image_sections[:IMAGE_SECTIONS_LIMIT],
    }
    system = render_prompt("medgen.md", **parts)
    system += build_extra_block(requirements, knobs)      # 迭代1/2 注入点
    system += build_web_block(web_materials, web_quota)   # §5.4 参考素材注入点
    system += build_reference_block(exam_text[:EXAM_CHAR_LIMIT],
                                    extra_text[:EXTRA_CHAR_LIMIT])  # v0.5.2 自备真题/资料注入点
    questions = _call_once(client, system, subject, exam, slice_, count, ratios,
                           contract_bad=contract_bad)
    questions = questions[:count]  # v0.5：LLM 超发 → 按配额截断
    # U4：不足配额 → 补 2 轮
    for _ in range(2):
        if len(questions) >= count:
            break
        extra = _call_once(client, system, subject, exam, slice_,
                           count - len(questions), ratios, supplement=True,
                           contract_bad=contract_bad)
        if not extra:
            break
        questions.extend(extra)
        questions = questions[:count]  # 补充轮同样截断
    return questions, ids_start + len(questions)


def make_client() -> Any:
    return _get_client("gen")
