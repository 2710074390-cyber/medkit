"""B11 回归（R8+W）：P2 最后两项——数据目录权限加固 / 备份恢复的用户可见路径。

- **S3-13**：`harden_config_dir()` —— POSIX 收紧到 0700；Windows **只检查不改**（避免误删继承项
  把用户锁在目录外），过宽时返回可操作告警。
- **S3-20**：应用内**不提供**自动恢复（原结论：运行中覆盖 SQLite 有 WAL/连接缓存一致性问题，
  需重启令牌 + 产品定案）。本批只做**安全收口**：备份回执补 `restore_hint`（手动步骤），
  并把 `downgrade_to` 为何零调用写进 docstring（不是死代码，是有意不接）。
"""

import ast
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


def test_harden_config_dir_posix_tightens(tmp_path, monkeypatch):
    """POSIX 分支：权限过宽必须被收紧到 0700。

    ## 为什么不再 `skipif(sys.platform == "win32")`（2026-09-29 修）

    原来这条在 Windows 上**整条跳过**，理由是「`os.chmod` 不支持 POSIX 权限位」。
    但这等于说：**本机（开发机就是 Windows）的 POSIX 收紧逻辑从未被验证过**——
    而 skip 与 pass 在 CI 里退出码相同，报告照样全绿。

    实际上该分支只有**两个**平台相关的接缝，两个都可以 patch：
    - `os.name`（决定走哪个分支）→ `monkeypatch.setattr(cfgmod.os, "name", "posix")`；
    - `Path.chmod`（真正落权限）→ 换成记录调用的替身（Windows 上真实 `os.chmod`
      只能切只读位，所以不能真调，但我们**只需证明它被以 0o700 调用**）。

    `st_mode & 0o077` 的读入在 Windows 上返回 `0o777`（实测），
    因此「过宽 → 触发收紧」这条路径在本机也能真实走到。
    """
    d = tmp_path / "d"
    d.mkdir()
    modes: list[int] = []
    monkeypatch.setattr(Path, "chmod",
                        lambda self, m, **kw: modes.append(m))

    monkeypatch.setattr(cfgmod.os, "name", "posix")
    assert cfgmod.harden_config_dir(d) is None, "收紧成功时应返回 None（无告警）"
    assert modes == [0o700], (
        "权限未被收紧：期望恰好一次 chmod(0o700)，实为 %r" % (modes,)
    )


def test_harden_config_dir_posix_noop_when_already_tight(tmp_path, monkeypatch):
    """反向：权限本来就紧（无组/其他位）时**不得**多此一举 chmod。

    否则每次启动都写一次权限位，既无意义又可能干扰只读挂载。
    判据是「chmod 一次都没被调用」——只断言返回 None 是不够的。
    """
    d = tmp_path / "d"
    d.mkdir()
    calls: list[int] = []
    monkeypatch.setattr(Path, "chmod", lambda self, m, **kw: calls.append(m))
    # 伪造一个"已经很紧"的 stat 结果（0o700 → & 0o077 == 0）
    real_stat = Path.stat

    class _St:
        st_mode = 0o700 | 0o040000  # 目录位 | 0700

    monkeypatch.setattr(Path, "stat", lambda self, **kw: _St() if self == d else real_stat(self, **kw))
    monkeypatch.setattr(cfgmod.os, "name", "posix")
    assert cfgmod.harden_config_dir(d) is None
    assert calls == [], "权限已紧仍调用 chmod（实为 %r）" % (calls,)


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

    ## 判据走 AST 而非文本子串（2026-09-29 R15 改）

    旧版 `assert "harden_config_dir()" in src` 绑**书写格式**：
    真身写 `cfg.harden_config_dir()` 恰好含该串而通过，但只要改成
    `cfg.harden_config_dir( )`（多一个空格）或把调用拆行，就会**假红**。
    要守的性质是「**存在对 harden_config_dir 的调用**」，与空格无关。
    另注：`main.py` 里函数名还出现在**注释**中（"…=死代码——守卫用例…盯着这行"），
    子串判据会把注释也算命中 ⇒ 用 AST 只认 `Call`。
    """
    src = (ROOT / "medkit" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Attribute) and n.func.attr == "harden_config_dir")
            or (isinstance(n.func, ast.Name) and n.func.id == "harden_config_dir")
        )
    ]
    assert calls, (
        "启动流程没有**调用** harden_config_dir（注释里提到不算）→ 目录加固是死代码")
    assert "dir_permission_warning" in (ROOT / "medkit" / "core" / "config.py").read_text(
        encoding="utf-8"), "缺少告警读取口"


def test_config_dir_hardening_wiring_guard_is_not_vacuous():
    """元守卫：证明 AST 判据抓得住「摘掉调用点」，且不被注释里的同名串骗过。"""
    src = (ROOT / "medkit" / "main.py").read_text(encoding="utf-8")

    def _calls(text: str) -> int:
        tree = ast.parse(text)
        return len([n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and (
                        (isinstance(n.func, ast.Attribute)
                         and n.func.attr == "harden_config_dir")
                        or (isinstance(n.func, ast.Name)
                            and n.func.id == "harden_config_dir"))])

    assert _calls(src) == 1, "真身里应恰好 1 处调用（形态变了请同步本条用例）"

    # 证伪 ①：注释掉调用行 —— 子串判据会因**注释里的同名串**继续命中
    commented = src.replace("    _dir_warn = cfg.harden_config_dir()",
                            "    # _dir_warn = cfg.harden_config_dir()", 1)
    assert commented != src, "证伪 ① 注入未生效"
    assert "harden_config_dir" in commented, "注释里仍有该词（正是旧判据的漏洞）"
    assert _calls(commented) == 0, "注释掉调用后 AST 判据仍说『有调用』——守卫是假绿"

    # 证伪 ②：等价改写（调用里加空格）必须**仍绿**（防假红）
    spaced = src.replace("cfg.harden_config_dir()", "cfg.harden_config_dir( )", 1)
    assert _calls(spaced) == 1, "等价改写（多一个空格）被判红 —— 判据仍绑书写格式"


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
