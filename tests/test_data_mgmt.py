"""routers/data 数据管理区测试（U-20）：摘要 / 一键备份 zip / 清空全部（二次确认 + 自动备份）。

注意：清空端点为破坏性操作，但 `cfg.CONFIG_DIR` 已被 conftest 重定向到 tmp；本文件再把
`projects_dir` 显式指向 tmp，确保既不触碰真实项目目录、也不触碰真实 home。
"""

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from medkit.main import app

client = TestClient(app, base_url="http://127.0.0.1")


def _seed_data(tmp_path: Path) -> Path:
    store = tmp_path / "library"
    store.mkdir(parents=True, exist_ok=True)
    (store / "mistakes.json").write_text(
        json.dumps([{"id": "m1", "subject": "儿科学", "chapter": "ch1",
                     "question": "q?", "answer": "a", "miss_count": 1}]),
        encoding="utf-8")
    (store / "review_queue.json").write_text(
        json.dumps([{"id": "c1", "subject": "儿科学", "kp_name": "k1", "state": "new"}]),
        encoding="utf-8")
    return store


def _seed_projects(tmp_path: Path) -> Path:
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "meta.json").write_text(json.dumps({"name": "p1", "version": 1}), encoding="utf-8")
    return tmp_path / "projects"


def _point_projects_dir(tmp_path: Path, monkeypatch) -> Path:
    proj_dir = tmp_path / "projects"
    cfgfile = tmp_path / "config.json"
    cfgfile.write_text(json.dumps({"projects_dir": str(proj_dir)}), encoding="utf-8")
    # 显式重定向，确保 router 读取配置的 projects_dir 指向 tmp（不触碰真实 home）
    return proj_dir


def test_data_summary_readonly(tmp_path, monkeypatch):
    _proj = _point_projects_dir(tmp_path, monkeypatch)
    _seed_data(tmp_path)
    _seed_projects(tmp_path)
    r = client.get("/api/data/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dir"] == str(tmp_path)
    assert body["projects_dir"] == str(_proj)
    assert set(body["size"]) >= {"config", "library", "projects", "total"}
    assert body["counts"]["mistakes"] == 1
    assert body["counts"]["review_cards"] == 1
    assert body["counts"]["projects"] == 1
    assert body["by_subject"].get("儿科学") == 1
    assert body["backups"] == []


def test_data_backup_creates_zip(tmp_path, monkeypatch):
    _proj = _point_projects_dir(tmp_path, monkeypatch)
    _seed_data(tmp_path)
    r = client.post("/api/data/backup", json={"include_projects": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    zip_path = Path(body["path"])
    assert zip_path.exists() and zip_path.suffix == ".zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "library/mistakes.json" in names
        assert "config.json" in names
        assert not any(n.startswith("projects/") for n in names)  # 未请求时不含项目


def test_data_backup_includes_projects_when_asked(tmp_path, monkeypatch):
    _proj = _point_projects_dir(tmp_path, monkeypatch)
    _seed_data(tmp_path)
    _seed_projects(tmp_path)
    r = client.post("/api/data/backup", json={"include_projects": True})
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(r.json()["path"]) as zf:
        names = set(zf.namelist())
        assert any(n.startswith("projects/") for n in names)


def test_data_clear_requires_confirm(tmp_path, monkeypatch):
    _proj = _point_projects_dir(tmp_path, monkeypatch)
    store = _seed_data(tmp_path)
    proj = _seed_projects(tmp_path)
    # 错误确认短语 → 400，数据原封不动
    r = client.post("/api/data/clear", json={"confirm": "不删除"})
    assert r.status_code == 400
    assert store.exists() and (proj / "p1" / "meta.json").exists()
    # 正确短语 → 200，自动备份 + 数据清空
    r = client.post("/api/data/clear", json={"confirm": "清空全部数据"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "library" in body["removed"]
    assert "projects" in body["removed"]
    assert not (store / "mistakes.json").exists()          # 学习数据已清空
    assert not (proj / "p1").exists()                       # 项目已清空
    # 自动完整备份（含项目）已生成
    backup = Path(body["backup"]["path"])
    assert backup.exists()
    with zipfile.ZipFile(backup) as zf:
        names = set(zf.namelist())
        assert "library/mistakes.json" in names
        assert any(n.startswith("projects/p1/") for n in names)


def test_data_clear_empty_safe(tmp_path, monkeypatch):
    _proj = _point_projects_dir(tmp_path, monkeypatch)
    r = client.post("/api/data/clear", json={"confirm": "清空全部数据"})
    # 空库清空也应成功且自动备份，不报错
    assert r.status_code == 200, r.text
