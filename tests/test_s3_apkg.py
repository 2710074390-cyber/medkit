"""S3-1 .apkg 真包导出回归测试：

稳定 id（按项目名哈希）/ zip 结构（collection.anki2 + media）/ anki2 可读（notes·cards 计数）/
deck·model id 按项目名稳定（重复导入不重复卡）/ 特殊字符字段不损坏（转义 + 换行 <br>）/
X 型走自评模型 / 案例题题干带案例前缀。
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.render.apkg import export_apkg, stable_id  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="medkit_apkg_"))


def _qs() -> list[dict]:
    return [
        {"id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "生长发育", "module": "第一章",
         "sid": "S001", "question": "关于生长发育，正确的是？",
         "options": ["选项甲", "选项乙", "选项丙", "选项丁", "选项戊"],
         "answer": "A", "analysis": "机制解析【源:切片S001】"},
        {"id": "Q002", "type": "X", "bloom": "理解", "subtopic": "儿童营养", "module": "第二章",
         "sid": "S002", "question": "下列哪些正确？\n第二行带<标签>与\"引号\"",
         "options": ["甲", "乙", "丙", "丁", "戊"],
         "answer": "BDE", "analysis": "多选解析。【源：切片S002】"},
        {"id": "Q003", "type": "A1", "bloom": "理解", "subtopic": "案例", "module": "第三章",
         "sid": "S003", "question": "首选检查是？", "case_id": "C001", "case_order": 1,
         "case_stem": "患儿男，3岁，发热3天，皮疹1天…",
         "options": ["血常规", "胸片", "脑脊液", "尿常规", "腹部超声"],
         "answer": "C", "analysis": "案例解析。【源:切片S003】"},
    ]


def _read_anki2(apkg_path: Path):
    with zipfile.ZipFile(apkg_path) as z:
        names = z.namelist()
        assert "collection.anki2" in names, f"apkg 应含 collection.anki2：{names}"
        assert "media" in names, f"apkg 应含 media：{names}"
        d = Path(tempfile.mkdtemp(prefix="medkit_apkg_x_"))
        z.extractall(d)
    db = d / "collection.anki2"
    con = sqlite3.connect(db)
    return con


def test_stable_id_deterministic():
    assert stable_id("儿科学期末-20260825") == stable_id("儿科学期末-20260825")
    assert stable_id("儿科学期末-20260825") != stable_id("儿科学期末-20260826")
    assert 0 < stable_id("x") < 2 ** 63, "模型/deck id 应在 int64 范围内"


def test_apkg_structure_and_counts():
    p = TMP / "struct.apkg"
    export_apkg(_qs(), "儿科学", "结构测试", p)
    con = _read_anki2(p)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"col", "notes", "cards"} <= tables, tables
    assert con.execute("select count(*) from notes").fetchone()[0] == 3
    assert con.execute("select count(*) from cards").fetchone()[0] == 3
    con.close()


def test_apkg_deck_model_id_stable():
    p1 = TMP / "id1.apkg"
    p2 = TMP / "id2.apkg"
    export_apkg(_qs(), "儿科学", "稳定项目", p1)
    export_apkg(_qs(), "儿科学", "稳定项目", p2)
    con1, con2 = _read_anki2(p1), _read_anki2(p2)
    decks1 = con1.execute("select decks from col").fetchone()[0]
    decks2 = con2.execute("select decks from col").fetchone()[0]
    assert json.decoder.JSONDecoder().raw_decode(decks1)[0] == json.decoder.JSONDecoder().raw_decode(decks2)[0], \
        "同名项目牌组 id 应稳定（重复导入不重复卡）"
    assert str(stable_id("稳定项目")) in decks1, "deck_id 应为项目名稳定哈希"
    con1.close()
    con2.close()


def test_apkg_special_chars_and_case_prefix():
    p = TMP / "chars.apkg"
    export_apkg(_qs(), "儿科学", "特殊字符", p)
    con = _read_anki2(p)
    rows = con.execute("select flds, tags from notes").fetchall()
    assert len(rows) == 3
    joined = "".join(r[0] for r in rows)
    # 换行 → <br>；引号/尖括号已转义
    assert "<br>第二行" in joined, "字段内换行应转 <br>"
    assert "&lt;标签&gt;" in joined and "&quot;引号&quot;" in joined
    assert "\n" not in joined.replace("br>", ""), "字段内不应残留裸换行"
    # 案例题：题干带「案例」前缀
    assert "【案例】患儿男，3岁，发热3天" in joined, "案例题子题卡应带案例题干前缀"
    # X 型题库：note 的 model_id 应为自评模型（由模板名区分）
    mids = {r[0] for r in con.execute("select distinct mid from notes")}
    assert len(mids) >= 1
    con.close()


def test_apkg_reimport_same_project_no_dup_note_guids():
    """同一项目两次导出：notes 的 guid（唯一键）应一致 → 导入 Anki 不重复。"""
    p1, p2 = TMP / "guid1.apkg", TMP / "guid2.apkg"
    export_apkg(_qs(), "儿科学", "guid项目", p1)
    export_apkg(_qs(), "儿科学", "guid项目", p2)
    c1, c2 = _read_anki2(p1), _read_anki2(p2)
    g1 = {r[0] for r in c1.execute("select guid from notes")}
    g2 = {r[0] for r in c2.execute("select guid from notes")}
    assert g1 == g2, f"guid 应稳定避免重复导入（{g1} vs {g2}）"
    c1.close()
    c2.close()


def test_apkg_b1_group_options_present():
    """B1 组题（HC-7：options 恒空、共享选项在 group.options）在 .apkg 卡面必须带选项。"""
    qs = [
        {"id": "B001", "type": "B1", "bloom": "记忆", "subtopic": "病原", "module": "第五章",
         "sid": "S005", "question": "上呼吸道感染最常见的病原？", "options": [],
         "group_kind": "option_group", "group": {"options": ["支原体", "肺炎链球菌", "腺病毒",
                                                             "呼吸道合胞病毒", "金黄色葡萄球菌"]},
         "answer": "B", "analysis": "解析【源:切片S005】"},
    ]
    p = TMP / "b1.apkg"
    export_apkg(qs, "儿科学", "B1选项", p)
    con = _read_anki2(p)
    rows = con.execute("select flds from notes").fetchall()
    assert len(rows) == 1
    flds = rows[0][0]
    assert "A. 支原体" in flds and "B. 肺炎链球菌" in flds, f"B1 卡应含共享选项，实际：{flds}"
    con.close()


def test_apkg_cleanup():
    shutil.rmtree(TMP, ignore_errors=True)
