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

import ast
import pathlib
import re
import subprocess
import sys

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
    "core/vision.py",
    "core/apkg_import.py",
    "core/errsearch.py",
    "agents/error_analysis.py",
    "agents/socratic_review.py",
    "render/notebook_md.py",
    "routers/errors.py",
)


def test_ep01_lists_are_not_empty():
    """**元守卫（必须非参数化）**：两个手写清单都不能为空。

    `test_ep01_pillar_*` / `test_ep01_prompts_*` 是 `@pytest.mark.parametrize` 用例，
    pytest 对**空** parametrize 的处理是「收集一个 `[NOTSET]` 用例并 **SKIP**」——
    输出 `N passed, 1 skipped` 看着完全正常，实则是**用例消失**。
    而 parametrize 里**没法断言非空**（空集合根本不进函数体）。
    本文件已有的 `test_active_docs_scan_face_is_not_empty` 只管 `_active_docs()`，
    管不到这两个手写清单 ⇒ 补上。

    判据两条腿（互不依赖，删任一条另一条仍能在塌缩时拦住）：
    ① 数量下限；② 关键成员在场。
    """
    assert len(EP01_PILLARS) >= 5, f"EP01_PILLARS 只剩 {len(EP01_PILLARS)} 项"
    assert len(EP01_PROMPTS) >= 3, f"EP01_PROMPTS 只剩 {len(EP01_PROMPTS)} 项"
    for must in ("core/errorpipe.py", "core/vision.py", "routers/errors.py"):
        assert must in EP01_PILLARS, f"EP01_PILLARS 缺关键支柱 {must}"
    for must in ("error_analysis.md", "error_image_extract.md"):
        assert must in EP01_PROMPTS, f"EP01_PROMPTS 缺关键提示词 {must}"


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


EP01_PROMPTS = ("error_analysis.md", "socratic_review.md", "error_image_extract.md")


@pytest.mark.parametrize("name", EP01_PROMPTS)
def test_ep01_prompts_mentioned(name: str):
    """EP-01 的提示词必须在 README 提及（提示词是行为契约的一部分）。"""
    section = _current_feature_section()
    assert name in section, f"README「已实现功能」未提及提示词 {name}"


@pytest.mark.parametrize("name", EP01_PROMPTS)
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

    ## 总体口径：必须和 README 的「质量」行同源（2026-10-02 修）

    此前本用例跑的是 `--ignore=tests/browser`——**只数单元层**，
    而 README「质量」行写的是**全量**（单元 + 浏览器层，共 82 项 browser）。
    两个总体差 6.6%，**恰好越过 5% 容差** ⇒ 一旦 README 忠实同步到全量，
    本守卫立刻假红（实测：README 1314 全量 vs 守卫报 `实际收集 1232`）。

    这类「**守卫拿 A 总体、文档写 B 总体**」是假红的标准成因：
    数字两边都对，只是量的不是一件事。⇒ 改为数**全量**（与 README 同一总体），
    并把容差从 5% 收到 **2%**（同源之后，两数只应有「本文件自身项数」级差异）。

    > **量取应当放宽**：这条命令在**全量套件运行期间**会变慢——
    > 全量跑时 `pytest --collect-only` 要 import 1300+ 条用例的模块，
    > 实测在 120s 边缘（单跑 3s）。故 `timeout` 给到 600s：
    > 超时假红比慢一点更糟。

    ## 为什么两处缺口都是 fail 而不是 skip（2026-09-29 修）

    此前「README 没写 N 项 pytest」与「解析不出收集数」两个分支都走 `pytest.skip`。
    在 CI 里 **skip 与 pass 的退出码相同**（`-q` 不带 `--strict-markers` 之类时
    并不区分），所以这两个分支等价于「**看不懂就放行**」：

    - 只要有人把 README 里那行删掉 / 改个措辞 → 守卫静默失效且报告全绿；
    - 只要 pytest 改了尾行格式（版本升级）→ 同样静默失效。

    这与本项目 `test_lint_gate.py::test_ruff_available` 的处理一致：
    **前提条件不成立时必须红，不能 skip**——因为忽略这个守卫的代价
    （README 数字陈旧 4 倍、无人发现）正是它存在的理由。
    """
    section = _current_feature_section()
    m = re.search(r"\*\*(\d+)\s*项\s*pytest\*\*", section)
    assert m, (
        "README「已实现功能」章节里找不到「**N 项 pytest**」形式的测试总数声明。"
        "该数字是面向用户的量级参考，不许悄悄删掉或换措辞——"
        "若确实要改格式，请同步更新本守卫。"
    )

    claimed = int(m.group(1))

    import subprocess
    import sys

    proc = subprocess.run(
        # 全量（= README「质量」行的同一总体）：单元 + 浏览器层。
        # 不许再写成 --ignore=tests/browser——那会少掉 82 项 browser 用例（差 6.6%，
        # 越过容差 ⇒ 假红；2026-10-02 实测踩过）。
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    mm = re.search(r"(\d+)\s+tests? collected", tail)
    assert mm, (
        f"未能从 `pytest --collect-only` 的输出解析收集数（尾行：{tail!r}）。"
        f"rc={proc.returncode} 输出 {len(proc.stdout)} 字节。"
        f"**不许 skip**，否则本守卫会静默失效；请修解析正则或调大 timeout。"
    )

    actual = int(mm.group(1))
    tolerance = 0.02
    low, high = actual * (1 - tolerance), actual * (1 + tolerance)

    assert low <= claimed <= high, (
        f"README 声称 {claimed} 项 pytest，实际收集 {actual} 项"
        f"（允许 ±2%：{low:.0f}~{high:.0f}）。"
        f"README 的「质量」行已陈旧到失去参考价值，请同步更新。"
        f"注意本判据数的是**全量**（含 tests/browser），与 README 同一总体。"
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


def test_active_docs_scan_face_is_not_empty():
    """元守卫：`_active_docs()` 的扫描面不许塌缩（R26 修的假绿）。

    ## 为什么（2026-09-29 R26 实测的真缺陷）

    `test_active_doc_path_refs_exist` 是 `@pytest.mark.parametrize` 用例，
    参数在**模块加载期**由 `list(_active_docs())` 求值。若扫描面为空：

        >>> list(_active_docs())
        []

    pytest 对空 parametrize 的处理是**收集一个 `[NOTSET]` 用例并 SKIP**——
    实测注入 `for f in []:` 后该文件输出 `22 passed, 1 skipped`，
    **整组守卫静默消失**，而本文件已有的 `test_no_silent_skip_in_doc_guards`
    （禁 `pytest.skip` 调用）**拦不住它**（那不是代码里的 skip，是 pytest 的兜底行为）。

    ⇒ 必须**另起一个非参数化用例**把「扫描面非空」钉死。
    三条腿缺一不可（照 `test_no_sleep_gambling.py::test_scan_covers_every_test_file`）：
      1. 非空 —— 防「排除规则写宽了把全部文档排掉」；
      2. 数量下限 —— 防整体塌缩成 1~2 个还"自洽"；
      3. 与 **git 索引**核对 —— 独立事实来源，照出「文件被改名/移出」时
         磁盘 glob 与排除规则**一起变小**的恒真盲区。
    """
    found = {p.relative_to(ROOT).as_posix() for p in _active_docs()}

    assert found, (
        "活跃文档扫描面为空——`test_active_doc_path_refs_exist` 会退化成"
        "「收集 1 个 [NOTSET] 并 SKIP」，守卫静默消失。"
        "检查 `_active_docs()` 的排除规则是否写宽了（如误排整个 docs/）。"
    )
    assert len(found) >= 10, (
        f"活跃文档只有 {len(found)} 个（下限 10）——扫描面疑似整体塌缩，"
        f"当前集合：{sorted(found)}"
    )

    tracked = _tracked_docs()
    if tracked is not None:
        # 与 git 索引核对时，先扣掉**设计上就该排除**的两类，只留「意料之外的漏扫」。
        # （不加这一步会把 archive/ 与 0.10.0-* 的每一份文档都报成缺失——我第一版
        #   正是这么写的，实测立刻 44 处「缺失」，是探针误报而非真缺陷。）
        expected_excluded = (
            {d for d in tracked if "/archive/" in d or "/reviews/" in d}
            | {d for d in tracked
               if pathlib.Path(d).name.startswith("0.10.0-")}
        )
        gone = tracked - found - expected_excluded
        assert not gone, (
            "以下文档在 git 索引里存在，但既不在活跃扫描面里、也不属于"
            "「设计上排除」的两类（archive/ · reviews/ · 0.10.0-*）：\n  "
            + "\n  ".join(sorted(gone))
        )


def _tracked_docs() -> set[str] | None:
    """从 **git 索引**取 `docs/**/*.md` —— 独立于磁盘 glob 与排除规则。

    返回 None 表示拿不到 git（源码包脱离仓库）——调用方应跳过该条断言，
    但仍保留上面的非空与数量下限（那两条不依赖 git）。
    """
    try:
        r = subprocess.run(
            ["git", "ls-files", "docs/"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return {x for x in r.stdout.split() if x.endswith(".md")}


@pytest.mark.parametrize("doc", list(_active_docs()), ids=lambda p: p.name)
def test_active_doc_path_refs_exist(doc):
    """活跃文档里反引号标注的仓库内路径，必须真实存在（或已显式豁免）。

    判据刻意保守：只认 5 个确定前缀 + 反引号包裹，避免把散文、示例、
    命令片段误判成文件引用。宁可漏检，不可误伤（误伤会逼人删守卫）。

    「扫描面非空」由 `test_active_docs_scan_face_is_not_empty` 单独把守
    （parametrize 里没法断言非空——空集合根本不会进入本函数体）。
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


# ---------------------------------------------------------------------------
# 元守卫：本文件不许用 skip 掩盖"前提不成立"
# ---------------------------------------------------------------------------


def test_no_silent_skip_in_doc_guards():
    """本文件里出现 `pytest.skip(...)` 调用即红。

    ## 为什么（2026-09-29 实测修掉的真缺陷）

    本文件此前有两处 `pytest.skip`：「README 没写 N 项 pytest」与
    「解析不出 --collect-only 的收集数」。它们看起来是"环境不满足就放过"的
    合理降级，但在 CI 里 **skip 与 pass 的退出码同为 0**，
    于是等价于「**看不懂就放行**」：

    - 删掉 README 那个数字 → 守卫静默失效，报告仍全绿；
    - pytest 升版改了尾行格式 → 同上。

    这正是本项目反复出现的「门禁假绿」第 (d) 类
    （对照 `test_no_sleep_gambling.py` 里修掉的 `except SyntaxError: continue`）。
    判据必须**拦在哪一步**：前提不成立时红，而不是绿。

    ## 判据用 AST，不用子串匹配（2026-09-29 踩坑后改）

    第一版用「剥注释再 `"pytest.skip" in code`」。**反向验证时注入一句
    `pytest.skip("模拟的静默放行")`，守卫却没有变红**——查下来是剥壳实现
    把 token 用空格 join，`pytest.skip` 变成 `pytest . skip`，
    子串永远匹配不上。

    教训：**"剥壳后再做子串匹配" 这条路本身就是脆的**。
    改用 AST 找 `Call` 节点，`func` 是 `Attribute(value=Name('pytest'),
    attr='skip')` —— 这才是真身，不依赖任何文本拼接方式。
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "skip"
                and isinstance(f.value, ast.Name) and f.value.id == "pytest"):
            offenders.append(node.lineno)
    assert not offenders, (
        "tests/test_docs_coverage.py 第 %s 行出现 pytest.skip——"
        "文档守卫的前提不成立时必须红，不能 skip"
        "（skip 在 CI 里与 pass 退出码相同，等价于静默放行）。"
        "若确有正当理由，请改为断言并在 docstring 里写明为什么它不会沦为假绿。"
        % sorted(offenders)
    )


# ---------------------------------------------------------------------------
# README「仓库结构」代码块里的数字声明
#
# 2026-10-02 发现的**扫描面盲区**：README 有两处写「同一组数字」——
#   ①「已实现功能」章节：`**N 项 pytest**` / `N 个端点`  ← 一直被守卫扫
#   ② 目录树代码块注释：`└── tests/  # N 项（单元 N / 浏览器层 M）`
#                       `│   ├── routers/  # …（/api/errors/* · N 端点）`
#                       ← **完全在扫描面外**
# 出 0.10.6 时实测：② 的两个数字分别陈旧到 1052（实际 1228）与 22（实际 30），
# 而**任何守卫都不报警**。这与 2026-10-05 事故（README 数字陈旧 4 倍无人发现）
# 同源，只是换了个位置——**守卫的扫描面写窄了**。
#
# 判据与①完全一致（都取自真身），只是把扫描面扩到目录树。
# ---------------------------------------------------------------------------

# 目录树代码块的起点标记（README 里唯一的 `medkit/` 顶格行）
_TREE_START = "medkit/"


def _readme_tree_block() -> str:
    """截取 README 里「仓库结构」那个代码块（从顶格 `medkit/` 行起）。

    为什么不用「第一个 ``` 代码块」定位：README 开头就有 powershell 代码块，
    按序取会取错。这里用**内容锚点**（顶格 `medkit/` 行）——它在本仓库里唯一。
    """
    text = _readme_text()
    m = re.search(rf"^{re.escape(_TREE_START)}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.start():]
    fence = re.search(r"^```\s*$", rest, re.MULTILINE)
    return rest[: fence.start()] if fence else rest


def test_readme_tree_test_counts_match_reality():
    """目录树注释里的「N 项（单元 N / 浏览器层 M）」三个数字都必须对得上真身。

    **为什么这条必须存在**：目录树那行与「已实现功能」段那行是**同一件事的两处
    写法**，而守卫只覆盖了后者（见本文件上方 `test_readme_test_count_is_not_stale`）。
    少覆盖的那一处会在每次新增测试时静默漂移。

    ## 三口径语义（2026-10-02 定，此前 README 表述是错的）

    那行历史写法是「`1228 项（单元 1228 / 浏览器层 82）`」——**自相矛盾**：
    单元数 = 总数，却又另说浏览器层 82。实测三口径为

        单元（--ignore=tests/browser）  ≠  全量（含 browser）  =  单元 + 浏览器层

    ⇒ 正确表述是「**全量（单元 + 浏览器层）**」。本守卫三条判据分别对真身：
    ① 括号内单元 == `--ignore=tests/browser` 收集数；
    ② 括号内浏览器层 == `tests/browser` 收集数；
    ③ 括号外总数 == 全量收集数（= ①+②，**不是**等于①）。
    """
    tree = _readme_tree_block()
    m = re.search(r"(\d+)\s*项（单元\s*(\d+)\s*/\s*浏览器层\s*(\d+)", tree)
    assert m, (
        "README 目录树里找不到「N 项（单元 N / 浏览器层 M）」形式的计数声明。"
        "该数字与「已实现功能」段的 `**N 项 pytest**` 是同一件事的两处写法，"
        "不许悄悄删掉或换措辞——若确实要改格式，请同步更新本守卫。"
    )
    total, unit, browser = int(m.group(1)), int(m.group(2)), int(m.group(3))

    def _collected(args: list[str]) -> int:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        mm = re.search(r"(\d+)\s+tests? collected", tail)
        assert mm, (
            f"未能从 `pytest --collect-only {' '.join(args)}` 输出解析收集数"
            f"（尾行：{tail!r}）。rc={proc.returncode}。"
            f"多半是 pytest 版本换了输出格式——**不许 skip**，请修解析正则。"
        )
        return int(mm.group(1))

    actual_unit = _collected(["--ignore=tests/browser"])
    actual_browser = _collected(["tests/browser"])
    actual_total = _collected([])

    bad = []
    if unit != actual_unit:
        bad.append(f"单元声称 {unit}，实际 {actual_unit}")
    if browser != actual_browser:
        bad.append(f"浏览器层声称 {browser}，实际 {actual_browser}")
    if total != actual_total:
        bad.append(f"总数声称 {total}，实际 {actual_total}")
    if total != unit + browser:
        bad.append(f"总数 {total} ≠ 单元 {unit} + 浏览器层 {browser}（自相矛盾）")
    assert not bad, (
        "README 目录树的计数与真身不符：\n  " + "\n  ".join(bad)
        + "\n新增/删除测试后请同步 README 目录树那行（真身：pytest --collect-only）。"
    )


def test_readme_tree_endpoint_count_matches_router():
    """目录树注释里的「/api/errors/* · N 端点」必须等于真身（与功能段同源判据）。"""
    tree = _readme_tree_block()
    m = re.search(r"/api/errors/\*\s*·\s*(\d+)\s*端点", tree)
    assert m, (
        "README 目录树里找不到「/api/errors/* · N 端点」声明。"
        "它与「已实现功能」段那处是同一件事的两处写法，不许只改一处或删掉。"
    )
    claimed = int(m.group(1))
    actual = _router_endpoint_count()
    assert claimed == actual, (
        f"README 目录树声称 {claimed} 个端点，实际 {actual} 个。"
        f"真身：medkit/routers/errors.py 的装饰器数。"
    )


def test_readme_tree_block_scan_face_is_not_empty():
    """元守卫：证明上面的扫描面真的抓到了目录树。

    没有这条，`_readme_tree_block()` 一旦返回空串（锚点被改、代码块被挪），
    上面两条守卫的 `re.search` 会**先在 `assert m` 处失败**——这还好；
    但若有人把两条守卫的 `assert m` 一起放宽，扫描面塌缩就会静默变成假绿。
    这条独立钉住「扫描面确实含那两行」，且**不依赖**上面两条的来源。
    """
    tree = _readme_tree_block()
    assert tree, (
        "README 目录树扫描面为空——锚点 `medkit/` 顶格行丢失或代码块被改动。"
        "上面的计数守卫会因此失去靶子（本守卫就是为拦住这种塌缩而存在）。"
    )
    assert len(tree.splitlines()) >= 10, (
        f"README 目录树只剩 {len(tree.splitlines())} 行（预期 ≥10）——"
        "扫描面疑似被写窄。"
    )
    # 关键成员：两条守卫各自依赖的形态，必须真的在场（独立于上面的 re.search）
    assert re.search(r"\d+\s*项（单元", tree), "目录树里没有测试计数行"
    assert re.search(r"/api/errors/\*\s*·", tree), "目录树里没有端点计数行"
