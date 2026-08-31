"""MedExplain：对「未掌握知识点」生成结构化教材讲解（M3）。

输入：知识点名 + 命中教材切片（可选）+ 关联错题（可选）+ 联网补充素材。
输出规约（结论先行 → 机制 → 鉴别/易混 → 记忆锚点），每段带来源引用：
  【教材·切片标题】 来自切片注入；【网: title | url】来自网络补充——不可混标。
无原文回退（2026-08-29）：切片检索不到原文时**先明确说明**「未检索到教材原文」，
再结合网络补充素材（如有）与模型医学知识输出完整讲解；不得把模型知识标注成【教材】。

网络补充默认开启（use_web=True）：切片覆盖不足时，用已有 web_search 多后端做 ≤1 轮补充检索，
素材进 system（与切片同通道注入，控成本）。孤立的切片充足时可不联网以省成本。
"""

import re
from typing import Any, Callable, Optional

from . import render_prompt

# 联网补充：单轮、单检索词（讲解补充不需要出题的三路考纲/真题/指南检索）
WEB_QUERY_LIMIT = 40
WEB_TIMEOUT = 20.0


def _build_web_query(subject: str, kp_name: str, mistake: Optional[dict[str, Any]]) -> str:
    """生成一条联网检索词：知识点 + 科目/章节，简洁。"""
    parts = [kp_name]
    if mistake:
        ch = mistake.get("chapter") or ""
        if ch:
            parts.append(ch)
    return f"{subject} {' '.join(parts)}".strip()[:WEB_QUERY_LIMIT]


def _search_web(query: str, search_fn: Optional[Callable[[str], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """联网补充：单后端调用，错误隔离（网络失败不阻断讲解，退化纯教材）。"""
    if search_fn is None:
        return []
    try:
        return search_fn(query) or []
    except Exception:  # noqa: BLE001  单后端错误隔离，不崩讲解
        return []


def _web_digest(materials: list[dict[str, Any]], limit: int,
                header: str = "## 网络检索补充素材（仅供校准与背景，答案以教材为准；带【网:】引用）"
                ) -> str:
    """网络素材 → 注入文本（带 URL 溯源）；header 可按调用方语境替换。"""
    out: list[str] = []
    for m in materials[:limit]:
        title = (m.get("title") or "")[:60]
        url = m.get("url") or ""
        snippet = (m.get("snippet") or "")[:240]
        line = f"- {title}" + (f" · {url}" if url else "")
        if snippet:
            line += f"\n  {snippet}"
        out.append(line)
    if not out:
        return ""
    return header + "\n" + "\n".join(out)


def prepare_explain(subject: str,
                      kp_name: str,
                      slices_text: str = "",
                      related_mistake: Optional[dict[str, Any]] = None,
                      web_materials: Optional[list[dict[str, Any]]] = None,
                      search_fn: Optional[Callable[[str], list[dict[str, Any]]]] = None,
                      use_web: bool = True) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """WP-8：构造讲解 LLM 请求（流式/非流式共用），返回 (messages, meta)。

    meta = {grounded, via_web, web_materials}，与 explain_knowledge 返回值对齐。
    """
    grounded = bool(slices_text.strip())
    via_web = False
    web_materials = list(web_materials or [])
    # 切片不足 → 联网补充（默认开启）
    if use_web and len(slices_text.strip()) < 120:
        query = _build_web_query(subject, kp_name, related_mistake)
        fetched = _search_web(query, search_fn)
        if fetched:
            via_web = True
            web_materials = fetched
    web_digest = _web_digest(web_materials, 4)

    system = render_prompt("medexplain.md", subject=subject, kp_name=kp_name)
    # 素材注入：切片 + 网络（两者独立标注，不混淆）
    body: list[str] = []
    if slices_text.strip():
        body.append(f"## 教材切片（可引用的唯一事实来源）\n{slices_text.strip()}")
    if web_digest:
        body.append(web_digest)
    if related_mistake:
        why = (related_mistake.get("error_reason") or "")
        q = (related_mistake.get("question") or "")[:200]
        body.append(f"## 关联错题\n- 题干：{q}\n- 可能错因：{why or '未知'}")
    if not grounded:
        # 无原文回退：先说明，再要求模型结合网络素材（如有）与医学知识输出完整讲解
        lead = ("（重要说明：本知识点在本地教材切片中未检索到原文"
                + ("，仅有下面的网络检索补充素材" if web_digest else "，也没有网络补充素材")
                + "。请先向学生明确说明这一情况，然后结合"
                + ("网络素材与" if web_digest else "")
                + "你的医学知识给出尽量完整的结构化讲解；"
                + "数值/指南条目给出通用公认值并注明“以最新教材/指南为准”，"
                + "不要标注【教材】，不要谎称内容出自教材。）")
        body.insert(0, lead)
    user = "\n\n".join(body)
    return ([{"role": "system", "content": system},
             {"role": "user", "content": user}],
            {"grounded": grounded, "via_web": via_web,
             "web_materials": web_materials})


def explain_knowledge(client: Any,
                      subject: str,
                      kp_name: str,
                      slices_text: str = "",
                      related_mistake: Optional[dict[str, Any]] = None,
                      web_materials: Optional[list[dict[str, Any]]] = None,
                      search_fn: Optional[Callable[[str], list[dict[str, Any]]]] = None,
                      use_web: bool = True) -> dict[str, Any]:
    """生成一篇结构化讲解。

    返回 {content, sources:[{kind:"textbook"|"web", title, url}], via_web,
          web_materials, grounded}——grounded=False 表示未命中教材切片原文，
    内容依据为网络素材与模型知识（前端据此展示「无教材原文」说明）。
    """
    messages, meta = prepare_explain(subject, kp_name, slices_text,
                                     related_mistake=related_mistake,
                                     web_materials=web_materials,
                                     search_fn=search_fn, use_web=use_web)
    msg = client.chat(messages, temperature=0.5)
    content = (msg or "").strip()
    sources = _sources_of(slices_text, meta["web_materials"])
    return {"content": content, "sources": sources, "via_web": meta["via_web"],
            "web_materials": meta["web_materials"], "grounded": meta["grounded"]}


def _sources_of(slices_text: str, web_materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从注入内容里抽取【教材·…】与【网: …】来源清单（供前端溯源展示）。"""
    sources: list[dict[str, Any]] = []
    for m in re.finditer(r"【教材切片\s*([^】]+)】", slices_text):
        sources.append({"kind": "textbook", "title": m.group(1).strip(), "url": ""})
    for m in web_materials[:6]:
        url = m.get("url") or ""
        sources.append({"kind": "web", "title": (m.get("title") or "")[:60], "url": url})
    return sources[:10]


def needs_client_and_price(subject: str, kp_name: str) -> int:
    """粗估输入 token（供 cost toast，不含输出）。"""
    seed = len(subject) + len(kp_name)
    return 600 + seed * 2 + 900  # 素材窗口 + 提示词开销
