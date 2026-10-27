"""题库同步面板（`web/js/bank-sync.js`）的浏览器层用例。

## 为什么这层必须存在

同步面板是**纯前端拼装**的（表格 / 勾选 / 结果汇总都由 JS 模板字符串生成），
`test_banksync.py` 只覆盖到 HTTP 层——面板自己点不动、勾选逻辑反了、结果文案错位，
单测全绿也发现不了。而本仓先例明确：`letters` / `ankiPreview` 那类跨域调用
「闸门管不到，只在点击时炸」。

## 为什么用 `page.route` 造桩，而不是真的连 GitHub

1. 用例必须离线可跑（CI 无外网、也不该被匿名限流左右结果）；
2. 桩能把**边界状态**摆出来（`已同步` / `有更新` / `离线`），真远端给不了这些组合；
3. 真实网络路径已由 `core/banksync.py` 的重试 / 限流逻辑与开发期端到端走查覆盖。

桩只拦 `**/api/bank/sync/*`，其余请求（静态资源、`/api/bank/banks`）照常打到本地后端子进程。
"""

from __future__ import annotations

import json

SEL_OPEN = "#tab-study button[onclick*='bankSyncOpen']"

_SOURCES = {
    "ok": True, "error": "", "error_kind": "",
    "repo": "2710074390-cyber/MedAgentWork",
    "url": "https://github.com/2710074390-cyber/MedAgentWork",
    "fetched_at": "2026-10-27T00:00:00", "remote_total": 6490,
    "remote_updated_at": "2026-10-27T00:27:02", "local_only": False,
    "sources": [
        {"path": "最终产物/batch032/ALL_questions_FIXED.json", "batch": "batch032",
         "subject": "外科", "sha": "s32", "size": 257074, "est_count": 297,
         "state": "changed", "bank_id": "bk1", "synced_at": "2026-10-26T10:00:00", "count": 300},
        {"path": "最终产物/batch033/ALL_questions_FIXED.json", "batch": "batch033",
         "subject": "内科", "sha": "s33", "size": 474976, "est_count": 443,
         "state": "synced", "bank_id": "bk2", "synced_at": "2026-10-26T10:00:00", "count": 460},
    ],
}

_RUN = {
    "ok": True, "added": 12, "updated": 285, "skipped": 3, "failed": 0, "unchanged": 0,
    "remote_total": 6490,
    "banks": [{"bank_id": "bk1", "name": "外科（MedAgentWork · batch032）",
               "subject": "外科", "count": 297}],
    "results": [{"path": "最终产物/batch032/ALL_questions_FIXED.json", "batch": "batch032",
                 "subject": "外科", "status": "synced", "bank_id": "bk1", "count": 297,
                 "added": 12, "updated": 285, "skipped": 3}],
}


def _stub(page, *, sources: dict | None = None, run: dict | None = None) -> None:
    body = json.dumps(sources or _SOURCES, ensure_ascii=False)
    page.route("**/api/bank/sync/sources*",
               lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    page.route("**/api/bank/sync/run",
               lambda route: route.fulfill(
                   status=200, content_type="application/json",
                   body=json.dumps(run or _RUN, ensure_ascii=False)))


def _open(page, server_url: str) -> None:
    page.goto(server_url + "/#study", wait_until="domcontentloaded")
    page.wait_for_selector(SEL_OPEN, timeout=15000)
    page.click(SEL_OPEN)
    page.wait_for_selector("#md_body table tbody tr", timeout=15000)


def test_panel_lists_sources_with_status(page, server_url):
    """面板按批次列出远端题库，并区分「有更新 / 已同步」。"""
    _stub(page)
    _open(page, server_url)
    rows = page.query_selector_all("#md_body table tbody tr")
    assert len(rows) == 2
    text = page.inner_text("#md_body")
    assert "batch032" in text and "外科" in text and "297" in text
    assert "有更新" in text and "已同步" in text
    assert "6490" in text                      # 远端总量（来自 registry_meta）


def test_selection_toggles(page, server_url):
    """「只选未同步 / 有更新」必须**跳过已同步**那行——这是重复下载的开关。"""
    _stub(page)
    _open(page, server_url)
    checked = "#md_body input[data-bank-sync]:checked"
    assert page.eval_on_selector_all(checked, "els => els.length") == 1   # 默认只勾「有更新」

    page.click("text=全选")
    assert page.eval_on_selector_all(checked, "els => els.length") == 2
    page.click("text=全不选")
    assert page.eval_on_selector_all(checked, "els => els.length") == 0
    page.click("text=只选未同步 / 有更新")
    assert page.eval_on_selector_all(checked, "els => els.length") == 1


def test_run_reports_counts_and_refreshes_bank_list(page, server_url):
    """同步后必须给出逐 batch 结果（新增/更新/跳过），而不是只弹一个 toast。"""
    _stub(page)
    _open(page, server_url)
    page.click("#btn_bank_sync_run")
    page.wait_for_selector("text=同步完成", timeout=15000)
    text = page.inner_text("#md_body")
    assert "新增 12" in text and "更新 285" in text and "跳过 3" in text
    assert "batch032" in text


def test_offline_panel_shows_banner_instead_of_blank(page, server_url):
    """远端不可达时，面板要说明原因并列出**上次同步记录**，不能空白一片。"""
    offline = {
        "ok": False, "error": "连不上 GitHub", "error_kind": "network",
        "repo": "2710074390-cyber/MedAgentWork",
        "url": "https://github.com/2710074390-cyber/MedAgentWork",
        "fetched_at": "", "remote_total": 0, "remote_updated_at": "", "local_only": True,
        "sources": [{"path": "最终产物/batch032/ALL_questions_FIXED.json", "batch": "batch032",
                     "subject": "外科", "sha": "s32", "size": 0, "est_count": 300,
                     "state": "offline", "bank_id": "bk1",
                     "synced_at": "2026-10-26T10:00:00", "count": 300}],
    }
    _stub(page, sources=offline)
    _open(page, server_url)
    text = page.inner_text("#md_body")
    assert "读不到远端清单" in text and "连不上 GitHub" in text
    assert "离线" in text and "batch032" in text
