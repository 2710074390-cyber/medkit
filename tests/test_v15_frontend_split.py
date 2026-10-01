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
# EP-01：learn 族增 learn-meta.js（元认知视图），接在 learn-review.js 之后
# EP-01 图像录入：learn 族再增 learn-meta-image.js（拍照录入），接在 learn-meta.js 之后
# EP-01 阶段 4：learn 族再增 learn-search.js（错题检索）——learn-study.js 加检索后达 871 行
#   超 800 判据，故把检索块**纯搬迁**成独立片
FAMILIES: dict[str, list[str]] = {
    "learn": ["learn.js", "learn-study.js", "learn-live.js", "learn-review.js",
              "learn-meta.js", "learn-meta-image.js", "learn-search.js"],
    "review-desk": ["review-desk.js", "review-desk-materials.js",
                    "review-desk-project.js", "review-desk-review.js"],
}

DECL_RE = re.compile(r"(?:^|[;}])\s*(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
# R9（2026-09-27）：**裸标识符引用**也要算 —— 函数名当值传递同样在加载期求值。
# 旧实现用 `CALL_RE = ...\s*\(` 只认「带括号的调用」，于是这些真实形态全部漏检：
#   `$("wz_skip").onclick = wzDone;`            （review-desk-review.js:708）
#   `loadConfig().then(maybeShowWizard);`       （review-desk-review.js:744）
#   `const x = mtSetMeta;`                      （声明右侧同样是加载期求值）
# 反向验证：4 种形态注入后旧扫描器全绿，新扫描器 4/4 红、对照组不假红。
IDENT_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)")
# 字面量 / 内建 / 语法关键字：出现在标识符位置但永远不是「本地函数引用」
NON_IDENT = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "function", "await",
    "new", "do", "else", "in", "of", "this", "true", "false", "null", "undefined",
    "Object", "Array", "JSON", "document", "window", "String", "Number", "Boolean",
    "parseInt", "parseFloat", "setTimeout", "clearTimeout", "console", "Math", "Date",
    "Map", "Set", "Promise", "Error", "localStorage", "location", "history", "fetch",
    "RegExp", "Symbol", "encodeURIComponent", "decodeURIComponent", "isNaN", "alert",
    "requestAnimationFrame", "URL", "Blob", "FileReader", "AbortController",
    "async", "let", "const", "var", "class", "export", "default",
}


def _referenced_idents(line: str) -> set[str]:
    """从加载期立即执行的一行里，提取**被引用的本地标识符**（调用 + 裸引用都算）。

    只做「宁多勿少」的保守提取：多报的后果是该函数被判为「更早片需要它」，
    由定义位置的比较自然淘汰；漏报才会让真前向引用逃逸。
    """
    out: set[str] = set()
    for m in IDENT_RE.finditer(line):
        name = m.group(1)
        if name in NON_IDENT:
            continue
        # 排除对象字面量的 key（`{foo: 1}`）与属性访问后缀（`.foo`）——前者由 `:` 判定，
        # 后者由 IDENT_RE 的负向后顾 `(?<![\w$.])` 已挡住。
        after = line[m.end():]
        if after.lstrip().startswith(":") and not after.lstrip().startswith("::"):
            # `foo: bar` 形态 → foo 是 key，不是引用；但 `foo ? a : b` 的 foo 是引用。
            # 保守判据：key 只出现在 `{`/`,` 之后，这里退化为「排除紧跟冒号且前面是 { 或 ,」。
            before = line[:m.start()].rstrip()
            if before.endswith("{") or before.endswith(","):
                continue
        out.add(name)
    return out



def _declared_names_in_line(line: str) -> str:
    """该行 `const|let|var` **声明出来的名字**（单个；用于从引用集里剔除自身）。"""
    m = re.match(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)", line)
    return m.group(1) if m else ""


def _script_srcs() -> list[str]:
    html = INDEX.read_text(encoding="utf-8")
    return re.findall(r"<script[^>]*\ssrc\s*=\s*[\"']([^\"']+)[\"']", html)


def _declared_in(text: str) -> set[str]:
    """**顶层**声明的名字（经典脚本下才进入共享全局作用域）。

    R9（2026-09-27）：旧实现是 `DECL_RE.finditer(text)` 全文件无差别扫描 + `setdefault`。
    这会把**函数体内的局部** `let v = null`（learn.js:80）、`const s = sessionStorage...`
    （learn.js:135）也当成顶层声明；更糟的是 `setdefault` 让「第一次出现」获胜——
    若局部变量先出现，还会把真正的顶层定义位置记错。
    实测后果：补上「裸标识符引用」扫描后，`v` / `s` 这类单字母局部变量被误报为
    「跨片前向引用」（3 处假红）。修法 = 只认花括号深度 0 处的声明。
    """
    names: set[str] = set()
    depth = 0
    for line in text.split("\n"):
        if depth == 0:
            for m in DECL_RE.finditer(line):
                names.add(m.group(1))
        depth += line.count("{") - line.count("}")
    return names


def _in_block_comment(lines: list[str]) -> list[bool]:
    """逐行标记该行是否属于注释（`/* ... */` 块注释内部，或 `//` 单行注释）。

    R9（2026-09-27）：旧实现只判 `stripped.startswith(("/*", "*", "//", "}"))`，
    **只识别「以 `*` 开头」的续行**。而本仓库的注释续行常以普通文字开头，例如
    `learn-live.js:250` 的 `   D-21：busy 禁用防连点（与 gradeBusy 同风格）——…`，
    于是注释里提到的 `gradeBusy` 被当成加载期引用，误报为「跨片前向引用」（假红）。
    修法 = 显式做块注释状态机，而不是靠行首字符猜。
    """
    flags: list[bool] = []
    in_block = False
    for line in lines:
        if in_block:
            flags.append(True)
            if "*/" in line:
                in_block = False
            continue
        s = line.lstrip()
        if s.startswith("//"):
            flags.append(True)
        elif s.startswith("/*"):
            flags.append(True)
            if "*/" not in s[2:]:
                in_block = True
        else:
            flags.append(False)
    return flags


def _immediate_calls(text: str) -> list[tuple[int, set[str]]]:
    """返回 [(行号, 该处加载期立即引用的标识符集合)]。

    只取两类「加载期立即执行」的位置：① 顶层 IIFE 的整个块；② 顶层非声明语句（含其整行）。
    箭头回调（addEventListener/onclick/forEach 的 handler）里的事件绑定是**延迟执行**，
    但 `forEach(...)` 本身立即执行——故这里保守地把顶层语句整行计入，宁可多报。

    R9：注释行/块注释内一律跳过（见 `_in_block_comment` 的踩坑记录）。
    """
    lines = text.split("\n")
    commented = _in_block_comment(lines)
    out: list[tuple[int, set[str]]] = []
    depth = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if depth == 0 and not commented[i] and re.match(r"^\(function\b", stripped):
            j, d = i, 0
            block: list[str] = []
            while j < len(lines):
                if not commented[j]:
                    block.append(lines[j])
                d += lines[j].count("{") - lines[j].count("}")
                if d <= 0 and j > i:
                    break
                j += 1
            body = "\n".join(block)
            out.append((i + 1, _referenced_idents(body)))
            for k in range(i, j + 1):
                depth += lines[k].count("{") - lines[k].count("}")
            i = j + 1
            continue
        if depth == 0 and not commented[i] and stripped:
            if re.match(r"^function\b", stripped):
                pass    # 函数声明：只是定义，不在加载期求值
            elif re.match(r"^(const|let|var)\s", stripped):
                # R9：声明**也要扫**——`const x = mtSetMeta;` 在加载期求值右侧表达式。
                # 旧实现把整行当「定义」跳过，于是这类前向引用完全漏检（反向验证实测：
                # 在 learn.js 尾部加 `const _r9probe = mtSetMeta;`（mtSetMeta 定义在第 5 片）
                # ⇒ 4 passed 全绿）。只把「声明出来的名字本身」排除掉即可。
                refs = _referenced_idents(line)
                refs -= {_declared_names_in_line(line)}
                out.append((i + 1, refs))
            else:
                out.append((i + 1, _referenced_idents(line)))
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
                        problems.append(f"{fam}: {name}:{lineno} 加载期引用 {c}，"
                                        f"但它定义在更晚的 {parts[j]}（第 {j + 1} 片）")
        assert not problems, "存在跨片加载期前向引用（会抛 ReferenceError）：\n  " + "\n  ".join(problems)


def test_forward_reference_scan_is_not_vacuous():
    """元守卫：扫描面必须真的覆盖到东西，否则上一条用例「空跑即绿」。

    ## 为什么要这条（2026-09-27）
    同目录的 `test_v15_frontend_handler_exposure.py` 有 `assert calls` 防「正则失效 ⇒ 空集 ⇒ 恒绿」，
    本文件这条却**没有**。而本文件依赖的 `_immediate_calls()` 恰好是最脆的一环：
    ② 分支靠「行首缩进/关键字」猜「这是不是加载期语句」，正则一改就可能全空。

    同时钉死：扫描面上必须同时存在**带括号调用**与**裸标识符引用**两种形态
    （后者是 R9 补的漏检点，见 `_referenced_idents` 的 docstring）。
    """
    total_calls = 0
    total_bare = 0
    for parts in FAMILIES.values():
        for name in parts:
            text = (JS_DIR / name).read_text(encoding="utf-8")
            for _lineno, refs in _immediate_calls(text):
                total_calls += len(refs)
                # 裸引用 = 该行里出现但后面不跟 `(` 的标识符
                pass
    assert total_calls >= 50, (
        f"加载期扫描面只剩 {total_calls} 个标识符（历史量级 ~74+）——"
        "正则/解析可能已失效，上一条用例会因此恒绿。请人工确认 _immediate_calls()。")

    # 直接自检：_referenced_idents 必须同时能抓到「调用」与「裸引用」
    sample = '$("wz_skip").onclick = wzDone; loadConfig().then(maybeShowWizard);'
    got = _referenced_idents(sample)
    assert "loadConfig" in got, "带括号调用形态漏检"
    assert "maybeShowWizard" in got, "回调（裸标识符）形态漏检 —— R9 的修复点"
    assert "wzDone" in got, "赋值右侧裸标识符漏检"
    total_bare = len([1 for _l, r in _immediate_calls(
        (JS_DIR / "review-desk-review.js").read_text(encoding="utf-8")) if "wzDone" in r])
    assert total_bare >= 1, "真实文件里的裸标识符引用未被扫到（回归）"



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
