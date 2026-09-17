"""B6 回归（R8+W 修复批次）：渲染层与内容安全加固。

覆盖：
- **S2-1**：`render_media` 的「图过大未嵌入」分支与 `export_md` 的 image_ref 必须转义/清洗
  （原实现直接插值，image_ref 来自上传文件名，可注入 HTML / 破坏 Markdown 结构）。
- **S2-4**：产物页自带 CSP（默认全禁、只放行内联与 data:），应用侧响应头带 CSP/nosniff/no-referrer。
- **S2-10 / S2-11**：产物页有医学免责声明；复习手册 prompt 要求「数字与标准速查」表下标注以教材为准。
- **S2-12**：冲突网络素材必须与可信素材**分节隔离**，而不是只靠一句文本提示。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import websearch as ws  # noqa: E402
from medkit.render import qbank_html, review_html  # noqa: E402

XSS = "<img src=x onerror=alert(1)>"


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """隔离配置目录 + projects_dir 指向 tmp（与 test_pipeline_offline 同款）。"""
    from medkit.core import config as cfgmod

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    orig_load = cfgmod.load

    def _load():
        c = orig_load()
        c["projects_dir"] = str(tmp_path / "projects")
        return c

    monkeypatch.setattr(cfgmod, "load", _load)
    return tmp_path


def _q(**kw) -> dict:
    base = {"id": "Q001", "type": "A1", "bloom": "记忆", "question": "下列哪项正确？",
            "options": ["选项甲", "选项乙", "选项丙", "选项丁", "选项戊"],
            "answer": "A", "analysis": "解析【源:切片S001】", "sid": "S001"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- S2-1

def test_render_media_escapes_image_ref_in_oversize_branch():
    """图读取失败/过大分支必须转义 image_ref（原实现漏了，另两个分支已转义）。"""
    out = qbank_html.render_media({"image_ref": XSS}, {XSS: {"path": "/nonexistent/x.png"}})
    assert XSS not in out, "原始标签不得出现在产物 HTML 里"
    assert "&lt;img" in out, "应转义为实体"


def test_export_md_sanitizes_image_ref():
    """MD 导出的 image_ref 必须只留文件名并转义结构字符。"""
    md = qbank_html.export_md([_q(image_ref="../../etc/pa]ss`wd|x.png")], "题库")
    seg = md.split("本题含图片", 1)[1][:40]
    assert "\\`" in seg, "反引号必须被转义（\\` 形式）"
    assert "\\]" in seg and "\\|" in seg, "`]`/`|` 必须被转义，否则破坏 Markdown 结构"
    assert "etc" not in seg and ".." not in seg, "只应保留 basename，不得留目录穿越路径"


# ---------------------------------------------------------------- S2-4

def test_product_html_carries_csp():
    """题库/押题卷/手册三套产物都必须自带 CSP，且是「默认全禁」口径。"""
    for html in (qbank_html.export_html([_q()], "题库"),
                 qbank_html.export_paper_html([_q()], "押题卷"),
                 review_html.review_to_html("# 手册\n\n正文", "复习手册")):
        assert "Content-Security-Policy" in html, "产物缺 CSP meta"
        assert "default-src 'none'" in html, "产物 CSP 必须是默认全禁口径"


def test_app_sets_security_headers():
    """应用侧响应头：CSP + nosniff + no-referrer（原报告 S2-4 的缺口）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    r = TestClient(app, base_url="http://127.0.0.1").get("/api/data/summary")
    assert r.status_code == 200, r.text
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_product_csp_matches_real_behavior():
    """CSP 必须与产物**真实行为**一致：押题卷会 `fetch("/api/...")` 回流错题到本机 API。

    2026-09-17 实测踩坑：首版把产物 CSP 写成 `connect-src 'none'`（以为「单文件零外部引用」
    所以可以全禁），结果押题卷的错题回流静默失效——由浏览器层用例
    `test_paper_case_subquestions_sync_individually` 以 `TypeError: Failed to fetch` 抓出。
    这条用例把「CSP 口径」与「产物里到底有没有 fetch」绑在一起，避免再次各改各的。
    """
    from medkit.render.pagechrome import PRODUCT_CSP

    paper = qbank_html.export_paper_html([_q()], "押题卷")
    assert "fetch(" in paper, "押题卷应有回流调用——若已移除，本用例的假设需同步更新"
    assert "connect-src 'self'" in PRODUCT_CSP, "CSP 必须放行同源连接（否则回流失效）"
    assert "connect-src 'none'" not in PRODUCT_CSP
    assert "connect-src 'self'" in paper, "产物页里必须带上修正后的 CSP"


# ---------------------------------------------------------------- S2-10 / S2-11

def test_product_html_has_medical_disclaimer():
    for html in (qbank_html.export_html([_q()], "题库"),
                 review_html.review_to_html("# 手册\n\n正文", "复习手册")):
        assert "不能替代教材" in html, "产物页缺医学免责声明"


def test_medreview_prompt_requires_authority_note():
    """S2-11：复习手册 prompt 必须要求「数字与标准速查」表下标注以教材/指南为准。"""
    text = (ROOT / "medkit" / "prompts" / "medreview.md").read_text(encoding="utf-8")
    assert "以现行教材与最新指南为准" in text


# ---------------------------------------------------------------- S2-12

def test_conflict_materials_are_isolated_section():
    """冲突素材必须单独成节并标注禁止作为答案依据，不能与可信素材混排。"""
    mats = [
        {"title": "WHO 指南", "url": "https://who.example/a", "snippet": "可信内容", "trusted": True},
        {"title": "某博客", "url": "https://blog.example/b", "snippet": "与教材冲突的说法",
         "conflict": True},
    ]
    d = ws.digest_for_prompt(mats)
    assert "【可信】WHO 指南" in d, "可信素材标记不得丢失"
    assert "https://who.example/a" in d and "https://blog.example/b" in d
    # 冲突素材必须在独立小节里
    assert "禁止" in d and "与教材冲突的素材" in d
    head, _, tail = d.partition("与教材冲突的素材")
    assert "blog.example" not in head, "冲突素材不得出现在可信小节里"
    assert "blog.example" in tail


def test_digest_without_conflict_has_no_extra_section():
    d = ws.digest_for_prompt([{"title": "A", "url": "https://a.example/x", "snippet": "s"}])
    assert "与教材冲突的素材" not in d
    assert "https://a.example/x" in d


# ---------------------------------------------------------------- S2-2 / S2-3（产物页脚本）

def test_paper_history_numbers_are_coerced():
    """S2-2：localStorage 的 score/total 必须经数值归一后才进 innerHTML（源码级守卫）。"""
    paper = qbank_html.export_paper_html([_q()], "押题卷")
    for raw in ("+last.score+", "+last.total+", "+best.score+", "+best.total+"):
        assert raw not in paper, f"仍存在未归一的插值：{raw}"
    assert "num(last.score)" in paper and "num(best.total)" in paper
    assert "const num=v=>" in paper, "缺少数值归一助手"


def test_paper_media_only_from_server_questions():
    """S2-3：只有服务端题目对象可注入 media——localStorage 灌入的题目必须失效。"""
    paper = qbank_html.export_paper_html([_q()], "押题卷")
    assert "new WeakSet()" in paper, "缺少服务端题目对象登记"
    assert "if(q.media && SRV_Q.has(q)) h+=q.media;" in paper, "media 注入必须带来源校验"
    assert paper.count("h+=q.media;") == 1, "media 注入点应只有一处（且带来源校验）"


# ---------------------------------------------------------------- S1-1b

def test_product_notice_banner_rendered_only_when_given():
    """S1-1b：传入 notice 时产物页顶部出现告警条；不传则没有。"""
    with_notice = qbank_html.export_html([_q()], "题库", notice="本批未经事实校验")
    assert "banner bad" in with_notice and "本批未经事实校验" in with_notice
    plain = qbank_html.export_html([_q()], "题库")
    assert "banner bad" not in plain
    paper = qbank_html.export_paper_html([_q()], "押题卷", notice="本批未经事实校验")
    assert "本批未经事实校验" in paper


def test_notice_is_escaped():
    html = qbank_html.export_html([_q()], "题库", notice=XSS)
    assert XSS not in html and "&lt;img" in html


def test_qc_unverified_reaches_product_page(isolated_cfg):
    """接线级（S1-1b）：质检整批故障 → 产物页必须带上「未经事实校验」告警。"""
    import json as _json

    from medkit.core import orchestrator as orch

    pid = "p_notice"
    base = isolated_cfg / "projects" / pid
    base.mkdir(parents=True)
    slices = [{"sid": "S001", "title": "第一章", "text": "生长发育三个高峰。", "role": "textbook"}]
    (base / "slices.json").write_text(_json.dumps(slices, ensure_ascii=False), encoding="utf-8")
    (base / "meta.json").write_text(_json.dumps({
        "pid": pid, "subject": "儿科", "exam": "期末", "stage": "quota", "seed": 42,
        "ratios": {"A1": 100}, "toggles": {"qbank": True, "paper": False, "review": False},
        "quota": [{"sid": "S001", "count": 2, "title": "第一章"}],
    }, ensure_ascii=False), encoding="utf-8")

    class _Gen:
        def chat_json(self, messages, **kwargs):
            return {"questions": [{
                "id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "章",
                "question": "生长发育有几个高峰？", "options": ["一个", "两个", "三个", "四个", "五个"],
                "answer": "C", "analysis": "三个。【源:切片S001】", "sid": "S001"}]}

    class _Boom:
        """质检整批故障（模拟 LLM 不可用）。"""

        def chat_json(self, messages, **kwargs):
            raise RuntimeError("模拟质检故障")

    orch.run_project(pid, overrides={"gen": _Gen(), "qc": _Boom(), "fix": _Gen()})
    qbank = (base / "最终产物" / "qbank.html").read_text(encoding="utf-8")
    assert "未经事实校验" in qbank, "质检未完成必须在产物页可见（S1-1b 接线回归）"


# ---------------------------------------------------------------- S3-6

def test_fts_quote_escapes_double_quote():
    """S3-6：FTS5 字面量转义——内部双引号必须翻倍（否则提前闭合字面量）。"""
    from medkit.core.db import _fts_quote

    assert _fts_quote('a"b') == '"a""b"*'
    assert _fts_quote("plain") == '"plain"*'


def test_fts_match_expr_escapes_token_with_quote(monkeypatch):
    """S3-6 接线级：token 含双引号时，生成的 MATCH 表达式必须已转义。

    用 monkeypatch 直接喂 token，避免依赖 jieba 的分词行为（实测裸 `"` 会被
    `len(t) >= 2` 过滤掉，所以靠真实分词构造不出该路径）。
    """
    from medkit.core import db as _db

    monkeypatch.setattr(_db, "fts_tokens", lambda q: ['a"b'])
    assert _db.fts_match_expr("x") == '"a""b"*'


def test_fts_match_expr_executes_on_quote_query():
    """真跑一次 FTS5：含引号的查询串不得让 MATCH 报语法错。"""
    import sqlite3

    from medkit.core import db as _db

    expr = _db.fts_match_expr('甲状腺"功能"减退')
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.execute("INSERT INTO t VALUES ('甲状腺功能减退')")
        n = conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()[0]
        assert n >= 0
    finally:
        conn.close()
