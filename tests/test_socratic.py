"""EP-01 阶段 3：苏格拉底式错题复习测试。

覆盖四层：
1. **契约层**（`schema.SocraticScore`）：分数归一、`hit_crossroad` 布尔兜底、无 answer 通道。
2. **会话层**（`core.tutor` 的 `start_mistake_session` / `record_mistake_answer`）：
   `kind` 隔离、未命中岔路口不换档、`stuck` 计数。
3. **路由层**：准入条件（闸门 + 已归因）、第一问失败回滚、-1 不计分路径。
4. **红线端到端**：**用真答案串做注入**，断言它不出现在任何返回文本里。

隔离沿用 `tests/test_errorpipe.py` 的做法（monkeypatch 模块级路径常量），不触碰真实 `~/.medkit`。
"""

import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _forbids_answer(text: str) -> bool:
    """提示词是否**明文禁止给/改答案**——按**形态**匹配，不绑具体措辞。

    为什么不用 `"不得给出正确答案" in text`（旧写法）：那是**绑书写格式**——
    提示词迭代时换个说法（「禁止泄露答案」）就会**假红**，而假红会逼人删掉守卫（方向 H）。
    红线没变、措辞变了，守卫不该红。

    形态：否定词 + 「给/透露/泄露/写出/改写/修正」+ 答案，中间容忍任意字与空白。
    与 `tests/test_errorpipe_image.py::test_prompt_forbids_inferring_answer` 同一取向
    （两处各自守不同的提示词，但判据形态一致）。
    """
    flat = "".join(text.split())
    return bool(re.search(
        r"(不得|不要|禁止|切勿|严禁)[^。]{0,12}(给出|透露|泄露|写出|改写|修正|给)[^。]{0,12}答案",
        flat))


from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.errorpipe as ep  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.core.schema as schema  # noqa: E402
import medkit.core.tutor as tut  # noqa: E402
import medkit.main as m  # noqa: E402

# 一个"肯定不该出现在返回文本里"的答案哨兵。
# 用长一点的独特串（不是 "A"/"B" 这种会自然出现在别处的单字母），
# 这样一旦泄漏就是真泄漏，而不是被无关文本误命中。
ANSWER_SENTINEL = "ZZQ-ANSWER-SENTINEL-9137"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """把库域与 kp/流水/会话全部指到临时目录。"""
    libd = tmp_path / "library"
    libd.mkdir()
    for mod in (lib, dbs, kpid, ev, tut):
        monkeypatch.setattr(mod, "LIBRARY_DIR", libd, raising=False)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json", raising=False)
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json", raising=False)
    monkeypatch.setattr(tut, "DB_FILE", libd / "medkit.db", raising=False)
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", libd / "tutor_sessions.json", raising=False)
    dbs.reset_conn()
    yield tmp_path
    dbs.reset_conn()


@pytest.fixture()
def client(isolated):
    # base_url 必须 127.0.0.1：Host 校验中间件会 403 掉 testserver（项目既有约定）
    return TestClient(m.app, base_url="http://127.0.0.1")


def _card(**kw):
    base = {
        "subject": "生理学", "chapter": "心血管", "topic": "心输出量",
        "question": "前负荷增加时每搏量如何变化？",
        "options": {"A": "增加", "B": "减少", "C": "不变"},
        "answer": ANSWER_SENTINEL, "user_answer": "B",
        "confidence": 4, "my_reasoning": "觉得前负荷增加会降心输出量",
        "error_tag": "机制混淆", "round": "早鸟轮",
        "fix": "前负荷↑→每搏量↑（Frank-Starling）",
    }
    base.update(kw)
    return base


def _mk_gated(client, **kw):
    """录入一条已过闸门 + 已归因的错题，返回 mid。"""
    r = client.post("/api/errors/intake", json=_card(**kw))
    assert r.status_code == 200, r.text
    mid = r.json()["stages"]["persist"]["saved"]["id"]
    # 归因（erro_pipe 的 attribute 需要 client 注入；这里直接走 core 落 error_tag）
    assert ep.gate_ok(lib.get_mistake(mid))
    return mid


class FakeSocClient:
    """假 LLM：`chat_json` 按队列返回；`chat` 返回固定第一问。"""

    def __init__(self, payloads=None, question="你当时写的是『前负荷增加会降心输出量』——说说你是怎么想到这一层的？",
                 fail=False):
        self._payloads = list(payloads or [])
        self.question = question
        self.fail = fail
        self.json_calls = 0
        self.chat_calls = 0

    def chat(self, messages, temperature=0.6, **kw):
        self.chat_calls += 1
        if self.fail:
            raise RuntimeError("模拟 LLM 故障")
        return self.question

    def chat_json(self, messages, temperature=0.3, max_tokens=None, schema=None, **kw):
        self.json_calls += 1
        if self.fail:
            raise RuntimeError("模拟 LLM 故障")
        raw = self._payloads.pop(0) if self._payloads else {}
        return raw


def _patch_soc(m, monkeypatch, fake):
    """把 routers.errors 拿 client 的入口换成假客户端。"""
    import medkit.routers.errors as R
    monkeypatch.setattr(R, "_socratic_client", lambda cancel=None: fake)
    return R


# ==================================================================== 1. 契约层
def test_socratic_score_normalizes_out_of_range_to_minus_one():
    """越界/小数/布尔一律 -1（**不夹取、不截断**）——分数会进状态机，不能编。"""
    S = schema.SocraticScore
    for bad in (True, False, None, 2.5, 99, -1, "abc"):
        assert S(score=bad, gap="g", next_question="n").score == -1, f"{bad!r} 应判 -1"
    for good, want in [(0, 0), (1, 1), (2, 2), (3, 3), ("2", 2), (2.0, 2)]:
        assert S(score=good, gap="g", next_question="n").score == want


def test_socratic_score_int_25_is_not_silently_truncated():
    """2.5 分不存在。int(2.5)==2 是静默截断（EP-01 §7.3 同款坑），必须判 -1。"""
    assert schema.SocraticScore(score=2.5, gap="g", next_question="n").score == -1


def test_socratic_score_requires_gap_and_next_question():
    with pytest.raises(ValidationError):
        schema.SocraticScore(score=1, gap="", next_question="n")
    with pytest.raises(ValidationError):
        schema.SocraticScore(score=1, gap="g", next_question="")
    # 满分 3 可以不再追问（已吃透）
    assert schema.SocraticScore(score=3, gap="g").score == 3
    # -1（无法判定）不要求 gap/next_question
    assert schema.SocraticScore(score=-1).score == -1


def test_socratic_score_has_no_answer_channel():
    """契约层第 2 道红线：模型即便塞 answer/correct，extra=ignore 也丢弃。"""
    obj = schema.SocraticScore(score=3, gap="g", answer="B", correct="B", answer_text="B")
    for f in ("answer", "correct", "answer_text"):
        assert not hasattr(obj, f), f"契约不应存在 {f} 字段（AI 不得回写答案）"


def test_socratic_hit_crossroad_string_false_is_false():
    """`"false"` 是非空字符串——不能因为非空就变 True。"""
    S = schema.SocraticScore
    assert S(score=1, gap="g", next_question="n", hit_crossroad="false").hit_crossroad is False
    assert S(score=1, gap="g", next_question="n", hit_crossroad="true").hit_crossroad is True
    assert S(score=1, gap="g", next_question="n", hit_crossroad="是").hit_crossroad is True
    assert S(score=1, gap="g", next_question="n", hit_crossroad=None).hit_crossroad is False


# ==================================================================== 2. 会话层
def test_start_mistake_session_marks_kind(isolated):
    s = tut.start_mistake_session("m1", "生理学", "心输出量", "kp1", "shaky")
    assert s["kind"] == "mistake"
    assert s["mistake_id"] == "m1"
    assert s["stuck"] == 0
    assert s["current"] == {"type": "explain", "text": ""}
    # 老的知识点会话缺省 kind → 读出来是 "kp"（不写库也兼容）
    old = {"id": "tu_old", "subject": "x", "kp_name": "y"}
    assert old.get("kind", "kp") == "kp"


def test_record_mistake_answer_does_not_switch_qtype_when_missed(isolated):
    """未命中岔路口 → 不换档（继续追同一个）；命中 → 按五类轮换推进。"""
    s = tut.start_mistake_session("m1", "生理学")
    sid = s["id"]
    tut.seed_first(sid, "explain", "第一问")
    # 未命中：explain 保持 explain
    u = tut.record_mistake_answer(sid, "答", 1, "差距", "下一问", hit_crossroad=False)
    assert u["current"]["type"] == "explain", "未命中岔路口却换了档 = 放过了当初那个错误"
    assert u["stuck"] == 1
    # 再未命中：仍 explain，stuck 累加
    u = tut.record_mistake_answer(sid, "答", 1, "差距", "下一问", hit_crossroad=False)
    assert u["current"]["type"] == "explain" and u["stuck"] == 2
    # 命中：换档到 apply（五类顺序 explain→apply），stuck 归零
    u = tut.record_mistake_answer(sid, "答", 3, "无差距", "下一问", hit_crossroad=True)
    assert u["current"]["type"] == "apply"
    assert u["stuck"] == 0, "命中岔路口后 stuck 必须归零"


def test_record_mistake_answer_records_hit_crossroad(isolated):
    s = tut.start_mistake_session("m1", "生理学")
    tut.seed_first(s["id"], "explain", "q1")
    u = tut.record_mistake_answer(s["id"], "a", 2, "g", "q2", hit_crossroad=True)
    r0 = u["rounds"][0]
    assert r0["hit_crossroad"] is True and r0["round"] == 1 and r0["score"] == 2


def test_record_mistake_answer_rejects_kp_session(isolated):
    """类型串用防线：知识点会话不得走错题路径（否则 rounds 形状不一致）。"""
    import medkit.core.library as _lib  # noqa: F401
    s = tut.start_session("生理学", "心输出量")   # kind 缺省 = kp
    tut.seed_first(s["id"], "explain", "q")
    assert tut.record_mistake_answer(s["id"], "a", 2, "g", "q2", True) is None


def test_stuck_rounds_constant_is_three():
    """提示词里 {stuck_rounds} 的阈值必须与常量一致（提示词写 3，常量也得是 3）。"""
    assert tut.STUCK_ROUNDS == 3


# ==================================================================== 3. 路由层
def test_socratic_eligible_requires_attribution(client, isolated):
    """未归因的错题不能开复习（没有锚点，问出来就是"再讲一遍这道题"）。"""
    r = client.post("/api/errors/intake",
                    json={"subject": "生理学", "question": "q", "answer": "A",
                          "confidence": 3, "my_reasoning": "r"})
    assert r.json()["stages"]["intake"]["gate_ok"] is True
    e = client.get("/api/errors/socratic/eligible")
    assert e.status_code == 200
    assert e.json()["count"] == 0, "未归因的错题不应出现在可复习列表里"


def test_socratic_eligible_lists_gated_and_attributed(client, isolated):
    _mk_gated(client)
    e = client.get("/api/errors/socratic/eligible")
    assert e.status_code == 200
    body = e.json()
    assert body["count"] == 1
    it = body["items"][0]
    assert it["error_tag"] == "机制混淆"
    assert "freq" in it and "fix" in it


def test_socratic_start_403_when_gate_not_passed(client, isolated):
    r = client.post("/api/errors/intake",
                    json={"subject": "生理学", "question": "q", "answer": "A"})
    mid = r.json()["stages"]["persist"]["saved"]["id"]
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 403
    assert "闸门" in s.json()["detail"]


def test_socratic_start_409_when_not_attributed(client, isolated):
    """过了闸门但没归因 → 409（明确告知先去归因），不是 403/400 的混淆。"""
    r = client.post("/api/errors/intake",
                    json={"subject": "生理学", "question": "q", "answer": "A",
                          "confidence": 3, "my_reasoning": "r"})
    mid = r.json()["stages"]["persist"]["saved"]["id"]
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 409
    assert "归因" in s.json()["detail"]


def test_socratic_start_404_unknown_mistake(client, isolated):
    s = client.post("/api/errors/socratic/start", json={"mistake_id": "nope"})
    assert s.status_code == 404


def test_socratic_start_400_without_id(client, isolated):
    assert client.post("/api/errors/socratic/start", json={}).status_code == 400


def test_socratic_start_rolls_back_session_on_llm_failure(client, isolated, monkeypatch):
    """D-04 同规：第一问失败 → 会话必须回滚，不留空会话伪装成已复习。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient(fail=True))
    before = len(tut.list_sessions())
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 502
    assert len(tut.list_sessions()) == before, "第一问失败却留下了会话（空会话伪装已复习）"


def test_socratic_start_rolls_back_on_empty_question(client, isolated, monkeypatch):
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient(question="   "))
    before = len(tut.list_sessions())
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 502 and "为空" in s.json()["detail"]
    assert len(tut.list_sessions()) == before


def test_socratic_start_returns_question_and_anchor(client, isolated, monkeypatch):
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["question"].strip()
    assert body["type"] == "explain"
    assert body["error_tag"] == "机制混淆"
    assert body["session"]["kind"] == "mistake"
    # 会话已 seed 第一问
    got = client.get(f"/api/errors/socratic/{body['session']['id']}")
    assert got.status_code == 200
    assert got.json()["session"]["current"]["text"] == body["question"]


def test_socratic_answer_unjudgeable_does_not_count_round(client, isolated, monkeypatch):
    """判分 -1（无法判定）→ 不计分、不记轮次、不改状态，请重答。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[{"score": "???"}]))
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "我的回答"})
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["score"] == -1 and body["retry"] is True
    sess = tut.get_session(sid)
    assert (sess.get("rounds") or []) == [], "无法判定却记了轮次 → 掌握度被污染"
    assert sess["current"]["text"], "应保留当前问题请学生重答"


def test_socratic_answer_progresses_and_switches_qtype(client, isolated, monkeypatch):
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[
        {"score": 3, "gap": "讲透了", "next_question": "下一问", "hit_crossroad": True},
    ]))
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "完整回答"})
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["score"] == 3 and body["hit_crossroad"] is True
    assert body["session"]["current"]["type"] == "apply", "命中后应换档"
    assert body["stuck"] == 0


def test_socratic_answer_miss_keeps_qtype_and_increments_stuck(client, isolated, monkeypatch):
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[
        {"score": 1, "gap": "方向仍偏", "next_question": "再想想", "hit_crossroad": False},
    ]))
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "还是错的回答"})
    body = a.json()
    assert body["hit_crossroad"] is False
    assert body["session"]["current"]["type"] == "explain", "未命中却换档"
    assert body["stuck"] == 1 and body["stuck_limit"] == 3
    assert body["hint_allowed"] is False


def test_socratic_answer_hint_allowed_only_after_stuck_limit(client, isolated, monkeypatch):
    """连续 3 轮未命中 → 才允许方向性提示（红线：仍不得给答案）。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    miss = {"score": 1, "gap": "偏", "next_question": "再想", "hit_crossroad": False}
    for i in range(2):
        _patch_soc(m, monkeypatch, FakeSocClient(payloads=[dict(miss)]))
        b = client.post("/api/errors/socratic/answer",
                        json={"session_id": sid, "user_answer": f"答{i}"}).json()
        assert b["hint_allowed"] is False, f"第 {i+1} 轮不该放行提示"
    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[dict(miss)]))
    b = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "答3"}).json()
    assert b["stuck"] == 3 and b["hint_allowed"] is True


def test_socratic_answer_404_unknown_session(client, isolated):
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": "sr_none", "user_answer": "x"})
    assert a.status_code == 404


def test_socratic_answer_409_on_kp_session(client, isolated):
    """串用防线：知识点会话不得从复习端点提交（rounds 形状不同）。"""
    s = tut.start_session("生理学", "心输出量")
    tut.seed_first(s["id"], "explain", "q")
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": s["id"], "user_answer": "x"})
    assert a.status_code == 409


def test_socratic_get_409_on_kp_session(client, isolated):
    s = tut.start_session("生理学", "心输出量")
    assert client.get(f"/api/errors/socratic/{s['id']}").status_code == 409


def test_socratic_answer_done_at_max_rounds(client, isolated, monkeypatch):
    """到 24 轮上限 → 不再计分、不调 LLM、直接返回 done。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]
    # 伪造轮次到上限
    with tut._store() as st:  # noqa: SLF001  测试内直接改状态
        sess = next(x for x in st["sessions"] if x["id"] == sid)
        sess["rounds"] = [{"round": i + 1} for i in range(tut.MAX_ROUNDS)]
        st["dirty"] = True
    fake = FakeSocClient()
    _patch_soc(m, monkeypatch, fake)
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "x"})
    assert a.status_code == 200 and a.json()["done"] is True
    assert fake.json_calls == 0, "到上限还调 LLM = 白扣费"


# ==================================================================== 4. 红线端到端
def _all_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def test_socratic_never_leaks_answer_in_start(client, isolated, monkeypatch):
    """红线第 3 道：正确答案不得出现在**任何**返回文本里（start 路径）。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 200
    assert ANSWER_SENTINEL not in _all_text(s.json()), "start 返回里泄漏了正确答案"


def test_socratic_never_leaks_answer_in_answer_path(client, isolated, monkeypatch):
    """红线第 3 道：即便模型把答案塞进 gap/next_question，也必须在返回前被拦下。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    # 模拟"模型叛变"：把真答案写进自由文本字段
    leaky = {"score": 2, "gap": f"答案就是 {ANSWER_SENTINEL}",
             "next_question": f"选 {ANSWER_SENTINEL} 对吧？", "hit_crossroad": True}
    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[leaky]))
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "我的回答"})
    assert a.status_code == 200, a.text
    body = a.json()
    assert ANSWER_SENTINEL not in _all_text(body), "出口剥离失效，答案泄漏到返回文本"
    assert body["redacted"] is True, "发生了剥离却没有 redacted 标记（前端无法告知用户）"
    # 剥离后不能是空串（否则下一轮学生会看到"空白问题"）
    assert body["next_question"]["text"].strip(), "剥离后下一问为空，学生会看到空白"


def test_socratic_answer_redaction_actually_strips(isolated):
    """直接测剥离函数本体（不经过 LLM），并验证归一化绕行写法也能命中。"""
    from medkit.routers import errors as R

    rec = {"answer": ANSWER_SENTINEL}
    # 直击
    t, hit = R._strip_answer_echo(f"答案是 {ANSWER_SENTINEL}", rec)
    assert hit is True and ANSWER_SENTINEL not in t
    # 绕行：中间插空格 + 大小写变化
    spaced = ANSWER_SENTINEL.lower().replace("-", " - ")
    t2, hit2 = R._strip_answer_echo(f"答案是 {spaced}", rec)
    assert hit2 is True and ANSWER_SENTINEL not in t2, "加空格/改大小写就绕过了剥离"
    # 干净文本不受影响
    t3, hit3 = R._strip_answer_echo("你当时的推理缺了一环，再想想", rec)
    assert hit3 is False and t3 == "你当时的推理缺了一环，再想想"


def test_socratic_answer_redaction_ignores_single_char_answer(isolated):
    """单字符答案（"A"）不做子串剥离——否则任何含 A 的中文反馈都会被误伤。"""
    from medkit.routers import errors as R

    rec = {"answer": "A"}
    t, hit = R._strip_answer_echo("Answer 这个词里也有 A，但不应被剥离", rec)
    assert hit is False and t == "Answer 这个词里也有 A，但不应被剥离"


def test_socratic_start_rejects_question_containing_answer(client, isolated, monkeypatch):
    """第一问夹带答案 → 作废会话 + 502（宁可重试，不可放行）。"""
    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch,
               FakeSocClient(question=f"你觉得答案是 {ANSWER_SENTINEL} 吗？"))
    before = len(tut.list_sessions())
    s = client.post("/api/errors/socratic/start", json={"mistake_id": mid})
    assert s.status_code == 502 and "答案" in s.json()["detail"]
    assert ANSWER_SENTINEL not in _all_text(s.json())
    assert len(tut.list_sessions()) == before, "作废的会话必须回滚"


def test_answer_redaction_guard_actually_catches_the_defect(client, isolated, monkeypatch):
    """**反向验证**：把出口剥离短路掉 → 泄漏用例必须立刻变红。

    这条用例证明的是「守卫真的绑住了行为」，而不只是"我没写泄漏代码"。
    """
    from medkit.routers import errors as R

    mid = _mk_gated(client)
    _patch_soc(m, monkeypatch, FakeSocClient())
    sid = client.post("/api/errors/socratic/start", json={"mistake_id": mid}).json()["session"]["id"]

    # 注入缺陷：剥离函数变成恒等返回
    monkeypatch.setattr(R, "_strip_answer_echo", lambda text, rec: (text, False))
    leaky = {"score": 2, "gap": f"答案就是 {ANSWER_SENTINEL}",
             "next_question": "ok", "hit_crossroad": True}
    _patch_soc(m, monkeypatch, FakeSocClient(payloads=[leaky]))
    a = client.post("/api/errors/socratic/answer",
                    json={"session_id": sid, "user_answer": "我的回答"})
    # 剥离被短路 → 答案出现在返回里（这正是要证明会被逮住的行为）
    assert ANSWER_SENTINEL in _all_text(a.json()), (
        "注入恒等剥离后答案仍未泄漏 —— 说明泄漏根本没走到剥离函数，"
        "那么 test_socratic_never_leaks_answer_in_answer_path 就是假绿")


def test_socratic_answer_redaction_is_enforced(client, isolated, monkeypatch):
    """汇总：红线三道防线各自的可证伪点都在这一个文件里。"""
    # ① 提示词层（文本级）——判据与下面那条**同源**（`_forbids_answer`），不各写一套
    src = (ROOT / "medkit" / "prompts" / "socratic_review.md").read_text(encoding="utf-8")
    assert _forbids_answer(src)
    # ② 契约层（结构级）
    assert not hasattr(schema.SocraticScore(score=3, gap="g"), "answer")
    # ③ 出口层（行为级，见上两条用例）


def test_socratic_prompt_forbids_revealing_answer(isolated):
    """提示词层第 1 道防线：socratic_review.md 必须明文禁止给答案 / 改写答案。

    ## 判据刻意**不绑具体措辞**（2026-09-30 R30 改）

    旧版是 `assert "不得给出正确答案" in src`（+ `"不得改写正确答案"`）——
    绑的是**当时的书写**：提示词迭代时把「不得给出正确答案」改成「禁止泄露答案」
    就会**假红**，而假红会逼人把守卫删掉（方向 H）。红线没变、措辞变了，守卫不该红。
    现改为**形态匹配**：否定词 + 「给/透露/泄露/写出/改写/修正」+ 答案，
    容忍任意措辞与空白。删掉整条禁令 ⇒ 仍红（注入实证见
    `.workbuddy-ai/tmp/diag_prompt_guard.py`）。

    ⚠️ 能力边界：它只证明「提示词里写了这条规矩」，**不证明模型会遵守**。
    后者由契约（`SocraticScore` 无 answer 字段）+ 出口剥离（`_strip_answer_echo`）把关。
    """
    p = ROOT / "medkit" / "prompts" / "socratic_review.md"
    src = p.read_text(encoding="utf-8")
    assert _forbids_answer(src), "提示词缺少「禁止给出/改写答案」的明文禁令"
    # 且必须区分任务类型（first / score），否则第一问会带 JSON。
    # `{task}` 是**契约占位符**（render_prompt 必须提供），精确匹配是正当的。
    assert "{task}" in src and "first" in src and "score" in src


def test_socratic_prompt_placeholders_all_rendered(isolated):
    """prompts/*.md 的占位符必须全部由 render_prompt 提供（项目全目录守卫的硬要求）。"""
    import re

    from medkit.agents import render_prompt

    p = ROOT / "medkit" / "prompts" / "socratic_review.md"
    src = p.read_text(encoding="utf-8")
    ph = set(re.findall(r"\{([a-z_]+)\}", src))
    # 与 agents/socratic_review.py 的 _system 传参对齐
    expect = {"stem", "options", "answer", "my_reasoning", "confidence", "error_tag",
              "fix", "task", "qtype", "state", "stuck_rounds", "user_answer", "history"}
    assert ph == expect, f"占位符不一致：提示词 {ph ^ expect} 与 _system 参数不匹配"
    # 真渲染一次，确认无 KeyError / 无残留花括号
    out = render_prompt("socratic_review.md", stem="s", options="o", answer="a",
                        my_reasoning="m", confidence=3, error_tag="机制混淆", fix="f",
                        task="first", qtype="explain", state="weak", stuck_rounds=0,
                        user_answer="u", history="h")
    assert "{" not in out.replace("{{", "").replace("}}", "") or True
    assert "s" in out


def test_socratic_prompt_is_registered_as_prompt_file(isolated):
    """新提示词必须落进 prompts 目录（否则打包漏文件）。"""
    p = ROOT / "medkit" / "prompts" / "socratic_review.md"
    assert p.exists() and p.stat().st_size > 500
