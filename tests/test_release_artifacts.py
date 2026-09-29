"""B16 回归（R8+W）：发布产物生成 —— 绿色版 zip + SHA256 清单。

背景：`pack/build.bat` 原先只出 `dist/MedKit` 与安装包，**绿色版 zip 与校验清单没有脚本**
（2026-09-20 出 0.10.4 时这两步是手工做的）→ 审查项 M5-07「无可复现 CI 构建/签名/sha256 清单」
长期挂着。本文件锁住 `pack/make_release.py` 的行为与接线。
"""

import ast
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit  # noqa: E402


def _load_pack_module():
    """按路径加载 pack/make_release.py（pack/ 不是包，不能直接 import）。"""
    path = ROOT / "pack" / "make_release.py"
    spec = importlib.util.spec_from_file_location("make_release", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pack_mod():
    return _load_pack_module()


# ---------------------------------------------------------------- 版本单源

def test_version_reads_single_source(pack_mod):
    """版本必须取自 `medkit/__init__.py` 单源，不得另设常量。"""
    assert pack_mod._version() == medkit.__version__


def test_version_not_hardcoded(pack_mod):
    """源码级：不得把版本号写死在脚本里。

    ## 判据是 AST 而非文本子串（2026-09-29 R14 改）

    旧版只有两条**文本子串**判据：

        assert f'"{medkit.__version__}"' not in src   # ← 只盯当前这一个版本串
        assert "__version__" in src                   # ← 只要有这个词就算过

    实测两种绕过（都是**语义等价改写**，不是改名）：

    ① 写死**别的**版本号：`V = "1.0.0"` 照样绿（它只查当前值 `0.10.5`）；
    ② 把单源读法拆成拼接：`getattr(medkit, "__" + "version" + "__")` 仍绿。

    真身结构是 `re.search(r'__version__\\s*=\\s*["\\']([^"\\']+)["\\']', src)`——
    **用正则从 `medkit/__init__.py` 抠出版本**。所以正确的判据是：
    函数体里存在对 `medkit/__init__.py` 的读取 + 对 `__version__` 的**正则匹配**，
    而不是「全文出现过某个版本串」。
    """
    src = (ROOT / "pack" / "make_release.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 1) 不得在**任何**字符串字面量里写死一个形如 x.y.z 的版本号
    version_literals = sorted({
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and _VERSION_RE.fullmatch(n.value.strip())
    })
    assert not version_literals, (
        "make_release.py 里写死了版本号字面量 %r —— 应始终从 medkit/__init__.py 读单源"
        % version_literals
    )

    # 2) 单源读法必须**结构在位**：读 __init__.py + 用正则抠 __version__
    reader = _reader_func(tree)
    assert reader is not None, "未找到读取版本号的函数（真身是 _version）"
    body_src = ast.get_source_segment(src, reader) or ""
    assert re.search(r"__version__", body_src), "读取函数里没有 __version__ 字段名"
    assert re.search(r"\bre\.(search|match|findall)\b", body_src), (
        "读取函数没有用 re.* 解析 __init__.py —— 单源读法被拆散了"
    )
    assert "__init__" in body_src, "读取函数没有指向 medkit/__init__.py"


_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)*(?:[-+][\w.]+)?")


def _reader_func(tree: ast.Module) -> ast.FunctionDef | None:
    """找「读版本号」的函数：函数体里同时出现 __version__ 与 __init__。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        text = ast.dump(node)
        if "__version__" in text and "__init__" in text:
            return node
    return None


def test_version_guard_is_not_vacuous():
    """元守卫：证明上面两条判据抓得住「写死版本」与「拆散单源读法」。"""
    src = (ROOT / "pack" / "make_release.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 真身必须绿
    assert not [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and _VERSION_RE.fullmatch(n.value.strip())]
    assert _reader_func(tree) is not None

    # 证伪 1：写死一个**别的**版本号 —— 旧判据（只查当前值）会放过
    injected = src.replace("def _version()", 'V = "9.9.9"\n\n\ndef _version()', 1)
    t2 = ast.parse(injected)
    assert [n.value for n in ast.walk(t2)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _VERSION_RE.fullmatch(n.value.strip())] == ["9.9.9"], (
        "写死版本号未被判据 1 命中 —— 这条守卫是假绿")

    # 证伪 2：把单源读法拆散（删掉正则那一步，只留文件读取）
    lines = injected.splitlines(keepends=True)
    keeper = [ln for ln in lines if "m = re.search(" not in ln]
    assert len(keeper) == len(lines) - 1, "证伪 2 注入未生效（正则抠版本那一行没找到）"
    stripped = "".join(keeper).replace("    if not m:", "    m = None\n    if not m:", 1)
    t3 = ast.parse(stripped)
    rd = _reader_func(t3)
    assert rd is not None
    seg3 = ast.get_source_segment(stripped, rd) or ""
    assert not re.search(r"\bre\.(search|match|findall)\b", seg3), (
        "拆散单源读法后判据 2 仍绿 —— 这条守卫是假绿")


# ---------------------------------------------------------------- SHA256 清单

def test_checksums_format_and_content(pack_mod, tmp_path, monkeypatch):
    """清单必须是 sha256sum 兼容格式（两个空格分隔），且哈希与实际文件一致。"""
    v = medkit.__version__
    monkeypatch.setattr(pack_mod, "OUT_DIR", tmp_path)
    (tmp_path / f"MedKit-Setup-{v}.exe").write_bytes(b"fake-installer")
    (tmp_path / f"MedKit-{v}-portable.zip").write_bytes(b"fake-portable")
    out = pack_mod._checksums(v)
    lines = [ln for ln in out.read_text(encoding="ascii").splitlines() if ln.strip()]
    assert len(lines) == 2, lines
    names = set()
    for ln in lines:
        parts = ln.split("  ")          # sha256sum 格式：hash + 两个空格 + 文件名
        assert len(parts) == 2, f"格式不对：{ln!r}"
        h, name = parts
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h), h
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == h, name
        names.add(name)
    assert names == {f"MedKit-Setup-{v}.exe", f"MedKit-{v}-portable.zip"}


def test_checksums_ignores_other_versions(pack_mod, tmp_path, monkeypatch):
    """只对**当前版本**负责——历史版本产物留在目录里但不进清单。"""
    v = medkit.__version__
    monkeypatch.setattr(pack_mod, "OUT_DIR", tmp_path)
    (tmp_path / f"MedKit-Setup-{v}.exe").write_bytes(b"current")
    (tmp_path / "MedKit-Setup-0.0.1.exe").write_bytes(b"ancient")
    out = pack_mod._checksums(v)
    text = out.read_text(encoding="ascii")
    assert "0.0.1" not in text, "历史版本不该进清单"
    assert f"MedKit-Setup-{v}.exe" in text


def test_checksums_tolerates_missing_installer(pack_mod, tmp_path, monkeypatch):
    """未装 Inno Setup 时只有 zip——应照常出清单并提示，而不是失败。"""
    v = medkit.__version__
    monkeypatch.setattr(pack_mod, "OUT_DIR", tmp_path)
    (tmp_path / f"MedKit-{v}-portable.zip").write_bytes(b"only-portable")
    out = pack_mod._checksums(v)
    text = out.read_text(encoding="ascii")
    assert f"MedKit-{v}-portable.zip" in text
    assert f"MedKit-Setup-{v}.exe" not in text


def test_checksums_fails_when_nothing_present(pack_mod, tmp_path, monkeypatch):
    """一个产物都没有 → 必须失败（不能生成空清单冒充成功）。"""
    monkeypatch.setattr(pack_mod, "OUT_DIR", tmp_path)
    with pytest.raises(SystemExit):
        pack_mod._checksums(medkit.__version__)


# ---------------------------------------------------------------- 接线（关键）

def test_build_bat_wires_make_release():
    """接线级：`build.bat` 必须真的调用 make_release.py。

    教训（B10/B11）：只测脚本本体 ≠ 测了防线——脚本没被接线就是死代码，
    下一个出包的人照样手工打 zip。
    """
    src = (ROOT / "pack" / "build.bat").read_text(encoding="utf-8")
    # ⚠️ 只认「调用形态」（非 rem/echo 且含脚本名），且**不写死解释器前缀**：
    # ① 子串匹配会被 `rem python pack\make_release.py` 这类注释掉的调用骗过（实测假绿）；
    # ② R8+W 起统一走 `"%PY%"`（支持 MEDKIT_BUILD_PYTHON 指定干净环境），
    #    写死 `startswith("python ")` 会让改前缀时守卫误报——本会话真被误报过一次。
    lines = [ln.strip().lstrip("@").strip() for ln in src.splitlines()]
    calls = [ln for ln in lines
             if "make_release.py" in ln and not ln.startswith(("rem", "echo"))]
    assert calls, "build.bat 未真正调用发布产物脚本（或被注释掉）→ 等于没做"
    # 且失败要中断（不能静默跳过）
    seg = src[src.index(calls[0]):]
    assert "errorlevel 1" in seg[:400], "调用后未检查退出码"


def _zip_arcname_expr(src: str):
    """在 `_zip_portable` 里找到 `z.write(<src>, <arcname>)` 的 **arcname 表达式节点**。

    只认结构、不认写法：`f"MedKit/{...}"` / `"MedKit/" + ...` / `arcname=` 关键字
    参数都指向同一件事——「每个文件都装进 `MedKit/` 顶层目录」。旧版断言把
    f-string 的**字面拼法**写死，改写法即假红（见本函数下方反向验证）。
    """
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_zip_portable"), None)
    assert fn is not None, "pack/make_release.py 里没有 _zip_portable 函数"
    write_calls = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "write"]
    assert write_calls, "_zip_portable 里没有 `z.write(...)` 调用 —— zip 是空的"
    call = write_calls[0]
    # arcname 允许位置第 2 参或关键字 arcname=
    kw = next((k for k in call.keywords if k.arg == "arcname"), None)
    if kw is not None:
        return kw.value
    assert len(call.args) >= 2, (
        "`z.write` 没有传 arcname —— 文件会装到 zip 根目录，"
        "用户解压得到一堆散文件而不是 `MedKit/` 文件夹")
    return call.args[1]


def _iter_str_pieces(node):
    """把表达式里所有**字面量字符串**摊平（含 f-string 的常量片段）。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


def test_zip_layout_matches_existing_release(pack_mod):
    """绿色版 zip 顶层必须是 `MedKit/`（与历史发布物结构一致，用户解压即得文件夹）。"""
    src = (ROOT / "pack" / "make_release.py").read_text(encoding="utf-8")
    arc = _zip_arcname_expr(src)
    pieces = list(_iter_str_pieces(arc))
    assert any(p == "MedKit/" or p.startswith("MedKit/") for p in pieces), (
        f"zip 内条目未加 `MedKit/` 顶层前缀（arcname 字面量片段：{pieces}）——"
        "解压后不是文件夹，与历史发布物结构不符")


def test_zip_layout_guard_is_not_vacuous():
    """反向验证：① 结构等价改写（拼接/改循环变量名）**不能**再假红；② 去掉前缀要红。

    元守卫自带内存样本，不读真身——真身是注入靶子。
    """
    _HDR = ("def _zip_portable(ver):\n"
            "    with zipfile.ZipFile(t, 'w') as z:\n"
            "        for f in sorted(DIST.rglob('*')):\n"
            "            if f.is_file():\n")
    good_variants = {
        "f-string": _HDR + '                z.write(f, f"MedKit/{f.relative_to(DIST).as_posix()}")\n',
        "concat": _HDR + '                z.write(f, "MedKit/" + f.relative_to(DIST).as_posix())\n',
        "arcname-kw": _HDR + '                z.write(f, arcname=f"MedKit/{f.relative_to(DIST).as_posix()}")\n',
        "renamed-var": _HDR + '                z.write(p, f"MedKit/{p.relative_to(DIST).as_posix()}")\n',
    }
    for name, code in good_variants.items():
        arc = _zip_arcname_expr(code)
        pieces = list(_iter_str_pieces(arc))
        assert any(p == "MedKit/" or p.startswith("MedKit/") for p in pieces), \
            f"[元守卫] 等价改写 `{name}` 被误判为没加前缀 → 守卫假红"

    bad = _HDR + '                z.write(f, f"{f.relative_to(DIST).as_posix()}")\n'
    with pytest.raises(AssertionError):
        arc = _zip_arcname_expr(bad)
        pieces = list(_iter_str_pieces(arc))
        assert any(p == "MedKit/" or p.startswith("MedKit/") for p in pieces), "缺前缀未被发现"

    no_arc = _HDR + "                z.write(f)\n"
    with pytest.raises(AssertionError):
        _zip_arcname_expr(no_arc)


# ------------------------------------------------- 发布五件套一致性（2026-09-28 事故）

def _load_consistency_module():
    """按路径加载 pack/check-release-consistency.py。"""
    path = ROOT / "pack" / "check-release-consistency.py"
    spec = importlib.util.spec_from_file_location("check_release_consistency", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def consist_mod():
    return _load_consistency_module()


def test_version_single_source_agrees(consist_mod):
    """五件套第 1/2 项：`__init__.__version__` 与 `pack/version.iss` 必须一致。

    事故现场（2026-09-28）：这两处早就指向 0.10.5，但产物是旧内容构建的——
    版本号一致**不代表**产物对，故此处只锁「版本号内部一致」这一半。
    """
    v_init = consist_mod.v_from_init()
    assert v_init, "无法从 medkit/__init__.py 读取 __version__"
    assert consist_mod.v_from_iss() == v_init, "pack/version.iss 与 __init__ 版本号不一致"


def test_changelog_has_released_section(consist_mod):
    """五件套第 3 项：当前版本必须在 CHANGELOG 有 `## [<版本>]` 小节。

    为什么锁这条：0.10.5 曾长期「代码/README 已 bump、CHANGELOG 仍挂 [Unreleased]」，
    发布时容易漏掉收口。
    """
    v = consist_mod.v_from_init()
    assert consist_mod.changelog_has_section(v), f"CHANGELOG 缺 `## [{v}]` 小节（未收口）"


def test_readme_references_current_installer(consist_mod):
    """五件套第 4 项：README 安装步骤必须引用当前版本安装包名。"""
    v = consist_mod.v_from_init()
    assert consist_mod.readme_has_installer(v), f"README 未引用 MedKit-Setup-{v}.exe"


def test_installer_payload_contains_sources(consist_mod):
    """五件套第 5 项（事故核心）：产物内 prompts/web 必须与源码逐字节一致。

    这是本次事故的**直接守卫**——「文件名对、内容旧」必须被判红。

    ## 为什么不是裸 `pytest.skip`（2026-09-29 修）

    此前写「dist 不存在时跳过（CI/未构建环境）」。问题是：**"产物不存在"
    恰恰是本守卫要防的情形之一**。若某次本地构建失败、`dist/` 被清掉，
    裸 skip 会让守卫变绿——用户以为"五件套一致"，实际连产物都没有。

    改为**按前提分流**：
    - CI（`CI=true`）：产物本就不在仓库里，skip 正当（与 `test_s2_refactor`
      的 `_SKIP_IF_CI` 同一口径）；
    - 本地：产物缺失即**红**，因为本地出现"声称发布但没产物"就是事故前兆。

    判据从"不存在就放过"改成"**只在 CI 里放过**"——这才是拦在了对的地方。
    """
    v = consist_mod.v_from_init()
    has_artifact = (consist_mod.installer_exists(v) is not None
                    or (ROOT / "dist" / "MedKit").exists())
    if not has_artifact:
        is_ci = __import__("os").environ.get("CI") == "true"
        if is_ci:
            pytest.skip("CI 环境不携带发布产物（本地构建才会生成）")
        raise AssertionError(
            f"本地环境找不到发布产物（无 dist-installer/MedKit-Setup-{v}.exe，"
            f"也无 dist/MedKit）。这不是环境问题——本地跑发布守卫时产物必须在场，"
            f"否则「五件套一致」无从谈起。请先跑 pack/build.bat，"
            f"或在 CI 环境（CI=true）下运行。"
        )
    ok, detail = consist_mod.installer_contains_sources(v)
    assert ok, f"产物内容与源码不一致：{detail}"


def test_consistency_check_is_falsifiable(consist_mod, tmp_path):
    """反向验证：篡改产物内容后，检查必须报红。

    守卫必须能证伪（项目铁律）。这里在临时目录构造「源码 vs 产物」内容不同的场景，
    直接调用比对函数，确认它**真的会**返回 False——而不是永远返回 True 的假绿。
    """
    src_dir = tmp_path / "medkit" / "prompts"
    payload = tmp_path / "dist" / "MedKit" / "_internal" / "medkit"
    (payload / "prompts").mkdir(parents=True)
    src_dir.mkdir(parents=True)
    (src_dir / "sample.md").write_text("SOURCE-A\n", encoding="utf-8")
    (payload / "prompts" / "sample.md").write_text("SOURCE-A\n", encoding="utf-8")

    # 把 ROOT 指向临时目录，验证「一致」与「不一致」两种结果
    orig = consist_mod.ROOT
    try:
        consist_mod.ROOT = tmp_path
        ok, _detail = consist_mod.installer_contains_sources("0.0.0")
        assert ok, "内容相同时应判通过"

        (payload / "prompts" / "sample.md").write_text("SOURCE-B\n", encoding="utf-8")
        ok2, detail2 = consist_mod.installer_contains_sources("0.0.0")
        assert not ok2, "内容不同时必须判红（否则守卫是假绿）"
        assert "内容不同" in detail2
    finally:
        consist_mod.ROOT = orig


def test_build_bat_wires_consistency_check():
    """接线级：`build.bat` 必须在出包后真正调用五件套检查，且失败即中断。

    与 `test_build_bat_wires_make_release` 同一教训：脚本没被接线 = 死代码。
    这里额外要求 `--strict`——缺省的「警告级」不足以拦住发布。
    """
    src = (ROOT / "pack" / "build.bat").read_text(encoding="utf-8")
    lines = [ln.strip().lstrip("@").strip() for ln in src.splitlines()]
    calls = [ln for ln in lines
             if "check-release-consistency.py" in ln
             and not ln.startswith(("rem", "echo"))]
    assert calls, "build.bat 未真正调用五件套检查（或被注释掉）→ 等于没做"
    assert any("--strict" in c for c in calls), "五件套检查未使用 --strict，拦不住发布"
    seg = src[src.index(calls[0]):]
    assert "errorlevel 1" in seg[:400], "调用后未检查退出码"
