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
    try:
        return subprocess.run(_ruff_cmd() + args, cwd=str(ROOT),
                              capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        pytest.skip("ruff 不可用：%s" % e)


def test_ruff_available():
    """ruff 不在就 skip 会掩盖问题——这里要求它必须可用。"""
    r = _run_ruff(["--version"])
    if r.returncode != 0:
        pytest.fail("ruff 不可用（README 声称跑过它）：%s" % (r.stderr or r.stdout))


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


def test_pack_dir_is_not_excluded_from_ruff():
    """`pack/` 不得被排除在 ruff 之外。

    否则「把目录加进 exclude」就能让 lint 假绿——那是关守卫，不是修代码。
    `pack/` 放的是会被执行的正式脚本（阶段 0 流水线），必须受管。
    """
    cfg = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    head = cfg.split("[tool.ruff.lint", 1)[0]
    for line in head.splitlines():
        if line.strip().startswith("exclude"):
            for bad in ("pack", ".", "tests"):
                assert '"%s"' % bad not in line, \
                    "ruff exclude 里出现了 %r，会让 lint 假绿：%s" % (bad, line)


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
# 若有人删掉第 [4/4] 步（打包纯净检查，R6-11 明确要求总闸须覆盖），
# pytest 全绿、verify.cmd 也全绿，而那条防线已经没了——静默失效。
_VERIFY_STEPS = (
    ("ruff check", "python -m ruff check ."),
    ("pytest 单测", "python -m pytest -q --ignore=tests/browser"),
    ("浏览器层", "python -m pytest tests/browser -q"),
    ("打包纯净检查", "python pack\\check-package.py"),
)


def _verify_cmd_text() -> str:
    p = ROOT / "verify.cmd"
    assert p.exists(), "verify.cmd 不存在——总闸没了"
    return p.read_text(encoding="utf-8", errors="replace")


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


def test_verify_cmd_browser_step_is_skippable():
    """浏览器层有 SKIP_BROWSER 旁路（本机无 chromium 时的正常态），但必须**显式**跳过。

    防的是「用 SKIP_BROWSER 把浏览器层永久关掉」这种软性失守：
    步骤仍须在文件里，且跳过时要打日志（不能无声）。
    """
    text = _verify_cmd_text()
    assert "SKIP_BROWSER" in text, "浏览器层缺少 SKIP_BROWSER 旁路（无浏览器环境会卡死）"
    assert "skipping browser tests" in text, "SKIP_BROWSER 跳过时没有日志——无声跳过不可接受"
