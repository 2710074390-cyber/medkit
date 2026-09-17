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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import websearch as ws  # noqa: E402
from medkit.render import qbank_html, review_html  # noqa: E402

XSS = "<img src=x onerror=alert(1)>"


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
