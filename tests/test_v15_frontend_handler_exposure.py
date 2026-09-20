"""V-15 配套闸门：内联事件处理器的「显式可解析」契约（经典脚本 + 零构建形态）。

背景：MedKit 前端维持经典 `<script src>`（2026-09-16 留档决策，见
``test_v15_frontend_split.py`` 文件头），不做 ES Module 化。经典脚本下跨文件入口有两种
合法暴露方式：
1. 顶层 ``function foo(){}`` / ``const foo = () => {}`` 等声明进入共享全局作用域
   （函数声明同时挂 window；const/let 进入全局词法环境，内联处理器仍可解析）；
2. 显式 ``window.foo = ...`` 挂载（md.js / 新手引导等采用的显式风格）。

``index.html`` 与各 JS 模板字符串里的内联处理器（``onclick="foo()"`` 等）在运行时按
**全局名字**解析；拼错或漏加载只会在点击时静默炸成 ReferenceError，静态检查原本抓不到。
本闸门把 ESLint flat config（``eslint.config.js`` 的 ownDeclarations 口径）同款扫描搬到
Python 侧：所有内联处理器引用的标识符，必须能在已加载的某片 JS 中找到顶层声明或
``window.X`` 挂载。

- 只校验「被内联处理器直接调用」的标识符（形如 ``foo(``），不碰 addEventListener 绑定；
- 加载顺序/前向引用由 ``test_v15_frontend_split.py`` 守，本测试不重复。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "medkit/web/js"
INDEX = ROOT / "medkit/web/index.html"

# 与 eslint.config.js 的 TOP_LEVEL_RE / WINDOW_ASSIGN_RE 保持同口径，
# 另兼容 `}async function` 同行与 window["x"] 括号挂载。
TOP_LEVEL_RE = re.compile(r"(?:^|[;}])\s*(?:async\s+)?(?:function|const|let|var|class)\s+"
                          r"([A-Za-z_$][\w$]*)", re.M)
WINDOW_DOT_RE = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=")
WINDOW_BRACKET_RE = re.compile(r"\bwindow\[\s*[\"']([A-Za-z_$][\w$]*)[\"']\s*\]\s*=")

# 内联事件属性中被直接调用的标识符（负向后顾排除 obj.method( 的 method）。
HANDLER_RE = re.compile(
    r"\bon(?:click|change|input|submit|toggle|keydown|keyup|keypress|"
    r"mousedown|mouseup|mouseover|mouseout|dblclick|focus|blur)\s*=\s*"
    r"[\"'][^\"']*?(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof", "function",
            "await", "new", "do", "else"}


def _declared_names() -> set[str]:
    names: set[str] = set()
    for f in JS_DIR.glob("*.js"):
        src = f.read_text(encoding="utf-8")
        names.update(m.group(1) for m in TOP_LEVEL_RE.finditer(src))
        names.update(m.group(1) for m in WINDOW_DOT_RE.finditer(src))
        names.update(m.group(1) for m in WINDOW_BRACKET_RE.finditer(src))
    return names


def _inline_handler_calls() -> dict[str, str]:
    """标识符 → 首次出现位置（文件名:行号），便于失败时定位。"""
    found: dict[str, str] = {}
    sources = [("index.html", INDEX.read_text(encoding="utf-8"))]
    for f in sorted(JS_DIR.glob("*.js")):
        sources.append((f.name, f.read_text(encoding="utf-8")))
    for fname, src in sources:
        for m in HANDLER_RE.finditer(src):
            name = m.group(1)
            if name in KEYWORDS:
                continue
            found.setdefault(name, f"{fname}:{src[:m.start()].count(chr(10)) + 1}")
    return found


def test_js_files_present():
    files = sorted(JS_DIR.glob("*.js"))
    assert len(files) >= 10, f"前端分片数量异常：{[f.name for f in files]}"


def test_every_inline_handler_is_exposed():
    """每个内联 on*="fn(...)" 的 fn 都必须有顶层声明或 window 挂载。"""
    declared = _declared_names()
    calls = _inline_handler_calls()
    assert calls, "未扫描到任何内联处理器——正则可能失效，需人工确认"
    unresolved = {name: where for name, where in calls.items() if name not in declared}
    assert not unresolved, (
        "以下内联处理器标识符在所有 JS 分片中均无顶层声明/window 挂载"
        "（点击时会 ReferenceError）：" + ", ".join(f"{n}@{w}" for n, w in sorted(unresolved.items())))
