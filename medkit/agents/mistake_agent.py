"""错题 Agent —— L4 助手层。**可缺席**：没配 API Key 时上面三层照常工作。

## 它只能引用你的 vault

三个工具，全部只读：

| 工具 | 作用 |
|---|---|
| `search_my_mistakes(query, subject?, reveal?)` | 在错题里找相关条目。**默认不给答案**（防剧透） |
| `get_topic_stats(subject?)` | 薄弱项表（= 前端那张表的同一份结果） |
| `read_file(path, section?)` | 读一个 vault 文件（含用户自己写的理解/疑问） |

## 四条硬约束（可证伪）

1. **必须带引用**：结论后面跟 `[来源: <相对路径>#<小节>]`；检索不到依据的句子不许下结论。
2. **无依据必须说不知道**：工具返回与问题无关时，答复只能是「我的错题库里没有相关记录」，
   **禁止**用通识补全（这是 NotebookLM 可信的原因，也是本 Agent 的底线）。
3. **必须回应用户自己的那个误解**：上下文里带该题的 `我的理解/我的疑问` 原文，
   答复要针对**他写的那个具体东西**，不是泛泛讲知识点。
4. **静默失败 = 失败**：任何工具抛错，答复里写明哪个工具失败了。

## 编排协议

不依赖服务商的 function-calling（各家兼容性参差）：让模型输出一段 JSON：

```json
{"action": "search_my_mistakes", "args": {"query": "慢阻肺"}}
{"action": "final", "answer": "……", "citations": [{"path": "…", "section": "解析"}]}
```

最多 `MAX_ROUNDS` 轮；轮数用尽仍未 final ⇒ 以工具结果为据直答并注明「未收敛」。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from ..core import vault, vault_index
from ..core.llm import LLMError
from . import get_client, render_prompt

MAX_ROUNDS = 5
MAX_TOKENS = 3000        # 推理模型会把预算烧在 reasoning 上（实测 1200 会被截断成空正文）
PROMPT_NAME = "mistake_agent.md"

_FINALIZE = {"role": "user", "content":
             "停止调用任何工具。现在直接输出 final JSON，基于上面已经取到的证据作答"
             "（证据不足就照实说没有相关记录）。"}

_TOOLS = {
    "search_my_mistakes",
    "get_topic_stats",
    "read_file",
}


class AgentError(RuntimeError):
    pass


# ------------------------------------------------------------------ 工具实现
def _tool_search(query: str, subject: str = "", reveal: bool = False) -> dict[str, Any]:
    items = vault_index.list_items(subject=subject, q=query)
    out = []
    for r in items[:12]:
        out.append({
            "path": r["path"], "question": r["question"],
            "chapter": r.get("chapter") or "", "topic": r.get("topic") or "",
            "analysis": r.get("analysis") or "",
            "understanding": r.get("understanding") or "",
            "doubts": r.get("doubts") or "",
            "answer": (r.get("answer") or "") if reveal else "",
        })
    return {"count": len(items), "shown": len(out), "items": out}


def _tool_stats(subject: str = "") -> dict[str, Any]:
    return vault_index.stats(subject=subject)


def _tool_read(path: str, section: str = "") -> dict[str, Any]:
    root = vault.vault_root()
    target = (root / path.replace("\\", "/").lstrip("/")).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise AgentError("read_file 越出 vault 根目录")
    if not target.is_file():
        raise AgentError(f"文件不存在：{path}")
    rec = vault.read_record(target)
    if section:
        key = {"解析": "analysis", "我的理解": "my_reasoning",
               "我的疑问": "doubts", "Agent 的答复": "agent_reply"}.get(section)
        if key:
            return {"path": path, "section": section, "text": rec.get(key, "")}
    return {"path": path, "question": rec["question"], "answer": rec.get("answer", ""),
            "analysis": rec.get("analysis", ""), "understanding": rec.get("my_reasoning", ""),
            "doubts": rec.get("doubts", ""), "reply": rec.get("agent_reply", "")}


def _run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "search_my_mistakes":
        return _tool_search(str(args.get("query") or ""), str(args.get("subject") or ""),
                            bool(args.get("reveal")))
    if name == "get_topic_stats":
        return _tool_stats(str(args.get("subject") or ""))
    if name == "read_file":
        return _tool_read(str(args.get("path") or ""), str(args.get("section") or ""))
    raise AgentError(f"未知工具 {name!r}")


# ------------------------------------------------------------------ 编排
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(raw: str) -> dict[str, Any]:
    m = _JSON_RE.search(raw or "")
    if not m:
        raise AgentError("模型没有输出可解析的 JSON（协议外内容）")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AgentError(f"模型输出的 JSON 解析失败：{e}")
    if not isinstance(obj, dict) or obj.get("action") not in _TOOLS | {"final"}:
        raise AgentError("模型输出的 action 不在允许集合内")
    return obj


def _ungrounded_message(tool_log: list[dict[str, Any]]) -> str:
    """没有可验证依据时说什么。**统一口径，不留模型自己发挥的空间**。"""
    failed = [str(t["tool"]) for t in tool_log if not t.get("ok")]
    if failed:
        return (f"我的错题库里没有可用依据（工具 {'、'.join(failed)} 执行失败），"
                "因此不给结论。请重试，或直接在文件夹里看那个文件。")
    return "我的错题库里没有相关记录（未检索到与问题相关的错题）。"


def _validate_citations(cites: Any) -> tuple[list[dict[str, str]], list[str]]:
    """引用必须是索引里真实存在的路径。**编造路径的一律剔除并如实报告**。"""
    known = {r["path"] for r in vault_index._rows()}
    ok, dropped = [], []
    for c in cites or []:
        if not isinstance(c, dict):
            continue
        p = str(c.get("path") or "")
        if p in known:
            ok.append({"path": p, "section": str(c.get("section") or "")})
        else:
            dropped.append(p)
    return ok, dropped


async def ask(question: str, *, scope: str = "", path: str = "",
              ) -> AsyncIterator[dict[str, Any]]:
    """主入口：逐事件 yield。最后一个必然是 `{"type": "result"}` 或 `{"type": "error"}`。"""
    client = get_client("gen")
    base = ""
    if path:
        try:
            rec = vault.read_record(vault.vault_root() / path)
            base = (f"用户正在看这一条错题：{rec['question']}\n"
                    f"他的理解：{rec.get('my_reasoning') or '（未写）'}\n"
                    f"他的疑问：{rec.get('doubts') or '（未写）'}\n")
        except OSError as e:
            base = f"（指定文件读失败：{e}）\n"

    system = render_prompt(PROMPT_NAME)
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"{base}{question}\n\n（范围限定：{scope or '全部科目'}）")},
    ]
    tool_log: list[dict[str, Any]] = []
    final: dict[str, Any] = {}

    for rnd in range(1, MAX_ROUNDS + 1):
        yield {"type": "stage", "round": rnd, "max_rounds": MAX_ROUNDS,
               "hint": "思考该调哪个工具" if rnd == 1 else "继续取证"}
        try:
            raw = await asyncio.to_thread(
                client.chat, convo, temperature=0.2, json_mode=True, max_tokens=MAX_TOKENS)
        except LLMError as e:
            yield {"type": "error", "message": f"模型调用失败：{e}", "tools": tool_log}
            return
        try:
            obj = _extract_json(raw)
        except AgentError as e:
            yield {"type": "error", "message": str(e), "raw": raw[:400], "tools": tool_log}
            return

        if obj.get("action") == "final":
            final = obj
            break

        name = str(obj.get("action"))
        args = obj.get("args") or {}
        yield {"type": "stage", "round": rnd, "tool": name, "args": args}
        try:
            res = _run_tool(name, args)
        except Exception as e:  # noqa: BLE001  工具失败必须显性，不得静默
            res = {"error": f"工具 {name} 执行失败：{e}"}
        tool_log.append({"tool": name, "args": args,
                         "ok": "error" not in res, "result": res})
        convo.append({"role": "assistant", "content": raw})
        convo.append({"role": "user", "content":
                      f"工具 {name} 的结果：\n{json.dumps(res, ensure_ascii=False)[:6000]}"})
    if not final:
        # 轮数用尽仍未 final ⇒ **再追一轮「只许 final」**（实测：推理模型在工具轮里
        # 常把预算耗在 reasoning 上，不追这一轮就会给用户一个空答复）。
        yield {"type": "stage", "hint": "达到工具轮数上限，基于已取证据作答"}
        note = f"未在 {MAX_ROUNDS} 轮内收敛，以下基于已取到的证据"
        try:
            raw = await asyncio.to_thread(
                client.chat, convo + [_FINALIZE],
                temperature=0.2, json_mode=True, max_tokens=MAX_TOKENS)
            obj = _extract_json(raw)
            final = obj if obj.get("action") == "final" else {"action": "final", "answer": ""}
        except (LLMError, AgentError):
            final = {"action": "final", "answer": ""}
        final["note"] = (str(final.get("note") or "") + " " if final.get("note") else "") + note

    answer = str(final.get("answer") or "").strip()
    cites, dropped = _validate_citations(final.get("citations"))
    stats_ok = any(t["tool"] == "get_topic_stats" and t["ok"] for t in tool_log)
    grounded = bool(cites) or stats_ok
    note = str(final.get("note") or "")
    if not grounded:
        # **硬约束 2**：没有任何可验证依据时，不许用通识补全。
        # 这是本 Agent 与通用聊天机器人唯一的分界线——宁少答，不乱答。
        answer = _ungrounded_message(tool_log)
        note = (note + " " if note else "") + "（结论因缺少可验证依据被拦下）"
    if not answer:
        # 兜底：即便取到了依据，模型也可能返回空答复（预算截断/未收敛）——
        # **不许把空白丢给用户**（实测：推理模型 + 轮数用尽即此形）。
        tools = "、".join(sorted({str(t["tool"]) for t in tool_log})) or "无"
        answer = (f"我这轮没能形成结论（已调用工具：{tools}）。"
                  "请换个问法，或直接在文件夹里打开那条错题看原文。")
    if dropped:
        answer += "\n\n（已剔除无效引用：" + "、".join(dropped[:3]) + "）"
    yield {"type": "result", "answer": answer, "citations": cites,
           "dropped_citations": dropped, "tools": tool_log,
           "grounded": grounded, "note": note}
