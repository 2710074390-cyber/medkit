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
