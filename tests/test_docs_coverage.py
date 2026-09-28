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


# ---------------------------------------------------------------------------
# 文档内「仓库相对路径」引用的完整性
#
# 2026-09-29 实测发现的真实缺陷：多份活跃文档把提示词写成 `prompts/xxx.md`，
# 但仓库里**没有 `prompts/` 目录**（正确位置是 `medkit/prompts/`）。
# 同类还有 `tests/_asyncio_util.py`（实际落在 `tests/conftest.py`）。
# 这类错误不会报错、不影响测试，只是**把人指向一个不存在的地方**——
# 正是本文件开头说的「文档不参与 CI，所以它会静默过期」。
# ---------------------------------------------------------------------------

# 只认这几个确定前缀，避免把示例路径、URL、命令参数误判为文件引用
_PATH_PREFIXES = ("docs/", "pack/", "tests/", "medkit/", "prompts/")

# 显式豁免：每条都必须写明理由（豁免不等于"不用管"，是"已知且合理"）
# 键必须与 `_norm_ref()` 的输出一致（已 rstrip("/")），否则豁免静默失效——
# 实测踩过：这里写 `docs/design/` 带斜杠，归一后是 `docs/design`，对不上。
_PATH_EXEMPT = {
    # 该目录曾被清理，报告在*记载这个动作*，路径已不存在是正常的历史事实
    "docs/design": "已删除的空目录，引用出现在'已删除'的记录里",
    # 报告记录的是"当时的计划文件名"，实际实现换了落点（已在原文加更正说明）
    "tests/_asyncio_util.py": "计划名，实际落在 tests/conftest.py（原文已更正）",
}


def _norm_ref(s: str) -> str:
    """去掉 pytest 节点 id（`::test_x`）与行号后缀（`:25-33`）。"""
    s = re.sub(r"::.*$", "", s)
    s = re.sub(r":\d+(-\d+)?.*$", "", s)
    return s.rstrip("/")


def _resolve(ref: str):
    """把引用解析为存在的路径；容忍省略扩展名（`docs/README` → `docs/README.md`）。"""
    p = ROOT / ref
    if p.exists():
        return p
    for ext in (".md", ".py", ".json", ".txt", ".html", ".js", ".css", ".iss"):
        if (ROOT / (ref + ext)).exists():
            return ROOT / (ref + ext)
    return None


def _active_docs():
    """活跃文档 = 排除历史快照与历史审查产物（它们记录的是当时的状态）。

    - `docs/archive/`：历史快照，路径按当时事实写，不该按现在校正
    - `docs/reviews/`：历史审查产物，同上
    - `0.10.0-*.md`：规划任务书，列的是**待创建**的文件
    """
    for f in sorted((ROOT / "docs").glob("**/*.md")):
        if "archive" in f.parts or "reviews" in f.parts:
            continue
        if f.name.startswith("0.10.0-"):
            continue
        yield f


@pytest.mark.parametrize("doc", list(_active_docs()), ids=lambda p: p.name)
def test_active_doc_path_refs_exist(doc):
    """活跃文档里反引号标注的仓库内路径，必须真实存在（或已显式豁免）。

    判据刻意保守：只认 5 个确定前缀 + 反引号包裹，避免把散文、示例、
    命令片段误判成文件引用。宁可漏检，不可误伤（误伤会逼人删守卫）。
    """
    text = doc.read_text(encoding="utf-8")
    missing = []
    for m in re.finditer(r"`([^`\s]+)`", text):
        raw = m.group(1)
        if not raw.startswith(_PATH_PREFIXES):
            continue
        if any(c in raw for c in "*?<>{}"):   # 通配符 / 占位符，非具体路径
            continue
        ref = _norm_ref(raw)
        if not ref or ref in _PATH_EXEMPT:
            continue
        if _resolve(ref) is None:
            missing.append(raw)

    assert not missing, (
        "%s 引用了不存在的仓库内路径：\n  %s\n"
        "要么改正路径，要么在 _PATH_EXEMPT 里登记并写明理由。"
        % (doc.relative_to(ROOT), "\n  ".join(sorted(set(missing))))
    )


def test_path_exempt_entries_are_still_needed():
    """豁免清单不许留"已经不需要"的条目。

    否则豁免会只增不减，慢慢把守卫稀释成空壳——
    这正是「守卫阈值不许写成预算式」的同一个毛病。
    """
    stale = []
    for ref, _reason in _PATH_EXEMPT.items():
        if _resolve(ref) is not None:
            stale.append(ref)
    assert not stale, (
        "这些豁免路径现在已存在，应从 _PATH_EXEMPT 移除：%s" % stale
    )
