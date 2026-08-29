"""M3 讲解测试：切片索引/检索 + medexplain mock + 产物 CRUD + 路由 TestClient。

隔离：monkeypatch lib/expl 的存储路径到临时目录；_PROJ_ROOT 指到临时项目目录。
不发起真实 LLM / 网络检索（全部 mock）。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.agents.medexplain as mex  # noqa: E402
import medkit.core.explain as expl  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402
import medkit.routers.library as r_lib  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(expl, "LIBRARY_DIR", libd)
    monkeypatch.setattr(expl, "SLICE_INDEX_FILE", libd / "slice_index.json")
    monkeypatch.setattr(expl, "EXPLAINS_FILE", libd / "explains.json")
    # 临时项目根：造一个含教材切片与 meta.subject 的项目
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    (proj / "meta.json").write_text(json.dumps({"subject": "儿科学"}), encoding="utf-8")
    slices = [
        {"sid": "S001", "title": "支气管肺炎", "role": "textbook",
         "text": "支气管肺炎多见于婴幼儿，起病急，发热咳嗽气促，双肺可闻及中小水泡音。"
                 "首选青霉素族/阿莫西林。", "source": "儿科书", "page": "p120"},
        {"sid": "S002", "title": "哮喘鉴别", "role": "textbook",
         "text": "哮喘以阵发性喘憋为主，多有家族史，发作时肺部以哮鸣音为主。",
         "source": "儿科书", "page": "p145"},
    ]
    (proj / "slices.json").write_text(json.dumps(slices), encoding="utf-8")
    monkeypatch.setattr(expl, "_PROJ_ROOT", tmp_path / "projects")
    return tmp_path


# ---------------------------------------------------------------- 切片索引 / 检索
def test_index_slices_by_subject(isolated):
    idx = expl.index_slices()
    assert "儿科学" in idx["subjects"]
    assert len(idx["subjects"]["儿科学"]) == 2
    assert idx["subjects"]["儿科学"][0]["pid"] == "p1"
    assert "_norm" in idx["subjects"]["儿科学"][0]


def test_index_slices_cache_hit_no_rescan(isolated, monkeypatch):
    """D-13：按 (文件, mtime, size) 缓存——命中不重扫不重建 FTS；文件变更自动失效重建。"""
    calls = {"writes": 0}
    orig_write = expl.write_json_atomic
    def counting(path, data):
        calls["writes"] += 1
        return orig_write(path, data)
    monkeypatch.setattr(expl, "write_json_atomic", counting)

    idx1 = expl.index_slices()
    assert calls["writes"] == 1
    idx2 = expl.index_slices()
    assert calls["writes"] == 1, "缓存命中不应重扫/重建（不写索引文件）"
    assert idx2 is idx1, "缓存命中应返回同一份结果"

    # 文件变更（写入后 mtime/size 变化）→ 自动失效重建
    p = Path(expl._PROJ_ROOT) / "p1" / "slices.json"
    extra = json.loads(p.read_text(encoding="utf-8"))
    extra.append({"sid": "S003", "title": "新增", "role": "textbook",
                  "text": "新增切片内容。"})
    p.write_text(json.dumps(extra), encoding="utf-8")
    idx3 = expl.index_slices()
    assert calls["writes"] == 2, "文件写入后应触发重建"
    assert len(idx3["subjects"]["儿科学"]) == 3


def test_retrieve_by_subject_and_keyword(isolated):
    expl.index_slices()
    hits = expl.retrieve(subject="儿科学", query="支气管肺炎首选")
    assert hits  # 命中教材切片
    assert hits[0]["title"] == "支气管肺炎"
    # 关键词命中排序：Note the first slice contains both tokens
    names = [h["title"] for h in hits]
    assert names[0] == "支气管肺炎"


def test_retrieve_subject_mismatch_falls_back(isolated):
    expl.index_slices()
    hits = expl.retrieve(subject="外科学", query="水泡音")
    assert any(h["title"] == "支气管肺炎" for h in hits)


def test_slice_text_of_labels_source(isolated):
    expl.index_slices()
    hits = expl.retrieve(subject="儿科学", query="支气管肺炎")
    txt = expl.slice_text_of(hits)
    assert "教材切片" in txt and "儿科书" in txt


# ---------------------------------------------------------------- medexplain mock
class _FakeClient:
    def __init__(self, reply=None):
        self.reply = reply or ("**结论先行**：…\n\n**机制**：…\n\n**鉴别**：…\n\n**记忆锚点**：…")

    def chat(self, messages, temperature=0.7):
        # 记录注入内容用于断言
        self.last_messages = messages
        return self.reply

    def chat_json(self, messages, temperature=0.7):
        return {}


def test_explain_with_slices_uses_textbook(isolated):
    expl.index_slices()
    hits = expl.retrieve(subject="儿科学", query="支气管肺炎")
    client = _FakeClient()
    r = mex.explain_knowledge(client, "儿科学", "支气管肺炎首选治疗",
                              slices_text=expl.slice_text_of(hits),
                              related_mistake=None, use_web=True)
    assert "结论先行" in r["content"]
    assert any(s["kind"] == "textbook" for s in r["sources"])
    # 切片不足 120 字 → 触发联网补充（无 search_fn → 不联网，via_web=False）
    assert r["via_web"] is False  # search_fn 为 None，联网失败被隔离
    assert r["grounded"] is True   # 命中切片 → 有原文依据


def test_explain_web_supplement_when_slices_short(isolated):
    client = _FakeClient()
    # 无切片 → 联网补充
    search_fn = lambda q: [  # noqa: E731
        {"title": "支气管肺炎诊疗指南", "url": "https://example.com/guide",
         "snippet": "阿莫西林为一线首选；重症加镇静等"}]
    r = mex.explain_knowledge(client, "儿科学", "支气管肺炎首选治疗",
                              slices_text="", related_mistake=None,
                              search_fn=search_fn, use_web=True)
    assert r["via_web"] is True
    assert any(s["kind"] == "web" for s in r["sources"])
    inject = client.last_messages[1]["content"]
    assert "网络检索补充素材" in inject and "example.com" in inject


def test_explain_no_web_when_flag_off(isolated):
    client = _FakeClient()
    search_fn = lambda q: [{"title": "t", "url": "u", "snippet": "s"}]  # noqa: E731
    r = mex.explain_knowledge(client, "儿科学", "x", slices_text="",
                              search_fn=search_fn, use_web=False)
    assert r["via_web"] is False


def test_explain_no_slices_explains_then_uses_model_knowledge(isolated):
    """无原文回退：切片/网络都没有 → 注入说明文案 + 要求模型知识输出（grounded=False）。"""
    client = _FakeClient()
    r = mex.explain_knowledge(client, "儿科学", "新型考点X", slices_text="",
                              related_mistake=None, use_web=False)
    assert r["grounded"] is False
    inject = client.last_messages[1]["content"]
    assert "未检索到" in inject          # 先说明：本地教材无原文
    assert "医学知识" in inject          # 再输出：结合模型知识
    assert "不要标注【教材】" in inject  # 不谎称出自教材


def test_explain_web_only_grounded_false_with_disclosure(isolated):
    """无原文回退：仅网络素材 → 说明 + 网络素材注入，grounded=False。"""
    client = _FakeClient()
    search_fn = lambda q: [{"title": "诊疗指南", "url": "https://g.cn/1",  # noqa: E731
                            "snippet": "阿莫西林为一线首选"}]
    r = mex.explain_knowledge(client, "儿科学", "X", slices_text="",
                              search_fn=search_fn, use_web=True)
    assert r["grounded"] is False and r["via_web"] is True
    inject = client.last_messages[1]["content"]
    assert "未检索到" in inject and "https://g.cn/1" in inject


# ---------------------------------------------------------------- 产物 CRUD
def test_explain_crud(isolated):
    base = {"id": "ex_1", "subject": "儿科学", "kp_name": "支气管肺炎首选治疗",
            "created_at": "2026-08-27T10:00:00", "content": "内容A",
            "sources": [{"kind": "textbook", "title": "支气管肺炎", "url": ""}]}
    lib.log_knowledge_event("支气管肺炎首选治疗", "explain")  # 无此知识点 → None，不炸
    expl.save_explain(base)
    assert len(expl.list_explains()) == 1
    assert expl.list_explains(subject="外科学") == []
    assert expl.get_explain("ex_1")["content"] == "内容A"
    md = expl.export_subject_md("儿科学")
    assert "支气管肺炎首选治疗" in md and "# 学习讲解手册" in md


def test_delete_explain(isolated):
    expl.save_explain({"id": "ex_2", "subject": "儿科学", "kp_name": "x",
                       "created_at": "2026-08-27T10:00:00", "content": "c"})
    assert expl.delete_explain("ex_2") is True
    assert expl.delete_explain("ex_2") is False


# ---------------------------------------------------------------- 路由（TestClient + mock LLM/搜索后端）
@pytest.fixture()
def mock_agents(monkeypatch):
    def _fake_explain(client, subject, kp_name, slices_text="",
                      related_mistake=None, web_materials=None, search_fn=None,
                      use_web=True):
        return {"content": "**结论先行**：首选阿莫西林。（【教材·支气管肺炎】）",
                "sources": [{"kind": "textbook", "title": "支气管肺炎", "url": ""}],
                "via_web": False, "web_materials": [], "grounded": True}
    monkeypatch.setattr(mex, "explain_knowledge", _fake_explain)
    monkeypatch.setattr(r_lib, "_explain_client", lambda: _FakeClient())
    monkeypatch.setattr(r_lib, "_resolve_search_fn", lambda: None)  # 测试离线：不解析真实检索后端
    return None


def test_router_subjects(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    c.post("/api/library/mistakes", json={"question": "q", "know_tags": ["A"],
                                          "subject": "儿科学"})
    r = c.get("/api/library/subjects")
    assert r.status_code == 200 and "儿科学" in r.json()["subjects"]
    # v0.8.1：每科统计随清单返回（刷题页科目卡片数据源，全部本地计算）
    stats = {s["subject"]: s for s in r.json()["stats"]}
    assert stats["儿科学"]["mistakes"] == 1
    assert stats["儿科学"]["knowledge"] == 1
    assert stats["儿科学"]["mastered_rate"] == 0
    assert {"review_total", "review_due", "review_new"} <= set(stats["儿科学"])


def test_router_explain_end_to_end(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/explain", json={
        "subject": "儿科学", "kp_name": "支气管肺炎首选治疗", "use_web": True})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["title"] == "支气管肺炎首选治疗"
    assert "结论先行" in j["explain"]["content"]
    assert j["explain"]["subject"] == "儿科学"
    assert j["explain"]["grounded"] is True   # 命中教材切片 → 有原文依据
    assert any(s["kind"] == "textbook" for s in j["explain"]["sources"])

    # 产物已存在 + 按科目过滤
    lst = c.get("/api/library/explains?subject=儿科学")
    assert lst.status_code == 200 and lst.json()["total"] == 1
    eid = j["explain"]["id"]
    got = c.get(f"/api/library/explains/{eid}")
    assert got.status_code == 200

    # 导出 markdown
    exp = c.post("/api/library/explains/export", params={"subject": "儿科学"})
    assert exp.status_code == 200 and "学习讲解手册" in exp.json()["markdown"]

    # 删除
    assert c.delete(f"/api/library/explains/{eid}").status_code == 200
    assert c.get("/api/library/explains").json()["total"] == 0


def test_router_explain_no_kp_raises(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    assert c.post("/api/library/explain", json={"subject": "儿科学",
                                                "kp_name": ""}).status_code == 400


def test_router_explain_ungrounded_records_flag(mock_agents, isolated):
    """无原文回退：切片未命中 → 产物记录 grounded=False（前端展示说明用）。"""
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/explain", json={
        "subject": "儿科学", "kp_name": "不存在的知识点xyz", "use_web": True})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["explain"]["grounded"] is False
    assert j["explain"]["slices_used"] == []


def test_router_explain_slices_info(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/library/explain/slices", params={"subject": "儿科学"})
    assert r.status_code == 200 and r.json()["count"] == 2


def test_router_explain_slices_keyword_filter(mock_agents, isolated):
    """v0.7.2 复习「查看提示」：query 关键词 top-k（零 LLM）。"""
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/library/explain/slices", params={"subject": "儿科学", "query": "哮喘"})
    j = r.json()
    assert j["query"] == "哮喘"
    assert len(j["slices"]) == 1 and j["slices"][0]["title"] == "哮喘鉴别", "关键词应过滤到命中切片"
    r2 = c.get("/api/library/explain/slices", params={"subject": "儿科学", "query": "不存在的关键词xyz"})
    assert r2.json()["slices"] == [], "无命中 → 空列表（前端提示补充素材）"
    # 空 query 保持浏览语义（不按关键词截断）
    r3 = c.get("/api/library/explain/slices", params={"subject": "儿科学", "limit": 3})
    assert len(r3.json()["slices"]) == 2


def test_knowledge_history_written_after_explain(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    c.post("/api/library/mistakes", json={"question": "q", "know_tags": ["支气管肺炎首选治疗"],
                                          "subject": "儿科学", "learned": False})
    c.post("/api/library/explain", json={"subject": "儿科学",
                                         "kp_name": "支气管肺炎首选治疗"})
    kp = next(k for k in lib.list_knowledge()
              if k["name"] == "支气管肺炎首选治疗")
    assert any(h["event"] == "explain" for h in kp.get("history", []))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
