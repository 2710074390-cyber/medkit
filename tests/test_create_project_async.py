"""W6 守卫：建项目端点不得阻塞事件循环（async handler + 落盘移出到线程）。

背景（2026-10-02 二轮审计 W6）：`POST /api/projects` 原是**同步** def，虽被 FastAPI
丢进线程池，但同文件隔壁 `upload_asset`、`pipeline` 的 trial/生成路径都已是
`async def` + `asyncio.to_thread`——**同类路径一半做了、一半没做**（正是本轮审计
反复出现的模式）。阻塞点：`allocate()` 内 `extract_keywords` 是 O(len²)
（4000×7 窗口 + 每窗口正则）、目录 `mkdir`、两次原子写。

本文件两道防线：
1. 结构守卫：`create_project` 必须是 `async def`，且**真的** `await asyncio.to_thread(...)`
   调 `create_project_record`（AST 判 `ast.Call`，不是文本子串）；
2. 行为守卫：走 TestClient 真发一次请求，断言**落盘语义未变**（pid/quota 结构 + 文件在场）。
   —— 结构改对了但把业务改坏，必须也能被照出来。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "medkit" / "routers" / "projects.py"


def _tree() -> ast.Module:
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def _find_fn(tree: ast.Module, name: str):
    # 兼容 async：AsyncFunctionDef 与 FunctionDef 都是 def 语句
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


# ------------------------------------------------------------------ 结构守卫
def test_create_project_is_async() -> None:
    """`create_project` 必须是 async def（阻塞落盘不得占用事件循环）。"""
    fn = _find_fn(_tree(), "create_project")
    assert fn is not None, "未找到 create_project"
    assert isinstance(fn, ast.AsyncFunctionDef), "create_project 不是 async def（应改 async + to_thread）"


def test_create_project_awaits_to_thread() -> None:
    """必须 `await asyncio.to_thread(core_projects.create_project_record, ...)`（AST 判调用）。"""
    fn = _find_fn(_tree(), "create_project")
    assert fn is not None
    awaited: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            cal = node.value.func
            # 形态一：asyncio.to_thread(...)
            if isinstance(cal, ast.Attribute) and cal.attr == "to_thread":
                awaited.append("to_thread")
                # 参数里必须出现 create_project_record
                for a in node.value.args:
                    if isinstance(a, ast.Attribute) and a.attr == "create_project_record":
                        awaited.append("create_project_record")
    assert "to_thread" in awaited, "create_project 未 await asyncio.to_thread（落盘仍在事件循环里）"
    assert "create_project_record" in awaited, "to_thread 未包住 create_project_record"


def test_sibling_blocking_endpoints_are_async() -> None:
    """元守卫：同文件其他「有阻塞 IO」的端点也应是 async（同类路径口径一致）。

    仅锚定已知已改造的两个（`upload_asset` / `create_project`），避免把框架细节写死；
    它们在场即证明「async 范式」在本文件是被采用的，`create_project` 不是孤例。
    """
    tree = _tree()
    for name in ("upload_asset", "create_project"):
        fn = _find_fn(tree, name)
        assert isinstance(fn, ast.AsyncFunctionDef), f"{name} 不是 async def"


# ------------------------------------------------------------------ 行为守卫（走 TestClient 真身）
@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离数据目录，返回 (TestClient, projects_dir)。

    base_url 用 `http://127.0.0.1`：`main._guard_local` 会拒 `testserver`（403），
    与 `tests/test_api.py:58` 同一范式。
    """
    from fastapi.testclient import TestClient

    from medkit.core import config as cfg
    from medkit.main import app

    projdir = tmp_path / "projects"
    projdir.mkdir(parents=True, exist_ok=True)
    saved = dict(cfg.load())
    saved["projects_dir"] = str(projdir)
    saved["api_key"] = "sk-test-key"
    monkeypatch.setattr(cfg, "load", lambda: dict(saved))
    monkeypatch.setattr(cfg, "save", lambda c: saved.update(c))

    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c, projdir


def _payload() -> dict:
    return {
        "subject": "内科学",
        "target": 20,
        "textbook_slices": [{"sid": "S001", "text": "心力衰竭的诊断与治疗。" * 20}],
        "teacher_slices": [{"sid": "T001", "text": "重点：心衰 心衰 心衰 分级"}],
        "ratios": {"A1": 100},
    }


def test_create_project_still_persists(client) -> None:
    """端到端：async 改造后落盘语义不变（pid + quota + slices.json 在场）。"""
    c, projdir = client
    r = c.post("/api/projects", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("pid"), f"未返回 pid：{body}"
    assert isinstance(body.get("quota"), list) and body["quota"], "quota 空"
    assert sum(q["count"] for q in body["quota"]) == 20, "配额总和应等于 target"
    pdir = projdir / body["pid"]
    assert (pdir / "meta.json").exists()
    assert (pdir / "slices.json").exists()


def test_create_project_validation_still_rejects(client) -> None:
    """校验分支不得因 async 改造而失效（目标题数越界 → 400）。"""
    c, _ = client
    bad = _payload()
    bad["target"] = 5
    r = c.post("/api/projects", json=bad)
    assert r.status_code == 400
