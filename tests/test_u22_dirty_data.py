"""U-22：脏数据（???/编码损坏）不参与掌握率分母与科目下拉。

UX B5：历史乱码记录（显示 `???`）仍被计入「知识点 / 掌握率」分母，并出现在科目下拉。
本测试验证：mastery 视图分母排除脏知识点；科目下拉聚合排除脏错题/知识点。
"""

import json

import medkit.core.library as lib


def _kp(name, subject, **kw):
    kp = {"id": f"kp_{name}", "name": name, "subject": subject, "chapter": "",
          "topic": "", "score": 0.0, "miss": 0, "last_tried": None, **kw}
    return kp


def _write(tmp_path, records):
    f = tmp_path / "knowledge.json"
    f.write_text(json.dumps(records), encoding="utf-8")
    return f


def test_mastery_denominator_excludes_dirty_kps(tmp_path, monkeypatch):
    clean = _kp("哮喘", "呼吸系", score=0.9, miss=1)
    dirty = _kp("???", "???", score=0.0)          # 乱码 name → 脏
    tagged = _kp("高血压", "循环", score=0.95, data_broken=True)  # 已打标记 → 脏
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", _write(tmp_path, [clean, dirty, tagged]))
    monkeypatch.setattr(lib, "list_mistakes", lambda: [])

    view = lib.get_mastery_view()
    assert view["stats"]["total_knowledge"] == 1          # 分母只含干净知识点
    assert [k["name"] for k in view["knowledge"]] == ["哮喘"]
    # 状态口径不因脏数据虚增
    assert view["stats"]["solid"] == 1
    assert view["stats"]["weak"] == 0


def test_mistake_dirty_flags():
    assert lib.is_mistake_dirty({"subject": "???", "question": "x"})
    assert lib.is_mistake_dirty({"question": "x", "data_broken": True})
    assert not lib.is_mistake_dirty({"subject": "呼吸", "question": "哮喘治疗的药物"})


def test_kp_dirty_flags():
    assert lib.is_kp_dirty({"name": "???", "subject": "x"})
    assert lib.is_kp_dirty({"name": "x", "data_broken": True})
    assert not lib.is_kp_dirty({"name": "哮喘鉴别", "subject": "呼吸系", "topic": "治疗"})


def test_subjects_aggregation_excludes_dirty(tmp_path, monkeypatch, capsys):
    """subjects 路由只暴露干净记录——乱码科目不再进入下拉。"""
    clean_m = {"id": "m1", "subject": "呼吸系", "question": "哮喘诊断"}
    dirty_m = {"id": "m2", "subject": "???", "question": "?"}
    clean_kp = _kp("哮喘", "呼吸系", score=0.8)
    dirty_kp = _kp("???", "???", score=0.0)

    monkeypatch.setattr(lib, "list_mistakes", lambda: [clean_m, dirty_m])
    monkeypatch.setattr(lib, "list_knowledge", lambda: [clean_kp, dirty_kp])

    from medkit.routers.library import subjects

    stats = subjects()
    assert "???" not in stats["subjects"]
    assert "呼吸系" in stats["subjects"]
    row = next(s for s in stats["stats"] if s["subject"] == "呼吸系")
    assert row["knowledge"] == 1
    assert row["mistakes"] == 1
