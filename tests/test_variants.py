"""错点变式训练（2026-10-29）的可证伪行为。

守四条：
1. 选点复用本地 priority 口径，不把「已掌握」的错题再拉出来练；
2. 生成结果必须过现有 options_check 门禁，重复题不许进训练；
3. 判分前不下发答案；判分用集合语义（与 bank.answers_equal 同源）；
4. 判错当天回流原错题，判对只记迁移证据、不直接改「已掌握」。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from medkit import main as m
from medkit.agents import variant_agent
from medkit.core import variants, vault, vault_index


@pytest.fixture()
def vroot(tmp_path, monkeypatch):
    root = tmp_path / "vaultroot"
    vault.init_vault(str(root))
    monkeypatch.setattr(vault, "CONFIG_CACHE", None, raising=False)
    vault.set_vault_dir(str(root))
    monkeypatch.setattr(variants, "VARIANTS_DIR", tmp_path / "variants")
    yield root
    vault.set_vault_dir("")


def _rec(**kw):
    base = {"id": "m-001", "subject": "内科学", "chapter": "呼吸系统", "topic": "慢阻肺",
            "know_tags": ["copd"], "question": "慢阻肺最主要的病因是",
            "options": ["A. 吸烟", "B. 感染", "C. 遗传", "D. 过敏", "E. 职业粉尘"],
            "answer": "A", "analysis": "吸烟是 COPD 最重要的环境致病因素。",
            "created_at": "2026-10-13T21:12:04", "source": "bank"}
    base.update(kw)
    return base


def _seed(root, **kw):
    rec = _rec(**kw)
    vault.write_record(rec, root)
    vault_index.rebuild(root)
    return vault_index.list_items()[0]["path"]


def _valid_item(i=1, question="慢阻肺患者稳定期最重要的干预是"):
    return {
        "question": question,
        "options": ["A. 长期抗生素", "B. 戒烟治疗", "C. 口服激素", "D. 利尿消肿", "E. 家庭吸氧"],
        "answer": "B",
        "analysis": "稳定期最重要的干预是戒烟，可延缓肺功能下降。",
        "option_rationales": {"A": "稳定期无感染时不首选抗生素", "B": "戒烟是唯一能延缓病程的干预",
                              "C": "激素用于急性加重", "D": "利尿不改善气道阻塞", "E": "吸氧用于低氧血症"},
        "misconceptions": {"A": "把急性加重期的用药搬到稳定期"},
        "source_quote": "",
        "difficulty": 3,
        "variant_type": "stem",
    }


class _Stub:
    def __init__(self, items):
        self.items = items

    def chat_json(self, messages, **kw):
        return {"items": self.items}


def test_agent_generate_validates_unique_items():
    src = _rec()
    items = [_valid_item(1), _valid_item(2, "慢阻肺急性加重期首选的处理是"),
             _valid_item(3, "慢阻肺与哮喘的鉴别要点是")]
    out = variant_agent.generate(src, n=3, client=_Stub(items))
    assert len(out["items"]) == 3
    assert all(it["answer"] == "B" for it in out["items"])


def test_agent_rejects_pure_duplicate_and_allows_question_variant():
    src = _rec()
    dup = _valid_item()
    dup["question"], dup["options"] = src["question"], list(src["options"])
    with pytest.raises(variant_agent.VariantError):
        variant_agent.generate(src, n=1, client=_Stub([dup]))

    # 题干相同、选项不同 = 干扰项变式，允许
    changed = _valid_item()
    changed["question"] = src["question"]
    changed["options"] = ["A. 长期感染", "B. 戒烟治疗", "C. 家族遗传", "D. 过敏反应", "E. 职业粉尘暴露"]
    changed["answer"] = "B"
    out = variant_agent.generate(src, n=1, client=_Stub([changed]))
    assert len(out["items"]) == 1


def test_agent_rejects_item_missing_option_rationale():
    bad = _valid_item()
    bad["option_rationales"].pop("E")
    with pytest.raises(variant_agent.VariantError):
        variant_agent.generate(_rec(), n=1, client=_Stub([bad]))


def test_plan_uses_priority_and_skips_learned(vroot):
    rel = _seed(vroot)
    p = variants.plan(limit=3)
    assert p["count"] == 1
    assert p["items"][0]["path"] == rel
    assert p["items"][0]["priority"] >= variants.PRIORITY_MIN
    # 已掌握的不该再被选出来
    rec = vault.read_record(vroot / rel)
    rec["status"] = "learned"
    vault.write_record_at(vroot / rel, rec)
    vault_index.reindex_file(vroot / rel, vroot)
    p2 = variants.plan(limit=3)
    assert p2["count"] == 0


def test_public_set_hides_answers_until_grade(vroot):
    rel = _seed(vroot)
    data = variants.save_set(rel, vault.read_record(vroot / rel),
                             [_valid_item(1), _valid_item(2, "另一道变式")])
    pub = variants.public_set(data)
    assert pub["items"][0]["question"]
    assert "answer" not in pub["items"][0]
    assert "analysis" not in pub["items"][0]
    assert pub["items"][0]["options"]
    # 判分后才返回答案/解析
    got = variants.grade(data["id"], {"0": "B", "1": "A"})
    assert got["items"][0]["answer"] == "B"
    assert got["items"][0]["analysis"]


def test_grade_failure_flows_back_today(vroot):
    rel = _seed(vroot)
    data = variants.save_set(rel, vault.read_record(vroot / rel),
                             [_valid_item(1), _valid_item(2, "另一道变式"),
                              _valid_item(3, "第三道变式")])
    got = variants.grade(data["id"], {"0": "A", "1": "A", "2": "A"})
    assert got["passed"] is False and got["correct"] == 0
    rec = vault.read_record(vroot / rel)
    assert rec["variant_fail"] == 1
    assert int(rec["miss_count"]) >= 2
    assert rec["status"] == "unlearned"
    assert rec["due"] == date.today().isoformat()


def test_grade_pass_records_evidence_but_does_not_mark_mastered(vroot):
    rel = _seed(vroot)
    data = variants.save_set(rel, vault.read_record(vroot / rel),
                             [_valid_item(1), _valid_item(2, "另一道变式"),
                              _valid_item(3, "第三道变式")])
    got = variants.grade(data["id"], {"0": "B", "1": "B", "2": "A"})
    assert got["passed"] is True and got["correct"] == 2
    rec = vault.read_record(vroot / rel)
    assert rec["variant_pass"] == 1
    assert rec["status"] == "unlearned", "变式通过只是迁移证据，不等于已掌握"


def test_variants_endpoints(vroot, monkeypatch):
    rel = _seed(vroot)
    items = [_valid_item(1), _valid_item(2, "另一道变式"), _valid_item(3, "第三道变式")]
    monkeypatch.setattr(variant_agent, "generate", lambda rec, n=3: {"items": items, "rejected": []})
    client = TestClient(m.app, base_url="http://127.0.0.1")

    r = client.get("/api/vault/variants/plan", params={"limit": 3})
    assert r.status_code == 200 and r.json()["count"] == 1

    r = client.post("/api/vault/variants/generate", json={"path": rel, "n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["set"]["set_id"]
    assert "answer" not in body["set"]["items"][0]

    r = client.post(f"/api/vault/variants/{body['set']['set_id']}/grade",
                    json={"answers": {"0": "B", "1": "B", "2": "A"}})
    assert r.status_code == 200
    assert r.json()["correct"] == 2 and r.json()["passed"] is True

    r = client.get("/api/vault/mistakes/item", params={"path": rel})
    assert r.status_code == 200 and r.json()["question"] == "慢阻肺最主要的病因是"


def test_variant_fields_round_trip_in_vault(vroot):
    rel = _seed(vroot)
    rec = vault.read_record(vroot / rel)
    rec["variant_pass"] = 2
    rec["variant_fail"] = 1
    rec["variant_last"] = "2026-10-29T12:00:00"
    vault.write_record_at(vroot / rel, rec)
    back = vault.read_record(vroot / rel)
    assert back["variant_pass"] == 2 and back["variant_fail"] == 1
    assert back["variant_last"] == "2026-10-29T12:00:00"

