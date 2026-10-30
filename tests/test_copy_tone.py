"""用户可见文案的「AI 味」守卫（2026-10-28）。

## 为什么这个仓需要它

本仓有 1300+ 项代码守卫，文案却**零约束**——AI 味不会让任何测试变红，所以它只增不减。
实证：README 首屏曾写「授人以渔：…本地**一键**生成**全新的**题库」；`index.html` 里
「⚡ 一键刷薄弱组卷」「一眼清楚，点一下就开始」；每张卡片下面挂一段 2~4 行的 `.hint`
解释设计理由。这些都是**能指到行号的文本特征**，不是"感觉"。

## 判据（都可复核）

| # | 判据 | 阈值 | 为什么是这个阈值 |
|---|---|---|---|
| A | 禁用词（营销腔 / 内部术语外露） | 命中即失败 | 这些词在用户界面里没有精确含义，只传递"热闹" |
| B | 静态 `.hint` 单条长度 | ≤ 60 字 | 超了就不是提示，是段落——「用长段落代替信息层级」的机械判据 |
| C | 单句 `（）` 嵌套深度 | ≤ 1 层 | 两层以上是"把前提、例外、原因全塞进括号"的签名 |
| D | 装饰性 emoji | 白名单外命中即失败 | 状态符号（✓ ✗ ★ ⚠）与箭头不算装饰 |

## 三个刻意的口径（避免把守卫做成噪音源）

1. **只看用户能看见的字**：JS 注释里的「门禁」「闭环」「读模型」是工程词汇，不该被拦
   ⇒ 真做词法分析（跳过注释与正则字面量），不是全文 grep。
2. **`<details>` 里的长文豁免长度判据**：渐进披露本来就允许长文（「素材要求」就是长文），
   它和"卡片正文塞满解释"不是一回事。禁用词仍然管。
3. **拿不准的不进词表**：比如「自动」是正常技术词（自动 OCR / 自动判分），不进禁用词；
   「智能」只在营销语境里坏，但它在本仓零出现，也不进——**词表越短越有人愿意维护**。

## 元守卫

每个检测器都配「用内存样本自证能命中」（本仓既有做法）：没有这一步，正则写错会让
守卫恒绿，而且**看起来仍在守**。
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "medkit" / "web"
README = ROOT / "README.md"

# ---------------------------------------------------------------- 判据表
# 键 = 命中即失败的词；值 = 建议替换（失败信息里直接给出改法，不让人去猜）
BANNED: dict[str, str] = {
    "一键": "说清点哪个按钮、会发生什么（如「备份数据（zip）」）",
    "赋能": "删掉；或写具体能力（「自动判分」）",
    "打造": "改成「生成 / 做成」",
    "助力": "删掉；直接说对谁有用",
    "一站式": "删掉；列出实际有哪几步",
    "极致": "删掉（不可验证的形容词）",
    "完美": "删掉（不可验证的形容词）",
    "全新": "删掉；或写「新生成的」",
    "闭环": "改成「进度 / 总览」等具体名词",
    "沉淀": "改成「归集 / 累积」",
    "溯源": "改成「出处 / 来源」",
    "门禁": "改成「自动检查 / 规则校验」",
    "契约": "改成「字段约定」",
}

HINT_MAX = 60          # 静态 .hint 单条字数上限
PAREN_MAX_DEPTH = 1    # 单句（）嵌套上限

# 句首连接词：它们不是错别字，是「AI 起草」最稳的指纹。
# 只拦**句首**（句号/分号/问号/换行之后），不拦「最后一题」「再次开始」这类词内用法。
CONNECTIVES: dict[str, str] = {
    "首先": "直接说第一件事，不必报数",
    "其次": "直接说第二件事",
    "综上所述": "删掉，直接给结论",
    "总而言之": "删掉，直接给结论",
    "总的来说": "删掉，直接给结论",
    "值得一提的是": "删掉，直接说那件事",
    "需要注意的是": "删掉，直接说要注意什么",
    "与此同时": "删掉；两件事就分两句",
    "此外": "删掉；另起一句",
    "另外": "删掉；另起一句",
    "因此": "改成「所以」或直接说结果",
    "然而": "改成「但」或直接说转折",
}
_CONNECTIVE_RE = re.compile(
    r"(?:^|[。；！？\n])\s*(" + "|".join(map(re.escape, CONNECTIVES)) + r")")

# 白名单：状态符号与箭头（它们有信息，不是装饰）
_EMOJI_OK = set("★☆✓✗✔✘⚠→←↑↓↔⇧⇩≤≥≈≠①②③④⑤")
_EMOJI_RE = re.compile(
    "[\u2460-\u24FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\U0001F000-\U0001FAFF]")

# JavaScript 里可以用在 `/` 前面、说明后面是正则字面量的字符（标准启发式）
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


# ---------------------------------------------------------------- 取文本
def _mask(m: re.Match) -> str:
    """把整段替换成等长空白：保住偏移量与行号（不然后面的报错行号全错）。"""
    return re.sub(r"[^\n]", " ", m.group(0))


def html_visible_text(src: str) -> str:
    """HTML → 用户能看见的文本（去注释 / 脚本 / 样式 / 标签），偏移量保持不变。"""
    out = re.sub(r"<!--.*?-->", _mask, src, flags=re.S)
    out = re.sub(r"<script\b.*?</script>", _mask, out, flags=re.S | re.I)
    out = re.sub(r"<style\b.*?</style>", _mask, out, flags=re.S | re.I)
    out = re.sub(r"<[^>]+>", _mask, out)
    return _html.unescape(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def js_strings(src: str) -> list[tuple[int, str]]:
    """JS 源码 → [(行号, 字符串内容)]。跳过注释与正则字面量，模板串整体算一条。

    **为什么不用正则一把梭**：`// 门禁按你设定的配比校验` 这种注释里的工程词汇不该被
    当成用户文案拦下来；而 `"门禁规则"` 这种字符串该拦。两者在纯文本扫描里长得一样，
    只有走一遍词法才分得清。
    """
    out: list[tuple[int, str]] = []
    i, n, line = 0, len(src), 1
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and src.startswith("/*", i):
            i += 2
            while i < n and not src.startswith("*/", i):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        if ch in "\"'`":
            quote, start_line, i, buf = ch, line, i + 1, []
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    buf.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == "\n":
                    if quote != "`":      # 单双引号不跨行：未闭合就到行尾
                        break
                    line += 1
                buf.append(src[i])
                i += 1
            i += 1
            out.append((start_line, "".join(buf)))
            continue
        if ch == "/":
            j = i - 1
            while j >= 0 and src[j] in " \t":
                j -= 1
            if j < 0 or src[j] in _REGEX_PREV:
                i += 1
                in_cls = False
                while i < n:
                    c = src[i]
                    if c == "\\":
                        i += 2
                        continue
                    if c == "[":
                        in_cls = True
                    elif c == "]":
                        in_cls = False
                    elif c == "/" and not in_cls:
                        i += 1
                        break
                    elif c == "\n":
                        break
                    i += 1
                continue
        i += 1
    return out


def static_hints(src: str) -> list[tuple[int, str]]:
    """静态 `.hint` 文本（`<details>` 内的豁免：渐进披露本来就允许长文）。"""
    hidden = [(m.start(), m.end()) for m in re.finditer(r"<details\b.*?</details>", src, re.S)]

    def _in_details(pos: int) -> bool:
        return any(a <= pos < b for a, b in hidden)

    out: list[tuple[int, str]] = []
    for m in re.finditer(r'<(\w+)[^>]*class="[^"]*\bhint\b[^"]*"[^>]*>(.*?)</\1>', src, re.S):
        if _in_details(m.start()):
            continue
        text = re.sub(r"<[^>]+>", "", m.group(2))
        text = _html.unescape(text).strip()
        if text:   # 空的是 JS 填充的容器（`id=...`），不是文案
            out.append((_line_of(src, m.start()), text))
    return out


def readme_first_screen() -> str:
    """README 首屏（到「开发者」小节之前）：这是"第一次打开仓库"看到的全部字。"""
    text = README.read_text(encoding="utf-8")
    cut = text.find("\n## 开发者")
    return text if cut < 0 else text[:cut]


# ---------------------------------------------------------------- 检测器
def find_banned(text: str) -> list[tuple[int, str, str]]:
    """→ [(行号, 命中词, 建议)]"""
    hits = []
    for word, advice in BANNED.items():
        for m in re.finditer(re.escape(word), text):
            hits.append((_line_of(text, m.start()), word, advice))
    return sorted(hits)


def find_banned_in_js(src: str) -> list[tuple[int, str, str]]:
    hits = []
    for line, s in js_strings(src):
        for word, advice in BANNED.items():
            if word in s:
                hits.append((line, word, advice))
    return sorted(hits)


def find_connectives(text: str) -> list[tuple[int, str, str]]:
    """→ [(行号, 命中词, 建议)]；只认句首，避免误伤「最后一题」「再次开始」。"""
    out = []
    for m in _CONNECTIVE_RE.finditer(text):
        word = m.group(1)
        out.append((_line_of(text, m.start()), word, CONNECTIVES[word]))
    return out


def find_long_hints(src: str, limit: int = HINT_MAX) -> list[tuple[int, int, str]]:
    return [(ln, len(t), t) for ln, t in static_hints(src) if len(t) > limit]


def find_deep_parens(text: str, depth: int = PAREN_MAX_DEPTH) -> list[tuple[int, int]]:
    """单句（）嵌套超过 `depth` 层 → [(行号, 实际深度)]。"""
    bad: list[tuple[int, int]] = []
    for m in re.finditer(r"[^。！？\n]+", text):
        deepest = 0
        cur = 0
        for ch in m.group(0):
            if ch == "（":
                cur += 1
                deepest = max(deepest, cur)
            elif ch == "）":
                cur = max(0, cur - 1)
        if deepest > depth:
            bad.append((_line_of(text, m.start()), deepest))
    return bad


def find_emoji(text: str) -> list[tuple[int, str]]:
    return [(_line_of(text, m.start()), m.group(0))
            for m in _EMOJI_RE.finditer(text) if m.group(0) not in _EMOJI_OK]


def _fmt(hits: list[tuple], label: str, src_name: str) -> str:
    return "\n".join(f"  {src_name}:{h[0]} {label} {h[1:] if len(h) > 1 else ''}" for h in hits)


# ---------------------------------------------------------------- 守卫
def test_no_marketing_words_in_index_html():
    """A：`index.html` 的可见文本不许出现营销腔与内部术语。"""
    src = (WEB / "index.html").read_text(encoding="utf-8")
    hits = find_banned(html_visible_text(src))
    assert not hits, (
        "index.html 可见文本命中禁用词：\n"
        + "\n".join(f"  第 {ln} 行「{w}」→ {advice}" for ln, w, advice in hits)
        + "\n（判据见本文件 BANNED：这些词在界面上没有精确含义，只传递热闹。）"
    )


def test_no_marketing_words_in_frontend_strings():
    """A：前端 JS 的**字符串字面量**同样不许（注释里的工程词汇不在此列）。"""
    bad = []
    for js in sorted(WEB.glob("js/*.js")):
        for line, word, advice in find_banned_in_js(js.read_text(encoding="utf-8")):
            bad.append(f"  {js.name}:{line}「{word}」→ {advice}")
    assert not bad, "前端字符串命中禁用词：\n" + "\n".join(bad)


def test_no_marketing_words_on_readme_first_screen():
    """A：README 首屏（第一次打开仓库看到的全部字）。"""
    hits = find_banned(readme_first_screen())
    assert not hits, (
        "README 首屏命中禁用词：\n"
        + "\n".join(f"  第 {ln} 行「{w}」→ {advice}" for ln, w, advice in hits)
    )


def test_static_hints_are_short_enough():
    """B：静态 `.hint` ≤ 60 字。超了就不是提示，是段落——理由该下沉到 `title` / 帮助页。"""
    src = (WEB / "index.html").read_text(encoding="utf-8")
    long = find_long_hints(src)
    assert not long, (
        f"index.html 有 {len(long)} 条静态 .hint 超过 {HINT_MAX} 字：\n"
        + "\n".join(f"  第 {ln} 行（{n} 字）{t[:40]}…" for ln, n, t in long)
        + "\n改法：卡片正文只留「做什么 / 怎么点」，理由进 title 或 <details>。"
    )


def test_no_nested_parens_in_visible_copy():
    """C：单句 `（）` 最多一层——两层以上是"把前提、例外、原因全塞进括号"的签名。"""
    src = (WEB / "index.html").read_text(encoding="utf-8")
    bad = find_deep_parens(html_visible_text(src))
    assert not bad, (
        "index.html 有嵌套括号：\n"
        + "\n".join(f"  第 {ln} 行嵌套 {d} 层" for ln, d in bad)
    )


def test_no_decorative_emoji_in_ui():
    """D：装饰性 emoji 不许进正文（状态符号与箭头在白名单里）。"""
    src = (WEB / "index.html").read_text(encoding="utf-8")
    bad = find_emoji(html_visible_text(src))
    assert not bad, (
        "index.html 出现装饰性 emoji：\n"
        + "\n".join(f"  第 {ln} 行 {ch!r}" for ln, ch in bad)
    )


def test_mistake_agent_prompt_keeps_banned_list():
    """错题助手的语气段必须同时有「必须」与「禁止」两张清单。

    为什么这条在文案守卫里：只写正面描述的提示词 -> LLM 回弹到默认人格（客服开场、
    emoji、「综上所述」）。禁用清单是**契约的一部分**，删了它不会有别的测试变红。
    """
    text = (ROOT / "medkit" / "prompts" / "mistake_agent.md").read_text(encoding="utf-8")
    assert "**必须**" in text and "**禁止**" in text, "语气段缺「必须 / 禁止」两张清单"
    for item in ("客服开场", "emoji", "综上所述", "再总结一遍"):
        assert item in text, f"禁用清单缺「{item}」——回弹就是从这里开始的"


def test_no_ai_connectives_in_user_copy():
    """E：用户可见文案不许用「首先 / 其次 / 综上 / 因此 / 然而」这类句首连接词。

    为什么单列一条：禁用词表管的是「营销腔」，连接词管的是「文章腔」。
    界面不是文章——用户读的是状态与动作，不是论证结构。
    只扫 `index.html` 可见文本与 JS 字符串；注释里的「因此」不拦。
    """
    bad: list[str] = []
    src = (WEB / "index.html").read_text(encoding="utf-8")
    vis = html_visible_text(src)
    for ln, w, advice in find_connectives(vis):
        bad.append(f"  index.html:{ln}「{w}」→ {advice}")
    for js in sorted(WEB.glob("js/*.js")):
        for ln, s in js_strings(js.read_text(encoding="utf-8")):
            for off, w, advice in find_connectives(s):
                bad.append(f"  {js.name}:{ln + off - 1}「{w}」→ {advice}")
    assert not bad, (
        "用户可见文案出现句首连接词：\n" + "\n".join(bad)
        + "\n判据见 CONNECTIVES：只拦句首，不拦「最后一题 / 再次开始」这类词内用法。"
    )


# ---------------------------------------------------------------- 元守卫
def test_detectors_hit_injected_samples():
    """元守卫：每个检测器都要在**内存样本**上自证能命中。

    没有这一步，正则写错会让守卫恒绿——而且看起来仍在守（本仓既有做法）。
    """
    bad_html = '<div class="hint">一键生成全新的题库（含押题卷（可选））⚡</div>'
    visible = html_visible_text(bad_html)
    assert [w for _, w, _ in find_banned(visible)] == ["一键", "全新"]
    assert find_long_hints(bad_html, limit=10), "超过阈值的 .hint 必须命中"
    assert find_deep_parens(visible), "两层括号必须命中"
    assert find_emoji(visible) == [(1, "⚡")]
    assert [w for _, w, _ in find_connectives("先说结论。因此，这里要删。")] == ["因此"]
    assert find_connectives("最后一题不自动跳；再次开始会续跑。") == []

    # JS：字符串里的命中、注释里的不命中、正则字面量不许把后面的代码吞掉
    js = '// 一键 是注释里的词，不该拦\nconst a = "一键备份";\nconst re = /\'/g;\nconst b = "好";'
    hits = find_banned_in_js(js)
    assert [h[:2] for h in hits] == [(2, "一键")], hits
    assert (4, "好") in [s for s in js_strings(js)], js_strings(js)

    # 白名单里的符号不该被当装饰
    assert find_emoji("按 ★ 收藏，✓ 已掌握 → 下一步") == []


def test_visible_text_keeps_line_numbers():
    """元守卫：脱标签必须**保住行号**——否则报错行号指到别处，比不报还费时间。"""
    src = '<p>第一行</p>\n\n<p>第三行（含\n换行）</p>'
    text = html_visible_text(src)
    assert text.count("\n") == src.count("\n")
    assert _line_of(text, text.index("第三行")) == 3


def test_scan_faces_are_not_empty():
    """元守卫：扫描面不许塌缩成空集（否则上面所有断言都恒真）。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert len(html_visible_text(html).strip()) > 4000, "index.html 可见文本过少——脱标签写窄了？"
    assert len(static_hints(html)) >= 8, f"只扫到 {len(static_hints(html))} 条静态 .hint"
    js_files = sorted(WEB.glob("js/*.js"))
    assert len(js_files) >= 10, f"只扫到 {len(js_files)} 个 JS 分片"
    n_strings = sum(len(js_strings(p.read_text(encoding="utf-8"))) for p in js_files)
    assert n_strings > 500, f"JS 字符串只扫到 {n_strings} 条——词法分析写窄了？"
    assert len(readme_first_screen()) > 800, "README 首屏截取过短——锚点被改了？"
