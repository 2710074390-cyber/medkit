"""S1-A 渲染层防崩溃回归测试：

LETTERS 扩容 / 选项超限 R14 / Anki 换行制表符转义 / 复习手册 href 白名单 /
押题卷过滤按钮 data-type / 计时器恢复 / MedFix 合并策略与 q_id 校验 / medqc 容错 / 渲染前终检。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.agents import medfix, medqc  # noqa: E402
from medkit.core.orchestrator import RENDER_MAX_OPTIONS, _render_precheck  # noqa: E402
from medkit.gates.options_check import check_all  # noqa: E402
from medkit.render.qbank_html import (  # noqa: E402
    LETTERS,
    export_anki,
    export_html,
    export_paper_html,
)
from medkit.render.review_html import sanitize_html  # noqa: E402


def test_letters_cover_render_max():
    assert LETTERS == "ABCDEFGHIJ"
    assert len(LETTERS) >= RENDER_MAX_OPTIONS


def test_options_over_limit_flags_R14():
    q = {"id": "Q1", "type": "A1", "bloom": "理解", "question": "题",
         "options": [f"选项{i}" for i in range(7)], "answer": "A",
         "analysis": "解析"}
    r = check_all([q])
    codes = {x["code"] for x in r["issues"]}
    assert "R14" in codes, r["issues"]
    assert r["fail_count"] >= 1


def test_anki_escapes_newline_tab_quotes():
    qs = [{"id": "Q001", "type": "X", "bloom": "理解", "subtopic": "测试",
           "question": "第一行\n第二行\t带\"双引号\"和'单引号'",
           "options": ["甲", "乙", "丙", "丁", "戊"],
           "answer": "BDE", "analysis": "解析含\n换行。【源:切片S001】"}]
    txt = export_anki(qs)
    assert "第一行<br>第二行 带&quot;双引号&quot;和&#39;单引号&#39;" in txt, \
        "换行→<br>、制表符→空格、引号→实体"
    assert "解析含<br>换行" in txt
    for ln in txt.splitlines():
        if ln and not ln.startswith("#"):
            assert ln.count("\t") == 1, f"数据行应恰好 1 个 Tab 分隔符：{ln!r}"


def test_review_href_scheme_whitelist():
    raw = ('<p><a href="https://ok.example.com/x">好链接</a> '
           '<a href="http://y.example.com">http</a> '
           '<a href="javascript:alert(1)">坏执行</a> '
           '<a href="JaVaScRiPt:alert(1)">坏混合大小写</a> '
           '<a href="java\nscript:alert(1)">坏控制字符</a> '
           '<a href="data:text/html,x">坏data</a> 正文</p>')
    out = sanitize_html(raw)
    assert 'href="https://ok.example.com/x"' in out, "http(s) 链接保留"
    assert 'href="http://y.example.com"' in out
    assert "javascript" not in out.lower(), f"javascript 应被剥成纯文本：{out}"
    assert "坏执行" in out and "坏混合大小写" in out and "坏控制字符" in out, "文本应保留"
    assert "data:text/html" not in out


def test_qbank_filter_uses_data_type():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "测试",
           "question": "题干？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    html = export_html(qs, "题库")
    assert 'data-type="A1"' in html, "details 应带 data-type 属性"
    ft_src = html.split("<script>")[1].split("</script>")[0]
    assert "dataset.type" in ft_src, "过滤函数应读取 data-type（旧版查 t-A1 类从未写入）"
    assert "t-" not in ft_src, "不应再依赖 t-A1 类名过滤"


def test_paper_timer_restores_from_state():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "测试",
           "question": "题干？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    html = export_paper_html(qs, "押题卷")
    assert "t0||Date.now()" in html, "计时器应从保存的 st.t0 恢复（重载不归零）"


def test_medfix_merge_keeps_provenance():
    class FakeClient:
        def chat_json(self, messages, **kwargs):
            return {"questions": [{
                "id": "Q001", "type": "X", "bloom": "记忆", "subtopic": "新主题",
                "question": "修复后的题面？",
                "options": ["甲", "乙", "丙", "丁", "戊"],
                "answer": "AB", "analysis": "修复后解析。【源:切片S001】"}]}

    questions = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "生长发育",
                  "question": "原题面？", "options": ["a", "b", "c", "d", "e"],
                  "answer": "A", "analysis": "原解析", "sid": "S001", "module": "第一章 生长发育"}]
    issues = [{"q_id": "Q001", "code": "R1", "severity": "fail", "reason": "x"}]
    out = medfix.fix_questions(FakeClient(), questions, issues, {"S001": "教材内容"})
    assert len(out["fixed"]) == 1
    fq = out["fixed"][0]
    # 合并策略：溯源/结构字段取原题，内容字段取新题
    assert fq["sid"] == "S001" and fq["module"] == "第一章 生长发育"
    assert fq["subtopic"] == "生长发育" and fq["type"] == "A1"
    assert fq["question"] == "修复后的题面？" and fq["answer"] == "AB"


def test_medfix_skips_unknown_qid():
    class FakeClient:
        def chat_json(self, messages, **kwargs):
            return {"questions": []}

    questions = [{"id": "Q001", "type": "A1", "bloom": "理解", "question": "题",
                  "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析",
                  "sid": "S001"}]
    issues = [{"q_id": "Q999", "code": "R1", "severity": "fail", "reason": "幽灵 issue"},
              {"q_id": "Q001", "code": "R1", "severity": "fail", "reason": "x"}]
    out = medfix.fix_questions(FakeClient(), questions, issues, {"S001": "教材"})
    assert out["fixed"] == []  # 不因幽灵 q_id 崩溃（原实现直接 KeyError）


def test_medqc_score_tolerance():
    class FloatScore:
        def chat_json(self, messages, **kwargs):
            return {"score": "82.5", "gate_decision": "PASS", "issues": [],
                    "summary": "ok"}

    class NoneScore:
        def chat_json(self, messages, **kwargs):
            return {"score": None, "gate_decision": "PASS", "issues": [], "summary": ""}

    q = [{"id": "Q001", "type": "A1", "bloom": "理解", "question": "题",
          "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析", "sid": "S001"}]
    r1 = medqc._qc_batch_once(FloatScore(), q, {"S001": "教材"})
    assert r1["score"] == 82, "浮点/数字字符串应兼容为 int"
    r2 = medqc._qc_batch_once(NoneScore(), q, {"S001": "教材"})
    assert r2["score"] == 50
    assert any(x["code"] == "QC_SCORE" and x["severity"] == "warn" for x in r2["issues"])


def test_medqc_empty_bank_not_pass():
    r = medqc.qc_batch(object(), [], {})
    assert r["gate_decision"] != "PASS"
    assert any(x["code"] == "EMPTY_BANK" for x in r["issues"])


def test_render_precheck_drops_invalid():
    good = {"id": "Q001", "type": "A1", "bloom": "理解", "question": "题？",
            "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析"}
    bad_over = {**good, "id": "Q002",
                "options": ["a", "b", "c", "d", "e", "f", "g"]}
    bad_type = {**good, "id": "Q003", "type": "Z9"}
    bad_answer = {**good, "id": "Q004", "answer": ""}
    kept, dropped = _render_precheck([good, bad_over, bad_type, bad_answer])
    assert [q["id"] for q in kept] == ["Q001"]
    assert {q["id"] for q in dropped} == {"Q002", "Q003", "Q004"}
    assert any("选项数 7 > 6" in ";".join(q["_drop_reasons"]) for q in dropped if q["id"] == "Q002")
