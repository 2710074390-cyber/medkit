"""WP-12：纯净安装包检查脚本（pack/check-package.py）单元测试。"""

import importlib.util
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_package", Path(__file__).resolve().parents[1] / "pack" / "check-package.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_check_pass_on_clean(tmp_path):
    m = _load()
    root = tmp_path / "MedKit"
    (root / "_internal" / "web" / "app.js").parent.mkdir(parents=True)
    (root / "_internal" / "web" / "app.js").write_text("x", encoding="utf-8")
    assert m.check_dist(root) == []


def test_check_fail_on_residue(tmp_path):
    m = _load()
    root = tmp_path / "MedKit"
    (root / "_internal" / "medkit" / "data" / "samples").mkdir(parents=True)
    (root / "_internal" / "data" / "syllabus_seed_306.json").parent.mkdir(parents=True)
    (root / "_internal" / "data" / "syllabus_seed_306.json").write_text("{}", encoding="utf-8")
    (root / "_internal" / "tests" / "x.pyc").parent.mkdir(parents=True)
    (root / "_internal" / "tests" / "x.pyc").write_bytes(b"x")
    bad = m.check_dist(root)
    assert any("samples" in b for b in bad)
    assert any("syllabus_seed_306.json" in b for b in bad)
    assert any("tests" in b for b in bad) and any(b.endswith(".pyc") for b in bad)


def test_main_missing_dist_returns_0(tmp_path):
    m = _load()
    assert m.main([str(tmp_path / "none")]) == 0
