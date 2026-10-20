"""设计令牌守卫（v0.19 前端设计重构 · S1+S2）。

## 为什么需要这道守卫

重构前的前端是「字面色值 + 8 种圆角 + 16 档字号 + 裸 z-index」的自由生长结果：
`base.css` 与 `learn.css` 里散布着 15 处硬编码色、11 档字号、7 种圆角取值。
这类漂移**不会报错、不会变慢、测试全绿**，只是让下一次改主题变成"逐处猜"。

本守卫把 S1/S2 的收敛结果钉住：令牌、字号、圆角、层级、间距、动效属性六项。
判据一律**可证伪**（见 `test_guards_are_falsifiable`），避免退化成装饰。

## 与 `test_css_no_orphans.py` 的分工

- 那个守卫管**类选择器**有没有消费者（DOM 侧）；
- 本守卫管**设计令牌**有没有消费者、以及取值是否来自体系（样式体系侧）。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS_DIR = ROOT / "medkit" / "web" / "css"
TOKENS = CSS_DIR / "tokens.css"
INDEX = ROOT / "medkit" / "web" / "index.html"
CSS_FILES = [CSS_DIR / "base.css", CSS_DIR / "learn.css"]  # tokens.css 是令牌的家，不在被检查之列

# JS/HTML 里用模板字符串动态拼出的令牌前缀（静态扫不到，但要算"有消费者"）。
# 例：review-desk.js 的 `var(--viz-${i + 1})` —— 配比条四段色。
DYNAMIC_PREFIXES = ("--viz-",)

# 覆盖层用法白名单：骑在其它颜色之上的白色/黑色叠色，**看得见底下才是有意义的**。
# 逐条登记理由；登记不得腐烂（条目若消失，守卫会红）。
OVERLAY_ALLOW = {
    "background:rgba(255,255,255,.9)": "配比条拖拽把手：骑在彩色段上的白色指示块",
    "background:linear-gradient(90deg,transparent,rgba(255,255,255,.30),transparent)": "OCR 进度条流光扫过",
    "background:rgba(0,0,0,.5)": "模态遮罩：必须透出底层内容",
}

# 间距网格：4pt 基础，另有 ≤3px 的光学微调（发丝线/描边内缩）与 off-screen 定位。
_SPACING_PROPS = (r"(?:margin|margin-top|margin-bottom|margin-left|margin-right|margin-inline|"
                  r"padding|padding-top|padding-bottom|padding-left|padding-right|"
                  r"gap|row-gap|column-gap)")
_SPACING_SKIP = {-9999}  # skip link 的 off-screen 定位

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_LITERAL_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def strip_comments(css: str) -> str:
    return _COMMENT_RE.sub("", css)


def declarations(css: str) -> list[str]:
    """把 CSS 摊成声明列表（`prop:value`），忽略选择器与嵌套括号。"""
    body = strip_comments(css)
    body = re.sub(r"[{}]", ";", body)   # 花括号（含 @media 壳）摊平，声明里不会出现花括号
    return [d.strip() for d in body.split(";") if ":" in d and d.strip()]


def define_tokens(css: str) -> set[str]:
    return set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", strip_comments(css), re.M))


def use_tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= set(re.findall(r"var\((--[a-z0-9-]+)\)", t))
    return out


def _consumer_text() -> str:
    parts = [_read(INDEX)]
    for p in sorted((ROOT / "medkit" / "web" / "js").glob("*.js")):
        parts.append(_read(p))
    for p in CSS_FILES:
        parts.append(_read(p))
    return "\n".join(parts)


def dynamic_prefixes(text: str) -> set[str]:
    """从 `var(--viz-${...})` 这类模板里抽出前缀，供"动态消费"判定。"""
    found = set(re.findall(r"var\((--[a-z0-9-]*)\$\{", text))
    return found or set()


def font_sizes(css: str) -> list[str]:
    return re.findall(r"font-size\s*:\s*([^;}]+)", strip_comments(css))


def border_radii(css: str) -> list[str]:
    return re.findall(r"border-radius\s*:\s*([^;}]+)", strip_comments(css))


def z_indexes(css: str) -> list[str]:
    return re.findall(r"z-index\s*:\s*([^;}]+)", strip_comments(css))


def spacing_values(css: str) -> list[float]:
    out: list[float] = []
    for m in re.finditer(_SPACING_PROPS + r"\s*:\s*([^;}]+)", strip_comments(css)):
        out += [float(v) for v in re.findall(r"(-?\d+(?:\.\d+)?)px", m.group(1))]
    return out


def literal_color_decls(css: str) -> list[str]:
    """返回含字面色值的声明（跳过 box-shadow —— 阴影是允许的字面用法）。"""
    bad = []
    for d in declarations(css):
        if not _LITERAL_COLOR_RE.search(d):
            continue
        prop = d.split(":", 1)[0].strip().lower()
        if prop == "box-shadow":
            continue
        if d in OVERLAY_ALLOW:
            continue
        bad.append(d)
    return bad


def layout_animated_transitions(css: str) -> list[str]:
    bad = []
    for m in re.finditer(r"transition\s*:\s*([^;}]+)", strip_comments(css)):
        val = m.group(1)
        if "none" in val:
            continue
        bad += re.findall(r"\b(width|height|top|left|right|bottom|margin|padding|font-size)\b", val)
    return bad


# ---------------------------------------------------------------- 元守卫
def test_scan_face_is_not_empty():
    assert TOKENS.exists(), "tokens.css 不存在——令牌体系的落点丢了"
    assert define_tokens(_read(TOKENS)), "tokens.css 里没有定义任何令牌"
    for p in CSS_FILES:
        assert _read(p).strip(), f"{p.name} 是空的"
    assert len(_consumer_text()) >= 200_000, "消费者文本疑似漏读"


# ---------------------------------------------------------------- 主守卫
def test_every_token_has_a_consumer():
    defined = define_tokens(_read(TOKENS))
    used = use_tokens(_consumer_text())
    prefixes = dynamic_prefixes(_consumer_text()) | set(DYNAMIC_PREFIXES)
    orphans = sorted(d for d in defined
                     if d not in used and not any(d.startswith(p) for p in prefixes))
    assert not orphans, (
        "以下令牌定义了却没有任何消费者（等于把同一个数字抄了两遍）：\n"
        + "\n".join("  " + o for o in orphans))


def test_font_size_comes_from_the_scale():
    fs_tokens = {t for t in define_tokens(_read(TOKENS)) if t.startswith("--fs-")}
    assert len(fs_tokens) == 5, f"字号档位应为 5 档，实为 {sorted(fs_tokens)}"
    offenders = []
    relative = re.compile(r"^[0-9.]+(em|rem|%)$")
    for p in CSS_FILES:
        for v in font_sizes(_read(p)):
            v = v.strip()
            if v.startswith("var(--fs-") or relative.match(v) or v == "inherit":
                continue   # 相对单位随上下文缩放，是合法的"继承字号"
            offenders.append(f"{p.name}: font-size:{v}")
    assert not offenders, "字号必须取自 --fs-1..5，不得写裸 px：\n" + "\n".join(offenders)


def test_border_radius_comes_from_the_scale():
    radii = {t for t in define_tokens(_read(TOKENS)) if t.startswith("--radius-")}
    assert len(radii) == 5, f"圆角应为 4 档 + 全圆，实为 {sorted(radii)}"
    allowed_literals = {"0", "2px", "3px", "4px", "50%", "inherit"}
    offenders = []
    for p in CSS_FILES:
        for v in border_radii(_read(p)):
            parts = [x.strip() for x in v.split()]
            for x in parts:
                if x.startswith("var(--radius-") or x in allowed_literals:
                    continue
                offenders.append(f"{p.name}: border-radius:{v}")
    assert not offenders, (
        "圆角必须取自 --radius-1..4 / --radius-pill（≤4px 的光学圆角与 50% 例外）：\n"
        + "\n".join(offenders))


def test_z_index_uses_the_ladder():
    offenders = []
    for p in CSS_FILES:
        for v in z_indexes(_read(p)):
            v = v.strip()
            if v.startswith("var(--z-") or (v.lstrip("-").isdigit() and abs(int(v)) <= 3):
                continue   # ≤3 是组件内部的局部层叠，不是全局层级
            offenders.append(f"{p.name}: z-index:{v}")
    assert not offenders, (
        "z-index 必须来自语义阶梯（--z-sticky/overlay/modal/toast），"
        "组件内部层叠允许 ≤3：\n" + "\n".join(offenders))


def test_spacing_sits_on_the_four_point_grid():
    offenders = []
    for p in CSS_FILES:
        for v in spacing_values(_read(p)):
            x = int(v) if v == int(v) else v
            if x in _SPACING_SKIP:
                continue
            if v % 4 != 0 and abs(v) > 3:
                offenders.append(f"{p.name}: {v}px")
    assert not offenders, (
        "间距取值必须落在 4pt 网格上（4/8/12/16/24/32/48/64…），≤3px 的光学微调例外：\n"
        + "\n".join(offenders))


def test_no_literal_colors_outside_tokens():
    offenders = []
    for p in CSS_FILES:
        for d in literal_color_decls(_read(p)):
            offenders.append(f"{p.name}: {d}")
    assert not offenders, (
        "色值必须走令牌（tokens.css 是唯一的色值家）。若确属覆盖层用法，"
        "请在 OVERLAY_ALLOW 登记并写明理由：\n" + "\n".join(offenders))


def test_overlay_allowlist_does_not_rot():
    blob = "\n".join(_read(p) for p in CSS_FILES)
    dead = [k for k in OVERLAY_ALLOW if k not in blob]
    assert not dead, ("以下覆盖层登记已过期（CSS 里找不到它了），请从 OVERLAY_ALLOW 删掉：\n"
                      + "\n".join("  " + d for d in dead))


def test_transition_does_not_animate_layout_properties():
    offenders = []
    for p in CSS_FILES:
        offenders += [f"{p.name}: {x}" for x in layout_animated_transitions(_read(p))]
    assert not offenders, (
        "动画只允许 transform / opacity —— 动布局属性会逐帧触发重排：\n" + "\n".join(offenders))


def test_js_injected_styles_use_tokens():
    """JS 里拼出的内联样式同样受令牌体系约束。

    否则体系只覆盖一半：`learn-study.js` 曾用 `LEARN_COLORS` 四组硬编码色画环形图，
    那几个色永远不会跟着主题走。
    """
    colors: list[str] = []
    sizes: list[str] = []
    for p in sorted((ROOT / "medkit" / "web" / "js").glob("*.js")):
        src = _read(p)
        colors += [f"{p.name}: {c}" for c in re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", src)]
        for v in re.findall(r"font-size:\s*([^;\"']+)", src):
            v = v.strip()
            if not v.startswith("var(--fs-"):
                sizes.append(f"{p.name}: font-size:{v}")
    assert not colors, "JS 里不得写字面色值（SVG 表现属性不吃 var，请写成 style=\"stroke:var(--x)\"）：\n" \
        + "\n".join(colors)
    assert not sizes, "JS 内联样式的字号也必须取自 --fs-1..5：\n" + "\n".join(sizes)


def test_index_links_every_css_file():
    html = _read(INDEX)
    missing = [p.name for p in sorted(CSS_DIR.glob("*.css"))
               if f"/assets/css/{p.name}" not in html]
    assert not missing, (
        "以下 CSS 文件没有被 index.html 引用（会静默失效）：\n" + "\n".join(missing))
    # 顺序即层叠契约：令牌必须先于消费它的文件加载
    assert html.index("/assets/css/tokens.css") < html.index("/assets/css/base.css"), \
        "tokens.css 必须在 base.css 之前加载，否则令牌还没定义就被消费"


# ---------------------------------------------------------------- 可证伪性
def test_guards_are_falsifiable():
    """反向验证：每个判据喂入"坏样本"都必须报出来，否则守卫是假绿。"""
    assert literal_color_decls(".x{color:#fff}") == ["color:#fff"]
    assert literal_color_decls(".x{background:rgba(1,2,3,.4)}") == ["background:rgba(1,2,3,.4)"]
    assert literal_color_decls(".x{box-shadow:0 1px 2px rgba(0,0,0,.2)}") == []

    assert layout_animated_transitions(".x{transition:width .3s}") == ["width"]
    assert layout_animated_transitions(".x{transition:transform var(--dur-1) var(--ease-out)}") == []
    assert layout_animated_transitions(".x{transition:none}") == []

    assert spacing_values(".x{margin:5px}") == [5.0]
    # 无单位 0 与 auto 不参与网格判定（0 永远合规）
    assert spacing_values(".x{margin:12px 0 4px}") == [12.0, 4.0]
    assert spacing_values(".x{margin:0 auto}") == []
    assert font_sizes(".x{font-size:14px}") == ["14px"]
    assert border_radii(".x{border-radius:7px}") == ["7px"]
    assert z_indexes(".x{z-index:9999}") == ["9999"]

    # 动态前缀必须能被识别（否则配比条四段色会被误判为孤立令牌）
    assert dynamic_prefixes("background:var(--viz-${i + 1})") == {"--viz-"}
