"""WP-12：纯净安装包检查脚本（pack/check-package.py）单元测试。"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


# ---- S2-19 / S2-18：把「空操作」变成真守卫 ----

def test_main_strict_missing_dist_returns_1(tmp_path):
    """--strict 下「没产物」即失败——原实现恒 return 0 使 CI 该步成为空操作（S2-19）。"""
    m = _load()
    assert m.main([str(tmp_path / "none"), "--strict"]) == 1


def test_closure_drift_detects_undeclared(tmp_path):
    """产物里出现 lock 闭包之外的发行包 → 必须被识别为「构建机环境污染」（S2-18）。"""
    m = _load()
    root = tmp_path / "MedKit"
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (internal / "attrs-26.1.0.dist-info").mkdir()          # 未声明
    (internal / "pydantic-2.13.4.dist-info").mkdir()       # 已声明
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2.13.4\n", encoding="utf-8")
    extras, _missing = m.closure_drift(root, lock)
    assert extras == ["attrs"], f"应识别出未声明的 attrs，实际 {extras}"


def test_strict_fails_on_undeclared_extra(tmp_path):
    m = _load()
    root = tmp_path / "MedKit"
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (internal / "attrs-26.1.0.dist-info").mkdir()
    # 通过真实仓库 lock 走一遍 main()：未声明包在 --strict 下必须让整体失败
    assert m.main([str(root), "--strict"]) == 1


def test_closure_no_drift_when_all_declared(tmp_path):
    m = _load()
    root = tmp_path / "MedKit"
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (internal / "pydantic-2.13.4.dist-info").mkdir()
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2.13.4\n", encoding="utf-8")
    assert m.closure_drift(root, lock) == ([], 0)


# ---- U-24：spec 体积断言——excludes 必须拦下与本应用无关的大件，防误差膨胀 ----
# 这几件一旦被分析器过度收集，绿色免安装包会凭空膨胀数十~数百 MB，且运行时全部用不到。
def _spec_excludes() -> list[str]:
    src = (ROOT / "medkit.spec").read_text(encoding="utf-8")
    m = re.search(r'excludes\s*=\s*\[(.*?)\]', src, re.S)
    assert m, "medkit.spec 缺少 excludes 块"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_spec_excludes_heavy_libs():
    ex = set(_spec_excludes())
    for heavy in ("cv2", "pandas", "matplotlib", "PIL", "numpy", "torch",
                  "scipy", "sklearn", "pyarrow", "transformers"):
        assert heavy in ex, f"medkit.spec excludes 遗漏大件：{heavy}"


def test_spec_excludes_no_test_frameworks():
    ex = set(_spec_excludes())
    # 测试框架/静态工具不该进产物
    for kept_out in ("pytest", "unittest", "tkinter"):
        assert kept_out in ex, f"medkit.spec excludes 遗漏：{kept_out}"


def test_spec_has_license_in_datas():
    # AGPL：许可证正文必须随安装包分发（AGPL「随分发提供许可证」要求）
    src = (ROOT / "medkit.spec").read_text(encoding="utf-8")
    assert '"LICENSE"' in src
