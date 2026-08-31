#!/usr/bin/env python
"""WP-12：纯净安装包检查——扫描 dist 确认不含学科/样例/测试数据（标准库，仅打包机/CI 使用）。

用法：python pack/check-package.py [dist_root]
默认 dist_root = 仓库根/dist/MedKit；返回码 0=通过，1=发现违规。
"""

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


def check_dist(root: Path) -> list[str]:
    """返回违规路径列表（空 = 通过）。"""
    found: list[str] = []
    if not root.exists():
        return found
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix().lower()
        if any(b in rel for b in BLACKLIST_SUBSTRINGS):
            found.append(rel)
    return found


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    default = Path(__file__).resolve().parents[1] / "dist" / "MedKit"
    root = Path(argv[0]) if argv else default
    if not root.exists():
        print(f"[跳过] {root} 不存在（尚未构建）")
        return 0
    bad = check_dist(root)
    if bad:
        print(f"[失败] 纯净安装包检查未通过：{root}")
        for rel in bad[:50]:
            print(f"  - {rel}")
        if len(bad) > 50:
            print(f"  … 共 {len(bad)} 项")
        return 1
    print(f"[通过] 纯净安装包检查：{root}（无样例/种子/测试/字节码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
