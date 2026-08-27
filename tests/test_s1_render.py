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


def test_paper_localstorage_survives_private_mode():
    """E：产物页所有 localStorage 读写包 try/catch（隐私模式不中断脚本）。"""
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "测试",
           "question": "题干？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    html = export_paper_html(qs, "押题卷")
    for pat in ("try{localStorage.setItem(KEY", "try{localStorage.removeItem(KEY)",
                "try{localStorage.setItem(RETRY_KEY", "r=JSON.parse(localStorage.getItem(RETRY_KEY"):
        assert pat in html, f"押题卷脚本应容错 localStorage：缺少 {pat}"
    # 复习手册页主题脚本同样容错
    from medkit.render.review_html import review_to_html

    h = review_to_html("# 标题\n正文")
    assert "try{if(localStorage.getItem" in h and "try{localStorage.setItem" in h


def test_review_html_table_scroll_toc_print():
    """P2#13：表格包 .tw 滚动容器、标题锚点 + 目录、打印样式。"""
    from medkit.render.review_html import review_to_html

    md_text = ("# 复习手册\n\n"
               "## 第一单元\n正文与表格：\n\n"
               "| 知识点 | 掌握度 |\n|---|---|\n| 蛋白质 | 80% |\n\n"
               "### 子要点\n细节。\n\n"
               "## 第一单元\n另一节（slug 冲突应去重）。")
    h = review_to_html(md_text)

    # 1) 表格包进横向滚动容器
    assert '<div class="tw"><table>' in h, "table 应被包进 .tw 滚动容器"

    # 2) 目录块 + 锚点 id + 去重
    assert 'class="toc"' in h, "应生成目录块"
    assert 'summary>目录' in h
    import re
    h2_ids = re.findall(r'<h2 id="([^"]+)"', h)
    assert h2_ids == ["第一单元", "第一单元-2"], f"标题应有锚点 id 且冲突去重：{h2_ids}"
    assert 'href="#第一单元"' in h and 'href="#第一单元-2"' in h, "目录链接应指向锚点"

    # 3) 打印样式
    assert "@media print" in h.replace("\n", "").replace(" ", "") or "@media print" in h
    assert ".tw{overflow:visible}" in h, "打印时应解除表格横滚"


def test_review_html_print_hides_theme_btn():
    """P2#13：打印时隐藏主题切换按钮（.mini）。"""
    from medkit.render.review_html import review_to_html

    h = review_to_html("# 标题")
    # @media print 内应隐藏 .mini；同时类名与按钮 class 对应
    assert ".mini{display:none}" in h
    assert 'class="mini"' in h


def test_index_html_global_error_guards():
    """E：主界面全局兜底 + 切页停轮询 + spinner 用 innerHTML。"""
    idx = (ROOT / "medkit" / "web" / "index.html").read_text(encoding="utf-8")
    assert "window.onerror" in idx, "应有全局脚本异常兜底"
    assert "unhandledrejection" in idx, "应有异步错误兜底"
    assert "if (name !== \"proj\")" in idx and "stopPoll()" in idx, "切走项目详情应停止轮询"
    assert "ocrRunToken++" in idx, "离开页面应终止 OCR 轮询"
    assert "stageEl.innerHTML = esc(s.stage_label)" in idx, "spinner 应写入 innerHTML（旧 textContent 显示字面文本）"
    assert "try { localStorage.setItem(\"medkit-theme\"" in idx, "主题写入应容错"


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
