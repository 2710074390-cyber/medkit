"""EP-01 录入层：Anki `.apkg` 导入解析测试。

覆盖四层：
1. **往返**（最强判据）：MedKit 自己 `export_apkg` 出来的包 → `parse_apkg` → 字段逐一对上；
2. **通用 Anki 包**：字段名是英文/别名的牌组也要认得（按**字段名**取，不按位置）；
3. **坏包与边界**：不是 ZIP / 缺 collection / 压缩炸弹 / 条数上限 —— 一律给可读原因，不抛裸异常；
4. **闸门**：导入通道与 JSONL 共用同一套归一/落库 ⇒ **不绕过闸门**（Anki 里本就没有
   confidence / my_reasoning，故导入条目一律 ungated、只入库不归因，且如实报出）。

反向验证见 `.workbuddy-ai/tmp/diag_apkg.py`。
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import genanki  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402
from medkit.core.apkg_import import MAX_NOTES, parse_apkg  # noqa: E402
from medkit.render.apkg import export_apkg, export_memory_apkg  # noqa: E402

QUESTIONS = [
    {"id": "q1", "type": "A1", "bloom": "理解", "module": "呼吸系统",
     "question": "患儿男，6 岁，双肺闻及中细湿啰音。最可能的诊断是",
     "options": ["支气管肺炎", "急性支气管炎", "支气管哮喘"], "answer": "A",
     "analysis": "双肺固定中细湿啰音是肺炎的典型体征。"},
    {"id": "q2", "type": "A1", "bloom": "应用", "module": "循环系统",
     "question": "前负荷增加时每搏量如何变化",
     "options": ["增加", "减少", "不变"], "answer": "A",
     "analysis": "Frank-Starling 机制。"},
]


def _apkg_bytes(tmp_path: Path, questions=None, subject="儿科学", key="proj1") -> bytes:
    p = tmp_path / "deck.apkg"
    export_apkg(questions if questions is not None else QUESTIONS, subject, key, p)
    return p.read_bytes()


# ==================================================================== 1. 往返
def test_roundtrip_medkit_export(tmp_path):
    """MedKit 导出的包导回来：题干 / 选项 / 答案 / 解析**逐一对上**。

    这条是"导入解析器真的按 MedKit 的字段名取"的强判据——
    字段名写错、顺序假设错、HTML 反转义错，任何一处都会让它红。
    """
    res = parse_apkg(_apkg_bytes(tmp_path))
    assert res["ok"] is True and res["error"] == ""
    assert len(res["items"]) == len(QUESTIONS)
    by_q = {it["question"]: it for it in res["items"]}
    for src in QUESTIONS:
        got = by_q.get(src["question"])
        assert got is not None, f"题干丢失：{src['question']}"
        # 渲染侧补了字母前缀 ⇒ 导入回来的选项带 `A. `（与库内既有格式一致）
        assert got["options"] == [f"A. {src['options'][0]}", f"B. {src['options'][1]}",
                                  f"C. {src['options'][2]}"], got["options"]
        assert got["answer"] == src["answer"]
        assert got["analysis"] == src["analysis"]
        assert got["source"] == "anki"
        assert got["subject"] == "" and got["chapter"] == "", "科目/章节不该臆造"
    assert res["meta"]["notes_total"] == 2


def test_roundtrip_escapes_special_chars(tmp_path):
    """字段里的 HTML 与换行：导出转义 → 导入反转义，**往返后仍是原文**。"""
    q = [{"id": "q1", "type": "A1", "bloom": "理解", "module": "m",
          "question": "含 <b>标签</b> 与 & 符号\n第二行",
          "options": ["甲 <x>", "乙 & 丙"], "answer": "A",
          "analysis": "解析里有 <script>alert(1)</script> 与 \"引号\""}]
    res = parse_apkg(_apkg_bytes(tmp_path, q))
    got = res["items"][0]
    assert "<b>标签</b>" in got["question"], "字面量 <b> 必须还原成文本，不能被当标签剥掉"
    assert "& 符号" in got["question"] and "第二行" in got["question"]
    assert got["options"][0] == "A. 甲 <x>"
    assert "<script>" in got["analysis"] and '"引号"' in got["analysis"]


def test_roundtrip_case_stem(tmp_path):
    """案例题：导出写成「【案例】题干<br>子题」，导入要拆回 case_stem + question。"""
    q = [{"id": "q1", "type": "A3", "bloom": "应用", "module": "m",
          "case_stem": "患儿男 6 岁发热咳嗽", "question": "最可能的诊断是",
          "options": ["甲", "乙"], "answer": "A", "analysis": "x"}]
    got = parse_apkg(_apkg_bytes(tmp_path, q))["items"][0]
    assert got["case_stem"] == "患儿男 6 岁发热咳嗽"
    assert got["question"] == "最可能的诊断是"


# ==================================================================== 2. 通用 Anki 包
def _generic_apkg(tmp_path: Path, fields: list[str], values: list[str],
                  model_name: str = "Basic") -> bytes:
    model = genanki.Model(1607392319, model_name,
                          fields=[{"name": f} for f in fields],
                          templates=[{"name": "c", "qfmt": "{{%s}}" % fields[0],
                                      "afmt": "{{FrontSide}}"}])
    deck = genanki.Deck(2059400110, "Imported")
    deck.add_note(genanki.Note(model=model, fields=values))
    p = tmp_path / "generic.apkg"
    deck.write_to_file(str(p))
    return p.read_bytes()


def test_generic_english_field_names(tmp_path):
    """别处导出的包（英文/别名字段）也要认得——按**名字**取，不按位置。"""
    data = _generic_apkg(tmp_path, ["Question", "Options", "Answer", "Explanation"],
                         ["题干文本", "A. 甲<br>B. 乙", "B", "解析文本"])
    res = parse_apkg(data)
    got = res["items"][0]
    assert got["question"] == "题干文本"
    assert got["options"] == ["A. 甲", "B. 乙"]
    assert got["answer"] == "B"
    assert got["analysis"] == "解析文本"


def test_field_order_does_not_matter(tmp_path):
    """字段顺序调换后仍要正确——这正是"按名字取"要防的（按位置取会错位）。"""
    data = _generic_apkg(tmp_path, ["Answer", "Question", "Options"],
                         ["B", "题干文本", "A. 甲<br>B. 乙"])
    got = parse_apkg(data)["items"][0]
    assert got["question"] == "题干文本"
    assert got["answer"] == "B"


def test_unknown_field_names_fall_back_to_first_field(tmp_path):
    """认不出字段名 → 回退位置约定（第 0 个字段当题干），**不静默丢整包**。"""
    data = _generic_apkg(tmp_path, ["F1", "F2"], ["题干文本", "别的东西"])
    res = parse_apkg(data)
    assert res["items"][0]["question"] == "题干文本"


def test_memory_card_deck_is_skipped_with_reason(tmp_path):
    """记忆卡包（正面/背面）不是题目 ⇒ 逐条跳过并**说明原因**，不产半条错题。"""
    p = tmp_path / "mem.apkg"
    export_memory_apkg([{"id": "c1", "kind": "value", "front": "正面",
                         "back": "背面", "kp_name": "知识点"}], "生理学", "k1", p)
    res = parse_apkg(p.read_bytes())
    assert res["ok"] is True
    assert res["items"] == []
    assert res["skipped"] and "不是题目" in res["skipped"][0]


# ==================================================================== 3. 坏包与边界
def test_empty_bytes(tmp_path):
    res = parse_apkg(b"")
    assert res["ok"] is False and "空" in res["error"]


def test_not_a_zip():
    res = parse_apkg(b"this is definitely not a zip file")
    assert res["ok"] is False and "ZIP" in res["error"]


def test_zip_without_collection():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hello")
    res = parse_apkg(buf.getvalue())
    assert res["ok"] is False and "collection.anki2" in res["error"]


def test_zip_bomb_ratio_rejected():
    """高压缩比 + 可观体积 ⇒ 明确拒绝（不先解压到内存再判断）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("collection.anki2", b"\x00" * (25 * 1024 * 1024))
    res = parse_apkg(buf.getvalue())
    assert res["ok"] is False and "压缩" in res["error"]


def test_max_notes_truncation_is_reported(tmp_path):
    """超过条数上限：**明确报出被截断**，不静默少导。"""
    many = [{"id": f"q{i}", "type": "A1", "bloom": "理解", "module": "m",
             "question": f"题干{i}", "options": ["甲", "乙"], "answer": "A",
             "analysis": "x"} for i in range(MAX_NOTES + 3)]
    res = parse_apkg(_apkg_bytes(tmp_path, many, key="big"))
    assert res["ok"] is True
    assert len(res["items"]) == MAX_NOTES
    assert any("上限" in s for s in res["skipped"]), res["skipped"]


# ==================================================================== 4. 端点与闸门
@pytest.fixture()
def isolated(tmp_path, monkeypatch):
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


@pytest.fixture()
def client(isolated):
    return TestClient(m.app, base_url="http://127.0.0.1")


def _post(client, data: bytes, **form):
    return client.post("/api/errors/import/apkg",
                       files={"file": ("deck.apkg", data, "application/octet-stream")},
                       data=form)


def test_endpoint_dry_run_does_not_write(client, tmp_path):
    """`dry_run=1` 只解析体检：**一条都不落库**，但统计与字段映射要给出。"""
    data = _apkg_bytes(tmp_path)
    r = _post(client, data, dry_run="true")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["created"] == 0
    assert lib.list_mistakes() == [], "dry_run 不该写库"
    assert body["meta"]["models"], "应给出字段映射供人工核对"
    assert any("字段映射" in w for w in body["warnings"])


def test_endpoint_import_writes_and_reports_gate(client, monkeypatch, tmp_path):
    """真导入：条目落库，且**全部 ungated**（Anki 里没有闸门字段）+ 明确告知。

    ⚠️ 闸门判据必须是**间谍断言**（`attribute_one` 一次都没被调），不能只看"落库结果里
    没有 ai_error_tag"——后者对「调了 LLM 但结果丢弃」这种**白花钱**的绕过恒真
    （注入实测：直接调一次 `ep.attribute_one(...)` 不落库 ⇒ 只看结果的判据照样绿）。
    """
    import medkit.core.errorpipe as ep_mod

    calls: list[str] = []
    monkeypatch.setattr(ep_mod, "attribute_one",
                        lambda rec, client=None: calls.append("attr") or {
                            "ok": True, "analysis": {}, "error": ""})

    r = _post(client, _apkg_bytes(tmp_path))
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2 and body["gated"] == 0 and body["ungated"] == 2
    assert any("未过闸门" in w for w in body["warnings"]), body["warnings"]
    cards = lib.list_mistakes()
    assert len(cards) == 2
    # 闸门不放宽：未过闸门的条目**一次 LLM 都不该调**（调了就是白烧钱 + 绕过红线）
    assert calls == [], "未过闸门却调了 LLM 归因 = 导入通道绕过了闸门"
    assert all(not c.get("ai_error_tag") for c in cards)


def test_endpoint_rejects_bad_package(client):
    r = _post(client, b"not a zip")
    assert r.status_code == 400 and "ZIP" in r.json()["detail"]
    r2 = _post(client, b"")
    assert r2.status_code == 400 and "空" in r2.json()["detail"]


def test_endpoint_and_jsonl_share_stats_shape(client, tmp_path):
    """两条导入通道的统计**同形**（共用 `_ingest_items`）——前端不必写两套读取逻辑。"""
    apkg = _post(client, _apkg_bytes(tmp_path)).json()
    jsonl = client.post("/api/errors/import/jsonl",
                        json={"items": [{"question": "q", "answer": "A"}]}).json()
    common = {"total", "gated", "ungated", "created", "skipped", "errors"}
    assert common <= set(apkg) and common <= set(jsonl)
    assert set(jsonl) <= set(apkg), "JSONL 的键应是 apkg 键的子集（apkg 多 meta/warnings）"
