"""本地错题 vault 守卫（L1 文件层 + L2 可弃索引 + L4 Agent 硬约束）。

## 为什么值得单独立一个文件

这个域的核心主张是**四条可证伪的性质**，每一条都是「做错了也不会报错、只会静默失真」
的那一类：

1. **索引可弃**：删掉 `vault_items` 后全量重建，结果与重建前逐行相等。
2. **重建只读 vault**：重建过程不得写/删任何用户文件。
3. **手改即时生效**：用户在外部编辑器改了文件，重建后索引必须跟着变。
4. **Agent 不许用通识补全**：没有可验证依据时必须说"不知道"；编造的引用必须被剔除。

另外三条是接口红线：路径不许越出 vault 根；`answer` 不许通过接口改写；
「从旧错题本迁移」不许动旧数据。

运行环境约定：本文件一律用 `run_coro` 驱动协程（禁 `asyncio.run()`，见 conftest）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medkit import main as m
from medkit.core import db as dbs
from medkit.core import vault, vault_index


# ---------------------------------------------------------------- 夹具
@pytest.fixture()
def vroot(tmp_path, monkeypatch):
    """隔离 vault：指向 tmp 下的目录，并写进配置（`vault_root()` 因此解析到它）。"""
    root = tmp_path / "vaultroot"
    vault.init_vault(str(root))
    monkeypatch.setattr(vault, "CONFIG_CACHE", None, raising=False)
    vault.set_vault_dir(str(root))
    yield root
    vault.set_vault_dir("")


def _rec(**kw):
    base = {"id": "m-001", "subject": "内科学", "chapter": "呼吸系统", "topic": "慢阻肺",
            "know_tags": ["copd"], "question": "慢阻肺最主要的病因是",
            "options": ["吸烟", "感染"], "answer": "A",
            "analysis": "吸烟是 COPD 最重要的环境致病因素。",
            "created_at": "2026-10-13T21:12:04", "source": "bank"}
    base.update(kw)
    return base


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


# ================================================================ L1 文件层
def test_serialize_parse_round_trip(vroot):
    rec = _rec(my_reasoning="我以为感染是病因。", doubts="稳定期会用抗菌药吗？")
    text = vault.serialize(rec)
    fm, sec = vault.parse(text)
    back = vault.to_record(fm, sec)
    for k in ("subject", "chapter", "topic", "know_tags", "question", "options",
              "answer", "analysis", "my_reasoning", "doubts"):
        assert back[k] == rec[k], k
    assert back["status"] == "unlearned"


def test_only_question_is_enough(vroot):
    """只写题干也能成文件——**没有录入闸门**（EP-01 就是这么劝退用户的）。"""
    rec = _rec(subject="", chapter="", topic="", know_tags=[], options=[],
               answer="", analysis="", source="")
    text = vault.serialize(rec)
    fm, sec = vault.parse(text)
    back = vault.to_record(fm, sec)
    assert back["question"] == "慢阻肺最主要的病因是"
    assert back["subject"] == "" and back["answer"] == "" and back["analysis"] == ""
    assert vault.write_record(rec, vroot).is_file()


def test_broken_front_matter_does_not_raise_or_rewrite(vroot):
    """front matter 坏了：记告警、不抛错、**不改用户文件**。"""
    p = vroot / "mistakes" / "2026" / "10" / "bad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = "---\nsubject: [没闭合\n---\n\n# 题干\n"
    p.write_text(raw, encoding="utf-8")
    fm, sec = vault.parse(p.read_text(encoding="utf-8"))
    assert fm.get("_warn"), "必须留下告警，让人知道这条读失败了"
    assert vault.to_record(fm, sec)["question"] == "题干"
    vault_index.rebuild(vroot)
    assert p.read_text(encoding="utf-8") == raw, "重建不得改写用户文件"


def test_broken_file_still_enters_index(vroot):
    """坏文件也要进索引（fwarn 如实标记）——否则它从用户视野里消失，比读不出来更糟。"""
    p = vroot / "mistakes" / "2026" / "10" / "bad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nsubject: [坏\n---\n\n# 题干\n", encoding="utf-8")
    out = vault_index.rebuild(vroot)
    assert out["warnings"] == 1
    rows = vault_index.list_items()
    assert len(rows) == 1 and rows[0]["fwarn"]


# ================================================================ L2 可弃索引
def _seed(vroot, n=3):
    for i in range(n):
        vault.write_record(_rec(id=f"m-{i:03d}", question=f"第 {i} 题",
                                created_at=f"2026-10-1{3 - i}T21:12:04"), vroot)


def test_rebuild_three_states(vroot):
    _seed(vroot, 3)
    first = vault_index.rebuild(vroot)
    assert (first["added"], first["scanned"]) == (3, 3)
    again = vault_index.rebuild(vroot)
    assert again["skipped"] == 3 and again["reparsed"] == 0, "内容没变必须全跳过"

    # 手改一个文件 → 只有它被重解析
    p = next(p for p in vault.iter_mistake_files(vroot) if "第 0 题" in p.read_text(encoding="utf-8"))
    p.write_text(p.read_text(encoding="utf-8").replace("第 0 题", "被手改的第 0 题"),
                 encoding="utf-8")
    third = vault_index.rebuild(vroot)
    assert third["reparsed"] == 1 and third["skipped"] == 2
    assert any("被手改" in r["question"] for r in vault_index.list_items())

    # 删一个文件 → 索引里移除，但**文件删除是用户干的**，重建不碰还在的文件
    p.unlink()
    assert vault_index.rebuild(vroot)["removed"] == 1
    assert vault_index.count() == 2


def test_index_is_disposable_and_idempotent(vroot):
    """删掉索引全量重建 ⇒ 与重建前**逐行相等**。这就是「删库 0 丢失」的可断言形式。"""
    _seed(vroot, 3)
    vault_index.rebuild(vroot)
    before = vault_index.list_items()
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM vault_items")
    assert vault_index.count() == 0
    out = vault_index.rebuild(vroot)
    assert out["added"] == 3
    after = vault_index.list_items()
    keys = ("path", "id", "subject", "question", "answer", "analysis", "understanding",
            "doubts", "status", "created")
    assert [{k: r[k] for k in keys} for r in before] == \
           [{k: r[k] for k in keys} for r in after]


def test_rebuild_never_writes_user_files(vroot):
    """重建对 vault 是**只读**的：把写函数全换成抛错，重建照样跑完。"""
    _seed(vroot, 1)
    before = {p: p.read_bytes() for p in vault.iter_mistake_files(vroot)}

    def _boom(*_a, **_kw):
        raise AssertionError("重建不得写 vault")

    import medkit.core.vault as vmod
    orig = vmod._write_atomic
    vmod._write_atomic = _boom
    try:
        out = vault_index.rebuild(vroot)
    finally:
        vmod._write_atomic = orig
    assert out["scanned"] == 1
    assert {p: p.read_bytes() for p in vault.iter_mistake_files(vroot)} == before


def test_search_escapes_like_wildcards(vroot):
    """搜索必须**转义** LIKE 通配符，而不是**剥除**它们。

    实测缺陷：旧实现把 `_` 从查询里删掉，而 `_` 在 LIKE 里是「任意单字符」通配 ⇒
    搜「外科学_vault检索」永远匹配不到原文（浏览器守卫先红，这里补一条快的单元守卫）。
    """
    vault.write_record(_rec(id="m-201", subject="外科学_vault检索",
                            question="外科学_vault检索-题干0",
                            created_at="2026-10-13T08:00:00"), vroot)
    vault.write_record(_rec(id="m-202", subject="生化", question="抑菌率 50% 的含义",
                            created_at="2026-10-13T09:00:00"), vroot)
    vault_index.rebuild(vroot)
    assert len(vault_index.list_items(q="外科学_vault检索")) == 1, "含下划线的查询必须命中"
    assert len(vault_index.list_items(q="50%")) == 1, "字面量 % 必须照原样匹配"
    # `_` 不再被当作「任意单字符」：把下划线换成 X 就不该命中
    assert vault_index.list_items(q="外科学Xvault检索") == []


def test_stats_is_local_only_and_ordered(vroot):
    """薄弱项按优先级降序；且统计模块**不 import llm/agents**（离线可用）。"""
    _seed(vroot, 3)
    vault.write_record(_rec(id="m-101", subject="外科学", chapter="腹外疝",
                             topic="嵌顿疝", question="嵌顿疝鉴别",
                             created_at="2026-10-01T08:00:00"), vroot)
    vault_index.rebuild(vroot)
    src = Path(vault_index.__file__).read_text(encoding="utf-8")
    assert "import llm" not in src and "agents" not in src, \
        "统计层必须零 LLM——没 API Key 时它也得能用"
    st = vault_index.stats()
    pr = [g["priority"] for g in st["groups"]]
    assert pr == sorted(pr, reverse=True), "必须按优先级降序"
    assert st["total"] == 4 and st["unlearned"] == 4


def test_stats_state_reflects_unlearned_not_answer_presence(vroot):
    """「薄弱项」的状态必须反映**未掌握**，不能用「有答案」冒充「已掌握」。

    实测缺陷：旧实现把「有答案的条数」当 `correct` 喂给 `compute_score` ⇒ 一整章
    全是未掌握的错题也会算成「较熟练」（score≈0.9），与「这是错题本」的语义正相反——
    而这张表正是设计文档里用户唯一要看的表（§4）。
    """
    vault.write_record(_rec(id="m-301", subject="内科学", chapter="呼吸系统", topic="慢阻肺",
                            question="错题一", created_at="2026-10-13T08:00:00"), vroot)
    vault_index.rebuild(vroot)
    g = vault_index.stats()["groups"][0]
    assert g["unlearned"] == 1 and g["total"] == 1
    assert g["state"] in ("weak", "shaky"), f"全未掌握却判为 {g['state']}（应偏弱）"


def test_stats_unlearned_scores_lower_than_learned(vroot):
    """方向性：同等规模下，全未掌握组的分必须严格低于全已掌握组（掌握度不反向）。"""
    for i in range(3):
        vault.write_record(_rec(id=f"u{i}", subject="甲科", question=f"未{i}",
                                created_at="2026-10-13T08:00:00"), vroot)
        vault.write_record(_rec(id=f"l{i}", subject="乙科", question=f"已{i}", status="learned",
                                created_at="2026-10-13T08:00:00"), vroot)
    vault_index.rebuild(vroot)
    g = {x["subject"]: x for x in vault_index.stats()["groups"]}
    assert g["甲科"]["score"] < g["乙科"]["score"], g
    assert g["甲科"]["state"] in ("weak", "shaky") and g["乙科"]["state"] in ("solid", "mastered")


def test_migration_preserves_miss_count(vroot):
    """反复错次数（miss_count）必须在序列化时保住——否则薄弱项密度恒为 1。"""
    vault.write_record(_rec(id="m-501", question="反复错的题", miss_count=4), vroot)
    back = vault.read_record(next(iter(vault.iter_mistake_files(vroot))))
    assert back["miss_count"] == 4, "miss_count 写进 front matter 后必须读回来"


def test_list_items_filters_by_chapter_topic(vroot):
    """薄弱项表点一行要能筛出该组错题 ⇒ `list_items` 必须支持 chapter/topic 精确过滤。"""
    vault.write_record(_rec(id="fa", subject="内科学", chapter="呼吸系统", topic="慢阻肺",
                            question="A", created_at="2026-10-13T08:00:00"), vroot)
    vault.write_record(_rec(id="fb", subject="内科学", chapter="循环系统", topic="心衰",
                            question="B", created_at="2026-10-13T09:00:00"), vroot)
    vault_index.rebuild(vroot)
    assert len(vault_index.list_items(subject="内科学")) == 2
    assert [r["question"] for r in vault_index.list_items(chapter="呼吸系统")] == ["A"]
    assert [r["question"] for r in vault_index.list_items(subject="内科学", chapter="循环系统",
                                                           topic="心衰")] == ["B"]
    # 三者都不匹配 ⇒ 空（精确等值，不是模糊命中）
    assert vault_index.list_items(subject="内科学", chapter="呼吸系统", topic="心衰") == []


# ================================================================ 接口红线
def test_section_endpoint_blocks_traversal_and_answer(vroot):
    _seed(vroot, 1)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    c = _client()
    # 路径穿越
    r = c.post("/api/vault/mistakes/..%2F..%2F..%2Fetc%2Fpasswd/section",
               json={"key": "doubts", "text": "x"})
    assert r.status_code == 400, r.text
    # 绝对路径也不是相对路径
    r = c.post("/api/vault/mistakes/%2Fetc%2Fpasswd/section",
               json={"key": "doubts", "text": "x"})
    assert r.status_code == 400, r.text
    # 答案不许通过接口改写（那是导入侧的事）
    r = c.post(f"/api/vault/mistakes/{rel}/section",
               json={"key": "answer", "text": "篡改"})
    assert r.status_code == 400, r.text
    # 三个允许的小节可以写，且真的落到了文件里
    for key, text in (("understanding", "我的理解"), ("doubts", "我的疑问")):
        r = c.post(f"/api/vault/mistakes/{rel}/section",
                   json={"key": key, "text": text})
        assert r.status_code == 200, r.text
        assert r.json()["record"][
            "my_reasoning" if key == "understanding" else "doubts"] == text
    fm, sec = vault.parse((vroot / rel).read_text(encoding="utf-8"))
    assert vault.to_record(fm, sec)["my_reasoning"] == "我的理解"
    assert vault.to_record(fm, sec)["doubts"] == "我的疑问"


def test_migration_does_not_touch_library(vroot):
    """一键迁移只往 vault 写文件，不删不改旧错题本。"""
    from medkit.core import library as lib
    lib.add_mistake({"subject": "内科学", "chapter": "呼吸系统", "question": "旧错题一",
                     "options": ["甲", "乙"], "answer": "A", "analysis": "旧解析"})
    before = [dict(r) for r in lib.list_mistakes()]
    c = _client()
    r = c.post("/api/vault/import-from-library")
    assert r.status_code == 200, r.text
    assert r.json()["written"] == 1
    assert [dict(x) for x in lib.list_mistakes()] == before, "迁移不得改动旧错题本"
    assert vault_index.count() == 1


# ================================================================ L4 Agent 硬约束
class _StubClient:
    """按预定顺序返回模型回复的假客户端（不联网、不花 Key）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.convos = []

    def chat(self, convo, **_kw):
        self.convos.append(convo)
        return self.replies.pop(0)


def _agent(monkeypatch, replies, question="慢阻肺和感染的关系是什么"):
    import medkit.agents.mistake_agent as agent
    monkeypatch.setattr(agent, "get_client", lambda role="gen": _StubClient(replies))

    async def _drain():
        return [ev async for ev in agent.ask(question)]
    return _drain


def test_agent_grounded_answer_keeps_real_citation(vroot, monkeypatch, run_coro):
    _seed(vroot, 2)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    drain = _agent(monkeypatch, [
        json.dumps({"action": "search_my_mistakes", "args": {"query": "慢阻肺"}},
                   ensure_ascii=False),
        json.dumps({"action": "final", "answer": "感染不是病因。[来源: %s#解析]" % rel,
                    "citations": [{"path": rel, "section": "解析"}]}, ensure_ascii=False)])
    ev = run_coro(drain())[-1]
    assert ev["type"] == "result" and ev["grounded"] is True
    assert ev["citations"] == [{"path": rel, "section": "解析"}]
    assert [t["tool"] for t in ev["tools"]] == ["search_my_mistakes"]


def test_agent_unfounded_knowledge_is_blocked(vroot, monkeypatch, run_coro):
    """**最重要的一条**：一次工具都没调 ⇒ 不许用通识作答。"""
    _seed(vroot, 2)
    vault_index.rebuild(vroot)
    drain = _agent(monkeypatch, [
        json.dumps({"action": "final", "answer": "慢阻肺由吸烟引起，属于环境致病因素。"},
                   ensure_ascii=False)])
    ev = run_coro(drain())[-1]
    assert ev["grounded"] is False
    assert "没有相关记录" in ev["answer"], "必须改口说不知道，而不是转述模型通识"


def test_agent_fabricated_citation_is_dropped(vroot, monkeypatch, run_coro):
    _seed(vroot, 2)
    vault_index.rebuild(vroot)
    drain = _agent(monkeypatch, [
        json.dumps({"action": "final", "answer": "bla [来源: mistakes/不存在.md#解析]",
                    "citations": [{"path": "mistakes/不存在.md"}]}, ensure_ascii=False)])
    ev = run_coro(drain())[-1]
    assert ev["citations"] == []
    assert ev["dropped_citations"] == ["mistakes/不存在.md"]
    assert "不存在.md" in ev["answer"], "剔除无效引用必须让用户看见"


def test_agent_tool_failure_is_explicit(vroot, monkeypatch, run_coro):
    _seed(vroot, 1)
    vault_index.rebuild(vroot)
    drain = _agent(monkeypatch, [
        json.dumps({"action": "read_file", "args": {"path": "mistakes/nope.md"}},
                   ensure_ascii=False),
        json.dumps({"action": "final", "answer": "上文工具坏了。"}, ensure_ascii=False)])
    ev = run_coro(drain())[-1]
    assert ev["tools"] and ev["tools"][0]["ok"] is False
    assert "read_file" in ev["answer"] and "失败" in ev["answer"], "工具失败必须显性"


def test_agent_stats_only_answer_is_allowed(vroot, monkeypatch, run_coro):
    """「你哪个章节最弱」这类问题天然没有单文件引用——允许，但前提是真调了统计工具。"""
    _seed(vroot, 2)
    vault_index.rebuild(vroot)
    drain = _agent(monkeypatch, [
        json.dumps({"action": "get_topic_stats", "args": {}}, ensure_ascii=False),
        json.dumps({"action": "final", "answer": "你的薄弱项是慢阻肺。", "citations": []},
                   ensure_ascii=False)], question="我最弱的是哪章？")
    ev = run_coro(drain())[-1]
    assert ev["grounded"] is True


def test_agent_round_cap_is_reported(vroot, monkeypatch, run_coro):
    """轮数用尽仍未收敛：如实注明，不许假装给了完整答案。"""
    _seed(vroot, 2)
    vault_index.rebuild(vroot)
    drain = _agent(monkeypatch,
                   [json.dumps({"action": "get_topic_stats", "args": {}},
                               ensure_ascii=False)] * 6)
    ev = run_coro(drain())[-1]
    assert "收敛" in ev["note"] and len(ev["tools"]) == 4


# ================================================================ SSE 接线
def test_ask_endpoint_streams_and_rejects_empty(vroot, monkeypatch):
    """`/api/vault/ask` 接线：空问题 400；正常问题把 Agent 事件流成 SSE 并以 `[DONE]` 收尾。

    这条不依赖任何 API Key——把 Agent 换成桩，只验证路由层的 SSE 封装与空值拦截。
    覆盖的正是「文件夹全域问」（`path=""`）走的那条路径。
    """
    import medkit.routers.vault as rv

    seen: dict[str, str] = {}

    async def _fake_ask(question, *, scope="", path=""):
        seen.update(question=question, scope=scope, path=path)
        yield {"type": "stage", "hint": "思考中"}
        yield {"type": "result", "answer": f"回答：{question}", "citations": [],
               "grounded": True, "note": ""}

    monkeypatch.setattr(rv, "_agent_ask", _fake_ask)
    c = _client()
    assert c.post("/api/vault/ask", json={"question": "   "}).status_code == 400
    with c.stream("POST", "/api/vault/ask", json={"question": "我最弱的是哪章？"}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert '"type": "result"' in body and "回答：我最弱的是哪章？" in body
    assert body.rstrip().endswith("[DONE]") or "[DONE]" in body
    assert seen == {"question": "我最弱的是哪章？", "scope": "", "path": ""}, seen


