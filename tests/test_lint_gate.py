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
