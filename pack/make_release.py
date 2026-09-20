#!/usr/bin/env python
"""生成发布产物：绿色版 zip + SHA256 清单（S2-22 / M5-07，R8+W）。

背景：原 `pack/build.bat` 只跑到「PyInstaller 出 dist/MedKit + Inno Setup 出安装包」，
**绿色版 zip 与校验清单都没有脚本**——2026-09-20 出 0.10.4 时这两步是手工做的，
于是「sha256 清单」在审查里长期挂着（M5-07：无可复现 CI 构建/签名/sha256 清单）。
本脚本把这两步固化，并在 build.bat 里接线。

产物（均落在 dist-installer/，版本号取自 medkit/__init__.py 单源）：
- MedKit-<ver>-portable.zip   绿色版，顶层目录 `MedKit/`（与历史 zip 结构一致）
- SHA256SUMS.txt              sha256sum 兼容格式，覆盖本次发布的全部产物

用法：python pack/make_release.py
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist" / "MedKit"
OUT_DIR = REPO / "dist-installer"


def _version() -> str:
    """从单源取版本（与 __init__ 保持一致，不另设常量）。"""
    src = (REPO / "medkit" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', src)
    if not m:
        raise SystemExit("[失败] 无法从 medkit/__init__.py 读取 __version__")
    return m.group(1)


def _zip_portable(ver: str) -> Path:
    """把 dist/MedKit 打成绿色版 zip（顶层 `MedKit/`，与既有发布物结构一致）。"""
    if not DIST.is_dir():
        raise SystemExit(f"[失败] 未找到 {DIST}——请先跑 PyInstaller 构建")
    target = OUT_DIR / f"MedKit-{ver}-portable.zip"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(DIST.rglob("*")):
            if f.is_file():
                z.write(f, f"MedKit/{f.relative_to(DIST).as_posix()}")
                n += 1
    print(f"[完成] 绿色版：{target.name}（{n} 个文件，{target.stat().st_size / 1048576:.1f} MB）")
    return target


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _checksums(ver: str) -> Path:
    """为本次版本的全部产物生成 sha256sum 兼容清单。

    只覆盖**当前版本**的产物（安装包 + 绿色版）——历史版本的产物留在目录里，
    但清单只对本次发布负责（避免「清单里混着旧版本、用户核对时困惑」）。
    """
    wanted = [f"MedKit-Setup-{ver}.exe", f"MedKit-{ver}-portable.zip"]
    lines: list[str] = []
    missing: list[str] = []
    for name in wanted:
        p = OUT_DIR / name
        if not p.exists():
            missing.append(name)
            continue
        lines.append(f"{_sha256(p)}  {name}")
        print(f"[完成] 校验：{name}")
    if not lines:
        raise SystemExit(f"[失败] dist-installer/ 里没有 {ver} 的任何产物")
    if missing:
        # 安装包缺失是常见情况（未装 Inno Setup）——不视为失败，但要说清楚
        print(f"[提示] 以下产物不存在，未纳入清单：{', '.join(missing)}")
    out = OUT_DIR / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    print(f"[完成] 清单：{out.name}（{len(lines)} 项）")
    return out


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ver = _version()
    print(f"=== 生成发布产物（版本 {ver}）===")
    _zip_portable(ver)
    _checksums(ver)
    print("\n=== 发布清单 ===")
    for f in sorted(OUT_DIR.glob(f"*{ver}*")) + [OUT_DIR / "SHA256SUMS.txt"]:
        if f.exists():
            print(f"  {f.name}  ({f.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
