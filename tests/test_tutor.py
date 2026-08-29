"""M4 提问式学习测试：状态机纯逻辑 + 会话持久化 + medtutor mock + 路由 TestClient。

隔离：monkeypatch lib/expl/tut 的存储路径到临时目录；不发起真实 LLM（全部 mock）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.agents.medtutor as mt  # noqa: E402
import medkit.core.explain as expl  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.core.tutor as tut  # noqa: E402
import medkit.main as m  # noqa: E402
import medkit.routers.library as r_lib  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", libd / "tutor_sessions.json")
    # 提问路由会检索教材切片：隔离 explain 的索引/项目根 → 空目录（grounded 确定性为 False）
    monkeypatch.setattr(expl, "LIBRARY_DIR", libd)
    monkeypatch.setattr(expl, "SLICE_INDEX_FILE", libd / "slice_index.json")
    monkeypatch.setattr(expl, "EXPLAINS_FILE", libd / "explains.json")
    projs = tmp_path / "projects"
    projs.mkdir()
    monkeypatch.setattr(expl, "_PROJ_ROOT", projs)
    return tmp_path


# ---------------------------------------------------------------- 状态机纯逻辑
def test_next_state_chain():
    assert tut.next_state("weak") == "shaky"
    assert tut.next_state("shaky") == "solid"
    assert tut.next_state("solid") == "mastered"
    assert tut.next_state("mastered") == "mastered"


def test_apply_score_pass_streak_advances():
    # 连续 pass（≥2）两次 → 升一档
    st, stk = tut.apply_score("weak", 0, 2)
    assert (st, stk) == ("weak", 1)
    st, stk = tut.apply_score("weak", stk, 3)
    assert (st, stk) == ("shaky", 0)


def test_apply_score_fail_resets_streak_no_downgrade():
    st, stk = tut.apply_score("weak", 1, 1)     # 虽有连击却判低分 → 打断
    assert (st, stk) == ("weak", 0)
    st, stk = tut.apply_score("solid", 2, 0)    # 高分状态遇 0 分也不降档
    assert (st, stk) == ("solid", 0)


def test_apply_score_clamps():
    assert tut.apply_score("weak", 0, 5)[1] <= 3
    assert tut.apply_score("weak", 0, -1)[1] == 0


def test_next_question_type_cycle_and_retry():
    assert tut.next_question_type("contrast", result_score=3) == "predict"
    assert tut.next_question_type("trace", result_score=3) == "explain"   # 循环回卷
    assert tut.next_question_type("apply", result_score=1) == "apply"     # 有差距 → 同类追问


# ---------------------------------------------------------------- 会话持久化
def test_start_and_seed_first(isolated):
    s = tut.start_session("儿科学", "支气管肺炎首选治疗")
    assert s["id"].startswith("tu_") and s["state"] == "weak"
    assert s["current"]["type"] == "explain"
    tut.seed_first(s["id"], "explain", "请解释首选阿莫西林的机制")
    got = tut.get_session(s["id"])
    assert got["current"]["text"].startswith("请解释")


def test_record_answer_advances_state_and_rotates(isolated):
    s = tut.start_session("儿科学", "支气管肺炎首选治疗")
    tut.seed_first(s["id"], "explain", "Q1")
    # 第一轮 pass（score=2）→ streak=1，下一问切到 apply
    s = tut.record_answer(s["id"], "答：因为阿莫西林…", 2, "很好", "Q2")
    assert len(s["rounds"]) == 1 and s["streak"] == 1 and s["state"] == "weak"
    assert s["current"]["type"] == "apply"
    # 第二轮 pass（score=3）→ 连击达线 → 升到 shaky
    s = tut.record_answer(s["id"], "答案B，机制是……", 3, "优", "Q3")
    assert len(s["rounds"]) == 2 and s["state"] == "shaky" and s["streak"] == 0
    assert s["current"]["type"] == "contrast"


def test_record_answer_low_score_continues_same_type(isolated):
    s = tut.start_session("儿科学", "x")
    tut.seed_first(s["id"], "contrast", "Q1")
    s = tut.record_answer(s["id"], "不会", 0, "差距", "再试")
    assert len(s["rounds"]) == 1 and s["state"] == "weak"
    assert s["current"]["type"] == "contrast"     # 同类追问


def test_quiz_writes_back_mastery(isolated):
    lib.add_mistake({"question": "q", "know_tags": ["支气管肺炎首选治疗"], "subject": "儿科学"})
    kp_before = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    miss0 = kp_before["miss"]
    lib.record_quiz("支气管肺炎首选治疗", 2)     # pass
    lib.record_quiz("支气管肺炎首选治疗", 1)     # fail
    kp = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    assert kp["attempts"] == miss0 + 2
    assert any(h["event"] == "quiz" for h in kp["history"])


def test_cleanup_stale_sessions(isolated):
    """C18：清理无活动会话——保留最近活跃，删除 days 天前无活动的。"""
    from datetime import datetime, timedelta

    from medkit.core import tutor as tut_mod

    old = datetime.now() - timedelta(days=60)
    tut.start_session("儿科学", "支气管肺炎首选治疗")
    tut.start_session("儿科学", "旧会话1")
    tut.start_session("儿科学", "旧会话2")
    sessions = tut.list_sessions()
    old1, old2 = sessions[1], sessions[2]
    for s in (old1, old2):
        data = tut_mod._load()
        for x in data:
            if x["id"] == s["id"]:
                x["updated_at"] = old.isoformat(timespec="seconds")
        tut_mod._save(data)
    removed = tut.cleanup_stale(30)
    assert removed == 2, "60 天前无活动的 2 场应被清理"
    remain = tut.list_sessions()
    assert len(remain) == 1
    assert remain[0]["kp_name"] == "支气管肺炎首选治疗"


def test_router_tutor_cleanup(mock_agents, isolated):
    """C18 路由：/api/library/tutor/cleanup 带确认语义（仅清理，返回删数）。"""
    from datetime import datetime, timedelta

    from medkit.core import tutor as tut_mod

    c = TestClient(m.app, base_url="http://127.0.0.1")
    tut.start_session("儿科学", "活跃会话")
    old = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
    data = tut_mod._load()
    data.append({"id": "tu_old_1", "subject": "儿科学", "kp_name": "弃会话",
                 "state": "weak", "streak": 0, "current": {"type": "explain", "text": ""},
                 "rounds": [], "created_at": old, "updated_at": old})
    tut_mod._save(data)
    r = c.post("/api/library/tutor/cleanup", json={"days": 30})
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert len(tut.list_sessions()) == 1


# ---------------------------------------------------------------- medtutor mock
class _FakeClient:
    def __init__(self, reply="", json_reply=None):
        self.reply = reply
        self.json_reply = json_reply
        self.last_messages = None

    def chat(self, messages, temperature=0.7):
        self.last_messages = messages
        return self.reply

    def chat_json(self, messages, temperature=0.7):
        self.last_messages = messages
        return self.json_reply


def test_start_applying_returns_question(isolated):
    client = _FakeClient(reply="请说说首选阿莫西林的原因")
    q = mt.start_applying(client, "儿科学", "支气管肺炎首选治疗", "weak", "explain", "切片…")
    assert "阿莫西林" in q or "原因" in q


def test_start_applying_no_slices_explains_and_uses_knowledge(isolated):
    """无原文回退：无切片 → 注入「未检索到原文」说明 + 模型常识引导。"""
    client = _FakeClient(reply="请谈谈机制")
    mt.start_applying(client, "儿科学", "新型考点X", "weak", "explain", "")
    inject = client.last_messages[1]["content"]
    assert "未检索到" in inject
    assert "医学常识" in inject


def test_start_applying_injects_web_materials_when_no_slices(isolated):
    """无原文回退：无切片 + 网络素材 → 素材与说明一并注入。"""
    client = _FakeClient(reply="请谈谈")
    mats = [{"title": "诊疗指南2024", "url": "https://g.cn/1", "snippet": "阿莫西林首选"}]
    mt.start_applying(client, "儿科学", "X", "weak", "explain", "", web_materials=mats)
    inject = client.last_messages[1]["content"]
    assert "网络检索补充素材" in inject
    assert "https://g.cn/1" in inject
    assert "未检索到" in inject


def test_score_answer_injects_web_materials(isolated):
    """无原文回退：判分追问同样注入网络素材与说明。"""
    client = _FakeClient(json_reply={"score": 2, "gap": "机制要补",
                                     "next_question": "试解释耐药机制",
                                     "next_type": "apply"})
    mats = [{"title": "指南", "url": "https://g.cn/2", "snippet": "s"}]
    mt.score_answer(client, "儿科学", "X", "weak", "explain", "Q", "答：……",
                    slices_text="", history=[], web_materials=mats)
    inject = client.last_messages[1]["content"]
    assert "网络检索补充素材" in inject and "未检索到" in inject


def test_score_answer_parses_json(isolated):
    client = _FakeClient(json_reply={"score": 2, "gap": "机制要补",
                                     "next_question": "试解释耐药机制",
                                     "next_type": "apply"})
    r = mt.score_answer(client, "儿科学", "支气管肺炎首选治疗", "weak",
                        "explain", "Q", "答：因为阿莫西林覆盖",
                        slices_text="切片…", history=[])
    assert r["score"] == 2 and r["gap"] == "机制要补"
    assert r["next_type"] == "apply"


def test_score_answer_falls_back_to_heuristic(isolated):
    # json 无合法 score → 启发式兜底：新口径无法定量判定 → 一律 -1（不计分重答）
    client = _FakeClient(json_reply={"bad": "not a judgement"})
    r = mt.score_answer(client, "儿科学", "x", "weak", "explain", "Q",
                        "因为阿莫西林能覆盖主要致病菌并口服吸收好", slices_text="")
    assert r["score"] == -1 and r["next_question"]


# ---------------------------------------------------------------- 路由（TestClient + mock LLM）
@pytest.fixture()
def mock_agents(monkeypatch):
    def _start(client, subject, kp_name, state, qtype, slices_text="", web_materials=None):
        return "请解释首选阿莫西林的原因"

    def _score(client, subject, kp_name, state, qtype, question, user_answer,
               slices_text="", history=None, web_materials=None):
        return {"score": 2, "gap": "机制可再补", "next_question": "试解释耐药机制",
                "next_type": "apply"}

    monkeypatch.setattr(mt, "start_applying", _start)
    monkeypatch.setattr(mt, "score_answer", _score)
    monkeypatch.setattr(r_lib, "_tutor_client", lambda: _FakeClient())
    monkeypatch.setattr(r_lib, "_resolve_search_fn", lambda: None)  # 测试离线：不解析真实检索后端
    return None


def test_router_tutor_flow(mock_agents, isolated):
    lib.add_mistake({"question": "q",
                     "know_tags": ["支气管肺炎首选治疗"], "subject": "儿科学"})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/tutor/start", json={"subject": "儿科学",
                                                 "kp_name": "支气管肺炎首选治疗"})
    assert r.status_code == 200, r.text
    j = r.json()
    sid = j["session"]["id"]
    assert j["state"] == "weak" and j["question"].startswith("请解释")
    # 无原文回退：空项目根 → grounded=False + 说明文案（前端提示数据源）
    assert j["grounded"] is False
    assert "未在本地教材中检索到原文" in j["note"]

    # 会话已建 + 可列出
    lst = c.get("/api/library/tutor/sessions")
    assert lst.status_code == 200 and lst.json()["total"] == 1

    # 提交作答 → 判分 + 状态推进
    a = c.post("/api/library/tutor/answer", json={"session_id": sid,
                                                  "user_answer": "因为阿莫西林"})
    assert a.status_code == 200, a.text
    aj = a.json()
    assert aj["score"] == 2 and len(aj["session"]["rounds"]) == 1
    assert aj["next_question"]["type"] == "apply"
    assert aj["grounded"] is False   # 无原文回退：判分响应同样携带 grounded 标记

    # 恢复会话
    got = c.get(f"/api/library/tutor/{sid}")
    assert got.status_code == 200 and len(got.json()["session"]["rounds"]) == 1
    # 掌握度已回写
    kp = next(k for k in lib.list_knowledge() if k["name"] == "支气管肺炎首选治疗")
    assert kp["attempts"] >= 1


def test_router_tutor_bad_session(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    assert c.post("/api/library/tutor/answer",
                  json={"session_id": "nope", "user_answer": "x"}).status_code == 404


def test_router_tutor_requires_kp(mock_agents, isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    assert c.post("/api/library/tutor/start", json={}).status_code == 400


def test_delete_session_isolation(isolated):
    s = tut.start_session("儿科学", "x")
    assert tut.delete_session(s["id"]) is True
    assert tut.get_session(s["id"]) is None
    assert tut.delete_session(s["id"]) is False


def test_router_tutor_delete_flow(mock_agents, isolated):
    lib.add_mistake({"question": "q",
                     "know_tags": ["支气管肺炎首选治疗"], "subject": "儿科学"})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/tutor/start", json={"subject": "儿科学",
                                                 "kp_name": "支气管肺炎首选治疗"})
    sid = r.json()["session"]["id"]
    assert c.delete(f"/api/library/tutor/{sid}").status_code == 200
    assert c.delete(f"/api/library/tutor/{sid}").status_code == 404
    assert c.delete("/api/library/tutor/nope").status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
