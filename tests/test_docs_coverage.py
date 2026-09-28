"""文档覆盖守卫：代码里的模块/端点，README 必须提及。

## 为什么需要这条守卫

2026-09-28 实测：EP-01 错题归因流水线（2473 行 / 22 端点 / 2 个新 agent）
**在 README 与 AGENT_HANDOFF 里完全不存在**。功能早已可用，但：

- 用户装完 0.10.5 后从 README 里看不到这个功能；
- 交接文档自称「不依赖对话记忆的工程交接入口」，新人读它不知道 EP-01 存在。

这不是代码缺陷，但同样让人失去线索。**文档不参与 CI，所以它会静默过期。**

判据（可检验）：**一个新人只读文档，能否知道这个功能存在、怎么用、有哪些坑？**

## 本守卫的边界（明确不测什么）

- **不测文档写得对不对**（那要靠人读）——只测「**有没有**」。
- **不测历史章节**。「开发里程碑（历史记录）」段落里的旧数字（如 `pytest 203 全绿`）
  是**当时的事实**，不随代码变化，必须豁免。
- **不做模糊语义匹配**。「有没有提到」用**精确路径/数字**判定，
  避免「提到了某个相似词」这类假绿。

## 假绿与假红的防范

- **假绿**（本守卫最怕的）：文档里写了词但**功能其实不存在**。
  → 用 `test_documented_modules_really_exist` 双向钉住：
  README 提到的模块必须真实存在，反过来代码里的关键模块也必须在 README 里。
- **假红**：把历史章节的旧数字当当前状态。
  → 只扫「已实现功能」章节，用章节切片而非全文扫描。
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
ERRORS_ROUTER = ROOT / "medkit" / "routers" / "errors.py"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _current_feature_section() -> str:
    """截取 README 的「已实现功能」章节（当前状态），排除历史里程碑。

    边界：从 `## 已实现功能` 到下一个 `## ` 标题。
    历史里程碑在它之后，含当时的旧数字，必须豁免。
    """
    text = _readme_text()
    m = re.search(r"^## 已实现功能.*?$", text, re.MULTILINE)
    assert m, (
        "README 缺少「## 已实现功能」章节——本守卫依赖它做当前/历史切分。"
        "改标题时请同步本守卫。"
    )
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _router_endpoint_count() -> int:
    """数 `/api/errors/*` 的端点数（真身：routers/errors.py 的装饰器）。"""
    src = ERRORS_ROUTER.read_text(encoding="utf-8")
    return len(re.findall(r"^@router\.(get|post|put|delete)\(", src, re.MULTILINE))


# --------------------------------------------------------------------------
# 1. 端点数：README 声称的数字必须等于真身
# --------------------------------------------------------------------------


def test_readme_endpoint_count_matches_router():
    """README 里「N 个端点 /api/errors/*」的 N 必须等于 routers/errors.py 的真实端点数。

    这是**最容易被忽略**的一类过期：加了端点没人去改 README 的数字。
    """
    section = _current_feature_section()
    m = re.search(r"(\d+)\s*个端点", section)
    assert m, "README「已实现功能」中应声明 `/api/errors/*` 的端点数（形如「22 个端点」）"

    claimed = int(m.group(1))
    actual = _router_endpoint_count()
    assert claimed == actual, (
        f"README 声称 {claimed} 个端点，实际 {actual} 个。"
        f"新增/删除端点后请同步 README 的数字（真身：medkit/routers/errors.py）。"
    )


# --------------------------------------------------------------------------
# 2. EP-01 关键模块：README 必须提到（正向）
# --------------------------------------------------------------------------

# 这些是 EP-01 的**架构支柱**——少了任何一个，读者都无法理解这个功能怎么运作。
# 清单变化时（新增/删除支柱模块）必须同步这里，否则守卫本身失效。
EP01_PILLARS = (
    "core/errorpipe.py",
    "core/kpid.py",
    "core/metacog.py",
    "core/error_events.py",
    "agents/error_analysis.py",
    "agents/socratic_review.py",
    "routers/errors.py",
)


@pytest.mark.parametrize("rel", EP01_PILLARS)
def test_ep01_pillar_mentioned_in_readme(rel: str):
    """每个 EP-01 支柱模块都必须在 README 的当前功能章节里被提及。"""
    section = _current_feature_section()
    name = pathlib.Path(rel).name
    assert name in section or rel in section, (
        f"README「已实现功能」未提及 EP-01 支柱模块 {rel}。"
        f"新功能落地后必须补文档——否则用户/接手者无从知晓。"
    )


@pytest.mark.parametrize("rel", EP01_PILLARS)
def test_ep01_pillar_really_exists(rel: str):
    """反向：README 提到的支柱模块必须真实存在（防「文档写了代码没有」）。"""
    assert (ROOT / "medkit" / rel).exists(), (
        f"{rel} 不存在，README 却在描述它——文档与代码脱节。"
    )


# --------------------------------------------------------------------------
# 3. 提示词文件：README 必须提到（EP-01 的两个新 prompt）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ("error_analysis.md", "socratic_review.md"))
def test_ep01_prompts_mentioned(name: str):
    """EP-01 新增的两个提示词必须在 README 提及（提示词是行为契约的一部分）。"""
    section = _current_feature_section()
    assert name in section, f"README「已实现功能」未提及提示词 {name}"


@pytest.mark.parametrize("name", ("error_analysis.md", "socratic_review.md"))
def test_ep01_prompts_really_exist(name: str):
    assert (ROOT / "medkit" / "prompts" / name).exists(), f"prompts/{name} 不存在"


# --------------------------------------------------------------------------
# 4. 测试数字：README 声称的总数必须与 --collect-only 一致
# --------------------------------------------------------------------------


def test_readme_test_count_is_not_stale():
    """README 声称的测试总数必须与实际收集数在 **±5%** 内。

    2026-09-28 实测 README 曾写「192 项 pytest」而实际 814——**过期 4 倍**。
    这类数字不参与 CI，所以只会越写越旧。本守卫把它钉住。

    ## 为什么是 ±5% 而不是精确相等

    精确相等会**自我指涉**：写守卫这个动作本身就改变了收集数
    （本文件贡献 20 项），而且以后每加一个用例都得改 README——
    摩擦成本会让人干脆绕过守卫（或删掉它）。

    容差抓的是**真问题**：数字陈旧到失去参考价值（192 vs 814 是 4 倍级偏离）。
    增删十几个用例（±5% 内）不会误红——那种偏离读者仍能正确理解量级。

    ## 边界

    只扫「已实现功能」章节——里程碑章节的历史数字（`pytest 203 全绿`）
    是当时事实，应当豁免。
    """
    section = _current_feature_section()
    m = re.search(r"\*\*(\d+)\s*项\s*pytest\*\*", section)
    if not m:
        pytest.skip("README 未以「**N 项 pytest**」形式声明测试总数（格式已变，需同步守卫）")

    claimed = int(m.group(1))

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--ignore=tests/browser"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    mm = re.search(r"(\d+)\s+tests? collected", tail)
    if not mm:
        pytest.skip(f"未能从 pytest 输出解析收集数（输出尾行：{tail!r}）")

    actual = int(mm.group(1))
    tolerance = 0.05
    low, high = actual * (1 - tolerance), actual * (1 + tolerance)

    assert low <= claimed <= high, (
        f"README 声称 {claimed} 项 pytest，实际收集 {actual} 项"
        f"（允许 ±5%：{low:.0f}~{high:.0f}）。"
        f"README 的「质量」行已陈旧到失去参考价值，请同步更新。"
    )
