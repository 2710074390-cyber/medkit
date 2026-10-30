# -*- coding: utf-8 -*-
"""命令面板聚合检索（`GET /api/palette/search`）的守卫（2026-10-28）。

## 这一组在守什么

Ctrl/⌘+K 面板从「只搜动作」扩到「一个入口搜题目 / 错题 / 考点」。这段胶水最容易
静默坏掉的不是单个域，而是**聚合层的三条承诺**：

1. **每段各取前 N 条**（`limit` 是每组上限，不是总数）——否则最长的那段会把面板撑满，
   另外两段等于不存在；
2. **一段坏了不拖垮其余**——某个域的索引坏了只让那一段缺席，不能整块变哑
   （前端 `catch` 只兜得住网络层，兜不住服务端异常）；
3. **固定展示顺序**（题目 → 错题 → 考点）——顺序抖动会让人以为结果在变。

另外守两件小事：空输入不发全库；不做同义词（这条是**边界**，写成用例免得
"以后加个同义词吧"把口径漂成"我以为搜到了"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.main as m  # noqa: E402
from medkit import state  # noqa: E402
from medkit.core import syllabus as sylllib  # noqa: E402
from medkit.core import vault_index  # noqa: E402
from medkit.routers import palette as pal  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def _bank(client, questions, name="呼吸系统"):
    """建一个题库。`questions` 是 (题干, 选项, 答案) 三元组列表。"""
    items = [{"question": q, "options": opts, "answer": ans}
             for q, opts, ans in questions]
    r = client.post("/api/bank/import/commit",
                    json={"name": name, "subject": "内科学", "items": items})
    assert r.status_code == 200, r.text
    return r.json()["bank_id"]


def _mistake(client, **kw):
    r = client.post("/api/vault/mistakes", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _search(client, q, **kw):
    r = client.get("/api/palette/search", params={"q": q, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(res):
    return [g["kind"] for g in res["groups"]]


# ---------------------------------------------------------------- 基本形态
def test_empty_query_returns_no_groups(client):
    """空输入 ⇒ 空结果，不是全库。面板还没输入时该显示命令，不该把千题撑到前端。"""
    _bank(client, [("慢阻肺最主要的病因是", ["吸烟", "感染"], "A")])
    for q in ("", "   "):
        res = _search(client, q)
        assert res["groups"] == [] and res["q"] == ""


def test_bank_group_carries_every_field_the_preview_needs(client):
    """题目段要一次给全预览所需字段（题干 / 选项 / 答案 / 解析 / 出处）。

    预览弹窗是只读的——字段缺一个，用户点进去就只能看到半道题，
    再回面板重搜一次。所以这里逐字段断言，不是"有 items 就算过"。
    """
    _bank(client, [("慢阻肺最主要的病因是", ["吸烟", "感染"], "A")])
    g = _search(client, "慢阻肺")["groups"][0]
    assert g["kind"] == "bank" and g["label"] == "题目" and g["total"] == 1
    it = g["items"][0]
    assert it["kind"] == "bank"
    assert "慢阻肺" in it["title"]
    assert it["options"] == ["吸烟", "感染"]
    assert it["answer"] == "A" and it["hit"] == "题干"
    assert "内科学" in it["sub"], f"出处行没带科目：{it['sub']!r}"


def test_mistake_group_exposes_my_own_words(client):
    """错题段必须带出「我的理解 / 我的疑问」——用户自己写的那两段是错题最有价值的部分，
    只回题干等于把搜索停在"我做过这道题"。"""
    _mistake(client, question="慢性阻塞性肺疾病最主要的病因",
             my_reasoning="我以为是感染。", doubts="稳定期要用抗菌药吗？",
             subject="内科学", chapter="呼吸系统")
    g = [x for x in _search(client, "病因")["groups"] if x["kind"] == "mistake"][0]
    it = g["items"][0]
    assert it["understanding"] == "我以为是感染。"
    assert it["doubts"] == "稳定期要用抗菌药吗？"
    assert "内科学" in it["sub"]


def test_kc_group_matches_syllabus_items_and_ranks_name_hits_first(client):
    """考点段：条目名命中排在「只命中章节/科目」之前（同档短名在前）。"""
    sylllib.add_teacher_items([
        {"subject": "内科学", "chapter": "呼吸系统", "item": "呼吸系统总论"},
        {"subject": "内科学", "chapter": "循环系统", "item": "急性心力衰竭的治疗"},
    ])
    g = [x for x in _search(client, "心力衰竭")["groups"] if x["kind"] == "kc"][0]
    assert g["label"] == "考点"
    assert g["items"][0]["title"] == "急性心力衰竭的治疗"
    assert "内科学" in g["items"][0]["sub"]


def test_groups_keep_a_fixed_order(client):
    """三段顺序固定：题目 → 错题 → 考点。顺序抖动会让人以为结果在变。"""
    _bank(client, [("慢阻肺的诊断标准", ["肺功能", "胸片"], "A")])
    _mistake(client, question="慢阻肺的诊断我总记混")
    sylllib.add_teacher_items([{"subject": "内科学", "chapter": "呼吸系统", "item": "慢阻肺的诊断"}])
    assert _kinds(_search(client, "慢阻肺")) == ["bank", "mistake", "kc"]


# ---------------------------------------------------------------- 三条承诺
def test_limit_is_per_group_not_total(client):
    """`limit` 是**每组**上限：短的段不因为长的段占位而消失，`total` 仍是命中总数。"""
    _bank(client, [("慢阻肺一", ["甲", "乙"], "A"), ("慢阻肺二", ["甲", "乙"], "A"),
                   ("慢阻肺三", ["甲", "乙"], "A")])
    _mistake(client, question="慢阻肺")
    res = _search(client, "慢阻肺", limit=1)
    by = {g["kind"]: g for g in res["groups"]}
    assert len(by["bank"]["items"]) == 1 and by["bank"]["total"] == 3
    assert len(by["mistake"]["items"]) == 1 and by["mistake"]["total"] == 1


def test_limit_is_clamped(client):
    """越界 `limit` 归一到上限（不报错）；非整数交给统一的 422 错误体。

    面板是敲键即发的，越界参数不该弹红字；但"根本不是整数"属于请求本身不合法，
    由框架统一回 422（本仓的统一错误体契约），不在这里另造一套。
    """
    assert _search(client, "x", limit=999)["limit"] == pal._PER_GROUP_MAX
    assert _search(client, "x", limit=0)["limit"] == pal._PER_GROUP_DEFAULT
    bad = client.get("/api/palette/search", params={"q": "x", "limit": "abc"})
    assert bad.status_code == 422
    assert bad.json()["error_code"] == "VALIDATION_ERROR"


def test_one_broken_source_does_not_silence_the_others(client, monkeypatch):
    """错题索引坏掉时：题目段照常返回，错误如实带回去（不静默当"没有结果"）。

    这是"面板不变哑"承诺的服务端一半——只在 JS 里 catch 只能兜住网络层。
    """
    _bank(client, [("慢阻肺最主要的病因是", ["吸烟", "感染"], "A")])

    def _boom(*_a, **_kw):
        raise RuntimeError("索引损坏")

    monkeypatch.setattr(vault_index, "list_items", _boom)
    res = _search(client, "慢阻肺")
    assert _kinds(res) == ["bank"]
    assert any("错题" in e and "索引损坏" in e for e in res["errors"]), res["errors"]


def test_kc_group_absent_when_syllabus_flag_off(client, monkeypatch):
    """关掉大纲域时考点段缺席，且**不报错**——面板是"顺带搜一下"，不该因为没开进阶功能弹错。"""
    monkeypatch.setitem(state.FLAGS, "syllabus", False)   # 真实开关（不是打桩 state.flag）
    _bank(client, [("慢阻肺", ["甲", "乙"], "A")])
    res = _search(client, "慢阻肺")
    assert _kinds(res) == ["bank"] and res["errors"] == []


# ---------------------------------------------------------------- 口径边界
def test_no_synonym_expansion(client):
    """**不做同义词**：「慢阻肺」搜不到只写「慢性阻塞性肺疾病」的题。

    这条写成用例是为了钉住**边界**，不是钉住缺陷：真做同义词需要一份医学别名词典，
    `bank_iq` 的归一只动格式不动语义，替代不了（方案见 ADR-002）。
    """
    _bank(client, [("慢性阻塞性肺疾病的诊断标准", ["肺功能", "胸片"], "A")])
    _mistake(client, question="慢性阻塞性肺疾病的治疗")
    assert _search(client, "慢阻肺")["groups"] == []


def test_tokens_are_anded_not_ored(client):
    """多词是 AND：只命中一个词的条目不算命中（并集在千题库里等于没筛）。"""
    _bank(client, [("慢阻肺的诊断", ["甲", "乙"], "A")])
    assert _kinds(_search(client, "慢阻肺 诊断")) == ["bank"]
    assert _search(client, "慢阻肺 心衰")["groups"] == []
