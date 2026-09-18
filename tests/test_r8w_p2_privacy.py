"""B11 回归（R8+W）：P2 最后两项——数据目录权限加固 / 备份恢复的用户可见路径。

- **S3-13**：`harden_config_dir()` —— POSIX 收紧到 0700；Windows **只检查不改**（避免误删继承项
  把用户锁在目录外），过宽时返回可操作告警。
- **S3-20**：应用内**不提供**自动恢复（原结论：运行中覆盖 SQLite 有 WAL/连接缓存一致性问题，
  需重启令牌 + 产品定案）。本批只做**安全收口**：备份回执补 `restore_hint`（手动步骤），
  并把 `downgrade_to` 为何零调用写进 docstring（不是死代码，是有意不接）。
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402

# ---------------------------------------------------------------- S3-13

def test_harden_config_dir_creates_dir(tmp_path):
    d = tmp_path / "medkit_data"
    assert not d.exists()
    cfgmod.harden_config_dir(d)
    assert d.is_dir(), "应顺带创建数据目录"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows 的 os.chmod 不支持 POSIX 权限位（只支持只读位），无法在本机验证")
def test_harden_config_dir_posix_tightens(tmp_path, monkeypatch):
    """POSIX 分支：权限过宽必须被收紧到 0700。"""
    import os as _os

    d = tmp_path / "d"
    d.mkdir()
    _os.chmod(d, 0o755)
    monkeypatch.setattr(cfgmod.os, "name", "posix")
    cfgmod.harden_config_dir(d)
    assert (d.stat().st_mode & 0o777) == 0o700, "权限未被收紧"


def test_harden_config_dir_windows_never_modifies(tmp_path, monkeypatch):
    """Windows 分支：只读检查——绝不执行会改 ACL 的 icacls 调用。"""
    calls: list[list[str]] = []

    class _R:
        stdout = b"BUILTIN\\Users:(OI)(CI)(RX)\r\n"

    def _fake_run(argv, **kw):
        calls.append(list(argv))
        return _R()

    monkeypatch.setattr(cfgmod.os, "name", "nt")
    monkeypatch.setattr("subprocess.run", _fake_run)
    warn = cfgmod.harden_config_dir(tmp_path / "d")
    assert calls and calls[0][0] == "icacls", "应调用 icacls 做只读检查"
    # 任何写 ACL 的开关都不得出现
    joined = " ".join(calls[0])
    for flag in ("/grant", "/inheritance", "/remove", "/deny", "/setowner"):
        assert flag not in joined, f"Windows 分支不得修改 ACL（出现 {flag}）"
    assert warn and "权限较宽" in warn, "过宽时应给出可操作告警"


def test_harden_config_dir_source_has_no_acl_write():
    """源码级守卫：本模块不得出现写 ACL 的 icacls 参数（只检查）。"""
    src = (ROOT / "medkit" / "core" / "config.py").read_text(encoding="utf-8")
    seg = src[src.index("def harden_config_dir"):]
    seg = seg[:seg.index("\ndef ", 1)]
    assert "icacls" in seg
    # ⚠️ 只扫 **subprocess.run(...) 的实参**——告警文案里会给出「建议用户手动执行」的命令串
    # （含 /grant:r），那是提示文本不是调用；扫整段函数体会误报。
    run_calls = [ln for ln in seg.splitlines() if "subprocess.run(" in ln]
    assert run_calls, "未找到 icacls 调用点"
    for ln in run_calls:
        for flag in ("/grant", "/inheritance", "/remove", "/deny"):
            assert flag not in ln, f"icacls 调用里出现写 ACL 的参数 {flag}"


def test_config_dir_hardening_is_wired():
    """S3-13 接线级：`harden_config_dir()` 必须在启动流程里被调用（否则是死代码）。

    教训（B10）：只测函数本体 ≠ 测了防线——注入「摘掉调用点」时用例必须变红。
    """
    src = (ROOT / "medkit" / "main.py").read_text(encoding="utf-8")
    assert "harden_config_dir()" in src, "启动流程未调用目录加固 → 死代码"
    assert "dir_permission_warning" in (ROOT / "medkit" / "core" / "config.py").read_text(
        encoding="utf-8"), "缺少告警读取口"


def test_harden_config_dir_records_warning(tmp_path, monkeypatch):
    """S3-13：过宽告警要落到模块级，供 UI/诊断读取（不只是返回值）。"""
    class _R:
        stdout = b"BUILTIN\\Users:(OI)(CI)(RX)\r\n"

    monkeypatch.setattr(cfgmod.os, "name", "nt")
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: _R())
    monkeypatch.setattr(cfgmod, "_DIR_WARN", None)
    cfgmod.harden_config_dir(tmp_path / "d")
    assert cfgmod.dir_permission_warning() and "权限较宽" in cfgmod.dir_permission_warning()


# ---------------------------------------------------------------- S3-20

@pytest.fixture
def iso_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


def test_backup_response_carries_restore_hint(iso_cfg):
    """S3-20：备份回执必须告诉用户**怎么恢复**（原只有「已备份到 X」）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    r = TestClient(app, base_url="http://127.0.0.1").post("/api/data/backup", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    hint = body.get("restore_hint") or ""
    assert "退出" in hint and "解压" in hint and "覆盖" in hint, f"恢复指引不完整：{hint}"
    assert "不可逆" in hint or "另存" in hint, "缺少风险提示"


def test_no_automatic_restore_endpoint(iso_cfg):
    """S3-20：自动恢复端点**有意不提供**——守卫此结论不被悄悄改动。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.post("/api/data/restore", json={"file": "x.zip"})
    assert r.status_code == 404, "出现了 restore 端点——若确要做，须先落实重启令牌与一致性方案"


def test_restore_deferral_is_documented():
    """S3-20：推迟结论与前置条件必须留档（否则下一个人会以为是漏做）。"""
    doc = (ROOT / "medkit" / "routers" / "data.py").read_text(encoding="utf-8")
    head = doc[:doc.index('"""', doc.index('"""') + 3)]
    assert "WAL" in head, "模块 docstring 未说明推迟原因"
    assert "重启令牌" in head, "未写明自动化前置条件"


def test_downgrade_to_documents_why_unwired():
    """S3-20：`downgrade_to` 零调用是有意的，须在 docstring 说明。"""
    src = (ROOT / "medkit" / "core" / "db.py").read_text(encoding="utf-8")
    seg = src[src.index("def downgrade_to"):]
    seg = seg[:seg.index("\ndef ", 1)]
    assert re.search(r"有意不接入|不是.*死代码|人工排障", seg), "未说明为何零调用"
