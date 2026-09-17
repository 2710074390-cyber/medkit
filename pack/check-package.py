#!/usr/bin/env python
"""WP-12 + S2-18/S2-19：纯净安装包检查（标准库，仅打包机/CI 使用）。

两项检查：
1. **黑名单**（WP-12）：dist 内不得含学科/样例/测试数据与字节码。
2. **闭包一致性**（S2-18/S2-19）：`dist/_internal` 里出现的发行包（以 `*.dist-info` 为准）
   必须是 `requirements.lock` 闭包的子集——**多出来的就是「构建机环境污染」**（未声明的组件
   被打进了产物）。这一条把「产物实际闭包 ≠ lock/notices」从"事后人工比对"变成可自动判定的守卫。

用法：
    python pack/check-package.py [dist_root] [--strict]

- 默认：dist 不存在 → 打印 `[跳过]` 并返回 0（开发机未构建时的正常态）。
- `--strict`：dist 不存在 → **返回 1**（供 CI 打包 job 使用——那种场景下「没产物」就是失败，
  原实现的 `return 0` 会让这一步成为空操作，S2-19 即指此）；且闭包多出未声明包时返回 1。
- 返回码：0=通过 / 1=违规。
"""

import re
import sys
from pathlib import Path

# 黑名单：命中即失败（路径小写比较；目录/文件均可）
BLACKLIST_SUBSTRINGS = (
    "syllabus_seed_306.json",
    "samples",
    "tests",
    "__pycache__",
    "medkit/data",
    ".pyc",
)

# 允许出现在产物里、但不属于 lock 闭包的发行包（空 = 不允许任何例外）。
# 留白是刻意的：新增例外必须写在这里并说明理由，否则 S2-18 的漂移会再次静默发生。
ALLOWED_EXTRA_DISTS: frozenset[str] = frozenset()

_DIST_INFO = re.compile(r"^(?P<name>.+?)-(?P<ver>\d[^-]*)\.dist-info$")


def _norm(name: str) -> str:
    """PEP 503 归一化：小写 + `-`/`_`/`.` 统一为 `-`。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def check_dist(root: Path) -> list[str]:
    """黑名单检查：返回违规路径列表（空 = 通过）。"""
    found: list[str] = []
    if not root.exists():
        return found
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix().lower()
        if any(b in rel for b in BLACKLIST_SUBSTRINGS):
            found.append(rel)
    return found


def lock_closure(lock_path: Path) -> set[str]:
    """从 requirements.lock 解析发行包名集合（归一化）。"""
    names: set[str] = set()
    if not lock_path.exists():
        return names
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        names.add(_norm(line.split("==")[0].split("[")[0]))
    return names


def dist_dists(root: Path) -> tuple[set[str], set[str]]:
    """返回 (产物内发行包集合, 模块目录集合)；dist-info 名归一化。"""
    internal = root / "_internal"
    base = internal if internal.is_dir() else root
    dists: set[str] = set()
    module_dirs: set[str] = set()
    if not base.is_dir():
        return dists, module_dirs
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        if p.name.endswith(".dist-info"):
            m = _DIST_INFO.match(p.name)
            if m:
                dists.add(_norm(m.group("name")))
        elif not p.name.startswith((".", "_")):
            module_dirs.add(_norm(p.name))
    return dists, module_dirs


def closure_drift(root: Path, lock_path: Path) -> tuple[list[str], int]:
    """返回 (产物里多出来的未声明发行包, 缺 dist-info 的已声明依赖数)。"""
    declared = lock_closure(lock_path)
    if not declared:
        return [], 0
    dists, _dirs = dist_dists(root)
    extras = sorted(d for d in dists
                    if d not in declared and d not in ALLOWED_EXTRA_DISTS)
    missing_info = max(0, len(declared) - len(dists & declared))
    return extras, missing_info


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台常为 cp1252 而本脚本输出中文——强制 UTF-8，避免 UnicodeEncodeError（R6-11 CI 实证）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    argv = list(argv if argv is not None else sys.argv[1:])
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    repo = Path(__file__).resolve().parents[1]
    root = Path(argv[0]) if argv else repo / "dist" / "MedKit"
    lock_path = repo / "requirements.lock"

    if not root.exists():
        if strict:
            print(f"[失败] --strict：未找到产物目录 {root}"
                  f"（CI 打包 job 里「没产物」即失败——原实现的 return 0 会让这一步成为空操作）")
            return 1
        print(f"[跳过] {root} 不存在（尚未构建）")
        return 0

    bad = check_dist(root)
    extras, missing_info = closure_drift(root, lock_path)
    rc = 0

    if bad:
        print(f"[失败] 纯净安装包检查未通过：{root}")
        for rel in bad[:50]:
            print(f"  - {rel}")
        if len(bad) > 50:
            print(f"  … 共 {len(bad)} 项")
        rc = 1

    if extras:
        level = "[失败]" if strict else "[警告]"
        print(f"{level} 产物含未声明发行包（不在 requirements.lock 闭包内）：{'、'.join(extras)}")
        print("       成因通常是**打包机环境不纯净**（PyInstaller 把环境里的无关包一并收集）。"
              "请在干净虚拟环境里构建，或把无关包排除出 medkit.spec。")
        if strict:
            rc = 1

    if missing_info:
        print(f"[提示] 有 {missing_info} 个已声明依赖未随产物保留 dist-info"
              f"（THIRD_PARTY_NOTICES 要求随产物保留 LICENSE 原文，出包后请抽查）")

    if rc == 0:
        if extras:
            print(f"[通过（有警告）] 纯净安装包检查：{root}"
                  f"（无样例/种子/测试/字节码；但产物含 {len(extras)} 个未声明发行包，见上方警告）")
        else:
            print(f"[通过] 纯净安装包检查：{root}（无样例/种子/测试/字节码；闭包无未声明包）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
