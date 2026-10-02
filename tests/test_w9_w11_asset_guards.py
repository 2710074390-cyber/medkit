"""W9 + W11 守卫。

- **W9**：`mineru._page_count` 读不出页数时（None）调用方会**跳过页数闸**——
  「没量到」被当成「没超限」。修正后必须**留痕**（`errors` 计数），使
  「闸门没跑」与「闸门通过」可区分。
- **W11**：`DELETE /assets/{sid}` 是真 `unlink()`（不可逆，不进回收站），
  新增 `?dry_run=1` 预演：只回报将删的文件，**不得动文件系统**。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_MINERU_SRC = Path(__file__).resolve().parents[1] / "medkit" / "core" / "mineru.py"
_PROJ_SRC = Path(__file__).resolve().parents[1] / "medkit" / "routers" / "projects.py"


# ================================================================ W9
def test_page_count_failure_records_error() -> None:
    """结构：`_page_count` 的失败分支必须调用 `errors.record`（不能裸 return None）。"""
    tree = ast.parse(_MINERU_SRC.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_page_count"), None)
    assert fn is not None, "未找到 _page_count"
    calls: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            calls.append(f.attr if isinstance(f, ast.Attribute) else
                         (f.id if isinstance(f, ast.Name) else ""))
    assert "record" in calls, f"_page_count 失败未留痕（调用了 {calls}）"


def test_page_count_failure_is_observable(tmp_path, monkeypatch) -> None:
    """行为：喂一个打不开的「PDF」→ 返回 None 且 `errors` 计数 +1（可观测）。"""
    from medkit.core import errors as errs
    from medkit.core.mineru import MinerUClient

    errs.reset()
    bad = tmp_path / "not_a_pdf.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    out = MinerUClient._page_count(bad)
    assert out is None, "打不开的文件应返回 None"
    snap = errs.snapshot(include_disk=False)
    assert snap["counts"].get("mineru.page_count_unavailable", 0) >= 1, \
        "页数读取失败必须计入 errors（否则闸门被静默跳过无从发现）"


# ================================================================ W11
def _delete_asset_ast():
    tree = ast.parse(_PROJ_SRC.read_text(encoding="utf-8"))
    return next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "delete_asset"), None)


def test_delete_asset_has_dry_run_param() -> None:
    """结构：`delete_asset` 必须收 `dry_run` 形参。"""
    fn = _delete_asset_ast()
    assert fn is not None, "未找到 delete_asset"
    args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert "dry_run" in args, f"delete_asset 无 dry_run 形参（实为 {args}）"


def test_delete_asset_dry_run_does_not_unlink() -> None:
    """结构：dry_run 分支必须在 `unlink` **之前** return（否则预演仍会删）。"""
    fn = _delete_asset_ast()
    assert fn is not None
    # 找到 dry_run 的 if 语句，断言其 body 里有 return，且该 return 的 lineno < 最近 unlink 行
    dry_if = None
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "dry_run":
            dry_if = node
            break
    assert dry_if is not None, "未找到 `if dry_run:` 分支"
    has_return = any(isinstance(b, ast.Return) for b in dry_if.body)
    assert has_return, "dry_run 分支没有 return（会继续往下 unlink）"
    unlinks = [n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "unlink"]
    assert unlinks, "delete_asset 里没有 unlink？"
    returns = [b.lineno for b in dry_if.body if isinstance(b, ast.Return)]
    assert min(returns) < min(unlinks), "dry_run 的 return 不在 unlink 之前"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from medkit.core import config as cfg
    from medkit.main import app

    projdir = tmp_path / "projects"
    projdir.mkdir(parents=True, exist_ok=True)
    saved = dict(cfg.load())
    saved["projects_dir"] = str(projdir)
    saved["features"] = dict(saved.get("features") or {})
    saved["features"]["image_q"] = True          # require_flag("image_q")
    monkeypatch.setattr(cfg, "load", lambda: dict(saved))
    monkeypatch.setattr(cfg, "save", lambda c: saved.update(c))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c, projdir


def _seed_project(projdir: Path, pid: str = "P1"):
    base = projdir / pid
    (base / "assets").mkdir(parents=True, exist_ok=True)
    img = base / "assets" / "fig1.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    slices = [{"sid": "S001", "role": "image", "text": "图 1 心脏解剖",
               "image": {"path": "assets/fig1.png"}},
              {"sid": "S002", "role": "textbook", "text": "正文"}]
    (base / "slices.json").write_text(json.dumps(slices, ensure_ascii=False), encoding="utf-8")
    (base / "meta.json").write_text(json.dumps({"pid": pid, "subject": "内科学"}), encoding="utf-8")
    return base, img


def test_delete_asset_dry_run_keeps_file(client) -> None:
    """端到端：`?dry_run=1` 后文件仍在、slices.json 不变。"""
    c, projdir = client
    base, img = _seed_project(projdir)
    before = (base / "slices.json").read_text(encoding="utf-8")

    r = c.delete("/api/projects/P1/assets/S001?dry_run=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("dry_run") is True
    assert body.get("exists") is True
    assert img.exists(), "dry_run 竟删掉了文件！"
    assert (base / "slices.json").read_text(encoding="utf-8") == before, "dry_run 改了索引！"


def test_delete_asset_real_delete_removes_file(client) -> None:
    """对照：不带 dry_run 时真的删除（证明 dry_run 只是旁路，不是把删除也关了）。"""
    c, projdir = client
    base, img = _seed_project(projdir)
    r = c.delete("/api/projects/P1/assets/S001")
    assert r.status_code == 200, r.text
    assert not img.exists(), "真删除未生效"
    left = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    assert all(s.get("sid") != "S001" for s in left), "索引未同步移除"
