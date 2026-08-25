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

FIX = ROOT / "medkit" / "data" / "samples"


def test_json_extract():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前缀 {"a": [1,2]} 后缀')["a"] == [1, 2]
    assert _extract_json("[1, 2, 3]") == [1, 2, 3]


def test_providers():
    # 2026-08：按用户要求移除 Ollama；保留 3 预置 + 自定义
    assert len(PROVIDERS) == 4
    assert all(p["id"] != "ollama" for p in PROVIDERS)
    for p in PROVIDERS:
        assert p["id"] in ("deepseek", "zhipu", "qwen", "custom")
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
        assert view["api_key_masked"].startswith("sk-k")
    finally:
        m.cfg.load, m.cfg.save = orig_load, orig_save


def test_project_ratio_validation():
    """配比合计 ≠ 100 → 400。"""
    import medkit.main as m
    body = m.ProjectBody(subject="儿科", target=100,
                         ratios={"A1": 40, "A2": 30, "B1": 20, "X": 30},
                         textbook_slices=[{"sid": "S001", "title": "章", "text": "x" * 300}],
                         teacher_slices=[{"sid": "T001", "title": "重点", "text": "y" * 200}])
    try:
        m.create_project(body)
        raise AssertionError("配比 120% 应被拒绝")
    except m.HTTPException as e:
        assert e.status_code == 400


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
