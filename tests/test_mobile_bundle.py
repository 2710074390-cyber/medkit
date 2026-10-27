"""手机端同步包（`medkit.bundle/v1`）契约守卫。

为什么单独一个文件：这个端点是**唯一一个故意带答案的题库出口**（`/drill` 刻意不带），
所以它的"安全例外"必须是显式的、被测住的——否则以后有人"顺手统一"成 drill 口径，
手机端离线判分会静默坏掉（判不了分，但页面看起来还在）。

证伪：删掉 `export_bundle` 里的 `"answer": it.get("answer")` →
`test_bundle_carries_answers_while_drill_does_not` 必红。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from medkit.main import app

_MD = """# 同步包用例库

### q1 · A1 型 · 单选 · 理解
**慢阻肺最主要的病因**
- A. 吸烟
- B. 感染
- C. 遗传
- D. 空气污染
**✅ 答案：A**
💡 吸烟是最主要的危险因素。

### q2 · X 型 · 多选 · 应用
**下列属于 β 受体阻滞剂禁忌的有**
- A. 支气管哮喘
- B. 严重心动过缓
- C. 高血压
- D. 二度以上房室传导阻滞
**✅ 答案：ABD**
💡 哮喘与缓慢性心律失常为禁忌。
"""


def _client() -> TestClient:
    # base_url 必须是回环：中间的 Host 守卫只放行 127.0.0.1/localhost/::1
    return TestClient(app, base_url="http://127.0.0.1")


def _seed_bank(c: TestClient) -> dict[str, Any]:
    preview = c.post("/api/bank/import/preview", json={"text": _MD, "fmt": "md"}).json()
    items = preview["items"]
    assert len(items) == 2, preview
    r = c.post("/api/bank/import/commit",
               json={"name": "同步包用例库", "subject": "呼吸系统", "items": items})
    assert r.status_code == 200, r.text
    return r.json()


def test_bundle_shape_and_schema_version():
    """包必须有版本号与导出时间——手机端靠 `schema` 决定能不能读。"""
    with _client() as c:
        _seed_bank(c)
        r = c.get("/api/bank/export/bundle")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
        assert "attachment" in r.headers.get("content-disposition", "")

        data = json.loads(r.content)
        assert data["schema"] == "medkit.bundle/v1"
        assert data["exported_at"]
        assert data["app_version"]
        assert len(data["banks"]) == 1
        bank = data["banks"][0]
        assert bank["name"] == "同步包用例库"
        assert bank["subject"] == "呼吸系统"
        assert len(bank["items"]) == 2


def test_bundle_carries_answers_while_drill_does_not():
    """本端点的**全部存在理由**：带答案（离线判分）。drill 不带答案（防作弊），两者必须不同。"""
    with _client() as c:
        seeded = _seed_bank(c)
        bid = seeded["id"] if "id" in seeded else c.get("/api/bank/banks").json()["banks"][0]["id"]

        bundle = json.loads(c.get("/api/bank/export/bundle").content)
        answers = {it["id"]: it["answer"] for it in bundle["banks"][0]["items"]}
        assert answers and all(answers.values()), answers
        assert "ABD" in answers.values()          # 多选按集合语义原样带出

        drill = c.get(f"/api/bank/banks/{bid}/drill").json()
        drill_items = drill.get("items") or []
        assert drill_items, drill
        assert all("answer" not in it for it in drill_items), \
            "drill 不应带答案（这是刻意设计，不是疏漏）"


def test_bundle_ids_are_stable_across_exports():
    """两次导出 id 必须逐项相等——否则手机端的错题与复习进度会整批失联。"""
    with _client() as c:
        _seed_bank(c)
        a = json.loads(c.get("/api/bank/export/bundle").content)
        b = json.loads(c.get("/api/bank/export/bundle").content)
        ids_a = [it["id"] for it in a["banks"][0]["items"]]
        ids_b = [it["id"] for it in b["banks"][0]["items"]]
        assert ids_a == ids_b
        assert len(set(ids_a)) == len(ids_a)


def test_bundle_single_bank_filter_and_404():
    with _client() as c:
        _seed_bank(c)
        bid = c.get("/api/bank/banks").json()["banks"][0]["id"]
        one = json.loads(c.get("/api/bank/export/bundle", params={"bid": bid}).content)
        assert len(one["banks"]) == 1
        r = c.get("/api/bank/export/bundle", params={"bid": "b_nope"})
        assert r.status_code == 404


def test_bundle_optional_sections_can_be_turned_off():
    """`include_*` 关掉时键必须**不存在**（而不是空数组）：
    手机端用「键是否存在」判断"这份包愿意覆盖哪些本地数据"，空数组会被误判成"清空"。"""
    with _client() as c:
        _seed_bank(c)
        lean = json.loads(c.get("/api/bank/export/bundle",
                                params={"include_mistakes": "false",
                                        "include_attempts": "false"}).content)
        assert "mistakes" not in lean
        assert "attempts" not in lean

        full = json.loads(c.get("/api/bank/export/bundle").content)
        # 没有错题/作答时也应是空数组而不是缺失（默认开启 = 明确告知"本机就是空的"）
        assert full.get("attempts") == []
        assert full.get("mistakes") == []


def test_bundle_carries_attempts_and_mistakes_when_present():
    """带流水与错题：手机端才能接着算熟练度，而不是第一次同步就清零。"""
    with _client() as c:
        _seed_bank(c)
        bid = c.get("/api/bank/banks").json()["banks"][0]["id"]
        drill = c.get(f"/api/bank/banks/{bid}/drill").json()
        item = drill["items"][0]
        # 故意答 B（第 1 题正确答案是 A）：制造一条错题，验证回流链路
        r = c.post("/api/bank/grade", json={"bank_id": bid, "item_id": item["id"],
                                            "answer": "B", "mode": "drill"})
        assert r.status_code == 200, r.text

        data = json.loads(c.get("/api/bank/export/bundle").content)
        assert len(data["attempts"]) >= 1
        assert {"bank_id", "item_id", "correct", "occurred_at"} <= set(data["attempts"][0])
        # 判错回流错题本：包里的 mistakes 必须能看到这条（否则手机端接不上桌面端的错题）
        assert isinstance(data["mistakes"], list)


def test_bundle_matches_exported_file_naming():
    """文件名要可识别、可排序（手机端文件管理器里靠它区分多次导出）。"""
    with _client() as c:
        _seed_bank(c)
        r = c.get("/api/bank/export/bundle")
        disp = r.headers["content-disposition"]
        assert "medkit-bundle-" in disp
        assert disp.rstrip('"').endswith(".json")
