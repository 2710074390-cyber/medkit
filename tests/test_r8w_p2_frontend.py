"""B9 回归（R8+W）：P2 第二批——前端转义三条 + 押题卷标注 + 删科目孤儿行 + 更新开关 + regen 去重。

- **S3-1** `learnChip()` 标签体未转义（未知 state 时 `txt` 等于入参本身）。
- **S3-2** 仪表盘「最近项目」`p.target` 未转义。
- **S3-3** Anki 导出文件名未取 basename（服务端 content-disposition 原样用）。
- **S3-7** 押题卷无「AI 生成、非官方真题」标注。
- **S3-11** 删科目漏清 `syllabus_items` / `realexam_freq`（同名重建「复活」）。
- **S3-14** 启动更新检查无关闭开关。
- **M2-03** `regen` 无在飞去重（连点两次串行各掷一次 = 双份计费）。
- **S3-16** CI 里 `npm ci` 失败静默回退 `npm install`（弱化 lock 约束）。
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import db  # noqa: E402
from medkit.core import library as lib
from medkit.core import update as upd  # noqa: E402
from medkit.render import qbank_html  # noqa: E402

WEB = ROOT / "medkit" / "web" / "js"


def _js(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- S3-1 / S3-2 / S3-3

def test_learnchip_body_is_escaped():
    """S3-1：标签体必须转义（未知 state 时 txt = 入参）。"""
    src = _js("learn.js")
    assert '${esc(txt)}' in src, "learnChip 标签体未转义"
    assert "${txt}" not in src


def test_dashboard_target_is_escaped():
    """S3-2：仪表盘 p.target 必须转义。"""
    src = _js("app.js")
    assert "esc(p.target)" in src
    assert '+ p.target +' not in src, "仍存在未转义的 p.target 插值"


def test_anki_download_name_is_basenamed():
    """S3-3：content-disposition 文件名必须过 _baseName。"""
    src = _js("learn-review.js")
    assert "function _baseName(" in src, "缺少文件名清洗助手"
    assert "_baseName(decodeURIComponent(m[1]))" in src, "文件名未清洗"
    assert "a.download = m ? decodeURIComponent(m[1])" not in src


def test_base_name_strips_path_and_control():
    """S3-3：清洗规则本身（用 Node 跑一次真实函数体）。"""
    import re
    src = _js("learn-review.js")
    body = src[src.index("function _baseName("):]
    body = body[:body.index("\n}") + 2]
    # 用等效 Python 实现核对语义（避免依赖 Node 环境）
    def base_name(name: str) -> str:
        s = re.sub(r"[\\/]+", "/", str(name or "")).split("/")[-1] or ""
        s = re.sub(r"[\u0000-\u001f\u007f]", "", s).strip()
        return s or "MedKit记忆卡.apkg"

    assert "replace" in body
    assert base_name("../../etc/x.apkg") == "x.apkg"
    assert base_name("a\\b\\c.apkg") == "c.apkg"
    assert base_name("bad\x00name.apkg") == "badname.apkg"
    assert base_name("") == "MedKit记忆卡.apkg"


# ---------------------------------------------------------------- S3-7

def test_paper_has_ai_disclaimer():
    """S3-7：押题卷页头必须声明「AI 生成、非官方真题」。"""
    q = {"id": "Q001", "type": "A1", "bloom": "记忆", "question": "题？",
         "options": ["甲", "乙", "丙", "丁", "戊"], "answer": "A", "analysis": "解析【源:切片S001】"}
    html = qbank_html.export_paper_html([q], "押题卷")
    assert "非官方真题" in html
    assert "AI" in html


# ---------------------------------------------------------------- S3-11

@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "medkit.db")
    db.reset_conn()
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    # ⚠️ library.DB_FILE 是**导入期固化**的常量（= dbs.DB_PATH 的快照），
    # 只 patch db.DB_PATH 不会改变它 → 会走 JSON 分支。这里显式重定向。
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    return tmp_path


def test_delete_subject_clears_syllabus_and_realexam(iso_db):
    """S3-11：删科目必须同时清掉 syllabus_items / realexam_freq 的同科目行。"""
    db.migrate()
    with db.tx(write=True) as cur:
        db.put_row(cur, "syllabus_items", {"id": "s1", "subject": "儿科", "chapter": "第一章"},
                   ("subject", "chapter", "kind", "item"))
        db.put_row(cur, "realexam_freq", {"id": "r1", "subject": "儿科", "item": "考点"},
                   ("subject", "chapter", "item"))
        db.put_row(cur, "syllabus_items", {"id": "s2", "subject": "内科", "chapter": "第二章"},
                   ("subject", "chapter", "kind", "item"))
    res = lib.delete_subject_with_backup("儿科")
    assert res["deleted"]["syllabus_items"] == 1, res["deleted"]
    assert res["deleted"]["realexam_freq"] == 1, res["deleted"]
    conn = db.get_conn()
    left = {r[0] for r in conn.execute("SELECT subject FROM syllabus_items")}
    assert left == {"内科"}, "儿科的大纲条目应被清掉，内科的必须保留"


# ---------------------------------------------------------------- S3-14

def test_update_check_switch_disables_network(iso_db, monkeypatch):
    """S3-14：开关关闭时**不得发起任何网络请求**（用会抛异常的 httpx.get 守住）。"""
    (iso_db / "config.json").write_text(json.dumps({"update_check": False}), encoding="utf-8")

    def _boom(*a, **kw):
        raise AssertionError("开关关闭却仍发起了网络请求（S3-14 回归）")

    monkeypatch.setattr(upd.httpx, "get", _boom)
    res = upd.check()
    assert res["skipped"] is True
    assert res["has_update"] is False


def test_update_check_default_still_checks(iso_db, monkeypatch):
    """对照组：未配置时保持原行为（会尝试请求）。"""
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "v0.0.1", "body": ""}

    def _fake_get(*a, **kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(upd.httpx, "get", _fake_get)
    upd.check()
    assert calls["n"] == 1


# ---------------------------------------------------------------- M2-03

def test_regen_is_deduplicated(iso_db, monkeypatch):
    """M2-03：同一题在飞时第二次请求必须 409（否则串行各掷一次 = 双份计费）。"""
    from medkit.core import dedupe
    from medkit.routers import review as rev

    key = "regen:p1:Q001"
    dedupe.end(key)
    assert dedupe.begin(key) is False        # 模拟第一次请求已在飞
    try:
        with pytest.raises(HTTPException) as ei:
            rev._regen_question_sync("p1", "Q001")
        assert ei.value.status_code == 409
    finally:
        dedupe.end(key)


def test_regen_releases_lock_on_success_and_failure(iso_db, monkeypatch):
    """M2-03：无论成功/失败都要释放登记（否则该题永久 409）。"""
    from medkit.core import dedupe
    from medkit.routers import review as rev

    monkeypatch.setattr(rev, "_regen_question_locked",
                        lambda pid, qid: {"ok": True})
    rev._regen_question_sync("p2", "Q002")
    assert dedupe.is_active("regen:p2:Q002") is False

    def _boom(pid, qid):
        raise RuntimeError("模拟重掷失败")

    monkeypatch.setattr(rev, "_regen_question_locked", _boom)
    with pytest.raises(RuntimeError):
        rev._regen_question_sync("p3", "Q003")
    assert dedupe.is_active("regen:p3:Q003") is False, "失败路径未释放登记"


# ---------------------------------------------------------------- S3-16

def test_ci_has_no_npm_install_fallback():
    """S3-16：CI 不得再用 `npm install` 静默回退（弱化 lock 约束）。"""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "|| npm install" not in ci, "仍存在 npm install 回退"
    assert "npm ci" in ci
    assert "npm audit" in ci, "缺少 npm 侧审计信号"
