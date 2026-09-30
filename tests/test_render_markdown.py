"""WP-9：md.js 本地极简 Markdown 渲染器（富文本 + XSS 安全）单测（node vm 执行）。

## 为什么 node 缺失时是 `fail` 而不是 `skip`

项目 skip 分级（技能 `gate-falsifiability`）：**环境缺失只在 `CI=true` 时 skip，
本地缺即红**。原写法是 `@pytest.mark.skipif(shutil.which("node") is None, …)`——
本机没装 node 时这几条「富文本渲染 / XSS 安全」用例会**静默消失**，
而 README 仍声称覆盖（这正是本项目最忌讳的「检查没跑却当成检查通过」）。
同一取向见 `tests/test_r8w_p2_frontend.py`（跑不了 node 就 fail）。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node() -> str:
    """取 node 可执行路径；**本机没有就 fail**（CI 里才允许 skip，见模块 docstring）。"""
    exe = shutil.which("node")
    if exe:
        return exe
    if os.environ.get("CI") == "true":
        pytest.skip("CI 环境未预装 node（本机开发环境必须有）")
    pytest.fail("本机没有 node —— md.js 的富文本/XSS 用例会静默消失，"
                "这是「检查没跑」而不是「检查通过」")


def test_md_render_rich_and_xss():
    node = _node()
    code = (
        "const fs=require('fs'); const vm=require('vm');"
        "const src=fs.readFileSync(process.argv[1],'utf8');"
        "const sandbox={window:{}}; vm.runInNewContext(src, sandbox);"
        "const md=sandbox.window.mdRender;"
        "const out=md('# 标题\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |\\n\\n**bold** and `code`');"
        "const x=md('<img src=x onerror=alert(1)><script>alert(1)</script>**ok**');"
        "console.log(out); console.log(x);"
    )
    r = subprocess.run([node, "-e", code, str(ROOT / "medkit" / "web" / "js" / "md.js")],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) >= 2
    out, x = lines[0], lines[1]
    assert "<table>" in out and "<h2>" in out and "<b>bold</b>" in out
    assert "<script>" not in x and "<img" not in x and "<b>ok</b>" in x
