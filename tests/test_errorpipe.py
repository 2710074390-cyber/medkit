"""EP-01 错题归因流水线测试。

覆盖三层：
1. **纯函数层**（`metacog` / `kpid`）：不需要数据库，直接用样本喂。
2. **编排层**（`errorpipe.run`）：注入假 LLM 客户端，隔离到临时库目录。
3. **路由层 + 源码级守卫**：TestClient 打端点；并断言「不存在补填 confidence 的通道」。

隔离方式沿用项目既有做法（monkeypatch 模块级路径常量），
不触碰真实 `~/.medkit`。
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.errorpipe as ep  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.core.metacog as mc  # noqa: E402
import medkit.main as m  # noqa: E402


def _strip_comments(src: str) -> str:
    """剥离 Python 源码里的注释与字符串字面量（源码扫描类守卫**必须**先做这一步）。

    本项目已两次踩坑：断言目标串恰好出现在我自己的说明性注释里 → 「删代码、留注释」
    照样通过（假绿）。守卫绑的应是**行为**，不是文本。

    顺序很关键：先三引号文档串、再 `#` 行注释（若先剥 `#`，文档串里的 `#` 会误伤）。
    字符串字面量也要去掉——否则 `"hasattr"` 这种字面量会命中。
    """
    s = re.sub(r'"""(?:.|\n)*?"""', "", src)
    s = re.sub(r"'''(?:.|\n)*?'''", "", s)
    s = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', s)
    s = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", s)
    s = re.sub(r"#[^\n]*", "", s)
    return s


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """把库域与 kp/流水全部指到临时目录（含 db 路径与连接重置）。"""
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


class FakeClient:
    """假 LLM 客户端：按预设返回归因 JSON（支持 override）。"""

    def __init__(self, payload=None, *, fail=False):
        self.payload = payload or {
            "error_tag": "机制混淆",
            "evidence": "原话把 Frank-Starling 关系记反了",
            "kp_point": "前负荷对每搏量的影响",
            "variants": ["换成后负荷问每搏量变化", "心衰代偿期与失代偿期的差异"],
            "fix": "前负荷↑→每搏量↑；心衰是代偿不足不是机制反转",
            "counterfactual": "若题干改为心肌收缩力下降，答案会变哪个？",
            "review_chapters": ["生理学-循环-心脏泵血功能"],
            "where_uncertain": [],
        }
        self.fail = fail
        self.calls = 0

    def chat_json(self, messages, temperature=0.7, max_tokens=None, schema=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模拟 LLM 故障")
        return schema.model_validate(self.payload) if schema else self.payload


def _card(**kw):
    base = {
        "subject": "生理学", "chapter": "心血管", "topic": "心输出量",
        "question": "前负荷增加时每搏量如何变化？",
        "options": ["增加", "减少", "不变"], "answer": "A", "user_answer": "B",
        "confidence": 4, "my_reasoning": "觉得前负荷增加会降心输出量",
        "error_tag": "机制混淆", "round": "早鸟轮",
    }
    base.update(kw)
    return base


# ==================================================================== P1 intake
def test_intake_options_dict_to_list():
    """JSONL 的 options 是 dict，MedKit 内部是 list —— 唯一格式转换点。"""
    n = ep.normalize_card({"options": {"A": "增加", "B": "减少", "C": "不变"},
                           "confidence": 3, "my_reasoning": "x"})
    assert n["card"]["options"] == ["增加", "减少", "不变"]


def test_intake_gate_blocks_without_confidence_or_reasoning():
    """闸门：缺任一必填项 → gate_ok=False，且如实报出缺了什么。"""
    n = ep.normalize_card({"question": "q", "answer": "A"})
    assert n["gate_ok"] is False
    assert set(n["missing"]) == {"confidence", "my_reasoning"}

    n2 = ep.normalize_card({"confidence": 3, "my_reasoning": ""})
    assert n2["gate_ok"] is False and n2["missing"] == ["my_reasoning"]


def test_intake_confidence_out_of_range_treated_as_missing():
    """confidence=0 / 6 / "abc" 一律视为未填 —— 不静默取默认值。"""
    for bad in (0, 6, -1, "abc", 2.5, None):
        n = ep.normalize_card({"confidence": bad, "my_reasoning": "x"})
        assert n["gate_ok"] is False, f"{bad!r} 应判为未填"


def test_intake_rejects_self_invented_error_tag():
    """自造标签必须被清空，否则污染热力图与迁移矩阵。"""
    n = ep.normalize_card({"confidence": 3, "my_reasoning": "x",
                           "error_tag": "我瞎编的标签"})
    assert n["card"]["error_tag"] == ""
    ok = ep.normalize_card({"confidence": 3, "my_reasoning": "x", "error_tag": "记忆偏差"})
    assert ok["card"]["error_tag"] == "记忆偏差"


def test_intake_correct_none_not_treated_as_false():
    """correct 缺失必须保持 None —— 把"未作答"当"答错"会系统性拉低正确率。"""
    n = ep.normalize_card({"confidence": 3, "my_reasoning": "x"})
    assert "correct" not in n["card"] or n["card"].get("correct") is None
    assert mc.calibration([{"confidence": 3, "correct": None}])["unknown_result"] == 1


# ==================================================================== P2 kp 对齐
def test_kpid_synonyms_collapse_to_same_id():
    """同义写法必须归到同一个 kp_id（否则跨轮次追踪断链）。"""
    a = kpid._canonical("生理学", "心血管", "心输出量")
    b = kpid._canonical("生理学", "心血管", "CO")
    assert a == b


def test_kpid_length_guard_protects_short_abbreviations():
    """缩写收缩只在整段命中时生效，不得误伤 co2 / co中毒。"""
    assert kpid._canonical("生理学", "心血管", "co2") != kpid._canonical("生理学", "心血管", "CO")
    assert kpid._canonical("生理学", "心血管", "co中毒") != kpid._canonical("生理学", "心血管", "CO")


def test_kpid_resolve_is_idempotent(isolated):
    """反复 resolve 不得产生新 ID（别名表幂等）。"""
    a = kpid.resolve("生物化学", "糖代谢", "三羧酸循环")
    b = kpid.resolve("生物化学", "糖代谢", "三羧酸循环")
    assert a == b and a
    assert kpid.stats()["aliases"] == 1


def test_kpid_probe_does_not_write(isolated):
    """只读探测不得改写别名表（统计接口不该有写副作用）。"""
    kpid.resolve("X", "Y", "Z", auto_register=False)
    assert kpid.stats()["aliases"] == 0


def test_kpid_merge_does_not_rewrite_history(isolated):
    """合并 kp_id 只影响今后归类，不改历史流水（流水不可变）。"""
    a = kpid.resolve("生理学", "心血管", "心输出量")
    ev.append(kp_id=a, round_="早鸟轮", event=ev.EVENT_ANSWER, business_key="m1")
    assert kpid.merge_into(a, "kp1_NEW") == 1
    assert kpid.resolve("生理学", "心血管", "心输出量") == "kp1_NEW"
    # 历史事件仍指向旧 kp_id
    assert ev.list_events(kp_id=a)[0]["kp_id"] == a


def test_kpid_empty_input_returns_empty():
    assert kpid.resolve("", "", "") == ""


# ==================================================================== P3 归因
def test_attribute_blocked_when_gate_not_passed():
    """未过闸门 → 不调 LLM（这是红线的执行点）。"""
    cli = FakeClient()
    res = ep.attribute_one({"question": "q", "answer": "A"}, client=cli)
    assert res["ok"] is False
    assert cli.calls == 0, "未过闸门却调了 LLM —— 闸门失守"


def test_attribute_llm_failure_is_fail_soft():
    """LLM 故障不许把异常抛给调用方（错题本体已保存）。"""
    res = ep.attribute_one(_card(), client=FakeClient(fail=True))
    assert res["ok"] is False and res["error"]


def test_apply_analysis_derives_tag_match():
    """tag_match 只在两边都有值时判定 —— 缺一边不判（避免误算成"不一致"）。"""
    both = ep.apply_analysis({"error_tag": "机制混淆"}, {"error_tag": "机制混淆"})
    assert both["tag_match"] == 1
    differ = ep.apply_analysis({"error_tag": "审题失误"}, {"error_tag": "记忆偏差"})
    assert differ["tag_match"] == 0
    only_human = ep.apply_analysis({"error_tag": "审题失误"}, {"error_tag": ""})
    assert "tag_match" not in only_human


def test_apply_analysis_does_not_overwrite_user_fix():
    """用户手写的 fix 不得被 AI 覆盖（AI 只补空位）。"""
    out = ep.apply_analysis({"fix": "我自己总结的"}, {"fix": "AI 的版本"})
    assert out["fix"] == "我自己写的" if False else out["fix"] == "我自己总结的"


def test_analysis_contract_rejects_wrong_tag_and_empty_fix():
    """契约层拦住自造标签与空 fix。"""
    from pydantic import ValidationError

    from medkit.core.schema import ErrorAnalysis

    with pytest.raises(ValidationError):
        ErrorAnalysis.model_validate({"error_tag": "随便编的", "evidence": "e", "fix": "f"})
    with pytest.raises(ValidationError):
        ErrorAnalysis.model_validate({"error_tag": "机制混淆", "evidence": "e", "fix": ""})
    with pytest.raises(ValidationError):
        ErrorAnalysis.model_validate({"error_tag": "机制混淆", "evidence": "", "fix": "f"})
    # 超长 fix 也要拦（提示词要求 ≤25 字）
    with pytest.raises(ValidationError):
        ErrorAnalysis.model_validate({"error_tag": "机制混淆", "evidence": "e", "fix": "长" * 61})


def test_analysis_contract_drops_answer_fields():
    """红线：模型即便输出 answer/correct，也不许进入归因结果。"""
    from medkit.core.schema import ErrorAnalysis

    m_ = ErrorAnalysis.model_validate({
        "error_tag": "机制混淆", "evidence": "e", "fix": "f",
        "answer": "B", "correct": "A",       # 恶意/多余字段
    })
    d = m_.model_dump()
    assert "answer" not in d and "correct" not in d


# ==================================================================== P4 落库
def test_persist_writes_meta_and_appends_event(isolated):
    m_ = lib.add_mistake(_card())
    res = ep.persist({"id": m_["id"], "confidence": 5, "my_reasoning": "改后",
                      "error_tag": "记忆偏差", "kp_id": "kp1_x", "round": "强化轮"})
    saved = res["saved"]
    assert saved["confidence"] == 5 and saved["error_tag"] == "记忆偏差"
    assert res["event_id"], "应追加一条流水"
    assert ev.count() == 1


def test_event_append_is_idempotent(isolated):
    """同 business_key 重复追加不得产生两条（幂等）。"""
    i1 = ev.append(kp_id="kp1_a", round_="早鸟轮", event=ev.EVENT_ANSWER, business_key="m1")
    i2 = ev.append(kp_id="kp1_a", round_="早鸟轮", event=ev.EVENT_ANSWER, business_key="m1")
    assert i1 == i2 and ev.count() == 1


def test_persist_missing_card_reports_warning(isolated):
    """目标错题不存在 → 不静默成功，给出 warning。"""
    res = ep.persist({"id": "no_such_id", "confidence": 4})
    assert res["saved"] is None and res["warnings"]


# ==================================================================== P5 统计
def test_calibration_excludes_unrated_and_flags_overconfidence():
    cards = [{"confidence": 5, "correct": False} for _ in range(4)]
    r = mc.calibration(cards)
    top = next(b for b in r["buckets"] if b["confidence"] == 5)
    assert top["accuracy"] == 0.0 and top["n"] == 4
    assert r["alert"] is True, "自评 5 却全错 → 必须触发过度自信告警"
    assert r["brier"] == pytest.approx((1.0 - 0.0) ** 2)

    # 未填 confidence 计入 unrated，不进曲线
    r2 = mc.calibration(cards + [{"confidence": None, "correct": True}])
    assert r2["unrated"] == 1 and r2["rated"] == 4


def test_calibration_low_sample_bucket_not_alerting():
    """样本不足的桶不得给结论（避免"1 题 100%"被当信号）。"""
    cards = [{"confidence": 5, "correct": False} for _ in range(2)]
    r = mc.calibration(cards)
    assert r["alert"] is False


def test_migration_detects_stuck_and_improved():
    events = [
        {"kp_id": "k1", "round": "早鸟轮", "error_tag": "机制混淆", "event": "answer"},
        {"kp_id": "k1", "round": "强化轮", "error_tag": "机制混淆", "event": "answer"},
        {"kp_id": "k1", "round": "冲刺轮", "error_tag": "机制混淆", "event": "answer"},
        {"kp_id": "k2", "round": "早鸟轮", "error_tag": "机制混淆", "event": "answer"},
        {"kp_id": "k2", "round": "强化轮", "error_tag": "审题失误", "event": "answer"},
        {"kp_id": "k3", "round": "早鸟轮", "error_tag": "知识盲区", "event": "answer"},
    ]
    r = mc.migration(events)
    verdict = {c["kp_id"]: c["verdict"] for c in r["chains"]}
    assert verdict["k1"] == "未变"        # 三轮不变 → 修补无效
    assert verdict["k2"] == "改善"
    assert verdict["k3"] == "数据不足"
    assert r["stuck_count"] == 1


def test_heatmap_auto_prefers_human_tag():
    """默认 auto：人工 tag 优先、AI 兜底（AI 是可选步骤，不能让它把图变空）。"""
    only_human = mc.heatmap([{"subject": "生理学", "error_tag": "机制混淆"}] * 3)
    assert only_human["rows"][0]["total"] == 3
    assert only_human["untagged"] == 0

    only_ai = mc.heatmap([{"subject": "生化", "ai_error_tag": "记忆偏差"}] * 3)
    assert only_ai["rows"][0]["total"] == 3

    ai_view = mc.heatmap([{"subject": "生化", "error_tag": "审题失误"}], value="ai_error_tag")
    assert ai_view["untagged"] == 1


def test_heatmap_advice_only_when_dominant():
    """建议只在某类占比 ≥60% 且样本足够时给（否则是噪声）。"""
    mixed = mc.heatmap([{"subject": "S", "error_tag": "机制混淆"}, {"subject": "S", "error_tag": "审题失误"}])
    assert mixed["rows"][0]["advice"] == ""
    dominant = mc.heatmap([{"subject": "S", "error_tag": "机制混淆"}] * 5)
    assert dominant["rows"][0]["advice"]


def test_subtract_marks_lowest_score_chapters_as_skip():
    """减法清单 = 按「边际收益」升序取末位，**分最低的进「本周不排」**。

    语义定稿（关键，容易搞反）：
    打分 = 题量 × 历史正确率 × 频次，衡量「这块再投入时间的边际收益」。
    - 正确率 100% → 分低 → **不看**（已经会了，再刷是浪费时间）
    - 正确率 0%   → 分高 → 必须看（这才是要补的）

    所以「分最低」= 「最不值得再花时间」，不是「最差」。这个反直觉点在实现时
    让测试与代码各错了一次，故把语义连同数值一起钉在这里。
    """
    cards = ([{"subject": "生理学", "chapter": "循环", "correct": True, "confidence": 5}] * 8 +
             [{"subject": "生理学", "chapter": "泌尿", "correct": False, "confidence": 2}] * 8)
    r = mc.subtract_plan(cards)
    rows = {x["chapter"]: x for x in r["rows"]}

    # 数值口径：log1p(8) * 正确率 → 循环 2.197；泌尿 0.0
    assert rows["循环"]["accuracy"] == 1.0 and rows["泌尿"]["accuracy"] == 0.0
    assert rows["循环"]["score"] > rows["泌尿"]["score"]
    # 低分进 skip
    skip = {s["chapter"] for s in r["skip"]}
    assert "泌尿" in skip, "分最低的章节应进「本周不排」"
    assert "循环" not in skip
    assert r["freq_missing"] is True, "未给频次必须如实标记，不假装有数据"


def test_subtract_cut_never_zero_with_few_chapters():
    """章节数很少时 cut 不得退化为 0 —— 否则清单恒空、功能看起来"没反应"。

    这是 `int(n*ratio)` 的真实缺陷：n=2, ratio=0.25 → int(0.5)=0 → 一条都不标。
    改为 ceil 后 n=2 仍能给出 1 条建议。
    """
    cards = [{"subject": "S", "chapter": f"章{i}", "correct": True} for i in range(2)]
    r = mc.subtract_plan(cards, bottom_ratio=0.25)
    assert r["cut"] >= 1, "cut 退化为 0 → 减法清单永远为空"
    assert r["skip"]


def test_subtract_freq_raises_priority():
    """频次高的章节更不该被砍（同样正确率下）。"""
    cards = ([{"subject": "S", "chapter": "高频章", "correct": True}] * 5 +
             [{"subject": "S", "chapter": "低频章", "correct": True}] * 5)
    r = mc.subtract_plan(cards, freq={"高频章": 10, "低频章": 1})
    rows = {x["chapter"]: x for x in r["rows"]}
    assert rows["高频章"]["score"] > rows["低频章"]["score"]


def test_router_freq_map_reads_confirmed_realexam_freq(isolated):
    """**端到端**：库里确认过真题频次 → 减法清单必须不再标 `freq_missing`。

    这是上面 `test_subtract_freq_raises_priority` **测不到**的一段：
    那条只喂纯函数 `subtract_plan(cards, freq=...)`，而真实链路上
    「从 `realexam_freq` 表读出章节频次、传给 subtract_plan」是 router 的活。

    真实缺陷（EP-01 修复）：`_freq_map()` 探测 `realexams.list_freq()`，
    而该模块公开函数叫 `freq_view()` → `hasattr` 恒为 False → **静默返回 {}**，
    减法清单永远带 `freq_missing=True`（真题考频导了等于没导，且毫无报错）。
    此类「用 hasattr 探测能力」的失败没有信号，只能靠端到端断言兜住。
    """
    from medkit.core import realexams
    from medkit.routers import errors as R

    # 造题库数据：两章，正确率相同 → 频次将决定谁进「本周不排」
    for ch in ("循环", "消化"):
        for _ in range(4):
            lib.add_mistake({"subject": "生理学", "chapter": ch,
                             "question": f"{ch}题干", "correct": True, "confidence": 4})

    # 未确认频次时：如实标 freq_missing
    assert R._freq_map() == {}, "无已确认频次时不应编造"
    assert R.subtract()["freq_missing"] is True

    # 确认频次后：必须被读出来，且清单不再标 missing
    realexams.confirm([
        {"subject": "生理学", "chapter": "循环", "item": "心脏泵血", "freq": 9},
        {"subject": "生理学", "chapter": "消化", "item": "胃液分泌", "freq": 1},
    ])
    fm = R._freq_map()
    assert fm == {"循环": 9, "消化": 1}, f"应读出已确认章节频次，实际 {fm}"

    plan = R.subtract()
    assert plan["freq_missing"] is False, "有已确认频次时不得再标 freq_missing"
    rows = {x["chapter"]: x for x in plan["rows"]}
    assert rows["循环"]["freq"] > rows["消化"]["freq"], "高频章应带上更高频次"
    assert rows["循环"]["score"] > rows["消化"]["score"], "高频章更不该被砍"


def test_freq_map_does_not_probe_nonexistent_helpers():
    """守卫：`_freq_map` 不得再退回「用 `hasattr` 探测函数名」的写法。

    该写法在本项目已实锤造成静默降级（探测了不存在的 `list_freq`）。
    正确做法是**直接调用确实存在的** `realexams.freq_view()`，
    失败走 `errors.swallow` 留痕——因此**代码**里不该再出现 hasattr 探测。

    ⚠️ 必须**先剥注释再扫**（本项目已有两次假绿教训）：我在这段说明/函数 docstring
    里提到了 `hasattr` 这个词，直接对原文做子串匹配会把「注释里写了」误判为
    「代码里用了」——守卫绑的是文本而不是行为。
    """
    src = (ROOT / "medkit/routers/errors.py").read_text(encoding="utf-8")
    body = src.split("def _freq_map", 1)[1].split("\ndef ", 1)[0]
    code = _strip_comments(body)
    assert "hasattr" not in code, (
        "_freq_map 不应再用 hasattr 探测能力：探测失败会静默降级且无信号。"
        "应直接调用 realexams.freq_view()")
    assert "freq_view" in code, "应直接调用确实存在的 realexams.freq_view()"
    assert "swallow" in code, "取用失败必须走 errors.swallow 留痕，不得静默 return {}"


def test_freq_map_guard_actually_catches_hasattr_probe():
    """反向验证：把 hasattr 探测**写回代码**（不只是注释）时，守卫必须变红。

    没有这条，「守卫是否真的在守」就无从证伪。
    """
    injected = (
        "def _freq_map():\n"
        "    from ..core import realexams\n"
        "    rows = realexams.list_freq() if hasattr(realexams, 'list_freq') else []\n"
        "    return {}\n"
    )
    body = injected.split("def _freq_map", 1)[1]
    code = _strip_comments(body)
    assert "hasattr" in code, "注入的 hasattr 应能被扫到（否则守卫无效）"
    # 反过来：把它放进注释里，剥注释后必须扫不到
    commented = (
        "def _freq_map():\n"
        "    # 曾经用 hasattr(realexams, 'list_freq') 探测——已废弃\n"
        "    return realexams.freq_view()\n"
    )
    cbody = _strip_comments(commented.split("def _freq_map", 1)[1])
    assert "hasattr" not in cbody, "注释里的 hasattr 必须被剥离（否则守卫绑文本不绑行为）"


def test_freq_view_really_exists():
    """钉住上游契约：`realexams.freq_view` 必须存在且返回 chapters。

    若上游改名/删除，本用例先红——比让 `_freq_map` 静默降级要好。
    """
    from medkit.core import realexams

    assert hasattr(realexams, "freq_view"), "上游契约变更：freq_view 不存在了"
    view = realexams.freq_view()
    assert isinstance(view, dict) and "chapters" in view


# ==================================================================== 编排全链路
def test_run_full_pipeline(isolated):
    m_ = lib.add_mistake(_card())
    cli = FakeClient()
    res = ep.run({"id": m_["id"], **_card()}, client=cli)
    assert res["stages"]["intake"]["gate_ok"] is True
    assert res["stages"]["kp_align"]["kp_id"]
    assert res["stages"]["attribute"]["ok"] is True
    assert res["stages"]["persist"]["saved"]["ai_error_tag"] == "机制混淆"
    assert res["stages"]["analyze"]["counts"]["events"] == 1
    assert cli.calls == 1


def test_run_offline_skips_attribute(isolated):
    """离线路径：不传 attribute 阶段 → 一次 LLM 都不调（《总纲》§2.4 离线优先）。"""
    m_ = lib.add_mistake(_card())
    cli = FakeClient()
    res = ep.run({"id": m_["id"], **_card()},
                 stages=("intake", "kp_align", "persist"), client=cli)
    assert res["stages"]["attribute"]["skipped"] is True
    assert cli.calls == 0


def test_run_skips_attribute_when_gate_fails(isolated):
    """闸门不过时，即使点名要跑 attribute 也必须跳过。"""
    m_ = lib.add_mistake({"subject": "生理学", "question": "q", "answer": "A"})
    cli = FakeClient()
    res = ep.run({"id": m_["id"], "question": "q", "answer": "A",
                  "subject": "生理学", "confidence": None, "my_reasoning": ""},
                 client=cli)
    assert res["stages"]["attribute"].get("skipped_reason") == "未过闸门"
    assert cli.calls == 0
    assert res["warnings"]


def test_run_analyze_only_shortcut(isolated):
    res = ep.run({}, stages=("analyze",))
    assert "counts" in res["stages"]["analyze"]


def test_run_progress_callback_never_breaks_pipeline(isolated):
    """进度回调抛异常不许影响主流程（进度是观测，不是契约）。"""
    m_ = lib.add_mistake(_card())

    def boom(stage, msg):
        raise RuntimeError("回调炸了")

    res = ep.run({"id": m_["id"], **_card()}, client=FakeClient(), progress=boom)
    assert res["stages"]["persist"]["saved"] is not None


def test_health_reports_db_ready(isolated):
    """建库由流水线阶段触发（kpid/error_events 首次调用即 migrate），不是 add_mistake。

    注意别把 `add_mistake` 当成建库点：库域自己**从不建库**（JSON/SQLite 双轨设计，
    见 library._backfill_json_once 的 V-10 说明）。故这里显式跑一次 kp_align 来建库。
    """
    assert ep.db_ready() is False, "测试起点不应有库（确保隔离生效）"
    m_ = lib.add_mistake(_card())
    ep.run({"id": m_["id"], **_card()}, stages=("kp_align",))
    h = ep.health()
    assert h["db_ready"] is True and h["db_version"] >= 8


def test_json_to_sql_track_flip_keeps_record_visible(isolated):
    """双轨切换后错题必须仍可见（V-10 防线）：JSON 轨写入 → 建库 → 回读不丢。"""
    m_ = lib.add_mistake(_card())
    assert ep.db_ready() is False            # 此刻仍在 JSON 轨
    assert len(lib.list_mistakes()) == 1
    ep.run({"id": m_["id"], **_card()}, stages=("kp_align",))   # 触发建库
    assert ep.db_ready() is True
    got = lib.list_mistakes()
    assert [c["id"] for c in got] == [m_["id"]], "切换轨道后错题丢失（补导未生效）"


# ==================================================================== 路由层
@pytest.fixture()
def client(isolated):
    # base_url 必须用 127.0.0.1：main.py 的 Host 校验中间件会 403 掉默认的 testserver
    # （项目既有约定，见 tests/test_api.py 同名 fixture）。
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_api_intake_and_gate(client):
    r = client.post("/api/errors/intake", json=_card())
    assert r.status_code == 200
    body = r.json()
    assert body["stages"]["intake"]["gate_ok"] is True
    mid = body["stages"]["persist"]["saved"]["id"]

    g = client.get(f"/api/errors/cards/{mid}/gate")
    assert g.status_code == 200 and g.json()["gate_ok"] is True


def test_api_intake_rejects_empty_stem(client):
    r = client.post("/api/errors/intake", json={"subject": "x", "question": ""})
    assert r.status_code == 400


def test_api_attribute_403_when_gate_not_passed(client):
    """红线端点行为：未过闸门 → 403（不给绕行路径）。"""
    r = client.post("/api/errors/intake", json={"subject": "生理学", "question": "q", "answer": "A"})
    mid = r.json()["stages"]["persist"]["saved"]["id"]
    a = client.post(f"/api/errors/cards/{mid}/attribute")
    assert a.status_code == 403
    assert "闸门" in a.json()["detail"]


def test_api_edit_cannot_touch_confidence_or_reasoning(client):
    """编辑端点不接受 confidence/my_reasoning —— 契约层面就关掉了事后补填。"""
    r = client.post("/api/errors/intake", json=_card())
    mid = r.json()["stages"]["persist"]["saved"]["id"]
    client.put(f"/api/errors/cards/{mid}", json={
        "error_tag": "审题失误", "confidence": 1, "my_reasoning": "事后伪造"})
    # 重新读回：confidence 与 my_reasoning 必须保持原值
    from medkit.core.library import list_mistakes
    got = next(c for c in list_mistakes() if c["id"] == mid)
    assert got["confidence"] == 4
    assert got["my_reasoning"] == "觉得前负荷增加会降心输出量"
    assert got["error_tag"] == "审题失误"      # 允许改的字段确实改了


def test_api_stats_endpoints_respond(client):
    client.post("/api/errors/intake", json=_card())
    for path in ("/api/errors/stats/calibration", "/api/errors/stats/heatmap",
                 "/api/errors/stats/migration", "/api/errors/stats/agreement",
                 "/api/errors/subtract", "/api/errors/overview", "/api/errors/health"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"


def test_api_heatmap_rejects_bad_value(client):
    assert client.get("/api/errors/stats/heatmap?value=bogus").status_code == 400


def test_api_kp_endpoints(client):
    r = client.get("/api/errors/kp/resolve?subject=生理学&chapter=心血管&topic=CO")
    assert r.status_code == 200 and r.json()["kp_id"]
    # 只读探测不写库
    assert client.get("/api/errors/kp/list").json()["stats"]["aliases"] == 0

    reg = client.post("/api/errors/kp/register",
                      json={"subject": "生理学", "chapter": "心血管", "topic": "CO"})
    assert reg.status_code == 200
    assert client.get("/api/errors/kp/list").json()["stats"]["aliases"] == 1

    src = reg.json()["kp_id"]
    mg = client.post("/api/errors/kp/merge", json={"src_kp_id": src, "dst_kp_id": "kp1_Z"})
    assert mg.status_code == 200 and mg.json()["merged"] == 1
    assert client.post("/api/errors/kp/merge",
                       json={"src_kp_id": "a", "dst_kp_id": "a"}).status_code == 400


def test_api_jsonl_import_dry_run_then_commit(client):
    items = [_card(), {"subject": "生化", "question": "q2", "answer": "B"}]
    dry = client.post("/api/errors/import/jsonl", json={"items": items, "dry_run": True}).json()
    assert dry["total"] == 2 and dry["gated"] == 1 and dry["ungated"] == 1
    assert dry["created"] == 0, "dry_run 不得落库"

    real = client.post("/api/errors/import/jsonl", json={"items": items}).json()
    assert real["created"] == 2


def test_api_export_jsonl_roundtrip(client):
    client.post("/api/errors/intake", json=_card())
    exp = client.get("/api/errors/export/jsonl").json()
    assert exp["count"] == 1
    row = exp["items"][0]
    assert row["stem"] and row["confidence"] == 4
    # 导出键名对齐《总纲》§3.1 的 JSONL schema
    for k in ("stem", "my_answer", "correct", "my_reasoning", "error_tag", "kp_id"):
        assert k in row


# ==================================================================== 源码级守卫
def test_no_confidence_backfill_endpoint():
    """红线守卫（源码级）：**不得存在**补填 confidence 的端点/入口。

    《总纲》§3.4：confidence 不许由 AI 代填，也不许事后补。
    这条断言的作用是：将来有人"顺手"加一个 `PATCH .../confidence` 或
    `POST .../backfill` 时立刻变红——而不是等数据悄悄失效三个月后才发现。

    实现要点：**必须先剥多行注释、再剥单行注释，而不是反过来**。
    先剥 `//` 会把 `/* ... */` 里的某一行误当成单行注释的起点，剥掉半段后
    残留的 `*/` 让多行注释正则匹配不上，注释里的内容就留在了"代码"里 →
    docstring 中提到的 `confidence ... patch` 之类词组自我命中（假红）。
    本项目在 2026-09-27 的产物下载守卫上踩过同一类坑（把注释里的目标串
    算成"代码里有"→ 假绿），故此处显式写下正确顺序。
    """
    import re
    src = (ROOT / "medkit" / "routers" / "errors.py").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)      # 先多行
    code = re.sub(r"(?<!:)//[^\n]*", "", code)            # 再单行（(?<!:) 免误伤 http://）
    code = re.sub(r"#[^\n]*", "", code)

    forbidden = [
        r'@router\.(patch|put|post)\([^)]*confidence',
        r'def\s+\w*(backfill|fill)\w*confidence',
    ]
    for pat in forbidden:
        assert not re.search(pat, code, flags=re.I), f"出现补填 confidence 的通道：{pat}"


def test_guard_actually_catches_backfill_route(tmp_path):
    """反向验证（注入即红）：把补填端点真的写进代码，守卫必须抓到。

    没有这条用例，上面的守卫只证明了"我没写"，没证明"写了会被拦"。
    """
    import re
    injected = '''
@router.patch("/api/errors/cards/{mid}/confidence")
def backfill_confidence(mid: str, value: int = 3) -> dict:
    return {"ok": True}
'''
    code = injected
    pat = r'@router\.(patch|put|post)\([^)]*confidence'
    assert re.search(pat, code, flags=re.I), "守卫正则失效——注入的补填端点没被匹配到"


def test_edit_body_has_no_confidence_field():
    """结构级守卫：EditBody 的字段集合里不得出现 confidence / my_reasoning。"""
    from medkit.routers.errors import EditBody

    fields = set(EditBody.model_fields)
    assert "confidence" not in fields
    assert "my_reasoning" not in fields


def test_intake_body_requires_question():
    """IntakeBody 保留 confidence（首次录入要用），但题干是硬要求。"""
    from medkit.routers.errors import IntakeBody

    assert "confidence" in IntakeBody.model_fields
    assert "question" in IntakeBody.model_fields


def test_prompt_forbids_rewriting_correct_answer():
    """提示词红线守卫：error_analysis.md 必须明确禁止改写正确答案。"""
    import re
    txt = (ROOT / "medkit" / "prompts" / "error_analysis.md").read_text(encoding="utf-8")
    # 必须有"不得改写/不要自行改写 correct"这类约束
    assert re.search(r"不得.{0,10}(改写|修正)", txt) or re.search(r"不要自行改写", txt), \
        "提示词缺少「不得改写正确答案」的约束"
    # 且必须保留【答案待核实】的降级标记
    assert "答案待核实" in txt


def test_agents_analysis_strips_answer_fields():
    """agents 层防御：即使模型输出 answer/correct 也被剔除。"""
    import medkit.agents.error_analysis as agent

    out = agent.analyze(FakeClient({"error_tag": "机制混淆", "evidence": "e", "fix": "f",
                                    "answer": "B", "correct": "A"}), _card())
    for k in ("answer", "correct", "user_answer"):
        assert k not in out
