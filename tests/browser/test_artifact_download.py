"""产物「下载到本地」浏览器用例（2026-09-27，Playwright，零 LLM）。

回归用户反馈「网站不能正常下载文档」：
`/api/projects/{pid}/files/{name}` 被审查文档定义为「产物下载」端点，但服务端不下发
Content-Disposition、前端产物卡片又对 md/txt/html 一律 `target="_blank"` 且不加
`download` → 点「题库 MD / 复习手册 MD / Anki 文本」只会开一个纯文本标签页，拿不到文件。

现行契约（后端 `project_file(dl=1)` + 前端 `artifactLinks` 配套）：
- 文档产物（.md/.txt/.json）主操作 → 触发浏览器下载（`?dl=1` → attachment）；
- HTML 产物主操作 → 新标签页在线打开（同源渲染才能答题/判分/打印），次操作 ⇩ → 下载；
- 中文产物名照常可下载。

用独立 pid + 独立 subject，避免与同会话其它浏览器用例串扰。
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

_PID = "dl_browser_test"
_SUBJECT = "下载用例科目"


def _seed(server_home: Path, pid: str = _PID) -> None:
    """向隔离服务器的 projects 目录播种一个含产物的项目。"""
    base = Path(server_home) / "projects" / pid
    out = base / "最终产物"
    out.mkdir(parents=True, exist_ok=True)
    (base / "meta.json").write_text(json.dumps({
        "pid": pid, "subject": _SUBJECT, "exam": "期末", "target": 5,
        "toggles": {"qbank": True, "paper": True, "review": True},
        "stage": "done", "created": "2026-09-27T00:00:00"}, ensure_ascii=False),
        encoding="utf-8")
    (out / "qbank.md").write_text("# 题库\n\n1. 题干\n", encoding="utf-8")
    (out / "复习手册.md").write_text("# 复习手册\n", encoding="utf-8")
    (out / "押题卷.html").write_text(
        "<!doctype html><meta charset='utf-8'><h1>押题卷</h1>", encoding="utf-8")
    (out / "anki_export.txt").write_text("正面\t反面\n", encoding="utf-8")


def _open_project(page, server_url: str, pid: str = _PID) -> None:
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.evaluate("(pid) => openRecentProject(pid)", pid)
    page.wait_for_selector("#pd_arts .artchip", timeout=15000)


def _chip(page, text: str):
    return page.locator("#pd_arts .artchip", has_text=text).first


def test_document_artifact_downloads_on_primary_action(page, server_url, server_home):
    """文档产物（.md/.txt）主操作必须触发下载——这是原缺陷的核心。"""
    _seed(server_home)
    _open_project(page, server_url)

    for label, expect_name in (("题库 MD", "qbank.md"), ("Anki 文本", "anki_export.txt")):
        chip = _chip(page, label)
        assert chip.count() == 1, f"未找到产物卡片「{label}」"
        with page.expect_download(timeout=15000) as dl:
            chip.locator("a.artmain").click()
        got = dl.value.suggested_filename
        assert got == expect_name, f"「{label}」下载文件名应为 {expect_name}，实际 {got}"

    # 中文名产物：下载文件名不得被编码破坏
    with page.expect_download(timeout=15000) as dl:
        _chip(page, "手册 MD").locator("a.artmain").click()
    assert dl.value.suggested_filename == "复习手册.md", dl.value.suggested_filename


def test_html_artifact_opens_online_and_can_be_downloaded(page, server_url, server_home):
    """HTML 产物：主操作仍「在线打开」（同源渲染），次操作 ⇩ 可下载到本地。"""
    _seed(server_home)
    _open_project(page, server_url)

    chip = _chip(page, "押题卷")
    assert chip.count() == 1, "未找到押题卷产物卡片"

    with page.expect_popup(timeout=15000) as pop:
        chip.locator("a.artmain").click()
    opened = pop.value
    opened.wait_for_load_state("domcontentloaded", timeout=15000)
    # URL 里中文名是百分号编码的，比对前先解码
    assert "押题卷.html" in urllib.parse.unquote(opened.url), opened.url
    assert "押题卷" in opened.content(), "在线打开未渲染出产物内容"
    opened.close()

    with page.expect_download(timeout=15000) as dl:
        chip.locator("a.artalt").click()
    assert dl.value.suggested_filename.endswith(".html"), dl.value.suggested_filename
