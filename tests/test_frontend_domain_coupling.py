"""前端分域耦合守卫（D2「生成链剥离为插件」的可核对前提）。

## 为什么需要这条守卫

`test_v15_frontend_split.py` 守的是**加载期**前向引用；而跨域耦合恰恰多在**调用时**
（函数体里调另一个域的顶层函数）——闸门管不到，删了另一个域只在**点击时**炸
（`ReferenceError`），而且本仓**没有浏览器用例覆盖**这些路径（实测：
`letters` / `ankiPreview` / `ankiHelp` 三个符号在 `tests/browser/` 里零出现）。

实测到的两处跨域耦合（此前只存在于一行代码注释里）：

| 方向 | 位置 | 符号 |
|---|---|---|
| **核心 → 插件** | `learn-review.js`（Anki 卡样预览） | `letters()`（当时定义在 `review-desk.js`） |
| 插件 → 核心 | `review-desk-project.js`（导出按钮） | `ankiPreview()` / `ankiHelp()`（定义在 `learn-review.js`） |

## 判据：**方向决定可否**

剥离生成链意味着「生成链」变成**可选插件**，于是依赖方向有了硬性要求：

- **核心 → 插件：禁止**（硬红线）。核心必须能在没有插件的构建里独立工作；
  否则剥离那天核心就断了。
- **插件 → 核心：允许，但必须登记**。插件依赖核心是**正常**的（插件本就该建在核心上）；
  登记是为了让"插件到底依赖了核心的哪些面"变成**可读的清单**，
  而不是散落在各处的隐式引用。

⇒ `letters()` 已从 `review-desk.js`（插件域）**移到 `app.js`（共享基础片）**，消除硬红线违规。
插件 → 核心的引用登记在下方 `PLUGIN_TO_CORE`。

## 元守卫

`test_detector_really_detects` 用**内存构造**的样本自证检测器真能命中
（核心引插件 / 插件引核心 / 同域引用三种），否则两条主判据会在"检测器恒返回空集"时恒真。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JS_DIR = ROOT / "medkit" / "web" / "js"

# 分域（与 index.html 的加载顺序族一致）
# - `app.js` = **外壳**（tab/仪表盘/数据管理/向导）：它是**宿主**，会主动调用生成链的功能
# - `learn*.js` = **EP-01 / 学习中心**：剥离生成链后必须能独立工作 ⇒ 对它**零容忍**
# - `review-desk*.js` = **生成链**（建课/审核台/出题）：待剥离的插件
# - `md.js` = 共享基础片（渲染器），两边都可用
SHARED = ("md.js",)
EP01 = ("learn.js", "learn-study.js", "learn-live.js", "learn-review.js",
        "learn-meta.js", "learn-meta-image.js", "learn-search.js")
SHELL = ("app.js",)
PLUGIN = ("review-desk.js", "review-desk-materials.js",
          "review-desk-project.js", "review-desk-review.js")

# 顶层声明（行首；与 eslint.config.js 的 TOP_LEVEL_RE 同口径）
_TOP_DECL = re.compile(r"^(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
# 任意层级的声明（含函数内局部）——用于**排除误报**：函数内局部变量不该算跨域引用
_ANY_DECL = re.compile(r"(?:^|[;{}\s])(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
# 标识符使用（排除属性访问 `.foo`）
_IDENT = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)")

# **外壳 → 插件** 的引用登记：{(文件, 符号): (处置, 理由)}
# 外壳是宿主，引用是**有意的**（题库/审核台本就是 tab）；登记是为了把"剥离时必须处理什么"写清。
SHELL_TO_PLUGIN: dict[tuple[str, str], tuple[str, str]] = {
    ("app.js", "loadProjects"): ("加 typeof 守卫", "切到「题库」tab 时重拉项目列表"),
    ("app.js", "ratioSum"): ("加 typeof 守卫", "切回题库时重算出题比例（服务商/模型可能已变）"),
    ("app.js", "bloomSum"): ("加 typeof 守卫", "同上，Bloom 层比例"),
    ("app.js", "updateReady"): ("已守卫", "同一行里**已经**有 `typeof updateReady === \"function\"` 守卫"),
    ("app.js", "loadPrompts"): ("加 typeof 守卫", "切到「我的」tab 时重拉提示词（提示词管理属生成链）"),
    ("app.js", "stopPoll"): ("加 typeof 守卫", "切 tab / hash 直达时停掉项目轮询"),
    ("app.js", "showProject"): ("摘掉或降级", "「最近项目」卡片点击后打开建课页（生成链功能）"),
    ("app.js", "wzClose"): ("加 typeof 守卫", "Esc / 遮罩点击时关掉新建向导（向导属生成链）"),
}

# **插件 → EP-01** 的引用登记：插件依赖核心是**允许**的（插件本就该建在核心上）
PLUGIN_TO_CORE: dict[tuple[str, str], str] = {
    ("review-desk-project.js", "ankiPreview"):
        "项目页的「预览 Anki 卡样」按钮——卡样渲染复用学习中心的实现，避免两套渲染器漂移",
    ("review-desk-project.js", "ankiHelp"):
        "项目页的「Anki 导入指引」按钮——弹层文案与学习中心共用一份（同一份指引不该写两遍）",
}


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _top_decls(path: Path) -> set[str]:
    return set(_TOP_DECL.findall(path.read_text(encoding="utf-8")))


def _local_decls(path: Path) -> set[str]:
    return set(_ANY_DECL.findall(_strip_comments(path.read_text(encoding="utf-8"))))


def _used(path: Path) -> set[str]:
    """文件里**被引用**的标识符（排除属性访问 `.foo` 与**对象字面量的键** `{pres: ...}`）。

    排除对象键这条是被误报逼出来的：`app.js` 的 `pres: { textbook: null, … }` 是**键**，
    却被当成"引用了插件的 `pres`"。判据与 `test_v15_frontend_split.py` 同口径
    （键只出现在 `{` / `,` 之后）。
    """
    src = _strip_comments(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for m in _IDENT.finditer(src):
        after = src[m.end():].lstrip()
        if after.startswith(":") and not after.startswith("::"):
            before = src[:m.start()].rstrip()
            if before.endswith("{") or before.endswith(","):
                continue          # 对象字面量的键，不是引用
        out.add(m.group(1))
    return out


def _cross_refs(files: tuple[str, ...], other: tuple[str, ...],
                *, js_dir: Path = JS_DIR) -> set[tuple[str, str]]:
    """`files` 里引用了「**只在** `other` 里声明的顶层符号」的 `(文件, 符号)` 集合。

    `other` 独有 = other 的顶层声明 − files 的顶层声明 − 共享基础片（`md.js`）。
    同时减去**本文件任意层级的声明**（函数内局部变量同名不算跨域引用——这是本检测器
    最容易误报的地方）。
    """
    other_only = (set().union(*[_top_decls(js_dir / f) for f in other])
                  - set().union(*[_top_decls(js_dir / f) for f in files])
                  - set().union(*[_top_decls(js_dir / f) for f in SHARED]))
    out: set[tuple[str, str]] = set()
    for f in files:
        own = _local_decls(js_dir / f)
        out |= {(f, s) for s in (_used(js_dir / f) & other_only) - own}
    return out


def test_ep01_never_references_plugin_symbols():
    """**硬红线**：EP-01 / 学习中心片不得引用插件（生成链）独有符号。

    理由：剥离生成链后，**学习中心必须能独立工作**。这类调用会在**点击时**炸
    （`ReferenceError`），而加载期闸门 `test_v15_frontend_split.py` 管不到
    （它只看加载期前向引用），本仓也没有浏览器用例覆盖这些路径
    （实测 `letters`/`ankiPreview`/`ankiHelp` 在 `tests/browser/` 里零出现）。

    `letters()` 原在 `review-desk.js`（插件域），由 `learn-review.js` 调用 ⇒ 已移到
    `app.js`（共享基础片）。**这是本守卫第一次跑就抓到的真违规。**
    """
    hits = sorted(_cross_refs(EP01, PLUGIN))
    assert not hits, (
        "EP-01 / 学习中心引用了**插件独有**的顶层符号——剥离生成链后这些调用会在点击时炸：\n  "
        + "\n  ".join(f"{f} → {s}" for f, s in hits)
        + "\n（修法：把该符号移到共享基础片 `md.js`/`app.js`，或让学习中心自带一份实现。）")


def test_shell_to_plugin_refs_are_registered():
    """**外壳 → 插件** 的引用允许（外壳是宿主），但每处都要登记**处置与理由**。

    登记表回答的是「剥离生成链时，这一处该怎么办」——这是 D2 评估的可核对前提，
    而不是散落在代码里的隐式引用。
    """
    found = _cross_refs(SHELL, PLUGIN)
    unregistered = sorted(found - set(SHELL_TO_PLUGIN))
    assert not unregistered, (
        "以下「外壳 → 插件」引用未登记——外壳引用插件是允许的，但必须写明"
        "「剥离时怎么处理」：\n  " + "\n  ".join(f"{f} → {s}" for f, s in unregistered))


def test_shell_registry_is_not_rotted():
    found = _cross_refs(SHELL, PLUGIN)
    rotten = sorted(set(SHELL_TO_PLUGIN) - found)
    assert not rotten, f"以下登记项已不再被引用，请从 SHELL_TO_PLUGIN 删掉：{rotten}"


def test_plugin_to_core_refs_are_registered():
    """**插件 → EP-01** 允许（插件本就该建在核心上），但必须登记。"""
    found = _cross_refs(PLUGIN, EP01)
    unregistered = sorted(found - set(PLUGIN_TO_CORE))
    assert not unregistered, (
        "以下「插件 → 核心」引用未登记：\n  "
        + "\n  ".join(f"{f} → {s}" for f, s in unregistered))


def test_plugin_registry_is_not_rotted():
    """**登记不得腐烂**：登记了但代码里已不再引用 ⇒ 红。"""
    found = _cross_refs(PLUGIN, EP01)
    rotten = sorted(set(PLUGIN_TO_CORE) - found)
    assert rotten == [], f"以下登记项已不再被引用，请从 PLUGIN_TO_CORE 删掉：{rotten}"


def test_detector_really_detects(tmp_path):
    """**元守卫**：检测器必须真能命中——用内存构造的样本自证（含**同域不误报**）。

    不这样做的话，一个"恒返回空集"的检测器会让上面三条断言在空集上恒真。
    """
    for name, body in (("app.js", "const esc = 1;\n"), ("md.js", ""),
                       ("learn.js", "function coreFn() { return 1; }\n"
                                    "function callCore() { return pluginFn(); }\n"),
                       ("review-desk.js", "function pluginFn() { return 1; }\n"
                                          "function callPlugin() { return coreFn(); }\n")):
        (tmp_path / name).write_text(body, encoding="utf-8")
    ep01, shell, plugin = ("learn.js",), ("app.js",), ("review-desk.js",)

    # ① 核心引插件 ⇒ 检出
    assert ("learn.js", "pluginFn") in _cross_refs(ep01, plugin, js_dir=tmp_path)
    # ② 插件引核心 ⇒ 检出
    assert ("review-desk.js", "coreFn") in _cross_refs(plugin, ep01, js_dir=tmp_path)
    # ③ 同域引用 ⇒ **不**检出（防误报：`esc` 是共享片，两边都可用）
    assert not _cross_refs(ep01, plugin, js_dir=tmp_path) - {("learn.js", "pluginFn")}

    # ④ 函数内局部同名不算跨域引用（本检测器最易误报处）
    (tmp_path / "learn.js").write_text(
        "function coreFn() { return 1; }\n"
        "function useLocal() { const pluginFn = 2; return pluginFn; }\n", encoding="utf-8")
    assert not _cross_refs(ep01, plugin, js_dir=tmp_path), \
        "函数内局部变量同名被误判成跨域引用"

    # ⑤ 对象字面量的**键**不算引用（`pres: {...}` 曾被误报成引用插件的 `pres`）
    (tmp_path / "app.js").write_text(
        "const cfg = { pluginFn: { a: 1 } };\n", encoding="utf-8")
    assert not _cross_refs(shell, plugin, js_dir=tmp_path), \
        "对象字面量的键被误判成跨域引用"


def test_letters_lives_in_shared_piece():
    """`letters()` 必须在**共享基础片**里（`app.js`）——它是 D2 那条硬红线修复的落点。

    这条把修复本身钉住：若有人把它挪回 `review-desk.js`，
    `test_core_never_references_plugin_symbols` 会红，但那时"该放哪"不够显眼，故这里明写。
    """
    app = (JS_DIR / "app.js").read_text(encoding="utf-8")
    desk = (JS_DIR / "review-desk.js").read_text(encoding="utf-8")
    assert _TOP_DECL.findall(app).count("letters") == 1
    assert "letters" not in _TOP_DECL.findall(desk), "letters 不该再声明在插件域"
    # 单源：全仓只声明一次
    decls = [f.name for f in JS_DIR.glob("*.js") if "letters" in _top_decls(f)]
    assert decls == ["app.js"], f"letters 的声明应唯一且落在 app.js，实际 {decls}"


def test_domain_lists_cover_all_js_files():
    """分域清单必须覆盖全部 JS（新增片忘了归类 ⇒ 它会成为"没人管的域"，耦合检测漏检）。"""
    on_disk = {p.name for p in JS_DIR.glob("*.js")}
    covered = set(EP01) | set(SHELL) | set(PLUGIN) | set(SHARED)
    assert on_disk == covered, (
        f"分域清单与磁盘不一致：\n  未归类 = {sorted(on_disk - covered)}\n"
        f"  清单里有而磁盘没有 = {sorted(covered - on_disk)}\n"
        "（新增 JS 片必须归入 EP01 / SHELL / PLUGIN / SHARED——否则跨域耦合检测会漏检它。）")


@pytest.mark.parametrize("f", EP01 + SHELL + PLUGIN + SHARED)
def test_every_domain_file_exists(f: str):
    assert (JS_DIR / f).is_file(), f"{f} 不存在（分域清单过期？）"
