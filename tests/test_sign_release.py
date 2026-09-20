"""B18 回归（R8+W）：代码签名脚本 —— 参数校验与「绝不静默跳过」。

背景：审查项 M5-07「无可复现 CI 构建/**签名**/sha256 清单」里的「签名」一直没落地——
不是不想做，而是**没有证书**，且签名步骤没有任何脚本承载（每换一台机器都要重新摸索顺序）。
本文件锁住 `pack/sign-release.ps1` 的两条关键性质：

1. **缺证书/缺工具时快速失败**（exit≠0 + 明确提示），绝不静默跳过——
   否则「发布了个未签名产物」这件事没人会发现；
2. **不进入交互式询问**（参数一旦设为 Mandatory，CI 里会挂在「请为以下参数提供值」上）。

⚠️ 本机无 signtool、无证书，所以只能验证**失败路径**；成功路径需有证书的环境才能跑，
   不在本文件里假装验证。
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pack" / "sign-release.ps1"
BUILD_BAT = ROOT / "pack" / "build.bat"

_PWSH = shutil.which("powershell") or shutil.which("pwsh")


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")  # 带 BOM（PowerShell 5.1 要求）


# ---------------------------------------------------------------- 静态性质

def test_script_exists_and_has_bom():
    """PowerShell 5.1 读无 BOM 的 .ps1 会按系统代码页解码 → 中文注释被解坏 → 解析失败。

    实测：本脚本首版无 BOM，运行时报「表达式或语句中包含意外的标记」且行号对不上；
    加 BOM 后正常。**这条必须守住**——否则脚本在中文 Windows 上根本跑不起来。
    """
    raw = SCRIPT.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "缺 UTF-8 BOM：PowerShell 5.1 会按 GBK 解码而解析失败"


def test_no_mandatory_params():
    """参数不得设 Mandatory——否则缺参时进入交互式询问，CI/批处理会挂死。"""
    src = _src()
    # ⚠️ 只看 param(...) 块——注释里提到那个词是允许的（首版就因扫全文而误报过）
    block = src[src.index("param("):src.index(")", src.index("param(")) + 1]
    assert "Mandatory" not in block, "param 块里出现 Mandatory → 缺参时会交互式询问（会挂死）"
    assert "必须提供 -Thumbprint 或 -Pfx" in src, "缺少手动校验 + 快速失败"


def test_script_avoids_here_string():
    """不得使用 here-string：其收尾标记必须顶格，缩进进代码块会解析失败。

    （踩过两次：第一次写成缩进的 here-string，第二次在**注释里**写出那个两字符标记
    也照样炸解析——所以连注释都要避免。）
    """
    src = _src()
    assert "@\"\n" not in src and "\n\"@" not in src, "使用了 here-string（缩进时极易解析失败）"


def test_verifies_signature_after_signing():
    """签完必须回验——只看 signtool 退出码不够（时间戳失败时仍可能返回 0）。"""
    src = _src()
    assert "Get-AuthenticodeSignature" in src
    assert "'Valid'" in src, "未断言签名状态为 Valid"


def test_documents_ordering_requirement():
    """必须写明「签名在 make_release.py 之前」——顺序错了清单就对不上签名后的字节。"""
    src = _src()
    assert "make_release.py" in src
    assert "必须最后" in src or "必须**最后**" in src


# ---------------------------------------------------------------- 接线

def test_build_bat_wires_signing_optional():
    """接线级：build.bat 必须在 make_release.py **之前**调用签名脚本，且失败即中断。"""
    src = BUILD_BAT.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in src.splitlines()]
    # ⚠️ 必须匹配**调用形态**（含 `-File`），不能只匹配文件名：
    # build.bat 里还有一行 `echo ...签名见 pack\sign-release.ps1 头部说明`——
    # 只按文件名找会把它当成"调用"，于是摘掉真正的调用行时守卫仍绿（反向验证实测过的假绿）。
    invocations = [ln for ln in lines
                   if "sign-release.ps1" in ln and "-File" in ln
                   and not ln.startswith(("rem", "echo"))]
    # 同 test_release_artifacts：不写死解释器前缀（R8+W 起统一走 "%PY%"）
    rel_idx = next((i for i, ln in enumerate(lines)
                    if "make_release.py" in ln and not ln.startswith(("rem", "echo"))), None)
    assert invocations, "build.bat 未真正调用签名脚本（或被注释/仅被 echo 提及）"
    sign_idx = next(i for i, ln in enumerate(lines) if ln in invocations)
    assert rel_idx is not None, "build.bat 未调用 make_release.py"
    assert sign_idx < rel_idx, "签名必须在 make_release.py 之前（否则 zip/清单覆盖的是未签名字节）"
    assert "MEDKIT_SIGN_THUMBPRINT" in src, "缺少「有证书才签、无证书明确跳过」的开关"


# ---------------------------------------------------------------- 失败路径（本机可实测）

pytestmark_ps = pytest.mark.skipif(_PWSH is None, reason="本机无 powershell/pwsh")


@pytestmark_ps
@pytest.mark.parametrize(("args", "expect"), [
    ([], "-Thumbprint"),                      # 断言只用 ASCII 子串（解码无关）
    (["-Thumbprint", "DEADBEEF"], "signtool.exe"),
])
def test_fails_fast_without_cert_or_tool(args, expect):
    """缺证书 / 缺 signtool 都必须**非零退出 + 明确提示**，绝不静默跳过。

    本机（无 signtool、无证书）正好能实测这两条失败路径。
    """
    r = subprocess.run(
        [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True, timeout=60, cwd=str(ROOT),
    )
    assert r.returncode != 0, f"应非零退出，实际 {r.returncode}（静默跳过？）"
    # ⚠️ 控制台输出走**系统代码页**（中文 Windows 是 GBK），按 UTF-8 解会得到乱码；
    # 这里按 GBK 解，且只断言 **ASCII 子串**（任何解码下都稳定）。
    out = (r.stdout or b"").decode("gbk", errors="replace") + \
        (r.stderr or b"").decode("gbk", errors="replace")
    assert expect in out, f"提示信息缺失：{out[:300]}"


@pytestmark_ps
def test_does_not_hang_waiting_for_input():
    """不带任何参数运行必须立刻返回（不能挂在交互式询问上）。"""
    r = subprocess.run(
        [_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)],
        capture_output=True, timeout=30, cwd=str(ROOT),
    )
    assert r.returncode != 0
    out = (r.stdout or b"").decode("gbk", errors="replace") + \
        (r.stderr or b"").decode("gbk", errors="replace")
    # 交互式询问的特征串（ASCII 部分稳定，任何解码下都可用于判据）
    assert "Thumbprint:" not in out, f"进入了交互式询问（CI 会挂死）：{out[:200]}"


if __name__ == "__main__":   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
