"""P1 冒烟测试：核心模块 + 样例夹具。

运行：python tests/test_smoke.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core.config import DEFAULTS, public_view  # noqa: E402
from medkit.core.extract import extract_text  # noqa: E402
from medkit.core.llm import _extract_json  # noqa: E402
from medkit.core.providers import PROVIDERS, get_provider  # noqa: E402
from medkit.core.quota import allocate  # noqa: E402
from medkit.core.slice import slice_text  # noqa: E402


def _fallback_run_coro(coro, timeout: float = 30.0):
    """`run_coro` 的**同语义**兜底实现（仅 `python tests/test_smoke.py` 直跑时用到）。

    本文件是**双用**的：既是 pytest 用例集（`pytest tests/test_smoke.py`），也是
    可直跑的冒烟脚本（`python tests/test_smoke.py`）。后者不经 conftest，拿不到
    `run_coro` fixture。故给一个**签名与语义完全一致**的兜底（同 conftest：
    新线程里 `asyncio.run`，协程内异常原样回抛）。

    ⚠️ 为什么两个入口都必须走「新线程 + asyncio.run」而不是就地 `asyncio.run()`：
    browser 层的 Playwright 同步 API 会占住主线程的 running-loop 标记（R6-01），
    此后主线程任何 `asyncio.run()` 都抛 `RuntimeError: cannot be called from a
    running event loop`。pytest 走 conftest 的 `run_coro`，直跑走本兜底，
    **两条路都不碰主线程事件循环**。
    """
    import asyncio
    import threading

    box: dict[str, object] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # 原样回抛（含 HTTPException / 断言失败）
            box["error"] = exc

    t = threading.Thread(target=_target, name="smoke-run-coro")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"协程在 {timeout}s 内未结束")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")

FIX = ROOT / "medkit" / "data" / "samples"


def test_json_extract():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前缀 {"a": [1,2]} 后缀')["a"] == [1, 2]
    assert _extract_json("[1, 2, 3]") == [1, 2, 3]


def test_providers():
    # 2026-08：按用户要求移除 Ollama；保留 4 预置（+Kimi）+ 自定义
    #
    # ## 为什么删掉 `assert len(PROVIDERS) == 5`（2026-09-29 R19）
    #
    # 它与**紧随其后**的那条断言完全冗余：`{p["id"] for p in PROVIDERS}`
    # 逐元素等于真源 id 集合时，长度必然相等；反之若只钉计数，从真源删掉
    # `qwen` 再塞个臆造的 `ghost`（仍是 5 个）这条魔数照样绿（已用
    # `diag_r19.py` 实证）。⇒ 改锁**id 集合**这一结构性质，不锁计数。
    assert {p["id"] for p in PROVIDERS} == {
        "deepseek", "zhipu", "qwen", "kimi", "custom"}, \
        "provider id 集合必须与产品声明一致（增删都要在此显式改）"
    assert all(p["id"] != "ollama" for p in PROVIDERS)
    for p in PROVIDERS:
        assert p.get("register_url") is not None or p["id"] == "custom"
    assert get_provider("custom")["base_url"] == ""
    assert get_provider("ollama") is None  # 旧配置 → config.load 会回退


def test_extract_slice_quota():
    blocks = extract_text(FIX / "样例_儿科学_节选.md")
    slices = slice_text(blocks)
    assert len(slices) >= 2, "章节标题应切出多个切片"
    assert all(s["text"] for s in slices)

    teacher = extract_text(FIX / "样例_教师重点.md")
    quota = allocate(slices, teacher[0]["text"], 100)
    assert sum(q["count"] for q in quota) == 100
    # 教师重点强调的章节（生长发育）应获得更多配额
    growth = [q for q in quota if "生长发育" in slices[0]["title"] or q["sid"] == quota[0]["sid"]]
    assert growth, "生长发育章节应出现在配额中"
    top = max(quota, key=lambda q: q["count"])
    assert top["count"] >= 25, "重点章节应分配到较多题数"


def test_config_view_masks_key():
    cfg = dict(DEFAULTS)
    cfg["api_key"] = "sk-1234567890abcdef"
    view = public_view(cfg)
    assert view["api_key"] == ""
    assert "1234567890" not in view["api_key_masked"]


def test_slice_cross_page_continuation():
    """B25：PDF 按页成块——章节跨页续接同一切片，标题不退化「P2」。"""
    blocks = [
        {"label": "P1", "source": "doc.pdf", "text": "第一章 生长发育\n婴儿期生长最快。"},
        {"label": "P2", "source": "doc.pdf", "text": "青春期第二高峰。"},
        {"label": "P3", "source": "doc.pdf", "text": "第二章 营养\n能量需求。"},
    ]
    slices = slice_text(blocks)
    assert slices[0]["title"] == "第一章 生长发育"
    assert "青春期第二高峰" in slices[0]["text"], "续页内容应并入同一章节切片"
    assert slices[1]["title"] == "第二章 营养"


def test_slice_health_warnings():
    """素材体检：无章节标题 → 警告；token 估算为正；切片含完整文本（创建课题需要）。"""
    from medkit.main import _analyze_slices
    plain = [{"index": 0, "label": "TXT",
              "text": "这是没有任何章节标题的普通正文文本。" * 40, "chars": 400}]
    info = _analyze_slices(slice_text(plain), plain)
    assert any("章节标题" in w for w in info["warnings"]), "无章节标题应给提示"
    assert info["est_tokens"] > 0
    assert "text" in info["slices"][0], "切片应含完整文本（创建课题需要）"


def test_mineru_zip_markdown():
    """MinerU zip 结果提取 full.md；模式判定（有 Token→v4 / 无→agent）。"""
    import io
    import zipfile

    from medkit.core.mineru import MinerUClient
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("demo/full.md", "# 解析结果\n正文")
    text = MinerUClient._markdown_from_zip(buf.getvalue())
    assert "解析结果" in text

    assert MinerUClient("").mode() == "agent"
    assert MinerUClient("mr-123").mode() == "v4"


def test_mineru_v4_poll_pick():
    """评审 P0-2 回归：v4 批量接口 pick 必须取 extract_result[0]（否则永远空转超时）。"""
    from medkit.core.mineru import MinerUClient
    raw = {"code": 0, "data": {"batch_id": "b1", "extract_result": [
        {"file_name": "x.pdf", "state": "done", "full_zip_url": "https://z"}]}}
    assert MinerUClient._v4_pick_state(raw)["state"] == "done"
    client = MinerUClient("mr-1")
    out = client._poll(lambda: raw, client._v4_pick_state, "v4")  # done → 立即返回
    assert out["full_zip_url"] == "https://z"


def test_parse_contract_slice_count():
    """评审 P1-4 回归：渲染契约字段 slice_count 必须存在且等于切片数。"""
    from medkit.main import _analyze_slices
    blocks = [{"index": 0, "label": "TXT", "text": "第一章 引言\n" + "内容内容内容。" * 60, "chars": 400}]
    slices = slice_text(blocks)
    info = _analyze_slices(slices, blocks)
    assert info["slice_count"] == len(info["slices"]) == len(slices)


def test_config_keep_key_on_empty():
    """评审 P0-1 回归：保存配置传空 Key 必须保留旧值，禁止静默清除。
    v0.3.0（S2）：保存时旧明文自动升级为 DPAPI 密文（resolve_key 应解回原值）。"""
    import medkit.main as m
    from medkit.core import config as cfgmod
    from medkit.core.config import resolve_key
    saved = {**cfgmod.DEFAULTS, "api_key": "sk-keep1234",
             "mineru": {"api_key": "mr-keep", "auto_ocr": True}}
    orig_load, orig_save = m.cfg.load, m.cfg.save
    m.cfg.load = lambda: dict(saved)
    captured = {}
    m.cfg.save = lambda c: captured.update(c)
    try:
        body = m.ConfigBody(provider="deepseek", base_url="", api_key="",
                            model_gen="deepseek-chat", model_qc="")
        view = m.put_config(body)
        assert resolve_key(captured["api_key"]) == "sk-keep1234", "空 Key 应保留旧值"
        assert resolve_key(captured["mineru"]["api_key"]) == "mr-keep", "空 MinerU Key 应保留旧值"
        # R4-18：短 Key（<12）只露前 2 后 2——中段明文（keep）不再可见
        masked = view["api_key_masked"]
        assert masked.startswith("sk") and masked.endswith("34") and "keep" not in masked
    finally:
        m.cfg.load, m.cfg.save = orig_load, orig_save


def test_project_ratio_validation(run_coro=_fallback_run_coro):
    """配比合计 ≠ 100 → 400。"""
    import medkit.main as m
    body = m.ProjectBody(subject="儿科", target=100,
                         ratios={"A1": 40, "A2": 30, "B1": 20, "X": 30},
                         textbook_slices=[{"sid": "S001", "title": "章", "text": "x" * 300}],
                         teacher_slices=[{"sid": "T001", "title": "重点", "text": "y" * 200}])
    # W6（2026-10-02）：create_project 已改 async（落盘移出事件循环）⇒ 必须 await 才会执行校验。
    #
    # ## 本用例连着两次「假绿」，两次形态不同，值得留档
    #
    # ① **第一版（HEAD 里那版）**：`m.create_project(body)` 直接调 async 函数 ⇒ 只拿到一个
    #    **从未运行的协程**。`try` 体全绿（没跑当然没抛），`except` 不触发 ⇒ 断言 0 次执行。
    #    它对「配比 120% 被拒」这件事**完全没有信息**，却一直以 PASS 计。
    # ② **第二版（我的修法）**：改 `asyncio.run(...)`。单跑绿，**全量跑红**
    #    （`RuntimeError: asyncio.run() cannot be called from a running event loop`）——
    #    browser 层的 Playwright 同步 API 在整会话占住主线程 running-loop 标记
    #    （R6-01 记录的就是这个坑，conftest 的 `run_coro` fixture 就是为它而建）。
    # ⇒ 正解是走项目自己的 `run_coro`：**新线程里 asyncio.run**，免疫主线程事件循环状态，
    #    且协程内的异常原样回抛。直接 `asyncio.run()` 在本仓一律禁止。
    #
    # ## 判据必须钉在「协程真的跑起来了」+「拦我的是哪一闸」
    #
    # `except m.HTTPException` 这种写法**区分不了**「校验拦住了」和「协程压根没执行」
    # （①就是这样骗过所有人的）。故读 `routers.projects._GATE_PROBE` 这一显式探针：
    # 只有真跑进 `create_project` 且命中**配比**那条闸门才置位成 `"ratio"`。
    # 注意探针是**列表**（可变对象）而非字符串——测试侧不能 `import` 一次名字就绑死，
    # 必须每次读 `[0]`，否则拿到的是被 re-import 覆盖前的旧值。
    from medkit.routers import projects as r_projects
    r_projects._GATE_PROBE[0] = None          # 清场：确保下面命中的是本用例这一击
    try:
        run_coro(m.create_project(body))
    except m.HTTPException as e:
        assert e.status_code == 400, f"应 400，实得 {e.status_code}"
    else:
        raise AssertionError("配比 120% 应被拒绝，但未抛 HTTPException")
    # 走到这里说明确实抛了 400；仍要确认抛的是**参数校验**那一条，而不是别的 400
    # （教材/教师重点校验在配比校验**之前**，若那条先红，本用例就在测一个别的东西）。
    assert r_projects._GATE_PROBE[0] == "ratio", (
        f"期望配比闸门拦下，实得 {r_projects._GATE_PROBE[0]!r}"
        f"（None = 协程未执行或更早的闸先红）")


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {name}: {e}")
    print("----")
    print("SMOKE OK" if failures == 0 else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
