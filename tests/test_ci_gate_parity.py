# -*- coding: utf-8 -*-
"""CI 闸门守卫：`.github/workflows/ci.yml` 自身不许被静默弱化。

## 为什么需要这条守卫（2026-09-29 反向验证实测）

`tests/test_lint_gate.py` 有 7 条 `test_verify_cmd_*` 用例，全部建立在同一句
假设上：**「CI 的 verify job 是可信基线，本地 verify.cmd 必须覆盖它」**。
但**这个基线自身没有任何守卫**——`_CI_BLOCKING_STEPS` 是一份**手写常量**，
与 `ci.yml` 之间没有任何机械联系。

实测（5 组注入，全部恒绿）：

| 注入 | 期望 | 实测 |
|---|---|---|
| 删除 CI 的 ruff step | 红 | **绿** |
| CI `pip_audit` 去掉 `--strict` | 红 | **绿** |
| CI `check-package.py` 去掉 `--strict` | 红 | **绿** |
| CI mypy 改成 `\\|\\| true`（永不失败） | 红 | **绿** |
| 删除整个 CI package job | 红 | **绿** |

后果比单条用例失效更严重：**「本地全绿 ⇒ CI 绿」这条保证链的源头无人看守。**
把 CI 的断言步骤删掉或改成软失败，本地一切照旧全绿，报告也全绿。

## 本守卫做什么

把 `ci.yml` 当**事实来源**（而非手写常量），机械提取它的阻断步骤，然后：

1. **覆盖性**：CI verify job 的每个阻断步骤，必须被 `verify.cmd` 覆盖，
   或在 `_CI_ONLY_STEPS` 里登记并写明「为什么本地不需要/不应该跑」；
2. **豁免不陈尸**（对应 `test_no_sleep_gambling` 的同名判据）：
   `_CI_ONLY_STEPS` 的每条必须仍对应 CI 里的一个真实步骤；
3. **不许软失败**：CI 里不得出现 `continue-on-error: true`，不得让
   断言步骤用 `|| true` 把失败吞掉；`|| echo "::warning::…"` 只允许出现在
   `_SOFT_FAIL_ALLOWED` 登记的步骤里（每步必须写明它为什么可以软）。
4. **解析面自检**：解析出的 step 数必须 ≥ 一个下限，否则视为解析器退化
   （正则/缩进随 YAML 格式演进失效 → 主守卫静默变恒绿，即 R9 修的那类缺陷）。

## 为什么不 import yaml

`pyyaml` **没有在任何依赖文件里声明**（`requirements.txt` /
`requirements.lock` / `requirements-dev.txt` 全无），但本机恰好装有（6.0.3）。
引它就会复制 `ruff` 那个坑——守卫依赖的工具没人负责装，新克隆上静默失效。
（教训见 `test_lint_gate.py::test_ruff_is_declared_as_dev_dependency`。）

故这里手写一个**窄用途**的缩进解析器：只提取 `jobs → steps → (name|uses|run|if)`，
不追求通用 YAML 兼容。缩进层级本身就是契约，由 `test_ci_parser_surface_is_not_vacuous`
钉住它没退化。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
VERIFY_CMD = ROOT / "verify.cmd"

# 安装 / 环境准备类步骤：只装东西、不断言，不算阻断步骤。
# 判据按 name 前缀（保守：宁多算为阻断，不少算——把安装类误当阻断只会误红，
# 而漏判一个真断言步骤会让守卫静默失效）。
_INSTALL_PREFIXES = ("Install", "Setup", "Cache", "Build", "Upload", "Download")

# CI 独有、本地 verify.cmd 有意不跑的阻断步骤。
# 键 = CI step 的 name（必须与 ci.yml 逐字一致）；值 = 为什么本地不需要跑。
# 理由必须可检验：要么「已被本地全量 pytest 覆盖」属实，要么「本机环境使然」可复现。
_CI_ONLY_STEPS: dict[str, str] = {
    "Lock closure consistency (S2-23)": (
        "tests/test_lock_closure.py 已含在本地全量 pytest 里（verify.cmd 第 3 步）；"
        "CI 单列它只是为了「先失败先报」的分组，不增加覆盖。"
        "同理由见 verify.cmd 头部对 `pytest -m migration` 的说明。"
    ),
    "Migration tests (IMP-10)": (
        "`pytest -m migration` 的用例已含在本地全量 pytest 里（verify.cmd 第 3 步）；"
        "CI 单列属分组，不增加覆盖。verify.cmd 头部已写明该理由。"
    ),
    "Dependency integrity (IMP-10)": (
        "`pip check` 只在 CI 的干净 runner 上有意义：开发者本机常装无关包"
        "（实测本机 crawl4ai 0.8.9 要 lxml~=5.3 而项目锁 6.1.0），"
        "纳入本地总闸会稳定误红 → 逼人学会忽略总闸。"
        "verify.cmd 头部已写明「有意不纳入」。"
    ),
}

# 允许用 `|| echo "::warning::…"` 软化的步骤（软失败不阻断 job）。
# 键 = CI step 的 name；值 = 为什么这一步软失败是可接受的。
_SOFT_FAIL_ALLOWED: dict[str, str] = {
    "Lint (frontend, U-17)": (
        "仅 `npm audit` 那条软（eslint 的 `npm run lint` 仍硬拦）："
        "npm 侧只装了 eslint，不进产物，上游告警不应阻断构建。"
        "ci.yml:57 已就地写明该理由。"
    ),
    "Dependency vulnerability audit (U-16)": (
        "仅**第二段**（`requirements.txt` 浮动声明侧）软：它只作"
        "「上游是否已出修复版」的前瞻提示，判定闸门是第一段的 lock 侧（硬拦）。"
        "ci.yml:69 已就地写明该理由。"
    ),
}

# 解析面下限：ci.yml 当前 3 job / 23 step（其中 verify 12 步）。
# 下限取比现状低、但远高于「解析器退化到近似 0」的值。
_MIN_STEPS = 12
_MIN_BLOCKING = 6

# CI **必须保留**的关键闸门（命令级，含强度标记）。
#
# ## 为什么必须有这个方向（2026-09-29 反向验证实测）
#
# 本文件此前的判据是单向的：「CI 的每个阻断步骤 → 本地要有对应物」。
# 实测三组注入**全部恒绿**：
#   · 删掉 CI 的 ruff step      → CI 少一步，循环里没有它，逐一检查照样通过；
#   · CI `pip_audit` 去掉 `--strict`  → 命令还在、本地也有，但闸门变松；
#   · CI `check-package.py` 去掉 `--strict` → 同上。
# 根因：**「命令在场」≠「闸门强度在场」**，且**两个集合一起变小则相等**。
# 这与 3e92d84 修掉的「两个 glob 自比」是同一类：判据缺少独立基线。
#
# 故这里把「CI 必须有的闸门」写成显式清单，**含强度标记**（`--strict` 等）。
# 删步骤、去 `--strict`、换命令，任一都会红。
_CI_REQUIRED_GATES: dict[str, str] = {
    "python -m ruff check .": "ruff 全仓 lint（README 声称「ruff 干净」的唯一背书）",
    "python -m mypy medkit": "mypy 类型基线（「只升不降」）",
    "npm run lint": "前端 eslint（零构建下的静态防线）",
    "python -m pip_audit -r requirements.lock --strict": (
        "依赖漏洞审计，**必须审锁定闭包且带 --strict**——"
        "去掉 --strict 后「有漏洞不留痕」，去掉 -r 后会退回审浮动声明（审计图 ≠ 产物图）"
    ),
    "python -m pytest -q --timeout=300 --ignore=tests/browser": (
        "单测主通道，**必须显式排除浏览器层**（同进程收集会因 Playwright "
        "session 级同步上下文令 asyncio 用例必失败）"
    ),
    "python pack/check-package.py": "打包纯净检查（verify job 侧，无产物时自行跳过）",
    "python pack/check-package.py --strict": (
        "打包纯净检查（package job 侧，**必须带 --strict**：无产物即失败，"
        "否则等于空操作——S2-19 就是修这个）"
    ),
}


# ---------------------------------------------------------------------------
# 解析器（窄用途，见文件头「为什么不 import yaml」）
# ---------------------------------------------------------------------------


def _parse_jobs(text: str) -> dict[str, list[dict]]:
    """返回 `{job_name: [step, ...]}`，step 形如
    `{"name":…, "uses":…, "run": str|list, "if":…}`。

    只认固定缩进层级（契约）：
      ``jobs:``                  顶层
      ``  <job>:``               job（2 空格）
      ``    steps:``             步骤段（4 空格）
      ``      - name:``/`- uses:` 步骤（6 空格）
      ``        <key>:``         步骤属性（8 空格）
      ``          <续行>``        ``run: |`` 块的正文（10 空格）
    """
    jobs: dict[str, list[dict]] = {}
    cur_job: str | None = None
    cur_step: dict | None = None
    in_jobs = False
    in_steps = False
    block_key: str | None = None

    for raw in text.split("\n"):
        if raw.strip() == "jobs:":
            in_jobs, in_steps, cur_job, cur_step = True, False, None, None
            continue
        # 其它顶层键（name:/on:）→ 退出 jobs 段
        if not raw.startswith(" ") and re.match(r"^[A-Za-z_][\w-]*:", raw):
            in_jobs, in_steps, cur_job, cur_step = False, False, None, None
            block_key = None
            continue
        if not in_jobs:
            continue

        m = re.match(r"^  ([A-Za-z_][\w-]*):\s*$", raw)
        if m:
            cur_job = m.group(1)
            jobs[cur_job] = []
            in_steps, cur_step, block_key = False, None, None
            continue

        if raw.strip() == "steps:":
            in_steps, cur_step, block_key = True, None, None
            continue

        if not in_steps or cur_job is None:
            continue

        m = re.match(r"^      - (name|uses):\s*(.*)$", raw)
        if m:
            cur_step = {}
            jobs[cur_job].append(cur_step)
            key, val = m.group(1), m.group(2).strip()
            if key == "name":
                cur_step["name"] = val
            else:
                cur_step["uses"] = val.split("#")[0].strip()
            block_key = None
            continue

        if cur_step is None:
            continue

        # run: | 块的正文续行。
        # 判据 = 「缩进比属性行（8 空格）更深且有内容」，**不能写死 10 空格**：
        # 实测踩过——`|| echo "::warning::…"` 写成 12 空格缩进时，
        # `^          \S`（恰好 10 空格后接非空白）匹配失败，该行被静默丢弃，
        # 于是 `test_soft_fail_allowlist_entries_are_still_used` 误判为「豁免已成陈尸」。
        if block_key and re.match(r"^ {9,}\S", raw):
            cur_step.setdefault(block_key, []).append(raw.strip())
            continue

        m = re.match(r"^        ([A-Za-z_][\w-]*):\s*(.*)$", raw)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val in ("|", ">"):
                cur_step[key] = []
                block_key = key
            else:
                cur_step[key] = val
                block_key = None
            continue

        block_key = None

    return jobs


def _load_jobs() -> dict[str, list[dict]]:
    assert CI_YML.exists(), "找不到 .github/workflows/ci.yml——CI 定义没了"
    return _parse_jobs(CI_YML.read_text(encoding="utf-8"))


def _run_text(step: dict) -> str:
    r = step.get("run")
    if r is None:
        return ""
    return " ; ".join(r) if isinstance(r, list) else r


def _step_name(step: dict) -> str:
    return step.get("name") or step.get("uses", "<未命名>")


def _is_blocking(step: dict) -> bool:
    """有 run 且不是安装/准备类 → 视为断言步骤（GitHub Actions 默认非零即失败）。"""
    if "run" not in step:
        return False
    return not _step_name(step).startswith(_INSTALL_PREFIXES)


def _verify_cmd_text() -> str:
    assert VERIFY_CMD.exists(), "verify.cmd 不存在——本地总闸没了"
    return VERIFY_CMD.read_text(encoding="utf-8", errors="replace")


def _norm_cmd(s: str) -> str:
    """把命令归一成可比较的形态。

    处理三类真实差异（都实测误红过）：
      · 行尾 `|| goto :fail`（verify.cmd 的硬拦后缀）→ 截断掉；
      · 行尾行延续符 `\\`；
      · `call npm …`（.cmd 里调外部命令）→ 去掉 `call ` 前缀；
      · 反斜杠路径分隔符 → 统一为正斜杠。
    """
    s = s.split("||", 1)[0]
    s = s.replace("\\", "/")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^call\s+", "", s, flags=re.I)
    return s.strip()


def _core_cmd(s: str) -> str:
    """取命令的「核心」——解释器/命令 + 模块 + 头一个参数，用于宽松匹配。

    为什么必须宽松（三处实测误红）：
      · CI 的 `Test` 带 `--cov=medkit --cov-report=… --cov-fail-under=80`，
        而 verify.cmd 那条**有意**不带覆盖率参数（理由写在 verify.cmd 头部）；
      · `pack\\check-package.py`（verify.cmd）vs `pack/check-package.py`（CI），
        只差一个分隔符（已由 `_norm_cmd` 归一）；
      · verify.cmd 的行尾 `|| goto :fail` 会被 token 切分吃掉一部分
        （实测得到 `python pack/check-package.py || goto`，与 CI 侧不等）。
        `_norm_cmd` 已截断 `||` 之后的内容。

    取前 3 个 token：足以辨识「解释器 + 模块」这一层（`python -m pytest`、
    `python -m ruff`、`npm run lint`），同时容忍尾部参数差异。
    代价是区分不了 `pytest -q` 与 `pytest tests/browser` —— 可接受：
    本用例要拦的是「**整条命令被删/换掉**」，不是参数微调。
    """
    return " ".join(_norm_cmd(s).split(" ")[:3])


# ---------------------------------------------------------------------------
# 0. 解析面自检（防解析器退化 → 主守卫静默恒绿）
# ---------------------------------------------------------------------------


def test_ci_parser_surface_is_not_vacuous():
    """解析器必须真的解析出 CI 的结构。

    没有这条，解析正则/缩进契约一旦与 YAML 实际格式脱钩，下面所有断言
    （都建立在「解析结果」之上）会**静默变成恒绿**——因为空集合没有元素
    需要覆盖。这正是 R9 在 `test_v15_frontend_split.py` 修掉的同一类缺陷。

    三条断言：
      1. 至少 3 个 job（verify / package / browser）；
      2. step 总数 ≥ `_MIN_STEPS`；
      3. 阻断步骤数 ≥ `_MIN_BLOCKING`，且 verify job 里必须能认出它那 9 个断言步骤。
    """
    jobs = _load_jobs()
    assert len(jobs) >= 3, f"只解析出 {len(jobs)} 个 job（{sorted(jobs)}）——解析器可能退化了"
    for required in ("verify", "package", "browser"):
        assert required in jobs, f"缺少 job `{required}`——解析结果：{sorted(jobs)}"

    total = sum(len(s) for s in jobs.values())
    assert total >= _MIN_STEPS, (
        f"只解析出 {total} 个 step（下限 {_MIN_STEPS}）——"
        "缩进契约或正则已与 ci.yml 实际格式脱钩，本文件的全部断言会静默失效。"
    )

    blocking = [s for job in jobs.values() for s in job if _is_blocking(s)]
    assert len(blocking) >= _MIN_BLOCKING, (
        f"只解析出 {len(blocking)} 个阻断步骤（下限 {_MIN_BLOCKING}）——"
        "要么 CI 被大幅削弱，要么 `_is_blocking` 的判据坏了。"
    )


def test_parser_handles_block_scalar_and_inline_run():
    """解析器必须同时吃下 `run: |` 块与 `run: 单行` 两种形态（自测，不依赖真文件）。"""
    sample = (
        "jobs:\n"
        "  demo:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Install deps\n"
        "        run: |\n"
        "          pip install -r requirements.txt\n"
        "          pip install ruff\n"
        "      - name: Assert\n"
        "        run: python -m ruff check .\n"
    )
    jobs = _parse_jobs(sample)
    assert list(jobs) == ["demo"], jobs
    steps = jobs["demo"]
    assert len(steps) == 3, steps
    assert steps[0]["uses"].startswith("actions/checkout")
    assert steps[1]["name"] == "Install deps"
    assert steps[1]["run"] == ["pip install -r requirements.txt", "pip install ruff"]
    assert steps[2]["run"] == "python -m ruff check ."
    # 安装类被排除、断言类被计入
    assert _is_blocking(steps[2]) is True
    assert _is_blocking(steps[1]) is False


# ---------------------------------------------------------------------------
# 1. 覆盖性：CI 的每个阻断步骤都要有本地对应物（或登记豁免）
# ---------------------------------------------------------------------------


def test_every_ci_blocking_step_is_covered_locally():
    """CI verify job 的每个阻断步骤，必须被 verify.cmd 覆盖或在 `_CI_ONLY_STEPS` 登记。

    判据用**命令片段**而非 name：name 可以随手改，命令是行为。
    每个 CI 阻断步骤必须至少有一个「它的 run 里出现、且 verify.cmd 里也出现」
    的实义片段；否则必须登记在 `_CI_ONLY_STEPS`。

    ## 为什么不复用 `test_lint_gate._CI_BLOCKING_STEPS`

    那是**手写常量**，与 ci.yml 无机械联系——CI 新增/删除步骤它不会变。
    本条从 ci.yml 现场解析，是独立事实来源。
    """
    jobs = _load_jobs()
    verify_cmd = _verify_cmd_text()

    # verify.cmd 里的实义命令行 → 归一化核心片段集合
    cmd_cores = set()
    for ln in verify_cmd.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("REM") or ln.startswith("echo"):
            continue
        if not ln.startswith(("python ", "call ", "npm ", "pip ")):
            continue
        cmd_cores.add(_core_cmd(ln))

    uncovered = []
    for step in jobs["verify"]:
        if not _is_blocking(step):
            continue
        name = _step_name(step)
        if name in _CI_ONLY_STEPS:
            continue
        run = _run_text(step)
        # 该步骤里的实义命令段（去注释）
        segs = [s.strip() for s in run.split(";")]
        segs = [s for s in segs if s and not s.startswith("#")]
        hit = any(_core_cmd(seg) in cmd_cores for seg in segs)
        if not hit:
            uncovered.append(
                f"{name}\n      CI run: {run[:100]!r}\n"
                f"      归一化核心: {[_core_cmd(s) for s in segs]!r}"
            )

    assert not uncovered, (
        "以下 CI 阻断步骤在 verify.cmd 里找不到对应命令，也未登记在 _CI_ONLY_STEPS：\n  "
        + "\n  ".join(uncovered)
        + "\n含义：CI 会拦、本地不会 —— 本地「一键全绿」不再代表 CI 绿。"
        "请补进 verify.cmd，或在 _CI_ONLY_STEPS 登记并写明理由。"
    )


def test_ci_only_exemptions_are_still_needed():
    """`_CI_ONLY_STEPS` 不得堆陈尸：每条豁免必须仍对应 ci.yml 里的一个真实步骤。

    否则豁免表只增不减，慢慢把守卫稀释成空壳——与
    `test_no_sleep_gambling.test_allowlist_entries_are_still_needed` 同一判据。
    """
    jobs = _load_jobs()
    live = {_step_name(s) for s in jobs["verify"]}
    stale = [name for name in _CI_ONLY_STEPS if name not in live]
    assert not stale, (
        "以下豁免在 ci.yml 的 verify job 里已找不到对应步骤（改名/删了）——"
        "请从 _CI_ONLY_STEPS 删除，否则它只是噪音：\n  " + "\n  ".join(stale)
    )


def test_ci_only_exemptions_have_substantive_reasons():
    """每条豁免的理由必须写到「可检验」的粒度，不能是一句「不需要」。

    判据：理由长度 ≥ 30 字，且提及具体文件/命令/环境事实（含 `.py` / `` ` `` / 数字）。
    防的是「为了让它绿而随手加一行豁免」。
    """
    weak = []
    for name, reason in _CI_ONLY_STEPS.items():
        if len(reason) < 30 or not re.search(r"[.`\d]", reason):
            weak.append(f"{name} → {reason!r}")
    assert not weak, (
        "以下豁免理由过于笼统（要求 ≥30 字且提及具体文件/命令/事实）：\n  "
        + "\n  ".join(weak)
    )


# ---------------------------------------------------------------------------
# 1b. 关键闸门必须在场（方向与上一条相反，见 `_CI_REQUIRED_GATES` 的说明）
# ---------------------------------------------------------------------------


def _all_ci_commands() -> set[str]:
    """CI 所有 job 里出现过的实义命令行（归一化，保留选项）。"""
    jobs = _load_jobs()
    out: set[str] = set()
    for steps in jobs.values():
        for step in steps:
            raw = step.get("run")
            lines = raw if isinstance(raw, list) else ([raw] if raw else [])
            for ln in lines:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                # 一行里可能有 `A ; B`（列表是单条，但单行 run 里可能有 ; 或 && ）
                for seg in re.split(r";|&&", ln):
                    seg = _norm_cmd(seg)
                    if seg:
                        out.add(seg)
    return out


def _is_prefix(needle: list[str], hay: list[str]) -> bool:
    """`needle` 是否作为**前缀**出现在 `hay` 里（逐 token 比较）。

    用于「清单登记的命令 + 强度标记」→ CI 实际命令：
    CI 侧可在其后追加度量参数（覆盖率等），但清单里的每个 token
    必须按序落在开头。`--strict` / `--ignore=tests/browser` 被删即不匹配。
    """
    return len(hay) >= len(needle) and hay[: len(needle)] == needle


def test_ci_keeps_every_required_gate():
    """CI 必须**包含** `_CI_REQUIRED_GATES` 里的每条命令，**含强度标记**（`--strict` 等）。

    ## 覆盖的真实缺陷（2026-09-29 实测，三组注入全绿）

    | 注入 | 期望 | 修前 |
    |---|---|---|
    | 删掉 CI 的 ruff step | 红 | **绿** |
    | CI `pip_audit` 去掉 `--strict` | 红 | **绿** |
    | CI `check-package.py` 去掉 `--strict` | 红 | **绿** |

    根因两条，缺一不可：
      1. **判据方向单一**：只测「CI 的步骤 → 本地要有对应物」。
         删掉 CI 一步，该步不在循环里，逐一检查自然全过。
         （两个集合一起缩小则相等——同 3e92d84 修的「两个 glob 自比」。）
      2. **在场 ≠ 会拦**：`--strict` 去掉后命令仍在、本地也有对应物，
         但闸门强度已消失——原判据只看命令名，看不见强度标记。

    本条把清单写成**命令级 + 强度标记**，两个方向都钉住。
    """
    cmds = _all_ci_commands()
    missing = []
    for cmd, why in _CI_REQUIRED_GATES.items():
        # 前缀匹配：清单登记的是「命令 + 必须保留的强度标记」，
        # CI 侧可以在其后追加参数（如 Test 步骤的 `--cov=… --cov-fail-under=80`，
        # 那些是**度量**参数，不属于闸门强度）。而**清单里的每个 token 都必须
        # 按序出现在 CI 的某条命令里**——故 `--strict` / `--ignore=tests/browser`
        # 被删即红。
        toks = cmd.split(" ")
        if not any(_is_prefix(toks, c.split(" ")) for c in cmds):
            missing.append(f"{cmd!r}\n      职责：{why}")
    assert not missing, (
        "CI 里找不到以下关键闸门命令（被删除或被弱化）：\n  "
        + "\n  ".join(missing)
        + "\n注意：`--strict` 这类强度标记是判据的一部分——"
        "去掉它命令仍在场，但闸门已经松了，本条会红。"
    )


def test_required_gate_clauses_are_accurate():
    """`_CI_REQUIRED_GATES` 的强度标记必须是**真的在 CI 里**，不是我想当然写的。

    防「清单与事实脱钩」：若我登记 `--strict` 而 CI 里其实没有，
    `test_ci_keeps_every_required_gate` 会红；但若我**登记时漏写了**强度标记
    （例如把带 `--strict` 的命令登记成不带），清单就会比事实更松。
    本用例逐条比对：登记的每条命令的**每个选项 token**，都必须能在 CI 的
    某条命令里找到同序出现。
    """
    cmds = _all_ci_commands()

    def _is_subsequence(needle: list[str], hay: list[str]) -> bool:
        i = 0
        for tok in hay:
            if i < len(needle) and tok == needle[i]:
                i += 1
        return i == len(needle)

    bad = []
    for cmd in _CI_REQUIRED_GATES:
        toks = cmd.split(" ")
        if not any(_is_subsequence(toks, c.split(" ")) for c in cmds):
            bad.append(cmd)
    assert not bad, (
        "以下登记的命令在 CI 里找不到对应（可能漏写了强度标记，或 CI 已改）——"
        "请核对 ci.yml 后修正清单：\n  " + "\n  ".join(bad)
    )


def test_required_gate_entries_are_still_meaningful():
    """每条必需闸门都必须写明「职责」——一句话说清它守什么。

    防「为了让它绿而把命令从清单里挪走」：挪走会同时触发
    `test_ci_keeps_every_required_gate`；而**理由栏空白**则是另一种稀释。
    """
    weak = [cmd for cmd, why in _CI_REQUIRED_GATES.items() if len(why) < 15]
    assert not weak, "以下闸门缺少实质职责说明（≥15 字）：\n  " + "\n  ".join(weak)


# ---------------------------------------------------------------------------
# 2. 不许软失败：CI 的断言步骤必须真的能拦住
# ---------------------------------------------------------------------------


def test_ci_has_no_continue_on_error():
    """CI 里不得出现 `continue-on-error: true`——那会让步骤失败也不阻断 job。

    这是最直接的「把守卫关掉」手法：断言还在，但红了不算数。
    """
    text = CI_YML.read_text(encoding="utf-8")
    offenders = [
        f"{i}: {ln.strip()}"
        for i, ln in enumerate(text.split("\n"), 1)
        if re.match(r"^\s*continue-on-error:\s*true\s*$", ln)
    ]
    assert not offenders, (
        "ci.yml 出现 `continue-on-error: true`——该步骤失败不阻断 job（假绿）：\n  "
        + "\n  ".join(offenders)
    )


def test_ci_assertion_steps_do_not_swallow_failures():
    """断言步骤不得用 `|| true` / `|| :` 之类把失败吞掉。

    `|| echo "::warning::…"` 是**允许**的（见 `_SOFT_FAIL_ALLOWED`），
    但只允许出现在登记过的步骤里——否则「加个 echo 就把闸门软化」无人察觉。
    """
    jobs = _load_jobs()
    swallowed = []
    unjustified_soft = []

    for step in jobs["verify"]:
        if not _is_blocking(step):
            continue
        name = _step_name(step)
        run = _run_text(step)

        # (a) 任何 `|| true` / `|| :` / `|| exit 0` 一律不许
        for m in re.finditer(r"\|\|\s*(true|:|exit\s+0)\b", run):
            swallowed.append(f"{name}: …{run[max(0, m.start()-30):m.end()]}…")

        # (b) `|| echo` 软化必须在白名单里
        if re.search(r"\|\|\s*echo\b", run) and name not in _SOFT_FAIL_ALLOWED:
            unjustified_soft.append(name)

    assert not swallowed, (
        "以下 CI 断言步骤用 `|| true` 之类的兜底吞掉了失败（该步永远不会红）：\n  "
        + "\n  ".join(swallowed)
    )
    assert not unjustified_soft, (
        "以下 CI 步骤用 `|| echo` 软化了失败，但未登记在 _SOFT_FAIL_ALLOWED：\n  "
        + "\n  ".join(unjustified_soft)
        + "\n若确属「只作告警」的旁路，请登记并写明为什么它不承担闸门职责。"
    )


def test_soft_fail_allowlist_entries_are_still_used():
    """`_SOFT_FAIL_ALLOWED` 不得堆陈尸：每条必须仍对应一个真的用了 `|| echo` 的步骤。"""
    jobs = _load_jobs()
    live_soft = {
        _step_name(s)
        for s in jobs["verify"]
        if _is_blocking(s) and re.search(r"\|\|\s*echo\b", _run_text(s))
    }
    stale = [name for name in _SOFT_FAIL_ALLOWED if name not in live_soft]
    assert not stale, (
        "以下软失败豁免已不再对应任何用了 `|| echo` 的步骤——请从 "
        "_SOFT_FAIL_ALLOWED 删除：\n  " + "\n  ".join(stale)
    )


def test_soft_fail_allowlist_but_hard_gate_survives_in_same_step():
    """软化的步骤里，**判定闸门那条命令必须仍是硬的**。

    这是本条最要紧的判据：`Dependency vulnerability audit` 有两段
    （lock 侧硬、浮动侧软），`Lint (frontend)` 有两段（eslint 硬、npm audit 软）。
    只要把**两段都**软化，步骤就成了纯告警——但若只检查「有没有 `|| echo`」，
    它仍会通过。故要求：软化的步骤里至少有一条实义命令**没有**被 `||` 兜住。

    反向验证：把 `Dependency vulnerability audit` 的 lock 段也加上
    `|| echo`，本条必须变红。
    """
    jobs = _load_jobs()
    broken = []
    for step in jobs["verify"]:
        name = _step_name(step)
        if name not in _SOFT_FAIL_ALLOWED:
            continue
        raw = step.get("run")
        lines = raw if isinstance(raw, list) else [raw]

        # 逐「物理行」判定软硬：把反向续行（上一行以 \ 结尾）折回来
        joined: list[str] = []
        for ln in lines:
            ln = ln.strip()
            if joined and joined[-1].endswith("\\"):
                joined[-1] = joined[-1].rstrip("\\").strip() + " " + ln
            else:
                joined.append(ln)

        hard = []
        for ln in joined:
            if not ln or ln.startswith("#"):
                continue
            if re.search(r"\|\|\s*(echo|true|:|exit\s+0)\b", ln):
                continue                      # 该行是软的
            hard.append(ln)

        if not hard:
            broken.append(f"{name}（全部命令都被 || 兜住）")

    assert not broken, (
        "以下步骤被**整段软化**了（没有任何一条命令是硬拦的）——"
        "它已退化为纯告警，不再承担闸门职责：\n  " + "\n  ".join(broken)
    )
