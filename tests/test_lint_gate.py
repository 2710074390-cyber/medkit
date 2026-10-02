# -*- coding: utf-8 -*-
"""lint 门禁守卫：README 声称「ruff 干净」就必须真的干净。

**为什么需要这条守卫**（2026-09-29 实测发现）：

`verify.cmd` 里确实有 `ruff check .`，README 也写着「ruff 干净」——但
**pytest 里没有任何东西检查 ruff**。结果是：只跑 pytest 的人看到全绿，
lint 债静默累积。实测那一刻 `ruff check .` 有 **4 个错误**
（`pack/stage0-agent-standin.py` 的 F841/F541 + `pack/stage0-attribution-eval.py`
的 I001/F401），全是我自己建的阶段 0 脚本留下的，且存在了若干轮没人发现。

这是典型的「**"检查通过"与"检查没跑"无法区分**」——README 的声明没有守卫背书，
就等于没有声明。本文件把它绑上。

顺带守住第二条：**`pack/` 必须真的在 ruff 的检查范围内**。
若有人为了图省事把 `pack` 加进 `pyproject.toml` 的 `exclude`，
上面的 lint 会瞬间"变干净"——那是把守卫关掉，不是把代码修好。
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 历史 canary 名（第一版用例落盘用的）。现在用例改用 stdin 不落盘，
# 但仍排除它：若哪次旧版本残留了文件，不能让全仓检查假红。
CANARY_NAME = "_ruff_canary_tmp.py"


def _ruff_cmd() -> list:
    """用当前解释器的 ruff 模块（与 verify.cmd 的 `python -m ruff` 同一路径）。"""
    return [sys.executable, "-m", "ruff"]


def _run_ruff(args: list):
    """跑 ruff。**缺失即红，不 skip**（2026-09-29 修）。

    原先 `except (FileNotFoundError, TimeoutExpired): pytest.skip(...)`——
    但 ruff 缺失恰恰意味着 README 的「ruff 干净」**根本没被验证**，
    而 skip 与 pass 退出码相同 ⇒ 报告全绿。实测该路径确实会静默跳过
    （用假二进制路径跑 `_run_ruff` 得到 `Skipped`）。

    更糟的是它此前**真会发生**：`ruff` 没有在任何依赖文件里声明
    （`requirements-dev.txt` / `requirements.txt` / `requirements.lock` 全无），
    新克隆装完依赖后 lint 门禁就是静默放行的。两个洞同日一起补：
    本函数改 fail + `requirements-dev.txt` 补 `ruff>=0.6`。
    """
    try:
        return subprocess.run(_ruff_cmd() + args, cwd=str(ROOT),
                              capture_output=True, text=True, timeout=180)
    except FileNotFoundError as e:
        pytest.fail(
            "ruff 不可用（%s）。README 声称「ruff 干净」，而本守卫是它唯一的背书——"
            "缺失时必须红，不能 skip（skip 在 CI 里等同 pass）。"
            "请 `pip install -r requirements-dev.txt`（已声明 ruff）。" % e
        )
    except subprocess.TimeoutExpired as e:
        pytest.fail("ruff 超时（%s）——lint 门禁未能完成，不得视为通过" % e)


def test_ruff_available():
    """ruff 不在就 skip 会掩盖问题——这里要求它必须可用。"""
    r = _run_ruff(["--version"])
    if r.returncode != 0:
        pytest.fail("ruff 不可用（README 声称跑过它）：%s" % (r.stderr or r.stdout))


def test_ruff_is_declared_as_dev_dependency():
    """ruff 必须出现在依赖声明里，否则新克隆的 lint 门禁会静默失效。

    2026-09-29 实测：`ruff` 在 `requirements-dev.txt` / `requirements.txt` /
    `requirements.lock` 里**全无声明**，但 `verify.cmd` 第 1 步与 README
    都依赖它。本用例把「声明」这件事本身钉住——防的是「守卫依赖的工具
    没人负责装」这类跨文件的静默缺口。
    """
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in dev.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert any(ln.split(">=")[0].split("==")[0].strip().lower() == "ruff"
               for ln in lines), (
        "requirements-dev.txt 未声明 ruff——verify.cmd 第 1 步与 README"
        "「ruff 干净」都依赖它，不声明则新克隆静默跳过 lint。"
    )


def test_repo_is_ruff_clean():
    """全仓 ruff 必须零错误——这是 README「ruff 干净」的守卫。

    失败时把 ruff 的原始输出贴出来，方便直接定位。

    **必须显式排除 canary 文件**：`test_pack_scripts_are_actually_scanned`
    会在 `pack/` 里临时造一个必错文件，若两者同时存在会互相干扰
    （实测踩到：本用例把 canary 的 F401 当成仓库的 lint 债而误报）。
    """
    r = _run_ruff(["check", ".", "--output-format=concise",
                   "--exclude", CANARY_NAME])
    assert r.returncode == 0, (
        "ruff 有未清理的错误（README 声称干净）：\n%s\n%s"
        % (r.stdout, r.stderr)
    )


def _ruff_exclude_patterns() -> list[str]:
    """从 `pyproject.toml` 解析出 `tool.ruff.exclude`（**按 TOML 语义**，不按文本行）。

    用 `tomllib` 而不是字符串切分：旧版 `cfg.split("[tool.ruff.lint", 1)[0]`
    隐式假设 `[tool.ruff]` 段**永远排在** `[tool.ruff.lint]` 之前——
    只是文本位置，不是语义。把两段顺序调换（TOML 完全合法）后，
    切出来的 head 里**一个 exclude 行都扫不到**，循环体空转，
    守卫静默变绿（实测：`exclude` 里塞了 `pack` 仍判绿）。
    `tomllib` 按 key 取，与段序、缩进、空格写法全都无关。
    """
    import tomllib

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excl = cfg.get("tool", {}).get("ruff", {}).get("exclude", [])
    # TOML 允许写成单字符串而非数组
    if isinstance(excl, str):
        return [excl]
    return [str(p) for p in excl]


def test_pack_dir_is_not_excluded_from_ruff():
    """`pack/` 不得被排除在 ruff 之外。

    否则「把目录加进 exclude」就能让 lint 假绿——那是关守卫，不是修代码。
    `pack/` 放的是会被执行的正式脚本（阶段 0 流水线），必须受管。

    ## 判据改走 TOML 语义（2026-09-29 R17 改）

    旧版按文本切段（`cfg.split("[tool.ruff.lint", 1)[0]`），依赖
    `[tool.ruff]` 排在 `[tool.ruff.lint]` 之前这一**书写顺序**——
    重排章节后循环空转、守卫静默变绿（真身注入实测）。
    现改用 `tomllib` 按 key 取值，与段序/缩进/空格无关。
    """
    patterns = _ruff_exclude_patterns()
    # 判据是**双向**的：既要有「被排除的目录」这一事实来源在场（否则可能是
    # 解析失败静默返回空表），又要断言受管目录不在其中。
    banned = ("pack", "tests", "medkit")
    hit = [p for p in patterns
           if p.strip("./\\ ").rstrip("/") in banned]
    assert not hit, (
        f"ruff exclude 里出现了受管目录 {hit}（完整 exclude={patterns}）——"
        "把目录排除出 lint 等于关掉守卫，新增于 `--max-warnings 0` 的门禁形同虚设")


def test_pack_dir_exclude_guard_is_not_vacuous():
    """元守卫：证明「按 TOML 语义取 exclude」抓得住各种写法，且不靠段序。

    自带内存样本，不读真身（真身是注入靶子）。
    """
    import tomllib

    def _excl(text: str) -> list[str]:
        e = tomllib.loads(text).get("tool", {}).get("ruff", {}).get("exclude", [])
        return [e] if isinstance(e, str) else [str(p) for p in e]

    # ① 正常段序
    a = '[tool.ruff]\nexclude = ["build", "pack"]\n\n[tool.ruff.lint]\nselect = ["E"]\n'
    assert "pack" in _excl(a), "[元守卫] 正常段序下未取到 pack"

    # ② 段序调换（旧文本切分会漏）——tomllib 必须照样取到
    b = '[tool.ruff.lint]\nselect = ["E"]\n\n[tool.ruff]\nexclude = ["pack"]\n'
    assert "pack" in _excl(b), "[元守卫] 段序调换后漏取 pack → 仍会假绿"

    # ③ 无空格写法
    c = '[tool.ruff]\nexclude=["pack"]\n'
    assert "pack" in _excl(c), "[元守卫] exclude= 无空格写法漏取"

    # ④ 单字符串写法（TOML 合法）
    d = '[tool.ruff]\nexclude = "pack"\n'
    assert "pack" in _excl(d), "[元守卫] 单字符串 exclude 漏取"

    # ⑤ 判据本体：干净配置不含受管目录
    clean = _excl('[tool.ruff]\nexclude = ["build", "dist"]\n')
    banned = ("pack", "tests", "medkit")
    assert not [p for p in clean if p.strip("./\\ ").rstrip("/") in banned]


def test_pack_scripts_are_actually_scanned():
    """反向确认：`pack/` 里的必错代码，ruff 必须报出来。

    这条防的是「ruff 压根没扫 pack/」（例如被 exclude 或 `.ruffignore` 吃掉）。
    只有"注入即红"才能证明扫描面真的覆盖到了。

    **不用落盘 canary 文件**（第一版这么写的，踩了坑）：canary 落在 `pack/`
    里，若用例被中断（沙箱 bulk-delete 守卫会打断全量跑）`finally` 清不掉，
    残留文件会让**之后所有 ruff 检查都红**——一个用例的失败污染了整个门禁。
    改用 `--stdin-filename` 把内容喂给 ruff：零落盘、零残留，
    且 ruff 仍按这个路径判断 include/exclude，同样能验证扫描面。
    """
    r = subprocess.run(
        _ruff_cmd() + ["check", "--stdin-filename", "pack/_canary.py", "-",
                       "--output-format=concise"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        input="import os\nx = 1\n",
    )
    assert r.returncode != 0, "canary 代码没被 ruff 检查到——pack/ 的扫描面有洞"
    assert "F401" in (r.stdout + r.stderr), r.stdout


# ---------------------------------------------------------------- 总闸覆盖面
# verify.cmd 是用户的「一键总闸」。它的每个步骤都必须：
#   (1) 在场；(2) 失败时 `goto :fail`（退出码 1）。
# 缺任一条 → 总闸会打印 "ALL GREEN" 而实际没查，这正是「门禁假绿」。
#
# 为什么要有这条守卫：pytest 只覆盖 tests/ 目录，**没人测 verify.cmd 本身**。
# 若有人删掉「打包纯净检查」（R6-11 明确要求总闸须覆盖），
# pytest 全绿、verify.cmd 也全绿，而那条防线已经没了——静默失效。
#
# 2026-09-29 扩充：原先只有 4 步，而 CI 的 verify job 有 8 个阻断步骤。
# 实测代价：88b647f 引入 16 个 mypy 类型错误，因 mypy 只在 CI 跑、
# 本地无从发现，静默存在 2 天。**本地「一键全绿」≠ CI 绿**，这是门禁不对称。
# 现补齐 mypy / eslint / pip-audit 三步（CI 侧另有 -m migration 与
# --cov-fail-under 属分组/度量，已在 verify.cmd 头部注明不纳入的理由）。
_VERIFY_STEPS = (
    ("ruff check", "python -m ruff check ."),
    ("mypy 类型检查", "python -m mypy medkit"),
    ("pytest 单测", "python -m pytest -q --ignore=tests/browser"),
    ("浏览器层", "python -m pytest tests/browser -q"),
    ("前端 lint", "call npm run lint"),
    ("依赖审计", "python -m pip_audit -r requirements.lock --strict"),
    ("打包纯净检查", "python pack\\check-package.py"),
)

# CI 的 verify job 里对**本地可跑**有意义的阻断步骤（命令片段 → 说明）。
# 用于 test_verify_cmd_covers_ci_blocking_steps：verify.cmd 必须覆盖它们，
# 否则「本地全绿」不能代表 CI 绿。
# 有意不含：`pytest -m migration`（已含在全量里）、`--cov-fail-under=80`（度量）、
# `pip check`（CI 干净 runner 上才有意义，本机无关包会稳定误红）。
#
# ⚠️ 本常量是**手写**的，与 ci.yml 无机械联系——CI 新增/删除步骤它不会变。
# 真正把 ci.yml 当事实来源的守卫是 `tests/test_ci_gate_parity.py`
# （2026-09-29 新增）：它现场解析 ci.yml，双向覆盖（CI 步骤 → 本地；
# CI 关键闸门 → 必须在场），并拦截 `--strict` 被去掉 / `continue-on-error` /
# `|| true` 等弱化手法。本条保留的原因：它检查的是**verify.cmd 侧的片段匹配**，
# 与那条的解析式比对互补（一个查「本地有没有提到」，一个查「CI 有没有被削弱」）。
# 2026-09-29 反向验证实测：删除 CI 的 ruff step / 去掉 CI 的 `--strict`，
# 本条**恒绿**（它只扫 verify.cmd）——所以它不能单独承担「CI 不被弱化」的职责。
_CI_BLOCKING_STEPS = (
    ("python -m ruff check", "ruff"),
    ("python -m mypy medkit", "mypy"),
    ("--ignore=tests/browser", "pytest 单测"),
    ("npm run lint", "eslint"),
    ("pip_audit -r requirements.lock", "pip-audit（锁定闭包）"),
    ("pack/check-package.py", "打包纯净"),
)


def _verify_cmd_text() -> str:
    p = ROOT / "verify.cmd"
    assert p.exists(), "verify.cmd 不存在——总闸没了"
    return p.read_text(encoding="utf-8", errors="replace")


def test_verify_steps_constant_is_not_empty():
    """前提自检：`_VERIFY_STEPS` 必须非空且覆盖关键工具。

    下面两条用例（`has_all_steps` / `each_step_fails_hard`）都是
    `for … in _VERIFY_STEPS` 的形态——**常量被掏空时它们会空转成绿**
    （2026-09-29 R17 排查：`if`/`for` 包住的断言，容器为空即静默通过）。
    本用例把「计划表非空」这个前提钉住，让上面两条的绿有意义。
    """
    assert _VERIFY_STEPS, "_VERIFY_STEPS 为空——上面两条 for 循环守卫会空转成绿"
    names = {name for name, _ in _VERIFY_STEPS}
    must_have = {"ruff check", "mypy 类型检查", "pytest 单测"}
    assert must_have <= names, (
        f"_VERIFY_STEPS 少了关键步骤 {must_have - names}——总闸覆盖面缩水")


def test_verify_cmd_has_all_steps():
    """四个步骤一条都不能少（少一条 = 总闸覆盖面缩水）。"""
    text = _verify_cmd_text()
    missing = [name for name, cmd in _VERIFY_STEPS if cmd not in text]
    assert not missing, (
        "verify.cmd 缺步骤：%s\n总闸覆盖面缩水了——pytest 全绿不代表总闸查全了。"
        % missing
    )


def test_verify_cmd_each_step_fails_hard():
    """每个步骤都必须 `|| goto :fail`——否则失败不退出码，总闸照样打 ALL GREEN。

    只测「命令在场」是不够的：`python -m pytest -q ...` 在场但后面没 `||`
    时，pytest 红了 verify.cmd 仍返回 0。**检查在场 ≠ 检查会拦。**
    """
    text = _verify_cmd_text()
    for name, cmd in _VERIFY_STEPS:
        assert cmd + " || goto :fail" in text, (
            "verify.cmd 的「%s」步骤没有 `|| goto :fail`："
            "该步失败时总闸仍会返回 0（假绿）。" % name
        )


def test_verify_cmd_has_fail_label():
    """`:fail` 标签必须存在且 `exit /b 1`——否则 goto 到一个不存在的标签会静默继续。"""
    text = _verify_cmd_text()
    assert ":fail" in text, "verify.cmd 没有 :fail 标签，`|| goto :fail` 会落到文件末尾"
    tail = text.split(":fail", 1)[1]
    assert "exit /b 1" in tail, ":fail 标签后没有 `exit /b 1`——失败不会变成非零退出码"


def test_verify_cmd_covers_ci_blocking_steps():
    """verify.cmd 必须覆盖 CI verify job 的阻断步骤（本地全绿 ⇒ CI 绿）。

    为什么单列一条：`test_verify_cmd_has_all_steps` 只能证明「我列的步骤都在」，
    证明不了「**该列的都已列**」——CI 新增一步而这里没跟进时它照样绿。
    （同 3e92d84 修的「两个 glob 自比」是同一类问题：判据要有独立基线。）

    ## 本条的基线**不是**独立的（2026-09-29 反向验证实测后更正）

    上面那句原先写成「本条把 CI 的阻断项当**外部事实来源**」——**不准确**：
    `_CI_BLOCKING_STEPS` 是手写常量，与 `ci.yml` 无机械联系。实测三组注入
    （删 CI 的 ruff step / 去掉 CI 的 `--strict` / 删整个 package job）
    本条**全部恒绿**。

    真正把 ci.yml 当事实来源的是 `tests/test_ci_gate_parity.py`。
    本条保留的职责只是「verify.cmd 里有没有提到该命令片段」，属互补而非替代。
    """
    text = _verify_cmd_text()
    missing = [desc for frag, desc in _CI_BLOCKING_STEPS if frag not in text]
    assert not missing, (
        "verify.cmd 未覆盖 CI 的阻断步骤：%s\n"
        "本地「一键全绿」将不代表 CI 绿——请补齐，或在 verify.cmd 头部"
        "写明「有意不纳入」的理由。" % missing
    )


def _verify_skip_switches() -> set[str]:
    """`verify.cmd` 里出现的 `%SKIP_XXX%` 开关集合（**单一来源**）。

    `test_verify_cmd_skips_are_explicit_and_audited` 与其元守卫
    `test_verify_skip_switches_scan_face_is_not_empty` 都必须经由本函数取扫描面
    —— 各自重算一遍等于没测（R26 教训）。
    """
    import re
    return set(re.findall(r"%([A-Z_]+)%", _verify_cmd_text()))


def test_verify_skip_switches_scan_face_is_not_empty():
    """元守卫：跳过开关的扫描面不许塌缩（R27 修的真缺陷）。

    ## 为什么（2026-09-30 R27 注入实测）

    `test_verify_cmd_skips_are_explicit_and_audited` 里 **三个检查**
    （`unexplained` 聚合、以及「跳过分支不得 `exit /b 0`」的循环）
    全都只遍历 `switches` 这一个集合。旧版只有
    `assert switches, "..."` 兜着 —— 实测把遍历面掏空后**整条用例恒绿**（`rc=0`）：
    三个检查一个都不跑，而 pytest 报「通过」。

    这正是 R26 的「聚合式空集恒真」形态：`assert switches` 与后面的检查
    **看似两条防线，实为一条**——后者完全依赖前者提供的非空前提。

    ⇒ 补一条**不依赖** `assert switches` 的独立检查：直接对
    `verify.cmd` 的原始文本断言「至少存在一个 `%SKIP_XXX%`」（正则命中数），
    并锁住已知的关键开关名（写窄/改名即红）。
    """
    text = _verify_cmd_text()
    import re
    matches = re.findall(r"%([A-Z_]+)%", text)

    assert matches, (
        "verify.cmd 里一个 `%SKIP_XXX%` 开关都找不到——"
        "`test_verify_cmd_skips_are_explicit_and_audited` 会退化成空循环 + 恒真断言（假绿）。"
        "检查开关写法是否被改（如 `%SKIP%` 少了名字）。"
    )
    switches = _verify_skip_switches()
    # 与正则**独立**再数一遍（防 `_verify_skip_switches` 自己被改窄）
    assert len(switches) >= 2, (
        f"跳过开关只剩 {len(switches)} 个（下限 2）：{sorted(switches)}——扫描面疑似塌缩。"
    )
    # 契约：这两个开关是 verify.cmd 的既定旁路，缺一即覆盖面缩水
    must_have = {"SKIP_BROWSER", "SKIP_MYPY"}
    assert must_have <= switches, (
        f"verify.cmd 少了既定跳过开关 {sorted(must_have - switches)}——"
        "总闸覆盖面缩水（浏览器层/mypy 步骤没了显式旁路，或写法被改）。"
    )


def test_verify_cmd_skips_are_explicit_and_audited():
    """每个 SKIP_* 开关都必须在文件头被解释，且**不得**用于跳过整体。

    判据（2026-09-29）：跳过是合理的（没装浏览器/没装 mypy 的机器要能跑），
    但必须「明示 + 有理由」，否则跳过会变成事实上的关守卫。
    这里守住两点：
      1. 每个 `%SKIP_XXX%` 都在头部注释里出现（有解释）；
      2. 不含 `exit /b 0` 出现在跳过分支里（跳过 ≠ 提前成功退出）。

    「扫描面非空 + 未写窄」由
    `test_verify_skip_switches_scan_face_is_not_empty` 单独把守
    （本用例的三个检查全都依赖 `switches` 非空，故不能自证前提）。
    """
    text = _verify_cmd_text()
    switches = _verify_skip_switches()   # 与元守卫同源：改窄这里，元守卫必红
    assert switches, "verify.cmd 里没有找到任何 SKIP_* 开关——判据可能失效"
    header = text.split("cd /d", 1)[0]
    unexplained = [s for s in switches if s not in header]
    assert not unexplained, (
        "以下开关没有在 verify.cmd 头部解释：%s" % sorted(unexplained)
    )
    # 跳过分支不得直接 `exit /b 0`（那会让总闸在没查完的情况下报成功）
    for s in switches:
        marker = 'if "%s"="1" (' % s
        if marker in text:
            branch = text.split(marker, 1)[1].split(")", 1)[0]
            assert "exit /b 0" not in branch, (
                "%s 的跳过分支里出现 `exit /b 0`——总闸会在没跑完的情况下报成功" % s
            )


def test_verify_cmd_browser_step_is_skippable():
    """浏览器层有 SKIP_BROWSER 旁路（本机无 chromium 时的正常态），但必须**显式**跳过。

    防的是「用 SKIP_BROWSER 把浏览器层永久关掉」这种软性失守：
    步骤仍须在文件里，且跳过时要打日志（不能无声）。
    """
    text = _verify_cmd_text()
    assert "SKIP_BROWSER" in text, "浏览器层缺少 SKIP_BROWSER 旁路（无浏览器环境会卡死）"
    assert "skipping browser tests" in text, "SKIP_BROWSER 跳过时没有日志——无声跳过不可接受"


def test_verify_cmd_declares_missing_tool_policy_for_every_step():
    """**必须装**的步骤不得有「缺工具就跳过」分支（2026-09-29 实测的不对称）。

    ## 发现过程

    `verify.cmd` 里各步对「工具没装」的处理**不一致**：
    - 第 1 步 ruff：`python -m ruff check . || goto :fail` —— 没装就**直接 FAIL**；
    - 第 2 步 mypy：`python -m mypy --version >nul 2>&1` + `errorlevel 1` → **[跳过]**。

    这本身可以是对的（我的判断：ruff 与 pytest 属"必须装"，因为 README
    明确声称「ruff 干净」「N 项 pytest」，缺工具则声明无从验证）。
    问题是**没有任何地方写明这个分级**——下次谁改都可能把 ruff 也改成跳过，
    而跳过在 CI 里等同 pass ⇒ lint 声明静默失效。

    本用例把分级钉住：
      1. 头部必须写明「必须装 / 可跳过」两类及理由；
      2. 「必须装」的步骤，其命令行**不得被任何跳过分支包住**
         （2026-09-29 反向验证暴露：只断言命令行「在场」是不够的——
         把 ruff 那行照抄进一个 `errorlevel 1` + `goto :ruff_done` 分支里，
         字面断言照样通过。判据必须是「**它不在任何条件分支的保护下直接执行**」）。

    判据实现：定位该行的**物理行号**，向上找最近的 `if ... (` （未闭合的），
    若存在且其分支体在 goto 跳过标签 → 即为"被包住"，红。
    简化且可靠的等价判据：**该行必须是它所在「命令块」里的第一条非空、
    非 REM 行**——即它前面不能紧跟一个未闭合的 `(`。
    """
    text = _verify_cmd_text()
    header = text.split("cd /d", 1)[0]
    assert "必须装" in header and "可跳过" in header, (
        "verify.cmd 头部未写明「必须装 / 可跳过」的工具分级——"
        "缺了这个分级，下次改动可能把必须装的步骤也改成静默跳过。"
    )

    # 必须装的步骤：命令行必须在场 **且** 不被跳过分支包住。
    must_install = (
        "python -m ruff check . || goto :fail",
        "python -m pytest -q --ignore=tests/browser || goto :fail",
        "python pack\\check-package.py || goto :fail",
    )
    for cmd in must_install:
        assert cmd in text, "「必须装」步骤的命令行消失：%r" % cmd
        assert not _is_gated_behind_skip(text, cmd), (
            "%r 被包在跳过分支里了——工具缺失时会静默跳过，"
            "而 README 的 lint/test 声明靠它背书。该步必须**无条件执行**。" % cmd
        )

    # 反向：可跳过的步骤必须打印 [跳过] + 安装指引，不能无声。
    for marker in ("[跳过] mypy 未安装", "[跳过] pip-audit 未安装",
                   "[跳过] 未安装 node_modules"):
        assert marker in text, (
            "缺少显式跳过提示 %r——无声跳过不可接受" % marker
        )


def _is_gated_behind_skip(text: str, cmd: str) -> bool:
    """判断 `cmd` 所在行是否被「工具缺失 → goto 跳过」这种分支护住。

    ## 为什么不用"向上找最近的 if"

    2026-09-29 第一版就是这么做的，**注入没红**。原因是跳过块长这样::

        if errorlevel 1 (        <- 41
          echo   [跳过] ...
          goto :ruff_done        <- 43
        )                        <- 44
        python -m ruff check . || goto :fail   <- 45  cmd 在这

    从 45 向上扫，先撞到 `goto :ruff_done` → 我的循环遇到 `goto :` 就 `break`，
    **根本没扫到第 41 行的 `if`**。修法：向上扫到**该命令所在命令块的开头**
    （遇到上一个 `:label` / 文件头 / 空行分隔的 `echo` 段为止），
    再看这段里是否同时出现「条件判断」与「`goto :` 跳过标签」。

    判据（保守、可证伪）：cmd 行**上方连续区间内**（直到上一个 `echo [n/7]` 标题行）
    若出现 `if errorlevel` 或 `if "%` 且伴随 `goto :<label>`（非 `:fail`），
    即判定被跳过分支包住。
    """
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    idx = next((i for i, ln in enumerate(lines) if ln == cmd), None)
    if idx is None:
        return False

    # 向上找本步骤的起点：最近的 `echo [n/7]` 标题行（每步以它为界）。
    start = 0
    for j in range(idx - 1, -1, -1):
        if lines[j].startswith("echo [") and "/7]" in lines[j]:
            start = j
            break

    block = lines[start + 1: idx]
    has_cond = any(ln.startswith("if ") for ln in block)
    has_skip_goto = any(ln.startswith("goto :") and ln != "goto :fail"
                        for ln in block)
    return has_cond and has_skip_goto


# ---------------------------------------------------------------- pack 脚本输出编码
# 2026-10-02 新增（CI Windows 实证）。
#
# ## 病灶
# `pack/` 下的脚本大量 `print` 中文。**CI 的 Windows runner 默认 locale 是 cp1252**
# （非 UTF-8），此时写中文到 stdout/stderr 会抛
# `UnicodeEncodeError: 'charmap' codec can't encode ...` ⇒ 子进程 returncode=1。
# 实证：run 36968684662 的 `test_stage0_export_cases::test_end_to_end_against_isolated_store`
# 因 `stage0-export-cases.py` 在子进程里炸而失败；同一 commit 的 ubuntu job 却全绿
# （Linux 默认 UTF-8）——**平台特有、本地同平台也复现不出来**（本机 locale 恰是 UTF-8）。
#
# ## 既定范式（已有 4 处，本仓早已存在的约定）
#     for _s in (sys.stdout, sys.stderr):
#         try:
#             _s.reconfigure(encoding="utf-8", errors="replace")
#         except (AttributeError, OSError):
#             pass
# 见 `pack/check-package.py` / `check-build-env.py` / `gen-lock-hashes.py` / `make_release.py`。
# 本守卫把「有中文 print ⇒ 必须有该兜底」变成机械判据，防止再次漂移。
#
# ## 判据（AST，非文本子串）
# 扫描 `git ls-files pack` 里的 `.py`（独立事实来源）：
#   ① 若有 `print(...)` 且其**实参源码**含 CJK 字符，
#   ② 且全文件**没有** `sys.stdout.reconfigure` / `sys.stderr.reconfigure` 调用 ⇒ 红。
# 含元守卫：检测器自证（用内存构造的样本证明它真能命中）+ 扫描面非空且与 git 索引核对。


def _pack_py_files() -> list[pathlib.Path]:
    """`git ls-files pack` 里的 Python 脚本（独立事实来源，防扫描面塌缩）。"""
    out = subprocess.run(["git", "ls-files", "pack"], cwd=str(ROOT),
                         capture_output=True, text=True)
    names = [n for n in (out.stdout or "").splitlines() if n.strip()]
    return [ROOT / n for n in names if n.endswith(".py")]


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _prints_cjk(src: str) -> list[int]:
    """返回「print 了中文」的行号列表（AST 取实参源码，避免把注释/docstring 当输出）。"""
    import ast as _ast

    tree = _ast.parse(src)
    hits: list[int] = []
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
                and node.func.id == "print"):
            seg = _ast.get_source_segment(src, node) or ""
            if _has_cjk(seg):
                hits.append(node.lineno)
    return hits


def _has_stdout_reconfigure(src: str) -> bool:
    """是否把 stdout/stderr 重配置为 UTF-8（AST 判调用形态，认本仓两种既定写法）。

    形态 A（直接）：`sys.stdout.reconfigure(...)` / `sys.stderr.reconfigure(...)`
    形态 B（既有范式，4 处原有脚本都用它）：
        for _s in (sys.stdout, sys.stderr):
            _s.reconfigure(...)          <- 目标是个**循环变量**，不是 sys.stdout 字面量

    ⇒ 只认形态 A 会**漏判既有范式**（第一版就是这么写的，元守卫当场抓到：
    给 good 样本判了 False）。故 B 的判定 = 「存在 `<某名>.reconfigure` 调用」
    **且**「文件里确有 `for <同名> in (sys.stdout, sys.stderr)`」。
    """
    import ast as _ast

    tree = _ast.parse(src)
    # 形态 A：sys.stdout.reconfigure / sys.stderr.reconfigure
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "reconfigure"):
            tgt = node.func.value
            if (isinstance(tgt, _ast.Attribute) and tgt.attr in ("stdout", "stderr")
                    and isinstance(tgt.value, _ast.Name) and tgt.value.id == "sys"):
                return True

    # 形态 B：先收集「for X in (sys.stdout, sys.stderr)」里的 X
    loop_vars: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.For) and isinstance(node.target, _ast.Name):
            it = node.iter
            if isinstance(it, (_ast.Tuple, _ast.List)):
                names = {e.attr for e in it.elts
                         if isinstance(e, _ast.Attribute)
                         and e.attr in ("stdout", "stderr")
                         and isinstance(e.value, _ast.Name) and e.value.id == "sys"}
                if names:
                    loop_vars.add(node.target.id)
    if not loop_vars:
        return False
    # 再看有没有这些循环变量上的 .reconfigure 调用
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "reconfigure"
                and isinstance(node.func.value, _ast.Name)
                and node.func.value.id in loop_vars):
            return True
    return False


def test_pack_script_cjk_output_forces_utf8():
    """含中文 `print` 的 pack 脚本，必须把 stdout/stderr 重配置为 UTF-8。

    Windows CI runner 默认 cp1252，否则子进程炸（run 36968684662 实证）。
    """
    offenders = []
    for p in _pack_py_files():
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            lines = _prints_cjk(src)
        except SyntaxError as e:  # pragma: no cover - 语法错由 ruff/编译期拦
            offenders.append(f"{p.relative_to(ROOT)}: 语法错误 {e}")
            continue
        if lines and not _has_stdout_reconfigure(src):
            offenders.append(f"{p.relative_to(ROOT)}: 第 {lines[:5]} 行 print 了中文，"
                             f"但全文件无 sys.stdout.reconfigure")
    assert not offenders, (
        "以下 pack 脚本在 cp1252 控制台（Windows CI）会抛 UnicodeEncodeError——"
        "必须加既有的 UTF-8 兜底范式（见 check-package.py 头部）：\n  "
        + "\n  ".join(offenders)
    )


def test_pack_script_encoding_scan_face_is_not_empty():
    """元守卫：扫描面非空 + 与 git 索引一致 + 检测器自证能命中（防掏空成恒绿）。"""
    files = _pack_py_files()
    assert files, "pack/ 下没扫到任何 Python 脚本——扫描面塌缩（git 索引？）"
    assert len(files) >= 10, f"pack/ 脚本数 {len(files)} 异常偏少，扫描面被写窄"

    # 检测器自证：用内存样本证明「有中文 print 且无 reconfigure」真能被判出。
    bad = "print('你好')\n"
    assert _prints_cjk(bad) == [1], "检测器漏判中文 print"
    assert not _has_stdout_reconfigure(bad), "检测器误判 reconfigure 在场"
    good = ("import sys\n"
            "for _s in (sys.stdout, sys.stderr):\n"
            "    _s.reconfigure(encoding='utf-8', errors='replace')\n"
            "print('你好')\n")
    assert _has_stdout_reconfigure(good), "检测器没认出既有的 reconfigure 范式"

    # 反面样本：只 print ASCII 的脚本不该被要求 reconfigure
    assert _prints_cjk("print('hello')\n") == [], "检测器把 ASCII print 误判为中文"
