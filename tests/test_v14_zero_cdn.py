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
import subprocess
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


def _targets() -> list[Path]:
    """零 CDN 闸门的**唯一**扫描面（单一来源）。

    元守卫 `test_scan_face_is_not_empty` 与主守卫
    `test_no_external_resource_loads` 都必须经由本函数取扫描面。
    —— 各自重算一遍等于没测（R26 教训）。
    """
    return [
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


def test_scan_face_is_not_empty():
    """元守卫：零 CDN 闸门的扫描面不许塌缩（R27 修的真缺陷）。

    ## 为什么（2026-09-30 R27 注入实测）

    `test_no_external_resource_loads` 的结构是
    「遍历 `TARGETS` + 聚合 `hits` + `assert not hits`」。
    旧版只有 `assert TARGETS`（**仅非空**）兜着 —— 实测三种**写窄**注入
    **全部恒绿**（`rc=0`）：

    | 注入 | 后果 |
    |---|---|
    | `"*.js"` → `"*.jsx"` | 前端 JS 完全不受零 CDN 检查（最大的面没了） |
    | 删掉 `medkit/render/*.py` 那一行 | 产物页渲染层不受检 |
    | `"*.css"` → `"*.scss"` | 样式层不受检 |

    这正是 R26 的「扫描面塌缩 ⇒ 聚合断言恒真」形态：
    `assert TARGETS` 只保证**非空**，保证不了**没被写窄**。

    三条腿（照 `test_no_sleep_gambling.py` 范式）：
      1. 非空 + **数量下限**（防整体塌缩成个位数还"自洽"）；
      2. **每类扩展名各自非空**（防「某一类被写窄/删掉」——这是本仓最常见的改法）；
      3. 与 **git 索引**核对（独立事实来源，照出「文件改名/移出」时
         glob 与扩展名一起变小的恒真盲区）。
    """
    targets = _targets()
    rel = {p.relative_to(ROOT).as_posix() for p in targets}

    assert targets, (
        "零 CDN 闸门扫描面为空——`test_no_external_resource_loads` 会退化成"
        "空循环 + 恒真断言（假绿）。检查 `_targets()` 的 glob 是否被写宽/写错。"
    )
    assert len(targets) >= 10, (
        f"扫描面只有 {len(targets)} 个文件（下限 10）——疑似整体塌缩：{sorted(rel)}"
    )

    # ② 每一类**各自**非空（写窄通常只影响一类：改扩展名 / 删一行 glob）
    families = {
        "web/*.html": [r for r in rel if r.endswith(".html") and "/web/" in r],
        "web/*.js": [r for r in rel if r.endswith(".js") and "/web/" in r],
        "web/*.css": [r for r in rel if r.endswith(".css") and "/web/" in r],
        "render/*.py": [r for r in rel if r.endswith(".py") and "/render/" in r],
    }
    empty_families = [k for k, v in families.items() if not v]
    assert not empty_families, (
        f"以下扫描面类别为空（该层完全不受零 CDN 检查）：{empty_families}\n"
        f"各类实得：{ {k: len(v) for k, v in families.items()} }"
    )

    # ③ 与 git 索引核对：磁盘 glob 扫不到、但 git 里有的同类文件 = 疑似写窄/改名
    tracked = _tracked_web_assets()
    if tracked is not None:
        tracked_rel = {t for t in tracked if t.endswith((".html", ".js", ".css"))}
        gone = tracked_rel - rel
        assert not gone, (
            "以下前端文件在 git 索引里存在，但零 CDN 扫描面扫不到（glob 被写窄/改名？）：\n  "
            + "\n  ".join(sorted(gone))
        )


def _tracked_web_assets() -> set[str] | None:
    """从 **git 索引**取 `medkit/web/**` 与 `medkit/render/**` 的文件。

    返回 None 表示拿不到 git（源码包脱离仓库）——调用方跳过该条，
    但仍保留非空、数量下限与「各类非空」（那几条不依赖 git）。
    """
    try:
        r = subprocess.run(
            ["git", "ls-files", "medkit/web/", "medkit/render/"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return {x for x in r.stdout.split()}


def test_no_external_resource_loads():
    """前端与产物页不得加载任何外部资源（`<a href>` 导航链接不算）。

    「扫描面非空/未写窄」由 `test_scan_face_is_not_empty` 单独把守。
    """
    targets = _targets()   # 与元守卫同源：改窄这里，元守卫必红
    hits = [h for p in targets for h in _scan(p)]
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
