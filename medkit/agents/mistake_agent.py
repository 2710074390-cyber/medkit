"""错题 Agent —— L4 助手层。**可缺席**：没配 API Key 时上面三层照常工作。

## 它只能引用你的 vault

三个工具，全部只读：

| 工具 | 作用 |
|---|---|
| `search_my_mistakes(query, subject?, reveal?)` | 在错题里找相关条目。**默认不给答案**（防剧透） |
| `get_topic_stats(subject?)` | 薄弱项表（= 前端那张表的同一份结果） |
| `read_file(path, section?)` | 读一个 vault 文件（含用户自己写的理解/疑问） |

另有 `handbook(subject)`：把「薄弱项分组 + 错题条目」交给模型归纳成**薄弱点复习手册**
（同一底线：只依据 vault，数据不足就明说）。它不走工具轮——证据一次性给全。

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

## 流式（2026-10-16）

协议是 JSON，而用户要的是**逐字流式**。两者不冲突的做法就是开源 agent 里常见的
「边流边做**部分 JSON 解析**」（partial / streaming JSON）：模型照旧吐 JSON，
我们在流里把 `answer` 字段的字符串值增量解出来，转成 `{"type": "delta"}` 事件。
收尾仍以**完整 JSON 解析**出的答复为准（`result` 覆盖已流出的文本，避免漂移）。

流式实现走 `chat_stream`（同步生成器）+ 工作线程 + `asyncio.Queue` 桥接；
取消用 `threading.Event` 传给 `LLMClient`——断连/停止时**立刻**截断 provider 流，
不让它在后台继续烧 token（这条口径来自本仓已删的讲解/提问域，见 RUNBOOK §C 前的既有范式）。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
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

_ANSWER_KEY = '"answer"'
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


def _partial_answer(raw: str) -> str | None:
    """从**尚未完成**的 JSON 文本里，解出 `answer` 字段当前的（可能不完整的）字符串值。

    这是「流式 JSON」的最小实现（开源 agent 的常见做法）。三条判据，缺一不可：

    ① 必须是**键**而不是值里的子串：`"answer"` 之后紧跟（允许空白）`:` 才算命中——
       否则 `{"action":"search_my_mistakes","args":{"query":"answer"}}` 会被误判成答复；
    ② 值必须已经开始（出现开引号），否则返回 `None`（还没到答复）；
    ③ 走到**未转义**的收尾引号才算完整；文本断在转义序列中间（`\\` 或 `\\u12`）
       就丢掉那半截——下一个 delta 会把它补全。

    返回 `None` = 这轮没有答复（工具轮），或还没轮到答复。
    """
    i = raw.find(_ANSWER_KEY)
    if i < 0:
        return None
    j = i + len(_ANSWER_KEY)
    while j < len(raw) and raw[j] in " \t\r\n":
        j += 1
    if j >= len(raw) or raw[j] != ":":       # 判据①：不是键
        return None
    k = j + 1
    while k < len(raw) and raw[k] in " \t\r\n":
        k += 1
    if k >= len(raw) or raw[k] != '"':       # 判据②：值还没开始
        return None
    out: list[str] = []
    p = k + 1
    while p < len(raw):
        ch = raw[p]
        if ch == '"':                        # 判据③：完整收尾
            return "".join(out)
        if ch == "\\":
            if p + 1 >= len(raw):
                return "".join(out)          # 转义序列还没流完
            nxt = raw[p + 1]
            if nxt == "u":
                hexs = raw[p + 2:p + 6]
                if len(hexs) < 4:
                    return "".join(out)
                try:
                    out.append(chr(int(hexs, 16)))
                except ValueError:
                    return "".join(out)
                p += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            p += 2
            continue
        out.append(ch)
        p += 1
    return "".join(out)                      # 还没到收尾引号 → 返回已解出的部分


async def _stream_chat(client: Any, convo: list[dict[str, Any]],
                       **kw: Any) -> AsyncIterator[str]:
    """把 `LLMClient.chat_stream`（**同步**生成器）桥成异步：工作线程拉，队列传。

    为什么要桥：`chat_stream` 内部是阻塞的网络 I/O，直接在事件循环里迭代会**卡住整个服务**
    （本仓单进程，卡住 = 全站无响应）；而本 Agent 的编排是 async（`ask()` 是 async generator，
    测试用 `run_coro` 驱动）。标准解法就是「线程 + asyncio 队列」。

    取消/断连：`client` 持有 `threading.Event`（见 `ask(cancel=…)`），事件置位后
    `chat_stream` 会在下一个 chunk 处返回 `canceled=True`，线程随即退出——
    不会在用户点了「停止生成」之后继续把 token 烧完。
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    done = object()

    def _put(item: Any) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:
            pass          # 事件循环已关闭（请求被取消）——线程安静退出，不刷栈

    def worker() -> None:
        try:
            for ev in client.chat_stream(convo, **kw):
                if ev.get("canceled"):
                    break
                d = ev.get("delta") or ""
                if d:
                    _put(d)
        except BaseException as e:  # noqa: BLE001  异常交给消费方显性处理
            _put(e)
        finally:
            _put(done)

    threading.Thread(target=worker, daemon=True, name="medkit-agent-stream").start()
    while True:
        item = await q.get()
        if item is done:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


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
              history: list[dict[str, Any]] | None = None,
              cancel: threading.Event | None = None,
              ) -> AsyncIterator[dict[str, Any]]:
    """主入口：逐事件 yield。最后一个必然是 `{"type": "result"}` 或 `{"type": "error"}`。

    事件流（前端按此消费）：
    - `{"type": "stage", "round": n, "hint"|"tool": …}` —— 正在干什么（取证中）
    - `{"type": "delta", "text": "…"}` —— **答复的增量文本**（逐字流式）
    - `{"type": "result", "answer": …, "citations": …}` —— 收尾（**以它为准**，覆盖已流出文本）
    - `{"type": "error", "message": …}` —— 出错（必须显性）

    `history`（可选）：聊天框里的前几轮（`[{"role": "user"|"assistant", "content": …}]`）。
    只用于**让追问能指代上文**（「那它和 X 怎么鉴别」）；四条硬约束不变——每一轮的结论
    仍必须由**本轮工具取到的证据**支撑，不因为上文说过就免证。

    `cancel`（可选）：`threading.Event`。断连/用户点「停止生成」时置位 ⇒ 立刻截断
    provider 流，不在后台把 token 烧完。
    """
    client = get_client("gen", cancel=cancel)
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
    convo: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for h in (history or [])[-8:]:
        role = "assistant" if str(h.get("role")) == "assistant" else "user"
        text = str(h.get("content") or "").strip()
        if text:
            convo.append({"role": role, "content": text[:4000]})
    convo.append({"role": "user", "content": (
        f"{base}{question}\n\n（范围限定：{scope or '全部科目'}）")})
    tool_log: list[dict[str, Any]] = []
    final: dict[str, Any] = {}

    async def _one_call(messages: list[dict[str, Any]]) -> AsyncIterator[tuple[str, str]]:
        """跑一次模型调用：边收边解 `answer`，yield `("delta", 增量)` / `("raw", 全文)`。"""
        raw = ""
        emitted = 0
        async for piece in _stream_chat(client, messages, temperature=0.2,
                                        json_mode=True, max_tokens=MAX_TOKENS):
            raw += piece
            ans = _partial_answer(raw)
            if ans is not None and len(ans) > emitted:
                yield ("delta", ans[emitted:])
                emitted = len(ans)
        yield ("raw", raw)

    try:
        for rnd in range(1, MAX_ROUNDS + 1):
            yield {"type": "stage", "round": rnd, "max_rounds": MAX_ROUNDS,
                   "hint": "思考该调哪个工具" if rnd == 1 else "继续取证"}
            raw = ""
            try:
                async for kind, val in _one_call(convo):
                    if kind == "delta":
                        yield {"type": "delta", "text": val}
                    else:
                        raw = val
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
            raw = ""
            try:
                async for kind, val in _one_call(convo + [_FINALIZE]):
                    if kind == "delta":
                        yield {"type": "delta", "text": val}
                    else:
                        raw = val
                obj = _extract_json(raw)
                final = obj if obj.get("action") == "final" else {"action": "final", "answer": ""}
            except (LLMError, AgentError):
                final = {"action": "final", "answer": ""}
            final["note"] = (str(final.get("note") or "") + " " if final.get("note") else "") + note
    finally:
        # 断连（GeneratorExit）/ 正常结束 / 异常：都置位 ⇒ 流桥线程立刻退出，
        # 不再从 provider 拉后续 chunk（「停止生成」要真的省 token，不是只停 UI）。
        if cancel is not None:
            cancel.set()

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


# ------------------------------------------------------------------ 薄弱点复习手册
HANDBOOK_MAX_TOKENS = 6000
HANDBOOK_MAX_ITEMS = 30          # 送进模型的最大错题条数（证据太长会把预算烧在输入上）
HANDBOOK_ANALYSIS_CHARS = 400    # 每条解析截断（手册是归纳，不需要全文）


def _handbook_evidence(subject: str, max_items: int) -> tuple[dict[str, Any], str, int]:
    """把「薄弱项分组 + 错题条目」摊成给模型看的证据文本。**只读**，不调模型。"""
    st = vault_index.stats(subject=subject)
    groups = [g for g in (st.get("groups") or []) if int(g.get("total") or 0) > 0][:15]
    items = vault_index.list_items(subject=subject)[:max_items]

    glines = [
        f"- [{g['subject'] or '未分类'} / {g['chapter'] or '未分章'} / {g['topic'] or '未分组'}] "
        f"共 {g['total']} 题 · 未掌握 {g['unlearned']} · 累计错 {g['miss_count']} 次 · "
        f"状态 {g['state']} · 优先级 {g['priority']}"
        for g in groups
    ]
    blocks = [
        f"### {it.get('path')}\n"
        f"- 科目/章节/主题：{it.get('subject') or '—'} / {it.get('chapter') or '—'} / {it.get('topic') or '—'}\n"
        f"- 题干：{it.get('question') or '—'}\n"
        f"- 选项：{(it.get('options') or '—')}\n"
        f"- 答案：{it.get('answer') or '—'}\n"
        f"- 解析：{str(it.get('analysis') or '—')[:HANDBOOK_ANALYSIS_CHARS]}\n"
        f"- 我的理解：{it.get('understanding') or '—'}\n"
        f"- 我的疑问：{it.get('doubts') or '—'}"
        for it in items
    ]
    evidence = ("## 薄弱项分组（按优先级降序）\n" + ("\n".join(glines) or "（无）")
                + "\n\n## 错题条目\n" + ("\n\n".join(blocks) or "（无）"))
    return st, evidence, len(items)


async def handbook(subject: str = "", *, max_items: int = HANDBOOK_MAX_ITEMS,
                   cancel: threading.Event | None = None) -> AsyncIterator[dict[str, Any]]:
    """按科目生成「薄弱点复习手册」（Markdown），**逐字流式**。

    与 `ask()` 同一条底线：证据不足时**明说数据不足**，不许用通识补全。
    空错题库直接给说明、**不调模型**（不烧 token、不产生幻觉空间）。

    事件流：`{"type":"stage"}` → `{"type":"delta","text":…}*` → `{"type":"result","markdown":…}`
    （或 `{"type":"error","message":…}`）。手册不是 JSON 协议，直接流正文即可。
    """
    st, evidence, n_items = _handbook_evidence(subject, max_items)
    total = int(st.get("total") or 0)
    if not total:
        md = (f"# {subject or '全部科目'} · 薄弱点复习手册\n\n"
              "错题库里还没有条目，没有可归纳的薄弱点。\n\n"
              "先在「错题本」录入错题（或从练习里判错自动回流），再生成手册。")
        yield {"type": "delta", "text": md}
        yield {"type": "result", "markdown": md, "subject": subject, "items": 0,
               "groups": 0, "grounded": False, "note": "空错题库：未调用模型。"}
        return

    client = get_client("gen", cancel=cancel)
    convo = [
        # ⚠️ 提示词名必须写成**字面量**：`tests/test_r8w_p2_hardening.py` 用 AST 认
        # 「`render_prompt("<字面量>")` 调用点存在」——写成常量会让它判「该提示词从不加载」。
        {"role": "system", "content": render_prompt("vault_handbook.md", subject=subject or "全部科目")},
        {"role": "user", "content": evidence + "\n\n请据此生成复习手册。"},
    ]
    yield {"type": "stage", "hint": f"正在归纳 {n_items} 条错题的薄弱点"}
    parts: list[str] = []
    try:
        async for piece in _stream_chat(client, convo, temperature=0.3,
                                        max_tokens=HANDBOOK_MAX_TOKENS):
            parts.append(piece)
            yield {"type": "delta", "text": piece}
    except LLMError as e:
        yield {"type": "error", "message": f"模型调用失败：{e}"}
        return
    finally:
        if cancel is not None:
            cancel.set()
    md = "".join(parts).strip()
    if not md:
        yield {"type": "error", "message": "模型返回了空手册（可重试一次）"}
        return
    groups = len([g for g in (st.get("groups") or []) if int(g.get("total") or 0) > 0])
    yield {"type": "result", "markdown": md, "subject": subject, "items": n_items,
           "groups": groups, "grounded": True, "note": ""}
