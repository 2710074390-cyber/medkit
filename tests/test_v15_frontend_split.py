"""V-15（U-17 剩余项）：前端按域拆分的**验收闸门**。

背景：`learn.js` 2401 行 / `review-desk.js` 2301 行，远超项目自定判据「最大 JS 文件 ≤800 行」
（R6-09 / U-17）。2026-09-16 按域拆为各 4 片，**纯搬迁、零逻辑改动**，并保持
「经典脚本 + 零构建 + 零 CDN」的既有形态（未做 ES Module 化——那会改 `index.html` 的加载语义，
风险与收益不成比例，已明确留档）。

本文件把三件事变成机械闸门：
1. **规模判据**：任一 `medkit/web/js/*.js` ≤ 800 行（U-17 的验收标准）；
2. **加载清单完整且有序**：`index.html` 必须**恰好一次**加载每个 JS，且按分片族顺序
   （经典脚本共享全局作用域 → 顺序即契约，漏加载/乱序都是静默故障）；
3. **加载期前向引用守卫**（本仓库自述的陷阱「跨文件函数加载期前向引用会挂」）：
   任一分片在**加载期立即调用**的本地函数，其定义必须落在**同片或更早片**。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "medkit/web/js"
INDEX = ROOT / "medkit/web/index.html"

MAX_LINES = 800

# 分片族（按**加载顺序**）——与 index.html 的 <script> 顺序、与拆分时的行区间顺序一致
FAMILIES: dict[str, list[str]] = {
    "learn": ["learn.js", "learn-study.js", "learn-live.js", "learn-review.js"],
    "review-desk": ["review-desk.js", "review-desk-materials.js",
                    "review-desk-project.js", "review-desk-review.js"],
}

DECL_RE = re.compile(r"(?:^|[;}])\s*(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof", "function",
            "Object", "Array", "JSON", "document", "window", "String", "Number",
            "Boolean", "parseInt", "parseFloat", "setTimeout", "console", "await"}


def _script_srcs() -> list[str]:
    html = INDEX.read_text(encoding="utf-8")
    return re.findall(r"<script[^>]*\ssrc\s*=\s*[\"']([^\"']+)[\"']", html)


def _declared_in(text: str) -> set[str]:
    return {m.group(1) for m in DECL_RE.finditer(text)}


def _immediate_calls(text: str) -> list[tuple[int, set[str]]]:
    """返回 [(行号, 该处加载期立即调用的标识符集合)]。

    只取两类「加载期立即执行」的位置：① 顶层 IIFE 的整个块；② 顶层非声明语句（含其整行）。
    箭头回调（addEventListener/onclick/forEach 的 handler）里的事件绑定是**延迟执行**，
    但 `forEach(...)` 本身立即执行——故这里保守地把顶层语句整行计入，宁可多报。
    """
    lines = text.split("\n")
    out: list[tuple[int, set[str]]] = []
    depth = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if depth == 0 and re.match(r"^\(function\b", stripped):
            j, d = i, 0
            block: list[str] = []
            while j < len(lines):
                block.append(lines[j])
                d += lines[j].count("{") - lines[j].count("}")
                if d <= 0 and j > i:
                    break
                j += 1
            body = "\n".join(block)
            out.append((i + 1, {m.group(1) for m in CALL_RE.finditer(body)} - KEYWORDS))
            for k in range(i, j + 1):
                depth += lines[k].count("{") - lines[k].count("}")
            i = j + 1
            continue
        if depth == 0 and stripped and not stripped.startswith(("/*", "*", "//", "}")):
            if not re.match(r"^(async\s+)?function\b", stripped) and not re.match(
                    r"^(const|let|var)\s", stripped):
                out.append((i + 1, {m.group(1) for m in CALL_RE.finditer(line)} - KEYWORDS))
        depth += line.count("{") - line.count("}")
        i += 1
    return out


def test_no_js_file_over_800_lines():
    """U-17 验收标准：任一前端 JS ≤ 800 行。"""
    files = sorted(JS_DIR.glob("*.js"))
    assert files, "未找到前端 JS 文件"
    over = {p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in files if len(p.read_text(encoding="utf-8").splitlines()) > MAX_LINES}
    assert not over, f"存在超过 {MAX_LINES} 行的 JS（U-17 判据未达）：{over}"


def test_index_loads_every_js_exactly_once_in_family_order():
    """index.html 必须恰好一次加载每个 JS，且分片族内部保持拆分时的顺序。"""
    srcs = [s.rsplit("/", 1)[-1] for s in _script_srcs()]
    on_disk = {p.name for p in JS_DIR.glob("*.js")}
    assert set(srcs) == on_disk, (
        f"加载清单与磁盘文件不一致：未加载 {sorted(on_disk - set(srcs))}，"
        f"多出 {sorted(set(srcs) - on_disk)}")
    assert len(srcs) == len(set(srcs)), f"有 JS 被重复加载：{srcs}"
    for fam, parts in FAMILIES.items():
        idx = [srcs.index(p) for p in parts]
        assert idx == sorted(idx), f"{fam} 族分片加载顺序被打乱：{[srcs[i] for i in idx]}"
    # 依赖方（app.js / md.js）必须先于依赖它们的业务脚本
    assert srcs.index("app.js") < srcs.index("learn.js")
    assert srcs.index("md.js") < srcs.index("review-desk.js")


def test_no_load_time_forward_reference_across_chunks():
    """加载期立即调用的本地函数，其定义必须在**同片或更早片**（经典脚本的硬约束）。"""
    for fam, parts in FAMILIES.items():
        where: dict[str, int] = {}
        texts: list[str] = []
        for i, name in enumerate(parts):
            text = (JS_DIR / name).read_text(encoding="utf-8")
            texts.append(text)
            for n in _declared_in(text):
                where.setdefault(n, i)
        problems: list[str] = []
        for i, (name, text) in enumerate(zip(parts, texts, strict=True)):
            for lineno, called in _immediate_calls(text):
                for c in sorted(called):
                    j = where.get(c)
                    if j is not None and j > i:
                        problems.append(f"{fam}: {name}:{lineno} 加载期调用 {c}()，"
                                        f"但它定义在更晚的 {parts[j]}（第 {j + 1} 片）")
        assert not problems, "存在跨片加载期前向引用（会抛 ReferenceError）：\n  " + "\n  ".join(problems)


def test_split_chunks_are_contiguous_in_original_order():
    """结构守卫：各片首行必须是「顶层声明 / 注释」边界，不得从函数体中间切开。

    判据：每片第一行（去掉 `/* exported */` 头后）不得是裸语句或孤立闭括号——
    从函数中间切开会在语法上仍合法（`node --check` 也过），但会让上一片的函数悬空。
    """
    for parts in FAMILIES.values():
        for name in parts:
            text = (JS_DIR / name).read_text(encoding="utf-8")
            body = re.sub(r"^/\*\s*exported\s+[^*]*\*/\n", "", text, count=1)
            first = body.split("\n", 1)[0].strip()
            assert not first.startswith("}"), f"{name} 从函数体中间切开（首行以 }} 开头）：{first[:60]!r}"
            assert not first.startswith(")"), f"{name} 首行以 ) 开头，疑似切在表达式中间"
