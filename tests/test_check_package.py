"""WP-12：纯净安装包检查脚本（pack/check-package.py）单元测试。"""

import ast
import importlib.util
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
#
# ## 2026-09-29（R11）：判据从正则改为 AST
#
# 旧实现 `re.search(r'excludes\s*=\s*\[(.*?)\]', src, re.S)` 的 `.*?` 是**非贪婪**，
# 遇到**第一个** `]` 就停。实测（`.workbuddy-ai/tmp/probe_excludes_regex.py`）：
#   · `"foo[bar]"` 放在列表**中间** → 旧正则得 48 条（AST 49 条），漏掉该项本身；
#   · `"foo[bar]"` 放在列表**首位** → 旧正则得 **0 条**。
#
# **准确表述：这是「假红」风险，不是假绿。** 断言是 `assert heavy in ex`，
# 所以 ex 变空/变小只会让守卫**误报**（红），不会放过真实遗漏。
# 危害在于：一旦有人加含方括号的条目（`"foo[bar]"`、正则字符串都很常见），
# 守卫开始无故变红 ⇒ 下一个人会去**删守卫或删条目**，而不是修判据。
# 改用 AST 后与文本形态完全解耦。
def _spec_tree() -> ast.Module:
    return ast.parse((ROOT / "medkit.spec").read_text(encoding="utf-8"))


def _spec_excludes() -> list[str]:
    """从 `Analysis(..., excludes=[...])` 取 excludes 条目（AST，不受方括号嵌套影响）。"""
    for node in ast.walk(_spec_tree()):
        if isinstance(node, ast.keyword) and node.arg == "excludes":
            assert isinstance(node.value, ast.List), (
                "medkit.spec 的 excludes= 不是字面量列表——本判据依赖它；"
                "若确实要改成变量引用，请同步本函数。"
            )
            return [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    raise AssertionError("medkit.spec 缺少 excludes= 参数")


def _spec_datas_local_paths() -> list[str]:
    """`datas` 里声明的**本地源路径**（字面量元组的第一个元素）。

    只取 `datas = [...]` 的字面量部分；`datas += collect_data_files(...)` /
    `datas += _dist_info_datas()` 这类动态追加不含本地路径，不计入。
    """
    out: list[str] = []
    for node in _spec_tree().body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "datas" for t in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for el in node.value.elts:
            if (isinstance(el, ast.Tuple) and el.elts
                    and isinstance(el.elts[0], ast.Constant)):
                out.append(el.elts[0].value)
    return out


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


def test_spec_excludes_survive_bracket_entries():
    """回归锁：excludes 里出现含方括号的条目时，解析**不得截断**。

    旧正则 `.*?` 会在第一个 `]` 处停止：`"foo[bar]"` 在列表首位时解析得 **0 条**。
    由于现有断言写成 `assert heavy in ex`，后果是**守卫误红**（不是假绿）——
    但无故变红会逼人删守卫或删条目，同样是防线失守。改用 AST 后不会发生。
    """
    sample = 'Analysis([], excludes=[\n    "tkinter", "foo[bar]", "pytest",\n])\n'
    saved = _spec_tree
    try:
        globals()["_spec_tree"] = lambda: ast.parse(sample)
        got = _spec_excludes()
    finally:
        globals()["_spec_tree"] = saved
    assert got == ["tkinter", "foo[bar]", "pytest"], (
        f"excludes 解析被截断：{got}（正则版会只得到 ['tkinter']）"
    )


# ---- R11：spec 的 data 收集必须**双向**与代码现状一致 ----
#
# ## 为什么需要（2026-09-29 反向验证实测）
#
# spec 的 `datas` 是**手工白名单**。原守卫只有文本子串断言，实测 4 组注入恒绿：
#   · 删掉 `("medkit/web", "medkit/web")`     → 绿（零 CDN 前端的全部数据没了）
#   · 删掉 `("medkit/prompts", "medkit/prompts")` → 绿（提示词模板没了）
#   · 把 `("LICENSE", ".")` 移走但在注释里留 "LICENSE" 字样 → 绿（子串匹配被绕过）
# 而 `test_spec_ships_dist_info_for_license_obligation`（有**整行**断言）则能正确变红——
# 差别就在「子串 vs 整行/AST」，这正是 R9 记的「文本判据脆」的同一类。
#
# 判据模型（双向）：
#   1. spec 声明的每个本地路径必须**存在** → 防「文档写了代码没有」；
#   2. `medkit/` 下每个含非 `.py` 文件的**顶层目录**，必须被 datas 覆盖，
#      或登记在 `_DATAS_INTENTIONALLY_EXCLUDED`（带理由）→ 防「新增目录忘同步」。

# 有意不打进产物的数据目录：键 = 相对仓库根的目录，值 = 为什么有意排除。
# （spec 里注释同样写明了第 1 条的理由；此处是它的**机械可检**版本。）
_DATAS_INTENTIONALLY_EXCLUDED: dict[str, str] = {
    "medkit/data": (
        "WP-12：示例素材与大纲种子仅保留在仓库供开发/CI 使用，不随安装包分发——"
        "用户自行上传教材/教师重点/官方大纲。spec 第 31-32 行有同口径注释。"
    ),
}

# 不构成「数据目录」的后缀白名单（编译产物 / 缓存，不该进 datas）
_NON_DATA_SUFFIXES = (".pyc", ".pyo")


def _top_level_data_dirs() -> dict[str, int]:
    """`medkit/` 下**直接子目录**里，含非 `.py` 文件者的 {目录: 文件数}。

    只看**顶层子目录**：`web/css`、`web/js` 由 `("medkit/web", "medkit/web")`
    递归覆盖，单独要求它们出现在 datas 里是错的。
    纯 Python 包目录（agents/core/gates/render/routers）不含非 `.py` 文件，天然不进结果。
    """
    out: dict[str, int] = {}
    for d in sorted((ROOT / "medkit").iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        n = sum(
            1 for p in d.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and not p.name.endswith(_NON_DATA_SUFFIXES)
            and p.suffix != ".py"
        )
        if n:
            out[f"medkit/{d.name}"] = n
    return out


def test_spec_datas_declared_paths_exist():
    """spec 声明的每个本地路径都必须真实存在（防「spec 指向不存在的目录」）。"""
    missing = [p for p in _spec_datas_local_paths() if not (ROOT / p).exists()]
    assert not missing, (
        "medkit.spec 的 datas 声明了不存在的路径——打包时会直接报错或静默缺文件：\n  "
        + "\n  ".join(missing)
    )


def test_spec_datas_covers_every_data_dir():
    """反向：`medkit/` 下每个数据目录都必须被 datas 覆盖或**登记为有意排除**。

    这条防的是 MEMORY.md 记的那类坑（新增数据目录忘同步 spec，测试全绿但产物缺文件）——
    jieba 的 `dict.txt` 就是这么踩过一次。缺 `medkit/web` 会让零 CDN 前端白屏；
    缺 `medkit/prompts` 会让所有 LLM 功能在打包版里直接失败。
    """
    declared = set(_spec_datas_local_paths())
    uncovered = []
    for d, n in _top_level_data_dirs().items():
        if d in declared or d in _DATAS_INTENTIONALLY_EXCLUDED:
            continue
        uncovered.append(f"{d}（{n} 个数据文件）")
    assert not uncovered, (
        "以下数据目录既不在 medkit.spec 的 datas 里，也未登记为「有意排除」：\n  "
        + "\n  ".join(uncovered)
        + "\n含义：打包版会缺这些文件，而测试全绿（源码目录能直接读到）。"
        "请加进 datas，或在 _DATAS_INTENTIONALLY_EXCLUDED 登记并写明理由。"
    )


def test_datas_exclusion_entries_are_still_needed():
    """`_DATAS_INTENTIONALLY_EXCLUDED` 不得堆陈尸：每条必须仍是**未进 datas**的真目录。

    否则某人把目录加进 datas 后豁免还留着，下次有人删掉 datas 行也不报警——
    与 `test_no_sleep_gambling.test_allowlist_entries_are_still_needed` 同一判据。
    """
    declared = set(_spec_datas_local_paths())
    live = set(_top_level_data_dirs())
    stale = [d for d in _DATAS_INTENTIONALLY_EXCLUDED if d in declared or d not in live]
    assert not stale, (
        "以下「有意排除」条目已失效（目录已进 datas，或目录已不存在）——"
        "请从 _DATAS_INTENTIONALLY_EXCLUDED 删除：\n  " + "\n  ".join(stale)
    )


def test_spec_ships_license_in_datas_structurally():
    """AGPL：`("LICENSE", ".")` 必须以**结构化形式**出现在 `datas` 字面量里。

    ## 取代原 `test_spec_has_license_in_datas`（2026-09-29 R11）

    原实现是 `assert '"LICENSE"' in src` ——**全文子串**匹配，实测可绕过：
    把 `("LICENSE", ".")` 从 datas 删掉，只在注释里留一句 `# "LICENSE"`，
    断言**照样通过**（`.workbuddy-ai/tmp/probe_spec_guards.py` 注入 1 复现）。

    这不是「理论上可能」：AGPL 要求「随分发提供许可证」，缺了是**合规问题**，
    而一个能被子串绕过的断言不构成任何保证。
    同文件 `test_spec_ships_dist_info_for_license_obligation` 早有正确做法
    （整行匹配，注释里写明「反向验证实测：注释掉该行时子串断言仍通过」）——
    本条改用 AST，连「换行/空格/引号风格」的形态差异也一并免疫。
    """
    datas_pairs = []
    for node in _spec_tree().body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "datas" for t in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for el in node.value.elts:
            if isinstance(el, ast.Tuple) and len(el.elts) == 2:
                vals = [e.value for e in el.elts if isinstance(e, ast.Constant)]
                if len(vals) == 2:
                    datas_pairs.append(tuple(vals))

    assert ("LICENSE", ".") in datas_pairs, (
        f"AGPL 要求许可证正文随安装包分发，但 datas 里找不到 ('LICENSE', '.')。"
        f"当前 datas 字面量条目：{datas_pairs}"
    )


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

    # ---- 闭包限定必须是「真的按 lock 算出来的」，不是「全文出现过 lock 一词」----
    # 2026-09-29 R14：旧判据只有 `"def _lock_closure_names(" in src` + `"requirements.lock" in src`
    # 两条文本子串。实测**假绿**两种：
    #   ① `_P("requirements.lock")` 换成 `_P("no-such-file.lock")` —— 文件名字符串仍在别处出现；
    #   ② `want = _lock_closure_names()` 改成 `want = set()` —— 函数定义还在，返回值却被废
    #      （= 闭包为空，构建期依赖全混进产物，正是本条要防的）。
    tree = _spec_tree()
    closure_fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef)
         and n.name == "_lock_closure_names"), None)
    assert closure_fn is not None, "未找到 _lock_closure_names 定义"

    # ① 函数体里必须**以字面量**指向 requirements.lock（不是变量/拼接/别的 .lock）
    # ⚠️ 首版写成 `n.value.endswith(".lock")` ⇒ `no-such-file.lock` 也通过（本轮实测的假绿），
    #    判据比意图宽了一档。必须钉死文件名本身。
    lock_literals = [n.value for n in ast.walk(closure_fn)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "requirements.lock" in lock_literals, (
        "_lock_closure_names 没有以**字面量**引用 requirements.lock"
        "（当前含 .lock 的字面量：%r）——"
        "闭包范围可能已经不按 lock 限定（会混入构建期依赖）"
        % [v for v in lock_literals if "lock" in v.lower()])
    # ② 函数必须真返回值（不是被改成恒空集）
    returns = [n for n in ast.walk(closure_fn) if isinstance(n, ast.Return)]
    assert returns, "_lock_closure_names 没有 return —— 闭包恒为空"
    assert any(not (isinstance(r.value, ast.Call) and isinstance(r.value.func, ast.Name)
                        and r.value.func.id in {"set", "frozenset"} and not r.value.args)
               for r in returns), (
        "_lock_closure_names 直接返回空集合——范围限定失效，构建期依赖会进产物")
    # ③ 调用点必须把结果真的用上（防 `want = set()` 这类掏空）
    want_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "want" for t in n.targets)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "_lock_closure_names"
    ]
    assert want_calls, (
        "没有 `want = _lock_closure_names()` 形态的赋值——"
        "范围限定算出来了却没接进过滤逻辑")


def test_spec_lock_closure_guard_is_not_vacuous():
    """元守卫：证明闭包判据抓得住「文件名换掉」与「返回值掏空」。

    ## 首版这里也漏了一次（2026-09-29 R14 自查）
    判据 ① 最初写的是 `… and n.value.endswith(".lock")`，
    于是 `_P("no-such-file.lock")` 照样通过 —— **判据比意图宽了一档**，
    注入实测「仍绿」。现判据钉死 `== "requirements.lock"`。本条证伪用例同时覆盖
    「换成别的 .lock」与「换成非 .lock」两种，防止再次放宽。
    """
    src = (ROOT / "medkit.spec").read_text(encoding="utf-8")

    def _lock_literals(text: str) -> list[str]:
        fn = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)
                  and n.name == "_lock_closure_names")
        return [n.value for n in ast.walk(fn)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    # 真身绿
    assert "requirements.lock" in _lock_literals(src)

    # 证伪 ①：换成**别的 .lock**（首版宽判据正是在这里漏的）
    alt_lock = src.replace('_P("requirements.lock")', '_P("no-such-file.lock")', 1)
    assert alt_lock != src, "证伪 ① 注入未生效"
    assert "requirements.lock" not in _lock_literals(alt_lock), (
        "把锁文件名换成别的 .lock 后仍命中 —— 判据绑的是全文/后缀而不是文件名")

    # 证伪 ②：换成非 .lock 的文件
    alt_txt = src.replace('_P("requirements.lock")', '_P("requirements.txt")', 1)
    assert alt_txt != src, "证伪 ② 注入未生效"
    assert "requirements.lock" not in _lock_literals(alt_txt)

    # 证伪 ③：把 want 直接赋值成空集（函数还在，但范围限定被掏空）
    emptied = src.replace("want = _lock_closure_names()", "want = set()", 1)
    assert emptied != src, "证伪 ③ 注入未生效"
    t3 = ast.parse(emptied)
    assert not [n for n in ast.walk(t3)
                if isinstance(n, ast.Assign)
                and any(isinstance(x, ast.Name) and x.id == "want" for x in n.targets)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "_lock_closure_names"], (
        "want 被掏空成 set() 后判据仍绿 —— 这条守卫是假绿")


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
