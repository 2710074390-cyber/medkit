"""WP-9：md.js 本地极简 Markdown 渲染器（富文本 + XSS 安全）单测（node vm 执行）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行 md.js")
def test_md_render_rich_and_xss():
    code = (
        "const fs=require('fs'); const vm=require('vm');"
        "const src=fs.readFileSync(process.argv[1],'utf8');"
        "const sandbox={window:{}}; vm.runInNewContext(src, sandbox);"
        "const md=sandbox.window.mdRender;"
        "const out=md('# 标题\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |\\n\\n**bold** and `code`');"
        "const x=md('<img src=x onerror=alert(1)><script>alert(1)</script>**ok**');"
        "console.log(out); console.log(x);"
    )
    r = subprocess.run(["node", "-e", code, str(ROOT / "medkit" / "web" / "js" / "md.js")],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) >= 2
    out, x = lines[0], lines[1]
    assert "<table>" in out and "<h2>" in out and "<b>bold</b>" in out
    assert "<script>" not in x and "<img" not in x and "<b>ok</b>" in x
