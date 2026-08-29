"""R3 批次3 产物组 + 既有 R3 批次3 修复回归测试（2026-08-29）。

覆盖：C-01/05/09/11/12/13/15/16/17 · D2 · R3-11/12/13/14/15/16/17/18/19/20。
隔离：项目/学习库全部走临时目录；不发起任何真实 LLM / 网络调用。
"""

import json
import sqlite3
import sys
import threading
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.config as cfgmod  # noqa: E402
import medkit.main as m  # noqa: E402
from medkit.core import realexams as rex_mod  # noqa: E402
from medkit.core.fsutil import safe_filename  # noqa: E402
from medkit.render import qbank_html as qh  # noqa: E402
from medkit.render import review_html  # noqa: E402
from medkit.routers import library as lib_router  # noqa: E402
from medkit.routers import review as review_mod  # noqa: E402


# ---------------------------------------------------------------- 配置隔离
@pytest.fixture()
def proj_cfg(tmp_path, monkeypatch):
    """projects_dir / api_key 隔离（review / projects 路由测试共用）。"""
    cfg = dict(cfgmod.DEFAULTS)
    cfg["projects_dir"] = str(tmp_path / "projects")
    cfg["api_key"] = "sk-test-key"
    monkeypatch.setattr(cfgmod, "load", lambda: dict(cfg))
    return cfg


def _mk_project(cfg, pid="p1", questions=None, subject="儿科学"):
    base = Path(cfg["projects_dir"]) / pid
    (base / "最终产物").mkdir(parents=True)
    (base / "meta.json").write_text(json.dumps(
        {"pid": pid, "subject": subject, "toggles": {"paper": False, "review": False},
         "final_count": len(questions or [])}, ensure_ascii=False), encoding="utf-8")
    (base / "slices.json").write_text("[]", encoding="utf-8")
    if questions is not None:
        (base / "最终产物" / "questions_final.json").write_text(
            json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    return base


def _client():
    return TestClient(m.app, base_url="http://127.0.0.1")


# ---------------------------------------------------------------- C-01 打印规则
def test_c01_qbank_print_rules():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "t",
           "question": "题?", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "a"}]
    h = qh.export_html(qs, "题库")
    assert "@media print" in h
    assert ".qpage{display:block!important}" in h
    assert ".filters,.qpager{display:none!important}" in h
    assert "beforeprint" in h and "afterprint" in h, "打印前应展开全部 details（含答案）"


# ---------------------------------------------------------------- C-05 apkg guid
def _read_note_guids(apkg_path: Path) -> list[str]:
    with zipfile.ZipFile(apkg_path) as z:
        d = Path(apkg_path).parent / ("x_" + apkg_path.stem)
        d.mkdir(exist_ok=True)
        z.extractall(d)
    con = sqlite3.connect(d / "collection.anki2")
    try:
        return [r[0] for r in con.execute("select guid from notes order by guid")]
    finally:
        con.close()


def test_c05_apkg_note_guid_stable_and_keyed(tmp_path):
    import genanki

    from medkit.render.apkg import export_apkg, export_memory_apkg

    qs = [{"id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "s",
           "question": "q1", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "a", "sid": "S1"},
          {"id": "Q002", "type": "X", "bloom": "理解", "subtopic": "s",
           "question": "q2", "options": ["a", "b", "c", "d", "e"],
           "answer": "BDE", "analysis": "a", "sid": "S1"}]
    p1 = tmp_path / "a.apkg"
    p2 = tmp_path / "b.apkg"
    export_apkg(qs, "儿科学", "稳定项目", p1)
    export_apkg(qs, "儿科学", "稳定项目", p2)
    g1 = _read_note_guids(p1)
    g2 = _read_note_guids(p2)
    assert g1 == g2, "同 pid+题集两次导出 guid 必须一致（重导不重复新增）"
    expect = sorted(genanki.guid_for("稳定项目" + q["id"]) for q in qs)
    assert sorted(g1) == expect, f"guid 应按 项目键+题目id 固定：{g1}"

    cards = [{"id": "c1", "kind": "concept", "front": "f1", "back": "b1", "kp_name": "k"},
             {"id": "c2", "kind": "concept", "front": "f2", "back": "b2", "kp_name": "k"}]
    mp1 = tmp_path / "mem1.apkg"
    mp2 = tmp_path / "mem2.apkg"
    export_memory_apkg(cards, "儿科学", "mem-key", mp1)
    export_memory_apkg(cards, "儿科学", "mem-key", mp2)
    assert _read_note_guids(mp1) == _read_note_guids(mp2)
    assert sorted(_read_note_guids(mp1)) == sorted(
        genanki.guid_for("mem-key" + c["id"]) for c in cards), "记忆卡 guid 按卡 id 固定"


# ---------------------------------------------------------------- C-09 渲染快照回滚
def test_c09_rerender_snapshot_restore(tmp_path, monkeypatch):
    from medkit.routers.review import _rerender_project

    base = tmp_path / "proj"
    out = base / "最终产物"
    out.mkdir(parents=True)
    (out / "qbank.html").write_text("OLD_HTML", encoding="utf-8")
    (out / "qbank.md").write_text("OLD_MD", encoding="utf-8")
    (base / "slices.json").write_text("[]", encoding="utf-8")
    meta = {"subject": "儿科学", "toggles": {"paper": True, "review": True}}
    monkeypatch.setattr("medkit.core.realexams.annotate_questions", lambda qs, s: qs)

    def boom(*a, **k):
        (out / "NEWFILE.txt").write_text("x", encoding="utf-8")
        raise RuntimeError("render failed")

    monkeypatch.setattr(qh, "export_md", boom)
    with pytest.raises(RuntimeError):
        _rerender_project(base, [{"id": "Q1", "type": "A1", "question": "q",
                                  "options": ["a"], "answer": "A"}], meta)
    assert (out / "qbank.html").read_text(encoding="utf-8") == "OLD_HTML"
    assert (out / "qbank.md").read_text(encoding="utf-8") == "OLD_MD"
    assert not (out / "NEWFILE.txt").exists(), "渲染失败后新建文件应被删除"


# ---------------------------------------------------------------- C-11 答案归一化第三口径
def test_c11_answer_issue_third_caliber():
    q = {"type": "X", "options": ["a", "b", "c", "d", "e"], "answer": "B,D"}
    assert review_mod._answer_issue(q) is None, "X 型 B,D 应归一为 BD 通过"
    q2 = {"type": "X", "options": ["a", "b", "c", "d", "e"], "answer": "B，D；E、A"}
    assert review_mod._answer_issue(q2) is None
    q3 = {"type": "A1", "options": ["a", "b", "c", "d", "e"], "answer": "B;D"}
    assert review_mod._answer_issue(q3) is not None, "单选 B,D 归一后 BD 仍非法"


def test_c11_review_save_stores_normalized_answer(proj_cfg, monkeypatch):
    qs = [{"id": "Q1", "type": "X", "bloom": "理解", "subtopic": "s", "question": "q1",
           "options": ["a", "b", "c", "d", "e"], "answer": "BDE", "analysis": "a", "sid": "S1"}]
    base = _mk_project(proj_cfg, questions=qs)
    monkeypatch.setattr(review_mod, "_rerender_project", lambda *a, **k: ["qbank.md"])
    r = _client().post("/api/projects/p1/questions/review", json={
        "edits": [{"id": "Q1", "answer": "B,D"}]})
    assert r.status_code == 200, r.text
    saved = json.loads((base / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    assert saved[0]["answer"] == "BD", f"保存应存归一化紧凑形式：{saved[0]['answer']}"


# ---------------------------------------------------------------- C-12 safe_filename
def test_c12_safe_filename():
    assert safe_filename("内科:呼吸/系统?") == "内科_呼吸_系统_"
    assert safe_filename("a" + chr(92) + "b*c?" + chr(39) + "<>|") == "a_b_c_____"
    assert safe_filename("  题  ") == "题"
    assert safe_filename("...") == "未命名"
    assert safe_filename("") == "未命名"
    assert safe_filename(None) == "未命名"
    assert safe_filename("a" + chr(0) + "b") == "a_b"


def test_c12_safe_filename_used_in_render_paths(tmp_path):
    """C-12：subject 拼文件名落点统一走 safe_filename（orchestrator/review/projects/library）。"""
    from medkit.core.fsutil import safe_filename as sf
    assert sf("内:科") == "内_科"
    # review.py / projects.py / library.py 已使用（静态断言防回退）
    for path, needle in [
        ("medkit/routers/review.py", "safe_filename(subject)"),
        ("medkit/routers/projects.py", "safe_filename(meta.get('subject'"),
        ("medkit/routers/library.py", "safe_filename(subject or '全部')"),
        ("medkit/core/orchestrator.py", "safe_filename(subject)"),
    ]:
        txt = (ROOT / path).read_text(encoding="utf-8")
        assert needle in txt, f"{path} 应使用 safe_filename：{needle}"


# ---------------------------------------------------------------- C-13 案例子题媒体
def test_c13_case_sub_render_media(tmp_path):
    sub = {"id": "Q2", "type": "A4", "bloom": "理解", "subtopic": "s", "question": "子题?",
           "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "a",
           "case_id": "C1", "case_order": 2, "group_kind": "case",
           "case_stem": "案例题干", "image_ref": "IMG1", "data_table": "|x|y|\n|---|---|\n|1|2|"}
    img = tmp_path / "fig_1.png"
    img.write_bytes(b'fake-png-data')

    image_index = {"IMG1": {"path": str(img), "caption": "图1"}}
    html = qh.export_html([sub], "题库", image_index=image_index)
    assert '<figure class="fig">' in html, "案例子题应渲染图片"
    assert "<table>" in html, "案例子题应渲染数据表格"


# ---------------------------------------------------------------- C-15 顺序与分组
def test_c15_case_blocks_order_and_group_id():
    qs = [
        {"id": "S1", "type": "A1", "question": "1", "options": [], "answer": "A"},
        {"id": "G1a", "type": "B1", "question": "g1a", "options": [], "answer": "A",
         "group_kind": "option_group", "group": {"id": "G1", "options": ["a", "b"]}},
        {"id": "S2", "type": "A1", "question": "2", "options": [], "answer": "A"},
        {"id": "G2a", "type": "B1", "question": "g2a", "options": [], "answer": "A",
         "group_kind": "option_group", "group": {"id": "G2", "options": ["a", "b"]}},
        {"id": "G1b", "type": "B1", "question": "g1b", "options": [], "answer": "A",
         "group_kind": "option_group", "group": {"id": "G1", "options": ["a", "b"]}},
    ]
    blocks = qh._case_blocks(qs)
    assert [b["kind"] for b in blocks] == ["single", "option_group", "single", "option_group"],         "应保持原题目顺序（章节序），不得按 (type,id) 重排"
    og1 = blocks[1]
    og2 = blocks[3]
    assert [x["id"] for x in og1["items"]] == ["G1a", "G1b"], "同组按原顺序聚合"
    assert [x["id"] for x in og2["items"]] == ["G2a"]
    # 两个选项相同的不同组（group.id 不同）不混排
    assert og1["items"][0]["id"] == "G1a" and og2["items"][0]["id"] == "G2a"


# ---------------------------------------------------------------- C-16 重掷剥离案例字段
def test_c16_regen_strips_case_fields(proj_cfg, monkeypatch):
    qs = [{"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "s", "question": "q1",
           "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "a", "sid": "S1"}]
    base = _mk_project(proj_cfg, questions=qs)
    (base / "slices.json").write_text(json.dumps(
        [{"sid": "S1", "role": "textbook", "title": "t", "text": "x"}]), encoding="utf-8")
    import medkit.agents.medgen as mg

    def fake_gen(client, subject, exam, slice_, count, ratios, teacher_text, **kw):
        return ([{"type": "A1", "question": "新题?", "options": ["a", "b", "c", "d", "e"],
                  "answer": "B", "analysis": "b", "sid": "S1",
                  "case_id": "C9", "group_kind": "case", "case_stem": "stem",
                  "case_order": 1}], None)

    monkeypatch.setattr(mg, "generate_slice", fake_gen)
    monkeypatch.setattr(review_mod, "_rerender_project", lambda *a, **k: ["qbank.md"])
    resp = review_mod._regen_question_locked("p1", "Q1")
    assert resp["warning"] and "案例组字段" in resp["warning"], resp
    saved = json.loads((base / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    q = next(x for x in saved if x["id"] == "Q1")
    for fld in ("case_id", "group_kind", "case_stem", "case_order"):
        assert fld not in q, f"重掷新题不应残留 {fld}"


# ---------------------------------------------------------------- C-17 手册 img 白名单
def test_c17_review_html_img_whitelist(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"png")
    raw = ('<p><img src="ok.png"><img src="missing.png">'
           '<img src="https://a.example.com/x.png">'
           '<img src="data:image/png;base64,AAA"></p>')
    out = review_html.sanitize_html(raw, out_dir=tmp_path)
    assert 'src="ok.png"' in out, "输出目录下存在的相对路径应保留"
    assert "图片相对路径未随附已省略" in out and "missing.png" in out, "缺失相对路径应给占位文字"
    assert "https://a.example.com/x.png" in out
    assert "data:image/png" in out


# ---------------------------------------------------------------- D2 noscript 兜底
def test_d2_paper_noscript_fallback():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "t",
           "question": "题?", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "a"}]
    h = qh.export_paper_html(qs, "押题卷")
    assert "<noscript>" in h
    assert "需要启用 JavaScript" in h
    assert "题?" in h and "答案：A" in h


# ---------------------------------------------------------------- R3-11 组同步（后端）
def test_r3_11_option_group_options_sync(proj_cfg, monkeypatch):
    qs = [
        {"id": "G1a", "type": "B1", "bloom": "记忆", "subtopic": "s", "question": "g1a",
         "options": [], "answer": "A", "analysis": "a", "sid": "S1",
         "group_kind": "option_group", "group": {"id": "G1", "options": ["a", "b", "c"]}},
        {"id": "G1b", "type": "B1", "bloom": "记忆", "subtopic": "s", "question": "g1b",
         "options": [], "answer": "A", "analysis": "a", "sid": "S1",
         "group_kind": "option_group", "group": {"id": "G1", "options": ["a", "b", "c"]}},
    ]
    base = _mk_project(proj_cfg, questions=qs)
    monkeypatch.setattr(review_mod, "_rerender_project", lambda *a, **k: ["qbank.md"])
    r = _client().post("/api/projects/p1/questions/review", json={
        "edits": [{"id": "G1a", "options": ["x", "y", "z"]}]})
    assert r.status_code == 200, r.text
    saved = json.loads((base / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    for q in saved:
        assert q["group"]["options"] == ["x", "y", "z"], "编辑一组应同步整组成员"


# ---------------------------------------------------------------- R3-12 og/case 筛选
def test_r3_12_qbank_filter_matches_single_types():
    qs = [{"id": "Q1", "type": "B1", "bloom": "记忆", "subtopic": "t", "question": "b1?",
           "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "a"},
          {"id": "Q2", "type": "A3", "bloom": "理解", "subtopic": "t", "question": "a3?",
           "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "a"}]
    h = qh.export_html(qs, "题库")
    assert 'data-type="B1"' in h and 'data-group="single"' in h
    assert "dt==='B1'" in h, "og 筛选项应命中 data-type=B1 的独立单题"
    assert "dt==='A3'||dt==='A4'" in h, "case 筛选项应命中 data-type=A3/A4"


# ---------------------------------------------------------------- R3-13 judged 持久化
def test_r3_13_paper_judged_persistence():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "t",
           "question": "题?", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "a"}]
    h = qh.export_paper_html(qs, "押题卷")
    assert "judged:!!st.judged" in h, "getState 应携带 judged"
    assert "stJ.judged=true" in h, "判分后应持久化 judged 标记"
    assert "_st0.judged?Date.now()" in h, "已判分重开应重置 T0（不按首开累计）"
    assert "if(judged){ el.textContent=fmtT(secs)+'（已判分）'" in h, "已判分计时冻结"


# ---------------------------------------------------------------- R3-14 前端拦截文案（静态）
def test_r3_14_review_keep_zero_confirm():
    js = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    assert "无法保存空题库" in js
    assert "你将剔除全部题目（后端拒绝保存空题库）" in js
    assert "知道了" in js


# ---------------------------------------------------------------- R3-15 只校验改动题
def test_r3_15_unrelated_invalid_answer_not_blocking(proj_cfg, monkeypatch):
    qs = [
        {"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "s", "question": "q1",
         "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "old", "sid": "S1"},
        {"id": "Q2", "type": "A1", "bloom": "理解", "subtopic": "s", "question": "q2",
         "options": ["a", "b", "c", "d", "e"], "answer": "Z", "analysis": "old", "sid": "S1"},
    ]
    base = _mk_project(proj_cfg, questions=qs)
    monkeypatch.setattr(review_mod, "_rerender_project", lambda *a, **k: ["qbank.md"])
    r = _client().post("/api/projects/p1/questions/review", json={
        "edits": [{"id": "Q1", "analysis": "new"}]})
    assert r.status_code == 200, f"编辑解析字段不应被未改动题的非法键阻断：{r.text}"
    saved = json.loads((base / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    assert next(x for x in saved if x["id"] == "Q1")["analysis"] == "new"


# ---------------------------------------------------------------- R3-16 letters 统一
def test_r3_16_letters_helper_unified():
    js = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    assert "function letters(" in js
    assert "letters((q.options || []).length)" in js, "试出一题应走 letters()"
    assert 'const LETTERS = "ABCDEF"' not in js, '\u4e0d\u5e94\u518d\u5b58\u5728 6 \u4f4d\u786c\u7f16\u7801\u5e38\u91cf'
    assert "ABCDEFGHIJ".startswith("ABCDEF")


# ---------------------------------------------------------------- R3-17 并发上传不丢索引
def test_r3_17_concurrent_asset_upload(proj_cfg):
    base = _mk_project(proj_cfg, pid="p1", questions=[])
    (base / "slices.json").write_text("[]", encoding="utf-8")
    errs: list[str] = []
    out: list[dict] = []

    def up(name):
        try:
            c = _client()
            r = c.post("/api/projects/p1/assets",
                       files={"file": (name, b"PNGDATA", "image/png")},
                       data={"caption": name})
            if r.status_code != 200:
                errs.append(f"{name}:{r.status_code}:{r.text}")
            else:
                out.append(r.json())
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}:{e}")

    ts = [threading.Thread(target=up, args=(f"a{i}.png",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    assert len(out) == 2
    sids = {o["sid"] for o in out}
    assert len(sids) == 2, f"两张图 sid 不应重复：{sids}"
    slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    img_sids = {s["sid"] for s in slices if s.get("role") == "image"}
    assert sids == img_sids, f"并发上传后 slices.json 应保留两条索引：{slices}"


# ---------------------------------------------------------------- R3-18 只读事务
def test_r3_18_realexams_readonly_tx(monkeypatch, tmp_path):
    calls: list[bool] = []

    class FakeCur:
        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    @contextmanager_patch()
    def fake_tx(write=True):
        calls.append(write)
        yield FakeCur()

    import medkit.core.db as dbs
    monkeypatch.setattr(dbs, "tx", fake_tx)
    monkeypatch.setattr(rex_mod, "dbs", dbs)
    monkeypatch.setattr(dbs, "migrate", lambda: None)
    rex_mod._dictionary()
    rex_mod.list_drafts("内科学")
    assert calls and all(c is False for c in calls), f"纯读路径应全部 write=False：{calls}"


def contextmanager_patch():
    from contextlib import contextmanager
    return contextmanager


# ---------------------------------------------------------------- R3-19 subjects 聚合口径
def test_r3_19_subjects_aggregation_matches_old(monkeypatch, tmp_path):
    import medkit.core.explain as expl
    import medkit.core.library as lib
    import medkit.core.review as rev

    for mod, attr, fname in ((lib, "MISTAKES_FILE", "mistakes.json"),
                             (lib, "KNOWLEDGE_FILE", "knowledge.json"),
                             (rev, "REVIEW_QUEUE_FILE", "review_queue.json"),
                             (expl, "EXPLAINS_FILE", "explains.json")):
        p = tmp_path / fname
        monkeypatch.setattr(mod, attr, p)
        monkeypatch.setattr(mod, "DB_FILE", tmp_path / "no.db")
    # 造两科目数据
    lib.add_mistake({"question": "q1", "subject": "内科学", "know_tags": ["肺通气"],
                     "learned": True})
    lib.add_mistake({"question": "q2", "subject": "内科学", "know_tags": ["心力衰竭"]})
    lib.add_mistake({"question": "q3", "subject": "外科学", "know_tags": ["骨折"]})
    rev.enqueue("肺通气", "内科学", "kp1")
    rev.enqueue("骨折", "外科学", "kp2")
    rev.enqueue("空科目卡", "", "kp3")          # 未分类卡应计入每科
    expl.save_explain({"id": "e1", "subject": "内科学", "kp_name": "肺通气",
                       "content": "x", "created_at": "2026-08-29T00:00:00"})

    got = lib_router.subjects()["stats"]
    # 旧口径：逐科目 get_mastery_view + rev.stats
    expected = []
    for s in sorted({"内科学", "外科学"}):
        mv = lib.get_mastery_view(s)["stats"]
        rs = rev.stats(s)
        total = mv["total_knowledge"] or 0
        expected.append({
            "subject": s,
            "mistakes": mv["total_mistakes"],
            "knowledge": mv["total_knowledge"],
            "mastered_rate": round(100 * (mv["solid"] + mv["mastered"]) / total) if total else 0,
            "review_total": rs["total"],
            "review_due": rs["due"],
            "review_new": rs["new"],
        })
    assert got == expected, f"聚合结果应与旧口径一致：{got} vs {expected}"


# ---------------------------------------------------------------- R3-20 孤儿项目
def test_r3_20_orphan_project_list_and_delete(proj_cfg):
    base = Path(proj_cfg["projects_dir"])
    base.mkdir(parents=True, exist_ok=True)
    normal = base / "正常项目"
    normal.mkdir()
    (normal / "meta.json").write_text(json.dumps(
        {"pid": "正常项目", "subject": "儿科", "stage": "done", "target": 20,
         "created": "2026-08-29T00:00:00"}), encoding="utf-8")
    orphan = base / "孤儿目录"
    orphan.mkdir()
    (orphan / "残留文件.txt").write_text("x", encoding="utf-8")

    c = _client()
    items = {p["pid"]: p for p in c.get("/api/projects").json()["projects"]}
    assert items["孤儿目录"]["meta_missing"] is True
    assert items["孤儿目录"]["stage_label"] == "孤儿项目"
    assert "meta_missing" not in items["正常项目"]

    r = c.delete("/api/projects/孤儿目录")
    assert r.status_code == 200, r.text
    assert "元数据缺失" in r.json()["msg"]
    assert not orphan.exists(), "meta 缺失时也应无条件删除目录"
    # 正常项目删除不受影响
    assert c.delete("/api/projects/正常项目").status_code == 200


# ---------------------------------------------------------------- C-01 打印（JS 静态，轻量）
def test_c01_print_rules_in_qbank_script():
    qs = [{"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "t",
           "question": "题?", "options": ["a", "b", "c", "d", "e"],
           "answer": "A", "analysis": "a"}]
    h = qh.export_html(qs, "题库")
    assert "window.addEventListener('beforeprint'" in h
    assert "window.addEventListener('afterprint'" in h
