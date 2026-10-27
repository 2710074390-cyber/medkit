"""手机端 PWA（`mobile/`）端到端守卫：导入 → 刷题 → 错题归纳 → 复习。

这一层存在的理由：`mobile/` 是**独立于 MedKit 服务**的静态应用，所有逻辑跑在浏览器里。
单元测试（pytest）看不到它，静态检查也只查语法。没有这个文件，"手机上能不能真的刷题"
就只能靠人肉点——而人肉点不出"重复导入是否覆盖复习进度"这种回归。

复用 conftest 的 `browser` 夹具（因此同样享受 SKIP_BROWSER / 缺引擎时的统一旁路），
但**不用 `server_url`**：这里要的是静态文件服务，不是 MedKit 后端。
（`browser` 依赖 `server_url`，所以后端仍会被拉起——代价约 2s，换来一致的跳过逻辑。）
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_MOBILE = Path(__file__).resolve().parents[2] / "mobile"
_SAMPLE = _MOBILE / "test" / "sample-bundle.json"


class _QuietHandler(SimpleHTTPRequestHandler):
    """静音 http.server：默认实现每个请求都往 stderr 打一行，测试输出会被淹没。"""

    def log_message(self, fmt, *args):  # noqa: A003  基类签名如此
        pass


@pytest.fixture(scope="module")
def mobile_url():
    handler = partial(_QuietHandler, directory=str(_MOBILE))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _open(browser, base: str):
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
    )
    pg = ctx.new_page()
    pg.set_default_timeout(10000)
    pg.goto(f"{base}/index.html")
    pg.wait_for_selector(".tabbar")
    return ctx, pg


def _idb(pg, store: str):
    """读 IndexedDB 的整个对象仓（用于断言"复习进度没被导入覆盖"这类不可见状态）。"""
    return pg.evaluate(
        """async (store) => {
            const db = await new Promise((res, rej) => {
                const r = indexedDB.open('medkit-mobile');
                r.onsuccess = () => res(r.result);
                r.onerror = () => rej(r.error);
            });
            const tx = db.transaction(store, 'readonly');
            const os = tx.objectStore(store);
            return await new Promise((res) => {
                const q = os.getAll();
                q.onsuccess = () => res(q.result);
                q.onerror = () => res([]);
            });
        }""", store)


def _import(pg, sample: Path = _SAMPLE) -> None:
    pg.click('.tabbar button[data-tab="data"]')
    pg.set_input_files("#file_input", str(sample))
    pg.wait_for_selector("#import_status.hint.ok", timeout=15000)


def test_empty_state_points_to_import(browser, mobile_url):
    """没有任何数据时，刷题页必须是"去导入"的引导，而不是空白页。"""
    ctx, pg = _open(browser, mobile_url)
    try:
        assert pg.is_visible("#drill_empty")
        assert "还没有题库" in pg.inner_text("#drill_empty")
        # 「去导入」必须真的切到数据页（否则引导是死路）
        pg.click('#drill_empty button[data-go="data"]')
        pg.wait_for_selector("#scr-data:not([hidden])")
        # 文件选择框本身是隐藏的（用 label 当按钮），可见的是那个 label
        assert pg.is_visible('label[for="file_input"]')
    finally:
        ctx.close()


def test_full_loop_import_drill_mistake_review(browser, mobile_url):
    """完整闭环：同步 → 刷题 → 答错进错题 → 错题按考点聚类 → 复习评分。"""
    ctx, pg = _open(browser, mobile_url)
    try:
        _import(pg)
        status = pg.inner_text("#import_status")
        assert "同步完成" in status and "10" in status, status

        # --- 刷题：选库 → 第一题答错（正确答案是 A，故意点 B）
        pg.click('.tabbar button[data-tab="drill"]')
        pg.wait_for_selector(".rowbtn[data-bank]")
        assert "10 题" in pg.inner_text(".rowbtn[data-bank]")
        pg.click(".rowbtn[data-bank]")
        pg.wait_for_selector("#drill_card .opt")
        assert "COPD" in pg.inner_text(".qstem")

        pg.click('#drill_card .opt[data-i="1"]')
        pg.wait_for_selector("#verdict .verdict.no")
        verdict = pg.inner_text("#verdict")
        assert "答错了" in verdict and "正确答案 A" in verdict
        assert "已收进错题" in verdict
        # 解析也要出现（否则用户只知道错，不知道为什么）
        assert "吸烟" in pg.inner_text("#verdict .explain")
        # 判分后选项必须锁死，防止改答案
        assert pg.eval_on_selector_all(
            "#drill_card .opt[disabled]", "els => els.length") == 4

        pg.click("#drill_quit")

        # --- 错题：新错的题 + 同步过来的 1 道，应按 subtopic 聚成多组
        pg.click('.tabbar button[data-tab="mistakes"]')
        pg.wait_for_selector("#mk_groups .rowbtn")
        groups = pg.eval_on_selector_all(
            "#mk_groups .rowbtn .rowbtn-title", "els => els.map(e => e.textContent.trim())")
        assert "慢阻肺" in groups, groups
        assert "支气管哮喘" in groups, groups          # 来自同步包的桌面端错题
        stats = pg.inner_text("#mk_stats")
        assert "2" in stats, stats                     # 错题总数 = 1 道新错 + 1 道同步

        mks = _idb(pg, "mistakes")
        assert len(mks) == 2, mks
        fresh = [m for m in mks if m["id"].startswith("it:")][0]
        assert fresh["wrong_count"] == 1 and fresh["last_answer"] == "B"
        assert fresh["due"] <= pg.evaluate("() => new Date().toISOString().slice(0,10)")

        # --- 复习：两道都到期 → 显示答案 → 记得
        pg.click('.tabbar button[data-tab="review"]')
        pg.wait_for_selector("#rv_run:not([hidden])")
        assert "/2" in pg.inner_text("#rv_counter")
        pg.click("#btn_reveal")
        pg.wait_for_selector(".rate-row .rate.known")
        assert "正确答案" in pg.inner_text("#rv_answer")
        pg.click(".rate.known")
        pg.wait_for_selector(".rate-row")   # 第二张
        pg.click("#btn_reveal")
        pg.wait_for_selector(".rate-row .rate.forgot")
        pg.click(".rate.forgot")
        pg.wait_for_selector("#rv_done:not([hidden])")
        assert "记得 1" in pg.inner_text("#rv_done_sub")
        assert "忘了 1" in pg.inner_text("#rv_done_sub")

        after = _idb(pg, "mistakes")
        known = [m for m in after if m["reps"] == 1]
        forgot = [m for m in after if m["state"] == "relearning"]
        assert len(known) == 1 and known[0]["interval"] == 1, known
        assert len(forgot) == 1 and forgot[0]["interval"] == 0, forgot
        assert forgot[0]["lapses"] == 1
    finally:
        ctx.close()


def test_reimport_does_not_reset_review_progress(browser, mobile_url):
    """重复同步**不得**覆盖手机上练出来的复习进度（SRS 状态以本地为准）。

    这是最容易写错、也最难被用户发现的一条：如果导入把 reps/interval 重置成新卡，
    表现是"每次同步后错题全部重新出现"，用户只会觉得这个 App 有病。
    """
    ctx, pg = _open(browser, mobile_url)
    try:
        _import(pg)
        pg.click('.tabbar button[data-tab="drill"]')
        pg.wait_for_selector(".rowbtn[data-bank]")
        pg.click(".rowbtn[data-bank]")
        pg.wait_for_selector("#drill_card .opt")
        pg.click('#drill_card .opt[data-i="1"]')      # 制造一道错题
        pg.wait_for_selector("#verdict .verdict.no")
        pg.click("#drill_quit")

        pg.click('.tabbar button[data-tab="review"]')
        pg.wait_for_selector("#rv_run:not([hidden])")
        pg.click("#btn_reveal")
        pg.click(".rate.known")
        pg.wait_for_selector(".rate-row")

        before = {m["id"]: (m["reps"], m["interval"], m["ease"]) for m in _idb(pg, "mistakes")}
        assert any(v[0] == 1 for v in before.values()), before

        _import(pg)                                     # 再同步一次同一个包

        after = {m["id"]: (m["reps"], m["interval"], m["ease"]) for m in _idb(pg, "mistakes")}
        for k, v in before.items():
            if v[0] >= 1:                               # 练过的卡，状态必须原样保留
                assert after[k] == v, f"{k}: {v} → {after[k]}"
        # 题目也不能被重复插入（id 稳定 ⇒ upsert 而非 append）
        items = _idb(pg, "items")
        assert len(items) == 10, len(items)
    finally:
        ctx.close()


def test_flat_bundle_is_rejected_with_a_clear_message(browser, mobile_url, tmp_path):
    """传错文件要给出人话，而不是静默成功或抛栈。"""
    bad = tmp_path / "wrong.json"
    bad.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    ctx, pg = _open(browser, mobile_url)
    try:
        pg.click('.tabbar button[data-tab="data"]')
        pg.set_input_files("#file_input", str(bad))
        pg.wait_for_selector("#import_status.hint.err", timeout=10000)
        assert "不是 MedKit 的手机同步包" in pg.inner_text("#import_status")
        assert not _idb(pg, "items"), "坏文件不应写入任何题目"
    finally:
        ctx.close()


@pytest.mark.parametrize("width,height", [(360, 740), (390, 844), (768, 1024)])
def test_no_horizontal_overflow_and_tap_targets(browser, mobile_url, width, height):
    """手机/平板视口下：整页不横向滚动，且主按钮命中区 ≥44px。

    这两条是移动端最容易"看起来没事、用起来难受"的地方：
    横向溢出会让人左右晃，命中区太小会频繁点错——而它们都不会报错。
    """
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              device_scale_factor=3, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    try:
        pg.goto(f"{mobile_url}/index.html")
        pg.wait_for_selector(".tabbar")
        pg.click('.tabbar button[data-tab="data"]')
        pg.set_input_files("#file_input", str(_SAMPLE))
        pg.wait_for_selector("#import_status.hint.ok", timeout=15000)

        for tab in ("drill", "mistakes", "review", "data"):
            pg.click(f'.tabbar button[data-tab="{tab}"]')
            pg.wait_for_timeout(150)
            sw, iw = pg.evaluate(
                "() => [document.documentElement.scrollWidth, window.innerWidth]")
            assert sw <= iw + 1, f"{width}px 下 #{tab} 横向溢出：{sw} > {iw}"

        # 主按钮（.btn）命中高度必须 ≥44（iOS HIG 下限）
        small = pg.eval_on_selector_all(
            ".btn",
            """els => els.filter(e => e.offsetParent !== null)
                        .map(e => Math.round(e.getBoundingClientRect().height))
                        .filter(h => h > 0 && h < 44)""")
        assert not small, f"{width}px 下有按钮命中区不足 44px：{small}"
    finally:
        ctx.close()
