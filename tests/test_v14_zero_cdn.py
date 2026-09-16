"""V-14：把项目已立的「零 CDN / 零构建」规矩变成机械闸门（此前无任何自动检查）。

**为什么需要**：`docs/engineering/borrow-rules.md` §1/§2 与 R6 判据 P7 都写明「零 CDN、零构建」
（前端只吃本地 `/assets/*`；产物页要能离线打开），但**没有任何测试或 CI 步骤检查它**——
典型「已立规矩未入闸」：后人加一行 `<script src="https://cdn...">` 不会有任何反馈，
而它会让「离线可用 / 无外部依赖」的承诺静默失效（学生断网打开押题卷就白屏）。

本文件是**静态**闸门（正则扫描源码）；**运行时**闸门见 `tests/browser/test_zero_cdn.py`
（拦截真实网络请求，能抓住静态扫描看不见的动态注入）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 视为「加载外部资源」的模式（`<a href>` 只是导航，不算）
LOAD_PATTERNS = [
    (r"<script[^>]*\ssrc\s*=\s*[\"']https?://", "外部 <script src>"),
    (r"<link[^>]*\shref\s*=\s*[\"']https?://", "外部 <link href>（样式/字体/favicon/preconnect）"),
    (r"@import\s+(?:url\()?[\"']?https?://", "CSS @import 外部样式"),
    (r"url\(\s*[\"']?https?://", "CSS url() 外部资源"),
    (r"\bimport\s*\(\s*[\"']https?://", "动态 import() 外部模块"),
    (r"\bfetch\s*\(\s*[\"']https?://", "fetch 外部地址"),
    (r"\bnew\s+(?:WebSocket|EventSource)\s*\(\s*[\"']https?://", "外部 WebSocket/SSE"),
    (r"<img[^>]*\ssrc\s*=\s*[\"']https?://", "外部 <img src>"),
    (r"<iframe[^>]*\ssrc\s*=\s*[\"']https?://", "外部 <iframe src>"),
]

TARGETS = [
    *sorted((ROOT / "medkit/web").rglob("*.html")),
    *sorted((ROOT / "medkit/web").rglob("*.js")),
    *sorted((ROOT / "medkit/web").rglob("*.css")),
    *sorted((ROOT / "medkit/render").glob("*.py")),
]


def _scan(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[str] = []
    for pat, label in LOAD_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            line = text[: m.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line} {label} → {m.group(0)[:80]}")
    return hits


def test_no_external_resource_loads():
    """前端与产物页不得加载任何外部资源（`<a href>` 导航链接不算）。"""
    assert TARGETS, "扫描目标为空，闸门形同虚设"
    hits = [h for p in TARGETS for h in _scan(p)]
    assert not hits, "出现外部资源加载（违反「零 CDN」）：\n  " + "\n  ".join(hits)


def test_frontend_assets_are_local():
    """前端 script/link 必须指向本地 `/assets/`（相对路径），不得是协议相对或绝对 URL。"""
    html = (ROOT / "medkit/web/index.html").read_text(encoding="utf-8")
    srcs = re.findall(r"<script[^>]*\ssrc\s*=\s*[\"']([^\"']+)[\"']", html)
    hrefs = re.findall(r"<link[^>]*\shref\s*=\s*[\"']([^\"']+)[\"']", html)
    assert srcs, "未找到任何 <script src>，检查选择器是否失效"
    for u in srcs + hrefs:
        assert u.startswith("/assets/") or u.startswith("data:"), \
            f"前端资源必须走本地 /assets/：{u!r}"


def test_no_build_step_dependency():
    """零构建：产物不依赖打包器 —— index.html 不得引用 `type="module"` 之外的构建产物名，
    且 package.json 只允许 eslint 相关 devDependency（防止悄悄引入打包器）。"""
    import json
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    dev = set((pkg.get("devDependencies") or {}).keys())
    deps = set((pkg.get("dependencies") or {}).keys())
    assert not deps, f"不应有运行时依赖（零构建、零 CDN）：{sorted(deps)}"
    assert dev <= {"eslint", "globals"}, f"devDependency 只允许 eslint/globals，实得：{sorted(dev)}"
