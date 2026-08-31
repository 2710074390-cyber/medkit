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
