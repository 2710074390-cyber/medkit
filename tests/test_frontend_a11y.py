"""前端可访问性静态守卫（2026-10-21 新增）。

## 为什么需要这道守卫

本仓对"可测的约束"执行力极强——域耦合、单文件 800 行、CSS 令牌、无孤儿类，四条守卫都在跑。
但**没有一条覆盖 HTML 语义**。结果是 2026-10-21 的前端审查里，可访问性成了唯一整类失守的维度：
55 个 `<label>` 只有 8 个与控件关联、三个 `aria-modal="true"` 的浮层没有焦点陷阱、
刷题选项是 `<div onclick>`（键盘完全进不来）——而这些问题**全都能靠静态扫描抓出来**。

守卫不追求"覆盖 WCAG"，只钉住四条**已经修好、且最容易被下一次改动破坏**的判据：

1. 每个 `<label>` 必须 `for=` 指向某控件，或**包裹**控件；
2. 每个 `role="dialog"` 必须有可访问名称（`aria-label` / `aria-labelledby`）；
3. `:focus-visible` 的焦点环规则必须覆盖 `textarea`（此前漏了，聊天输入框没有焦点指示）；
4. 不允许新增 `<div onclick>` / `<span onclick>` 而不带 `tabindex` + `role`。

## 判据口径

- 扫描面：`index.html` + 全部 `medkit/web/js/*.js`（模板字符串里的 HTML 同样会被渲染进 DOM）；
- 豁免用 `EXEMPT` 登记，**登记不得腐烂**（问题修好后必须删登记，否则红）；
- 每条检测器都配**元守卫**：用内存构造的样本自证"能命中"，防止判据退化成恒真。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "medkit" / "web"
INDEX = WEB / "index.html"
CSS = WEB / "css" / "base.css"
JS_DIR = WEB / "js"

# 豁免表：`位置标识 -> 理由`。空表是正常状态；有登记就必须能被解释。
EXEMPT_LABEL: dict[str, str] = {}
EXEMPT_DIALOG: dict[str, str] = {}
EXEMPT_CLICKABLE: dict[str, str] = {}

# 可点击的"非交互"标签：它们没有原生键盘语义，必须补 tabindex + role
_NON_INTERACTIVE = ("div", "span", "li", "i", "td", "tr", "section", "b", "em", "p")
_LABEL_RE = re.compile(r"<label([^>]*)>(.*?)</label>", re.S)
_DIALOG_RE = re.compile(r"<[^>]*\brole=\"dialog\"[^>]*>", re.S)
_CONTROL_RE = re.compile(r"<(input|select|textarea)\b", re.I)
_CLICKABLE_RE = re.compile(
    r"<(" + "|".join(_NON_INTERACTIVE) + r")\b([^>]*\bonclick\s*=[^>]*)>", re.S)


def _sources() -> list[tuple[str, str]]:
    out = [("index.html", INDEX.read_text(encoding="utf-8"))]
    for f in sorted(JS_DIR.glob("*.js")):
        out.append((f.name, f.read_text(encoding="utf-8")))
    return out


def _line_of(text: str, idx: int) -> int:
    return text[:idx].count("\n") + 1


# ---------------------------------------------------------------- 检测器（纯函数，便于自证）
def unassociated_labels(html: str) -> list[str]:
    """返回未与控件关联的 label 位置（`行号:文本片段`）。"""
    out: list[str] = []
    for m in _LABEL_RE.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        if "for=" in attrs or _CONTROL_RE.search(inner):
            continue
        text = re.sub(r"<[^>]+>", "", inner).strip()[:24]
        out.append(f"{_line_of(html, m.start())}:{text}")
    return out


def unnamed_dialogs(html: str) -> list[str]:
    """返回没有可访问名称的 role=dialog 位置。"""
    out: list[str] = []
    for m in _DIALOG_RE.finditer(html):
        tag = m.group(0)
        if "aria-label=" in tag or "aria-labelledby=" in tag:
            continue
        out.append(f"line {_line_of(html, m.start())}: {tag[:56]}")
    return out


def clickable_without_semantics(html: str) -> list[str]:
    """返回带 onclick 却没有 tabindex / role 的非交互元素。"""
    out: list[str] = []
    for m in _CLICKABLE_RE.finditer(html):
        attrs = m.group(2)
        if "tabindex" in attrs and "role=" in attrs:
            continue
        out.append(f"line {_line_of(html, m.start())}: <{m.group(1)}{attrs[:44]}…>")
    return out


def focus_visible_covers_textarea(css: str) -> bool:
    """含 :focus-visible 的规则里是否出现了 textarea。"""
    for m in re.finditer(r"([^{}]*:focus-visible[^{}]*)\{([^}]*)\}", css):
        if "outline" in m.group(2) and "textarea" in m.group(1):
            return True
    return False


# ---------------------------------------------------------------- 元守卫：检测器自证能命中
def test_detectors_can_actually_hit():
    """用内存样本自证四条检测器都**真的会报**——否则守卫等于装饰。"""
    assert unassociated_labels('<label>未关联</label>') == ["1:未关联"]
    assert unassociated_labels('<label for="x">已关联</label>') == []
    assert unassociated_labels('<label>包裹<input id="y"></label>') == []

    assert unnamed_dialogs('<div role="dialog" aria-modal="true">') != []
    assert unnamed_dialogs('<div role="dialog" aria-labelledby="t">') == []

    assert clickable_without_semantics('<div onclick="go()">x</div>') != []
    assert clickable_without_semantics('<span onclick="go()" tabindex="0" role="button">x</span>') == []

    assert focus_visible_covers_textarea("a:focus-visible{outline:2px solid red}") is False
    assert focus_visible_covers_textarea("a:focus-visible,textarea:focus-visible{outline:2px}") is True


def test_scan_face_is_not_empty():
    """扫描面不许塌缩：HTML 与 JS 数量都要有下限。"""
    srcs = _sources()
    assert INDEX.exists(), "index.html 不存在"
    assert len(srcs) >= 10, f"只扫到 {len(srcs)} 个源文件，扫描面疑似写窄"
    assert any(s for n, s in srcs if n.endswith(".js")), "没有读到任何 JS 分片"


# ---------------------------------------------------------------- 主守卫
def test_every_label_is_associated():
    """每个 <label> 必须 for= 或包裹控件；否则读屏只念「编辑框」。"""
    bad = []
    for name, text in _sources():
        for item in unassociated_labels(text):
            key = f"{name}:{item}"
            if key not in EXEMPT_LABEL:
                bad.append(key)
    assert not bad, (
        "以下 <label> 未与任何控件关联（读屏用户听不到字段名）：\n  "
        + "\n  ".join(bad)
        + "\n修法：加 for=\"控件id\"，或把控件包进 label；分组标签用 .flabel。")


def test_every_dialog_has_accessible_name():
    """role=dialog 必须有 aria-label / aria-labelledby；否则读屏只念「对话框」。"""
    bad = []
    for name, text in _sources():
        for item in unnamed_dialogs(text):
            key = f"{name}:{item}"
            if key not in EXEMPT_DIALOG:
                bad.append(key)
    assert not bad, (
        "以下 role=\"dialog\" 没有可访问名称：\n  " + "\n  ".join(bad)
        + "\n修法：指向标题 id（aria-labelledby）或补 aria-label。")


def test_clickable_non_interactive_elements_have_semantics():
    """带 onclick 的 div/span 等必须有 tabindex + role —— 否则键盘按不到。"""
    bad = []
    for name, text in _sources():
        for item in clickable_without_semantics(text):
            key = f"{name}:{item}"
            if key not in EXEMPT_CLICKABLE:
                bad.append(key)
    assert not bad, (
        "以下元素可点击但不可键盘操作（WCAG 2.1.1）：\n  " + "\n  ".join(bad)
        + "\n修法：优先改成 <button>；确需保留原标签时补 tabindex=\"0\" 与 role，并处理 Enter/Space。")


def test_focus_ring_covers_textarea():
    """焦点环规则必须覆盖 textarea（聊天输入 / 粘贴导入是最高频输入）。"""
    css = CSS.read_text(encoding="utf-8")
    assert focus_visible_covers_textarea(css), (
        "base.css 的 :focus-visible 焦点环规则未覆盖 textarea——"
        "聊天输入框会退化成只有 1px 边框变色，键盘用户看不出焦点在哪。")


def test_exempt_entries_do_not_rot():
    """登记不得腐烂：豁免的问题若已修好，必须把登记删掉。"""
    stale: list[str] = []
    texts = dict(_sources())
    for key in list(EXEMPT_LABEL):
        name, _, item = key.partition(":")
        if name in texts and item not in unassociated_labels(texts[name]):
            stale.append("EXEMPT_LABEL " + key)
    for key in list(EXEMPT_DIALOG):
        name, _, item = key.partition(":")
        if name in texts and item not in unnamed_dialogs(texts[name]):
            stale.append("EXEMPT_DIALOG " + key)
    for key in list(EXEMPT_CLICKABLE):
        name, _, item = key.partition(":")
        if name in texts and item not in clickable_without_semantics(texts[name]):
            stale.append("EXEMPT_CLICKABLE " + key)
    assert not stale, "以下登记已过期（问题已修复），请从豁免表删除：\n  " + "\n  ".join(stale)
