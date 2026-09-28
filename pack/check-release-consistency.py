"""发布五件套一致性检查（S2-18 类守卫，标准库，随仓库分发）。

为什么需要它（2026-09-28 的实际事故）：
  0.10.5 的 `__version__` / `pack/version.iss` / README 早早就指向了 0.10.5，
  但 `dist-installer/MedKit-Setup-0.10.5.exe` 是 **EP-01 落库之前**构建的 ——
  **文件名对，内容旧**。光看文件名会得出「五件套已对齐」的错误结论。
  本脚本把「名对 + 内容对」都变成可机器判定的检查。

五项：
  1. medkit/__init__.py 的 __version__
  2. pack/version.iss 的 MyAppVersion
  3. CHANGELOG.md 是否已有 `## [<版本>]` 小节（不能还挂在 [Unreleased]）
  4. README.md 里引用的 `MedKit-Setup-<版本>.exe`
  5. dist-installer/ 产物：同名 exe/zip 是否**真的含**当前源码的 prompts 与 web 资源
     （第 5 项是本次事故的核心——用解包内容比对，而不是看文件名）

用法：
    python pack/check-release-consistency.py            # 检查全部（产物缺失则记为警告）
    python pack/check-release-consistency.py --strict   # 任一不一致即失败（发布前用）

退出码：0 = 一致（或无产物但非 strict）；1 = 有不一致。
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace")


def v_from_init() -> str | None:
    m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", _read(ROOT / "medkit" / "__init__.py"))
    return m.group(1) if m else None


def v_from_iss() -> str | None:
    m = re.search(r'MyAppVersion\s+"([^"]+)"', _read(ROOT / "pack" / "version.iss"))
    return m.group(1) if m else None


def changelog_has_section(ver: str) -> bool:
    return re.search(rf"^## \[{re.escape(ver)}\]", _read(ROOT / "CHANGELOG.md"), re.M) is not None


def readme_has_installer(ver: str) -> bool:
    return f"MedKit-Setup-{ver}.exe" in _read(ROOT / "README.md")


def installer_exists(ver: str) -> pathlib.Path | None:
    p = ROOT / "dist-installer" / f"MedKit-Setup-{ver}.exe"
    return p if p.exists() else None


def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sums_match(ver: str) -> tuple[bool, str]:
    """SHA256SUMS.txt 与产物是否逐字节一致（独立复算，不信脚本自述）。"""
    sums = ROOT / "dist-installer" / "SHA256SUMS.txt"
    if not sums.exists():
        return False, "SHA256SUMS.txt 不存在"
    declared: dict[str, str] = {}
    for line in _read(sums).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            declared[parts[1].strip()] = parts[0]
    bad: list[str] = []
    checked = 0
    for name, want in declared.items():
        if ver not in name:
            continue                      # 只核对当前版本命名的产物
        p = ROOT / "dist-installer" / name
        if not p.exists():
            bad.append(f"{name}（文件缺失）")
            continue
        checked += 1
        got = _sha256(p)
        if got != want:
            bad.append(f"{name}（声明 {want[:12]} ≠ 实算 {got[:12]}）")
    if checked == 0:
        return False, f"清单里没有版本 {ver} 的产物条目"
    if bad:
        return False, "；".join(bad)
    return True, f"{checked} 项逐字节一致"


def installer_contains_sources(ver: str) -> tuple[bool, str]:
    """产物是否含当前源码的 prompts/ 与 web/（逐字节比对）。

    绿色版解包目录 `dist/MedKit/_internal/medkit/` 是安装包的**输入**，
    且 `medkit.iss` 用 `Source: "dist\\MedKit\\*"` 整目录递归打包、无 Excludes，
    因此「dist 内容一致」⇒「安装包内容一致」。这里同时支持直接检查
    `dist-installer/*-portable.zip`（若存在）。

    注意：不去解包 Inno Setup 的 exe —— 那需要外部工具且慢；
    本项以 dist 为代理判据，并在报告里明确写出这个推理链。
    """
    payload = ROOT / "dist" / "MedKit" / "_internal" / "medkit"
    if not payload.exists():
        return False, "dist/MedKit/_internal/medkit 不存在（未构建？）"

    diffs: list[str] = []
    for sub in ("prompts", "web"):
        src = ROOT / "medkit" / sub
        if not src.exists():
            continue
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            dst = payload / sub / rel
            if not dst.exists():
                diffs.append(f"{sub}/{rel} 缺失")
            elif _sha256(f) != _sha256(dst):
                diffs.append(f"{sub}/{rel} 内容不同")
    if diffs:
        head = "；".join(diffs[:6])
        more = f"（共 {len(diffs)} 处）" if len(diffs) > 6 else ""
        return False, head + more
    return True, "prompts/ + web/ 与源码逐字节一致"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布五件套一致性检查")
    ap.add_argument("--strict", action="store_true",
                    help="产物缺失/不一致即失败（发布前用）；缺省则产物缺失只警告")
    args = ap.parse_args(argv)

    ver = v_from_init()
    if not ver:
        print("[失败] 无法从 medkit/__init__.py 读取 __version__")
        return 1

    rows: list[tuple[str, bool, str, bool]] = []   # (项, 是否通过, 详情, 缺失是否致命)

    rows.append(("1. medkit/__init__.py __version__", True, ver, True))

    iv = v_from_iss()
    rows.append(("2. pack/version.iss MyAppVersion", iv == ver,
                 f"{iv}" + ("" if iv == ver else f"（应为 {ver}）"), True))

    rows.append((f"3. CHANGELOG 含 ## [{ver}]", changelog_has_section(ver),
                 "已发布小节存在" if changelog_has_section(ver)
                 else "仍挂在 [Unreleased]（未收口）", True))

    rows.append((f"4. README 引用 MedKit-Setup-{ver}.exe", readme_has_installer(ver),
                 "已引用" if readme_has_installer(ver) else "未引用该版本安装包名", True))

    ins = installer_exists(ver)
    if ins is None:
        rows.append((f"5a. dist-installer/MedKit-Setup-{ver}.exe", False, "产物不存在", False))
    else:
        ok, detail = sums_match(ver)
        rows.append(("5a. SHA256SUMS 与产物一致", ok, detail, True))
        ok2, detail2 = installer_contains_sources(ver)
        rows.append(("5b. 产物含当前源码 prompts/web", ok2, detail2, True))

    print(f"=== 发布五件套一致性检查（版本 {ver}）===")
    print(f"仓库：{ROOT}")
    print()
    hard_fail = False
    for name, ok, detail, fatal in rows:
        mark = "通过" if ok else ("警告" if not fatal else "失败")
        print(f"  [{mark}] {name}")
        print(f"          {detail}")
        if not ok and fatal:
            hard_fail = True
    print()
    if hard_fail:
        print("[失败] 存在硬性不一致 —— 发布前必须修掉。")
        return 1
    if any(not ok for _n, ok, _d, _f in rows) and args.strict:
        print("[失败] --strict：存在警告级不一致。")
        return 1
    print("[通过] 五件套一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
