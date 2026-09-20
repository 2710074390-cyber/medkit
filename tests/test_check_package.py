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


def test_spec_ships_dist_info_for_license_obligation():
    """S2-18 / S2-22（R8+W）：spec 必须把 lock 闭包的 dist-info 打进产物。

    PyInstaller **默认剥掉** dist-info，而 `THIRD_PARTY_NOTICES.md` 承诺「随产物保留 LICENSE 原文」
    —— 原产物因此长期报「33 个已声明依赖缺 dist-info」。2026-09-19 实测：补齐后
    `check-package.py --strict` 从「通过（有警告）」变为**完全通过**（38 个 dist-info）。
    本用例锁住该修复，防止回归。
    """
    src = (ROOT / "medkit.spec").read_text(encoding="utf-8")
    assert "def _dist_info_datas(" in src, "缺少 dist-info 收集函数"
    # ⚠️ 必须**整行**匹配：子串匹配会把 `# datas += _dist_info_datas()` 这类注释也算命中
    # （反向验证实测：注释掉该行时子串断言仍然通过 = 假绿）。
    lines = [ln.strip() for ln in src.splitlines()]
    assert "datas += _dist_info_datas()" in lines, "收集函数未接到 datas（或被注释掉）"
    assert "def _lock_closure_names(" in src, "未按 requirements.lock 闭包限定范围（会混入构建期依赖）"
    # 范围限定必须真的读 lock（否则会把 pyinstaller 等构建期依赖也打进产物）
    assert "requirements.lock" in src


# ---------------------------------------------------------------- R8+W：测试产物加固

def _dirty(tmp_path, *rel_paths, dirs=()):
    """构造一个只含指定条目的假产物目录。"""
    root = tmp_path / "MedKit"
    (root / "_internal").mkdir(parents=True, exist_ok=True)
    (root / "MedKit.exe").write_text("x", encoding="utf-8")
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return root


def test_blacklist_covers_test_reports_logs_and_debug(tmp_path):
    """R8+W：黑名单必须覆盖测试报告 / 覆盖率 / 测试缓存 / 日志 / 临时 / 调试产物。

    原表只有「样例/种子/测试目录/字节码」四类——测试报告、覆盖率、日志、调试符号
    这些同样是「不该进安装包」的东西，且混进去没人会发现。
    """
    m = _load()
    cases = [
        "_internal/.coverage",
        "_internal/coverage.xml",
        "_internal/htmlcov/index.html",
        "_internal/.pytest_cache/CACHEDIR.TAG",
        "_internal/junit.xml",
        "_internal/app.log",
        "_internal/run.pdb",
        "_internal/backup.bak",
        "_internal/debugpy/_vendored/x.py",
        "tests/test_foo.py",
        "_internal/conftest.py",
    ]
    for rel in cases:
        root = _dirty(tmp_path / rel.replace("/", "_").replace(".", "_"), rel)
        hits = m.check_dist(root)
        assert hits, f"黑名单漏检：{rel}"


def test_test_only_modules_detected(tmp_path):
    """R8+W：测试专用依赖要按**模块名**拦（闭包检查只看 dist-info，裸目录形式会漏判）。"""
    m = _load()
    for mod in ("_pytest", "pluggy", "coverage", "playwright", "debugpy", "iniconfig"):
        root = _dirty(tmp_path / mod, dirs=[f"_internal/{mod}"])
        hits = m.test_only_modules(root)
        assert hits == [f"_internal/{mod}"], f"{mod} 未被识别：{hits}"


def test_test_source_files_detected(tmp_path):
    """R8+W：测试源码文件（不限目录）必须被拦——单测源码进产物是信息泄露。"""
    m = _load()
    for name in ("test_foo.py", "foo_test.py", "test_bar.pyc"):
        root = _dirty(tmp_path / name, f"_internal/pkg/{name}")
        hits = m.test_files(root)
        assert hits == [f"_internal/pkg/{name}"], f"{name} 未被识别：{hits}"


def test_legit_files_not_flagged(tmp_path):
    """对照组：正常运行时文件不得误报（避免守卫因误报被关掉）。"""
    m = _load()
    root = _dirty(tmp_path / "ok",
                  "_internal/medkit/web/app.js",
                  "_internal/medkit/prompts/medgen.md",
                  "_internal/LICENSE",
                  "_internal/setuptools/_vendor/importlib_metadata/__init__.py")
    assert m.check_dist(root) == []
    assert m.test_only_modules(root) == []
    assert m.test_files(root) == []


def test_main_returns_1_on_dirty_dist(tmp_path, capsys):
    """端到端：脏产物必须让 main() 返回 1（不是只打印警告）。"""
    m = _load()
    root = _dirty(tmp_path / "d", "tests/test_x.py", dirs=["_internal/_pytest"])
    rc = m.main([str(root), "--strict"])
    out = capsys.readouterr().out
    assert rc == 1, f"脏产物竟返回 {rc}"
    assert "测试专用内容" in out


def test_main_returns_0_on_clean_dist(tmp_path, capsys):
    """对照组：干净产物返回 0。"""
    m = _load()
    root = _dirty(tmp_path / "c", "_internal/medkit/web/index.html")
    rc = m.main([str(root), "--strict"])
    assert rc == 0, capsys.readouterr().out


# ---------------------------------------------------------------- R8+W：构建环境体检

def _load_build_env():
    spec = importlib.util.spec_from_file_location(
        "check_build_env", ROOT / "pack" / "check-build-env.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_setuptools_is_not_treated_as_dev_only():
    """`setuptools` 写在 requirements-dev.txt 里，但 jieba 运行期要 pkg_resources。

    若把它当 dev 专用，体检会误报、还会误导人把它从产物里剔掉 → jieba 直接崩。
    """
    m = _load_build_env()
    names = m.dev_only_names()
    assert "setuptools" not in names, "setuptools 被误判为 dev 专用（运行期需要它）"
    for must in ("pytest", "playwright", "pip-audit"):
        assert must in names, f"{must} 应被识别为 dev 专用"


def test_build_env_check_wired_before_pyinstaller():
    """接线级：build.bat 必须在 PyInstaller **之前**跑环境体检，且失败即中断。

    ⚠️ 匹配**调用形态**（含 `check-build-env.py` 且非 rem/echo）——只匹配文件名会被
    echo 提示行骗过（本会话踩过 4 次同类问题）。
    """
    src = (ROOT / "pack" / "build.bat").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in src.splitlines()]
    env_calls = [i for i, ln in enumerate(lines)
                 if "check-build-env.py" in ln and not ln.startswith(("rem", "echo"))]
    pyi_calls = [i for i, ln in enumerate(lines)
                 if "PyInstaller" in ln and "-m" in ln and not ln.startswith(("rem", "echo"))]
    assert env_calls, "build.bat 未调用环境体检（或被注释）"
    assert pyi_calls, "build.bat 未调用 PyInstaller"
    assert min(env_calls) < min(pyi_calls), "体检必须在 PyInstaller 之前"
    seg = src[src.index("check-build-env.py"):]
    assert "errorlevel 1" in seg[:600], "体检失败后未中断构建"


def test_spec_excludes_test_only_packages():
    """spec 必须主动排除测试/开发专用包（前置一道闸，不只靠事后检查）。"""
    spec = (ROOT / "medkit.spec").read_text(encoding="utf-8")
    seg = spec[spec.index("excludes=["):spec.index("]", spec.index("excludes=["))]
    for pkg in ("pytest", "_pytest", "coverage", "playwright", "debugpy", "pluggy"):
        assert f'"{pkg}"' in seg, f"spec excludes 缺 {pkg}"
    assert '"setuptools"' not in seg, "setuptools 是运行期依赖，不得排除"
