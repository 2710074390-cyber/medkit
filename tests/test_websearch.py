"""WP-6：可信来源标记/排序/过滤 + 多轮检索 search_fn 注入（单元测试，零网络）。"""

from medkit.core import websearch as ws


def test_trusted_mark_sort_and_digest():
    mats = [
        {"title": "普通站", "url": "https://blog.example.com/a", "snippet": "s"},
        {"title": "WHO", "url": "https://www.who.int/health", "snippet": "s"},
    ]
    out = ws.trusted_filter(mats)
    assert out[0]["url"].startswith("https://www.who.int"), "可信来源应排前"
    assert out[0]["trusted"] is True
    digest = ws.digest_for_prompt(out)
    assert "【可信】" in digest and "【可信】WHO" in digest


def test_trusted_custom_domain_and_only():
    mats = [
        {"title": "A", "url": "https://a.example.org/p", "snippet": "s"},
        {"title": "B", "url": "https://mydocs.example.com/q", "snippet": "s"},
    ]
    out = ws.trusted_filter(mats, trusted_domains=["mydocs.example.com"])
    assert out[0]["url"].endswith("mydocs.example.com/q")
    kept = ws.trusted_filter(mats, trusted_only=True,
                             trusted_domains=["mydocs.example.com"])
    assert len(kept) == 1 and kept[0]["title"] == "B"
    assert ws._is_trusted("https://www.pumc.edu.cn/x") is True
    assert ws._is_trusted("https://unknown.example.org/x") is False


def test_run_search_rounds_marks_and_filters_trusted():
    class QC:
        def chat_json(self, messages, **kwargs):
            return {"queries": ["test"]}

    def fn(q):
        return [{"title": "WHO", "url": "https://who.int/health", "snippet": "s"},
                {"title": "B", "url": "https://blog.example.com/x", "snippet": "s"}]

    res = ws.run_search_rounds(QC(), "儿科", "生长发育", "关键词", "bocha",
                               search_fn=fn, trusted_only=False, max_rounds=2)
    assert len(res["materials"]) == 2
    assert res["materials"][0]["trusted"] is True
    res2 = ws.run_search_rounds(QC(), "儿科", "生长发育", "关键词", "bocha",
                                search_fn=fn, trusted_only=True, max_rounds=2)
    assert len(res2["materials"]) == 1 and res2["materials"][0]["trusted"] is True


def test_search_deepseek_parses_real_responses_shape(monkeypatch):
    """2026-09-01：真实 Responses API 结构（web_search_call.action.url + message
    annotations/正文 URL）解析与去重——此前只取首个 message 兜底，0 条结果的场景处理不完整。"""
    data = {
        "output": [
            {"type": "reasoning", "content": []},
            {"type": "web_search_call",
             "action": {"type": "web_search", "queries": ["q1"]}},   # 规划记录：无结果
            {"type": "web_search_call",
             "action": {"type": "web_search",
                        "url": "https://who.int/health#ws_call_id=call_01"}},
            {"type": "message", "content": [
                {"type": "output_text",
                 "text": "详见 https://who.int/health 原文，以及卫健委通知。",
                 "annotations": [{"type": "url_citation",
                                  "url": "https://www.nhc.gov.cn/xw/2026/p.html",
                                  "title": "国家卫健委 2026 通知",
                                  "snippet": "正文摘要"}]}
            ]},
        ]
    }

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return data

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            return FakeResp()

    monkeypatch.setattr(ws.httpx, "Client", FakeClient)
    out = ws.search_deepseek("儿科学 考试大纲", "sk-x", "deepseek-v4-flash")
    urls = [m["url"] for m in out]
    assert "https://who.int/health" in urls                     # action.url（含 #ws_call_id）
    assert "https://www.nhc.gov.cn/xw/2026/p.html" in urls      # annotations url_citation
    assert len(out) == 2, f"正文重复 URL 应去重：{urls}"
    # 非 v4 模型回退不改动
    assert ws._normalize_deepseek_model("deepseek-chat") == "deepseek-v4-flash"


def test_search_deepseek_400_falls_back_legacy_tool(monkeypatch):
    """C-05：DeepSeek 400（工具版本变体）→ 用 web_search_2025_08_26 重试——有测试锁定。"""
    calls: list[dict] = []

    class FakeResp:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            # 注意：tools 的 body dict 会被原地改写（重试复用同一 body）——记录调用时的工具名
            calls.append(kw["json"]["tools"][0]["type"])
            if len(calls) == 1:
                r = FakeResp({})
                r.status_code = 400
                return r
            return FakeResp({"output": [
                {"type": "web_search_call",
                 "action": {"type": "web_search", "url": "https://who.int/x#c1"}}]})

    monkeypatch.setattr(ws.httpx, "Client", FakeClient)
    out = ws.search_deepseek("儿科学 考试大纲", "sk-x", "deepseek-v4-flash")
    assert len(calls) == 2, "400 后应重试一次"
    assert calls[0] == ws.SEARCH_TOOL_CURRENT
    assert calls[1] == ws.SEARCH_TOOL_LEGACY, "回退体应使用 2025 版工具名"
    assert [m["url"] for m in out] == ["https://who.int/x"]


def test_run_search_rounds_cancel_discards_inflight(monkeypatch):
    """C-03：在途检索被取消 → 本轮结果不采用（logs 留痕），不再当作素材。"""
    import threading

    ev = threading.Event()
    called = []

    class QC:
        def chat_json(self, messages, **kwargs):
            return {"queries": ["q1"]}

    def fn(q):
        called.append(q)
        ev.set()   # 检索进行中取消
        return [{"title": "后到结果", "url": "https://x.example.com/a", "snippet": "s"}]

    res = ws.run_search_rounds(QC(), "儿科", "生长发育", "关键词", "bocha",
                               search_fn=fn, cancel=ev, max_rounds=2)
    assert called, "检索函数应被调用（在途取消的模拟前提）"
    assert res["materials"] == [], f"取消后的在途结果不应采用：{res['materials']}"
    assert any("已取消" in lg for lg in res["logs"]), res["logs"]
