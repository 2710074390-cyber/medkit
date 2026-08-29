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


def test_qbank_html_source_tag_and_year_filter():
    """v0.8.1 真题标记：来源标签（20XX 真题）与年份筛选器进入题库 HTML（含 data-yr）。"""
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
           "question": "肺通气机制？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析",
           "source_type": "真题", "source_year": "2023"},
          {"id": "Q002", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
           "question": "另一题？", "options": ["a", "b", "c", "d", "e"],
           "answer": "B", "analysis": "解析"}]
    h = export_html(qs, "题库")
    assert '<span class="tag src">2023 真题</span>' in h
    assert 'id="qbyear"' in h
    assert '<option value="2023">2023 年</option>' in h
    assert 'data-yr="2023"' in h and 'data-yr=""' in h
    # 押题卷卡面同样带来源标签（QUESTIONS 内嵌字段 → JS 渲染）
    p = export_paper_html(qs, "押题卷")
    assert '"source_year": "2023"' in p


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
    """E：主界面全局兜底 + 切页停轮询 + spinner 用 innerHTML。

    IMP-07 前端拆分后这些守卫/逻辑分布在 web/js/*.js（index.html 只留骨架），
    断言改为「读全 web 目录所有 js/css 后包含」——语义不变（纯搬运）。
    """
    web = ROOT / "medkit" / "web"
    blob = "".join(p.read_text(encoding="utf-8")
                   for p in web.rglob("*.js")) + \
           "".join(p.read_text(encoding="utf-8")
                   for p in web.rglob("*.css")) + \
           (web / "index.html").read_text(encoding="utf-8")
    assert "window.onerror" in blob, "应有全局脚本异常兜底"
    assert "unhandledrejection" in blob, "应有异步错误兜底"
    assert "if (name !== \"bank\")" in blob and "stopPoll()" in blob, "切走题库（项目详情）应停止轮询"
    assert "ocrRunToken++" in blob, "离开页面应终止 OCR 轮询"
    assert "stageEl.innerHTML = esc(s.stage_label)" in blob, "spinner 应写入 innerHTML（旧 textContent 显示字面文本）"
    assert "try { localStorage.setItem(\"medkit-theme\"" in blob, "主题写入应容错"


def test_paper_html_a11y_and_retry_markers():
    """IMP-08/12：押题卷产物 a11y 标记 + 同步失败重试按钮（模块级持续防御）。"""
    from medkit.render.qbank_html import export_paper_html

    qs = [{"id": "Q001", "type": "X", "bloom": "理解", "subtopic": "测试",
           "question": "哪些正确？", "options": ["a", "b", "c", "d", "e"],
           "answer": "BDE", "analysis": "解析"}]
    h = export_paper_html(qs, "押题卷")
    assert '<fieldset class="optfs"><legend class="sr">' in h, "选项应包 fieldset/legend"
    assert 'aria-label="\'+tip+\'"' in h, "答题卡格子应有 aria-label"
    assert 'role="status" aria-live="polite" tabindex="-1"' in h, "判分结果应角色化"
    assert "aria-pressed" in h and "aria-label=\"切换亮暗主题\"" in h, "主题按钮应带 aria"
    assert "bannerRetry" in h and "重试 ↻" in h, "同步失败提示条应带重试按钮"


def test_qbank_toolbar_a11y():
    """NX-07：题库页筛选工具区可访问性（搜索框/过滤组/结果计数）。"""
    from medkit.render.qbank_html import export_html

    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "测试",
           "question": "题干？", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "解析"}]
    h = export_html(qs, "题库")
    assert 'role="group" aria-label="筛选工具"' in h, "筛选容器应 role=group"
    assert 'role="group" aria-label="按题型过滤"' in h, "题型 chip 组应 role=group"
    assert 'aria-label="搜索题干 / 考点 / 章节"' in h, "搜索框应带 aria-label"
    assert 'id="qcount" role="status" aria-live="polite"' in h, "结果计数应 role=status"
    assert 'aria-label="清除全部筛选"' in h, "重置按钮应带 aria-label"
    assert 'aria-label="按认知层级过滤"' in h, "Bloom 下拉应带 aria-label"


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


# ---------------------------------------------------------------- NX-03（R-2）：契约硬闭环（修复-重试）
def test_medqc_contract_repair_once_then_ok():
    """契约校验失败 → 带错误重发 1 次修复成功 → 正常计分（ADR-003 闭环）。"""

    class RepairOnce:
        def __init__(self):
            self.calls = 0

        def chat_json(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"score": "not-a-number", "issues": "bad",
                        "gate_decision": "PASS", "summary": ""}
            return {"score": 88, "gate_decision": "PASS", "issues": [], "summary": "ok"}

    q = [{"id": "Q001", "type": "A1", "bloom": "理解", "question": "题",
          "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析", "sid": "S001"}]
    r = medqc._qc_batch_once(RepairOnce(), q, {"S001": "教材"})
    assert r["score"] == 88 and r["decision"] == "PASS"
    assert not any(x["code"] == "QC_CONTRACT" for x in r["issues"])


def test_medqc_contract_fail_after_repair_not_counted():
    """重发仍失败 → score=-1 不计分 + QC_CONTRACT fail（进人工复核）。"""

    class AlwaysBad:
        def chat_json(self, messages, **kwargs):
            return {"score": "not-a-number", "issues": "bad"}

    q = [{"id": "Q001", "type": "A1", "bloom": "理解", "question": "题",
          "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析", "sid": "S001"}]
    r = medqc._qc_batch_once(AlwaysBad(), q, {"S001": "教材"})
    assert r["score"] == -1
    assert any(x["code"] == "QC_CONTRACT" and x["severity"] == "fail" for x in r["issues"])
    assert r["decision"] == "BLOCKED"


def test_medqc_avg_excludes_contract_fail_batch():
    """qc_batch 平均分跳过 -1 批次（契约失败不计分），且 fail → BLOCKED。"""

    class Mixed:
        def __init__(self):
            self.calls = 0

        def chat_json(self, messages, **kwargs):
            self.calls += 1
            if self.calls <= 2:      # 批次1：首次 + 修复重发都失败 → 契约失败不计分
                return {"score": "bad", "issues": "zzz"}
            return {"score": 80, "gate_decision": "PASS", "issues": [], "summary": ""}

    q = [{"id": f"Q{i:03d}", "type": "A1", "bloom": "理解", "question": f"题{i}",
          "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "解析", "sid": "S001"}
         for i in range(medqc.BATCH_SIZE + 1)]
    r = medqc.qc_batch(Mixed(), q, {"S001": "教材"}, concurrency=1)
    assert r["score"] == 80.0, "契约失败批（-1）不应计入平均分"
    assert r["gate_decision"] == "BLOCKED"
    assert any(x["code"] == "QC_CONTRACT" for x in r["issues"])


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
