"""B16 回归（R8+W）：发布产物生成 —— 绿色版 zip + SHA256 清单。

背景：`pack/build.bat` 原先只出 `dist/MedKit` 与安装包，**绿色版 zip 与校验清单没有脚本**
（2026-09-20 出 0.10.4 时这两步是手工做的）→ 审查项 M5-07「无可复现 CI 构建/签名/sha256 清单」
长期挂着。本文件锁住 `pack/make_release.py` 的行为与接线。
"""

import hashlib
import importlib.util
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
    """源码级：不得把版本号写死在脚本里。"""
    src = (ROOT / "pack" / "make_release.py").read_text(encoding="utf-8")
    assert f'"{medkit.__version__}"' not in src, "版本号被写死——应始终读单源"
    assert "__version__" in src


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
    # ⚠️ 整行匹配：子串匹配会被 `rem python pack\make_release.py` 这类**注释掉**的调用骗过
    # （反向验证实测：注释掉该行时子串断言仍然通过 = 假绿）。
    lines = [ln.strip().lstrip("@").strip() for ln in src.splitlines()]
    calls = [ln for ln in lines if ln.startswith("python pack\\make_release.py")]
    assert calls, "build.bat 未真正调用发布产物脚本（或被注释掉）→ 等于没做"
    # 且失败要中断（不能静默跳过）
    seg = src[src.index(calls[0]):]
    assert "errorlevel 1" in seg[:400], "调用后未检查退出码"


def test_zip_layout_matches_existing_release(pack_mod):
    """绿色版 zip 顶层必须是 `MedKit/`（与历史发布物结构一致，用户解压即得文件夹）。"""
    src = (ROOT / "pack" / "make_release.py").read_text(encoding="utf-8")
    assert 'f"MedKit/{f.relative_to(DIST).as_posix()}"' in src
