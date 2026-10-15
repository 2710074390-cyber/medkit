"""CSS 孤儿守卫：每个 class 选择器都必须有消费者。

## 为什么需要这道守卫（真实缺陷，不是假想）

2026-10-13 删掉 EP-01 错题归因域时，删了 15 个后端文件 + 3 个前端片，
但**没人管 CSS**。实测结果（2026-10-15）：

- `medkit/web/css/learn.css` **311 行里有 105 行（33.8%）**在给**已经不存在的 DOM** 化妆
  （`.mt-*` 元认知 17 类 + `.mi-*` 图像录入 12 类，整块连续）；
- 全仓 **385 个 CSS 类里 41 个是孤儿**，其中 **29 个（71%）来自 EP-01**。

这类残留**不会报错、不会变慢、测试全绿**——它只是让下一个人以为"元认知视图还在"。
和 `wizard.css` 那次（整个文件没人引用）同一类失效：**删功能时 CSS 是唯一的盲区**。

## 判据

CSS 里出现的每个 class 选择器，必须在「消费者」文本里作为**完整词**出现一次。
消费者集合 = `medkit/web/**/*.js` + `medkit/web/**/*.html` + **`medkit/**/*.py`**。

> ⚠️ **必须包含 Python**：`medkit/render/*.py` 会产出带 class 的 HTML
> （`qbank_html.py` 1160 行就是在拼 HTML）。只扫前端会把它们全部误判成孤儿
> ——本文件第一版就踩了这个坑（把 `.qcard` 等 30+ 个在用类报成孤儿）。

## 为什么不用 `pytest.skip` 放行

按本仓通则：**「检查通过」与「检查没跑」必须能区分**。扫描面为空、CSS 目录缺失、
正则写窄，都必须**红**，不能静默通过（否则守卫会退化成装饰）。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS_DIR = ROOT / "medkit" / "web" / "css"

# 豁免表：`类名 -> 理由`。**登记不得腐烂**——被登记的类若重新有了消费者，
# 守卫会红（要求你把登记删掉）。空表是正常状态。
EXEMPT: dict[str, str] = {}

_CLASS_RE = re.compile(r"\.([a-zA-Z][\w-]*)")


def _consumer_text() -> str:
    """消费者文本：前端 js/html + 全部 Python（render/*.py 会拼 HTML）。"""
    parts: list[str] = []
    for p in list((ROOT / "medkit" / "web").rglob("*.html")) + \
            list((ROOT / "medkit" / "web").rglob("*.js")):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    for p in (ROOT / "medkit").rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _css_classes() -> set[str]:
    out: set[str] = set()
    for p in CSS_DIR.glob("*.css"):
        out |= set(_CLASS_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
    return out


def _orphans(consumers: str) -> list[str]:
    """纯函数：给定消费者文本，返回没有消费者的类名（排序）。"""
    return sorted(c for c in _css_classes()
                  if not re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", consumers))


# ---------------------------------------------------------------- 元守卫
def test_scan_face_is_not_empty_and_not_narrow():
    """扫描面不许塌缩：CSS 文件、类名数量都要有下限。

    旧式写法（只 `assert TARGETS`）在「正则写窄到只认一个文件」时仍然恒真。
    这里三个口径一起钉：文件数、类名数、以及**两个 css 文件都真的被读到**。
    """
    files = sorted(p.name for p in CSS_DIR.glob("*.css"))
    assert files, "没有扫到任何 CSS 文件——扫描面塌缩"
    assert set(files) >= {"base.css", "learn.css"}, f"CSS 文件清单异常：{files}"
    classes = _css_classes()
    assert len(classes) >= 300, f"只扫到 {len(classes)} 个类，扫描面疑似写窄"
    consumers = _consumer_text()
    assert len(consumers) >= 200_000, f"消费者文本只有 {len(consumers)} 字符，疑似漏读"


def test_detector_can_actually_hit():
    """自证能命中：内存构造一个孤儿类，检测器必须报出来。

    反向验证（本仓通则）：**能命中才叫检测器**。若把判据改成恒真，
    这条会立刻红——它防的是"守卫变成装饰"。
    """
    fake_css = ".definitely-not-a-real-class-xyz{color:red}"
    got = [c for c in _CLASS_RE.findall(fake_css)
           if not re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", _consumer_text())]
    assert got == ["definitely-not-a-real-class-xyz"], "检测器抓不到明显的孤儿类（判据失效）"


# ---------------------------------------------------------------- 主守卫
def test_no_orphan_css_class():
    """每个 CSS class 都必须有人消费；例外必须显式登记。"""
    consumers = _consumer_text()
    orphans = [c for c in _orphans(consumers) if c not in EXEMPT]
    assert not orphans, (
        "以下 CSS 类没有任何消费者（JS / HTML / Python 渲染里都不出现）：\n"
        + "\n".join(f"  .{c}" for c in orphans)
        + "\n\n删功能时请连同它的 CSS 一起删（EP-01 就是这样留下 105 行残渣的）；"
          "确有正当理由（如动态拼类名）请在 EXEMPT 里登记并写明原因。"
    )


def test_exempt_entries_do_not_rot():
    """登记不得腐烂：被豁免的类若重新有了消费者，必须把登记删掉。"""
    consumers = _consumer_text()
    alive = [c for c in EXEMPT if c not in _orphans(consumers)]
    assert not alive, (
        "以下类已在 EXEMPT 里登记，但它们**已经有了消费者**——登记过期了：\n"
        + "\n".join(f"  .{c}  （原登记理由：{EXEMPT[c]}）" for c in alive)
        + "\n请从 EXEMPT 里删掉这些条目。"
    )
