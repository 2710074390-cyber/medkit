"""EP-01 阶段 4：在自己的错题与笔记里检索（原方案 D1）测试。

覆盖四层：
1. **召回与排序**（纯函数）：二元组兜底、字段权重、覆盖率排序、作用域过滤；
2. **边界**：空查询 / 单字查询 / 零命中——都不抛异常，且**回显 tokens** 让前端能区分
   「没搜」与「搜了没命中」；
3. **已知边界钉住**：缩写「心衰」**搜不到**「心力衰竭」（真 FTS5 实测同样 0 命中）——
   把它写成用例，既防以后被当成 bug 乱改，也防有人以为模糊匹配能解决；
4. **结构守卫**：「检索面严格限定在自己的数据内」= 模块只吃 `list[dict]`，
   不 import 教材检索（`explain`/`syllabus`/`mineru`）也不建索引。

反向验证见 `.workbuddy-ai/tmp/diag_errsearch.py`。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.errsearch as es  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402

SEARCH_SRC = ROOT / "medkit" / "core" / "errsearch.py"

CARDS: list[dict[str, Any]] = [
    {"id": "m1", "subject": "儿科学", "chapter": "呼吸", "topic": "支气管肺炎",
     "question": "患儿男，6 岁，双肺闻及中细湿啰音", "options": ["甲", "乙"],
     "answer": "B", "user_answer": "A", "my_reasoning": "只记得湿啰音",
     "error_tag": "机制混淆", "analysis": "固定湿啰音是肺炎体征", "round": "早鸟轮"},
    {"id": "m2", "subject": "生理学", "chapter": "循环", "topic": "心力衰竭",
     "question": "前负荷增加时每搏量如何变化", "options": ["增加", "减少"],
     "answer": "A", "analysis": "Frank-Starling 机制", "round": "跟课轮"},
    {"id": "m3", "subject": "儿科学", "chapter": "呼吸", "topic": "鉴别诊断",
     "question": "咳嗽咳痰的鉴别", "analysis": "偶见湿啰音", "round": "强化轮"},
]


# ==================================================================== 1. 召回与排序
def test_partial_overlap_recall_via_bigrams():
    """**二元组的真实贡献 = 部分重叠召回**（不是"词内查找"）。

    对照实验（这条用例的价值就在这个对照上）：
    - 卡片文本「中细湿啰音」含查询「湿啰音」的**连续子串** ⇒ 那是**子串匹配**命中的，
      与二元组无关（`"湿啰音" in "中细湿啰音"` 为真）；
    - 卡片文本「湿啰的杂音」**不含**「湿啰音」这个连续串，但含二元组「湿啰」
      ⇒ 这才需要二元组。

    第一版把前者当成"二元组兜底"的例子（说反了），见模块 docstring 的留档。
    """
    assert "湿啰音" in "中细湿啰音", "前提：连续子串场景"
    assert "湿啰音" not in "湿啰的杂音", "前提：非连续场景"

    cards = [
        {"id": "contig", "question": "双肺闻及中细湿啰音"},
        {"id": "partial", "question": "湿啰的杂音"},
        {"id": "none", "question": "心率增快"},
    ]
    r = es.search(cards, "湿啰音")
    ids = {i["id"] for i in r["items"]}
    assert ids == {"contig", "partial"}, f"应命中连续与部分重叠两条，实际 {ids}"
    # 连续命中的那条分数更高（命中 2 个 token vs 1 个）⇒ 排前面
    assert [i["id"] for i in r["items"]][0] == "contig"


def test_field_weight_ranks_stem_above_analysis():
    """同一 token 命中题干 vs 只命中解析：**题干那条排前面**（权重 5 vs 1）。"""
    r = es.search(CARDS, "湿啰音")
    assert [i["id"] for i in r["items"]] == ["m1", "m3"], \
        "m1（题干+想法+解析）应排在 m3（仅解析）前"
    m1, m3 = r["items"][0], r["items"][1]
    assert m1["score"] > m3["score"]
    assert "question" in m1["matched_fields"]
    assert m1["matched_fields"] == sorted(m1["matched_fields"])
    assert m3["matched_fields"] == ["analysis"]


def test_coverage_beats_score():
    """**先覆盖率、再分数**：低分但全 token 命中的，排在"高分但只中一个词"的前面。

    构造（查询两个 2 字词 ⇒ 恰好 2 个 token）：
    - A：两个 token 都命中，但只在 `analysis`（权重 1）⇒ 分数低、**覆盖率 1.0**
    - B：只命中一个 token，但在 `question`（权重 5）⇒ 分数高、**覆盖率 0.5**
    ⇒ A 应排 B 前（这正是"覆盖率优先"的含义）。
    """
    q = "啰音 负荷"
    toks = es.query_tokens(q)
    assert len(toks) == 2, f"构造前提：查询应恰好切成 2 个 token，实际 {toks}"
    a, b = toks
    cards = [
        {"id": "A", "question": "无关题干", "analysis": f"{a} {b}"},
        {"id": "B", "question": f"{a} 出现在题干里"},
    ]
    r = es.search(cards, q)
    assert [i["id"] for i in r["items"]] == ["A", "B"], [
        (i["id"], i["coverage"], i["score"]) for i in r["items"]]
    assert r["items"][0]["coverage"] == 1.0 and r["items"][0]["score"] < r["items"][1]["score"]
    # 覆盖率单调不增
    cov = [i["coverage"] for i in r["items"]]
    assert cov == sorted(cov, reverse=True)


def test_subject_is_scope_not_query():
    """`subject` 是**作用域过滤**：不参与打分，只是把范围缩小。"""
    all_hits = es.search(CARDS, "湿啰音")["count"]
    scoped = es.search(CARDS, "湿啰音", subject="生理学")
    assert scoped["count"] == 0, "生理学那条不含湿啰音"
    assert es.search(CARDS, "湿啰音", subject="儿科学")["count"] == all_hits
    # 同一条在不同作用域下分数一致（说明 subject 没进打分）
    a = es.search(CARDS, "湿啰音")["items"][0]["score"]
    b = es.search(CARDS, "湿啰音", subject="儿科学")["items"][0]["score"]
    assert a == b


def test_scope_declares_no_textbook():
    """`scope` 必须**明说**不含教材正文（《总纲》§2.4：不许把讲义灌进知识库）。"""
    r = es.search(CARDS, "湿啰音")
    assert "教材正文" in r["scope"] and "错题" in r["scope"]


def test_limit_is_clamped():
    assert es.search(CARDS, "湿啰音", limit=1)["count"] == 1
    assert es.search(CARDS, "湿啰音", limit=0)["count"] >= 1, "limit 0 应被夹到 1，而不是返回空"
    assert es.search(CARDS, "湿啰音", limit=10 ** 6)["count"] == 2, "上限夹取"


# ==================================================================== 2. 边界
def test_empty_query_returns_nothing_and_no_tokens():
    for q in ("", "   ", None):
        r = es.search(CARDS, q or "")
        assert r["count"] == 0 and r["tokens"] == []


def test_single_char_query_is_filtered():
    """单字查询：**tokens 为空** ⇒ 前端能区分「没搜」与「没命中」。"""
    r = es.search(CARDS, "的")
    assert r["tokens"] == [] and r["count"] == 0


def test_no_hit_returns_empty_not_error():
    r = es.search(CARDS, "完全不存在的词组xyz")
    assert r["count"] == 0 and r["items"] == []
    assert r["tokens"], "零命中时 tokens 仍应有值（证明是'搜了没命中'）"


def test_empty_card_list():
    assert es.search([], "湿啰音")["count"] == 0


def test_cards_with_missing_or_odd_fields_do_not_crash():
    """字段缺失/类型异常（None / dict / 数字）不得抛异常——错题是用户随手录的。"""
    odd: list[dict[str, Any]] = [
        {"id": "x1", "question": None, "options": {"A": "甲"}, "analysis": 123},
        {"id": "x2"},                                     # 几乎空
        {"id": "x3", "question": "湿啰音", "options": None, "my_reasoning": None},
    ]
    r = es.search(odd, "湿啰音")
    assert r["count"] == 1 and r["items"][0]["id"] == "x3"


# ==================================================================== 3. 已知边界
def test_abbreviation_is_a_known_boundary_not_a_bug():
    """**缩写召回不在能力范围内**——把边界钉住，防以后被当 bug 乱改。

    「心衰」是「心力衰竭」的缩写（取「心力」+「衰竭」各首字），**不是前缀**
    ⇒ 子串匹配、二元组、FTS5 前缀**三条路都命中不了**（真 FTS5 实测 `"心衰"*` → 0 命中）。
    要解决只能上**别名表**（方案 §9.4 / 待办 D4，需用户提供常用表述）。
    """
    assert es.search(CARDS, "心衰")["count"] == 0, (
        "若这条开始通过，说明加了模糊/别名能力——请同步更新模块 docstring 的「已知边界」")
    # 反向：真前缀能命中（「心力」是「心力衰竭」的前缀）
    assert es.search(CARDS, "心力")["count"] == 1


def test_snippet_contains_the_hit():
    """片段：**命中点必须在片段里**（否则片段没有信息量，用户看不出为什么这条被搜出来）。"""
    r = es.search(CARDS, "湿啰音")
    assert r["count"] >= 1
    for it in r["items"]:
        assert "湿啰" in it["snippet"] or "啰音" in it["snippet"], it["snippet"]


def test_snippet_never_comes_from_answer_field():
    """片段**不从答案字段取材**——免得搜索页变成"答案速查"（与复盘笔记/苏格拉底同一取向）。

    构造：查询只命中 `answer` 一个字段 ⇒ 片段应退化为题干开头，而不是把答案显示出来。
    （第一版用例的查询命中了 `topic`，而 `topic` 也在 skip 集里 ⇒ 两条路都退化到题干，
    根本没测到「跳过 answer」这件事——注入验证时才发现。）
    """
    cards = [{"id": "a1", "question": "题干里不含那个词", "answer": "支气管肺炎"}]
    r = es.search(cards, "支气管肺炎")
    assert r["count"] == 1
    assert r["items"][0]["matched_fields"] == ["answer"], "前提：只命中 answer 字段"
    assert "支气管肺炎" not in r["items"][0]["snippet"], "片段不得从答案字段取材"
    assert "题干里不含那个词" in r["items"][0]["snippet"]


# ==================================================================== 4. 结构守卫
def _imports() -> set[str]:
    tree = ast.parse(SEARCH_SRC.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
    return out


def test_search_surface_is_own_data_only():
    """**结构守卫**：检索面严格限定在用户自己的数据内。

    判据 = 模块**不**依赖教材检索链（`explain` / `syllabus` / `mineru` / `websearch`），
    也不建索引（`reindex`）——三者任一出现即红。
    正面证据：它只吃 `list[dict]` 且只从 `db` 取**分词器**这一个符号。
    """
    imports = _imports()
    forbidden = {m for m in imports
                 if any(k in m for k in ("explain", "syllabus", "mineru", "websearch", "slice"))}
    assert not forbidden, f"检索模块引入了教材/网络检索链：{sorted(forbidden)}"
    assert ".db" in imports or "db" in imports, "应复用 db 的分词器（单源）"
    # 正面：只从 db 取分词器（不取 reindex/连接等）
    tree = ast.parse(SEARCH_SRC.read_text(encoding="utf-8"))
    from_db = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and (n.module or "").endswith("db") for a in n.names}
    assert from_db == {"fts_tokens"}, f"从 db 只应取分词器，实际取了 {from_db}"


def test_search_is_pure_same_input_same_output():
    """纯函数性质：同入参两次调用结果一致（**不依赖任何 db/缓存状态**）。"""
    a = es.search(CARDS, "湿啰音")
    b = es.search(CARDS, "湿啰音")
    assert a == b
    # 且不改动入参
    snapshot = [dict(c) for c in CARDS]
    es.search(CARDS, "湿啰音")
    assert CARDS == snapshot, "search 不得修改传入的 cards"


# ==================================================================== 5. 端点
@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    for mod in (lib, dbs, kpid, ev):
        monkeypatch.setattr(mod, "LIBRARY_DIR", libd, raising=False)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json", raising=False)
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json", raising=False)
    dbs.reset_conn()
    yield tmp_path
    dbs.reset_conn()


@pytest.fixture()
def client(isolated):
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_endpoint_searches_own_mistakes(client):
    client.post("/api/errors/intake", json={
        "question": "患儿男，6 岁，双肺闻及中细湿啰音", "answer": "B",
        "subject": "儿科学", "chapter": "呼吸", "confidence": 4,
        "my_reasoning": "只记得湿啰音", "error_tag": "机制混淆"})
    r = client.get("/api/errors/search", params={"q": "湿啰音"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["subject"] == "儿科学"
    assert "湿啰" in body["items"][0]["snippet"] or "啰音" in body["items"][0]["snippet"]
    assert "教材正文" in body["scope"]


def test_endpoint_short_query_and_scope(client):
    client.post("/api/errors/intake", json={
        "question": "湿啰音相关题", "answer": "A", "subject": "儿科学",
        "confidence": 3, "my_reasoning": "x"})
    assert client.get("/api/errors/search", params={"q": "的"}).json()["tokens"] == []
    assert client.get("/api/errors/search",
                      params={"q": "湿啰音", "subject": "生理学"}).json()["count"] == 0
