#!/usr/bin/env python
"""构建前环境体检：当前解释器是否装了「测试/开发专用」依赖（R8+W）。

**为什么需要这一步**：0.10.3 的产物里混进了 4 个未声明发行包
（attrs / email-validator / importlib-metadata / itsdangerous）以及 tzdata、chardet、brotli 等
——它们**都不在 `requirements.lock`**，是**构建机环境不纯净**被 PyInstaller 连带收集的结果。
`pack/build.bat` 原先直接用 PATH 上的 `python -m PyInstaller`，**没有环境隔离、也不校验环境**，
所以「在装过 pytest / playwright / pip-audit 的开发环境里出包」就会把测试专用依赖带进产物。

本脚本把这件事从「事后 check-package 才发现」提前到「构建前就拦住」。

用法：
    python pack/check-build-env.py            # 有 dev 依赖 → 非零退出
    python pack/check-build-env.py --allow-dirty   # 只警告不拦（本地临时构建用）

判定口径：
    dev_only = （requirements-dev.txt 里的包） − （requirements.txt / requirements.lock 里的包）
    —— 必须做这个减法：`setuptools` 同时出现在 dev 里，但 jieba 运行期需要 pkg_resources，
       把它算作「dev 专用」会导致误报。
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import distributions
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 兜底名单：即便 requirements-dev.txt 漏写，这些也绝不该出现在构建环境里
ALWAYS_SUSPECT = (
    "pytest", "playwright", "pip-audit", "pytest-cov", "pytest-timeout",
    "freezegun", "hypothesis", "coverage", "nose", "mock",
)

# 例外：出现在 requirements-dev.txt 里、但**运行期确实需要**的包（不得当 dev 专用剔除）。
# `setuptools`：jieba 运行期 import pkg_resources（requirements-dev.txt 的注释写明
# 「pin until new release」），产物里也确实带着 setuptools/_vendor/。
# 不排除它，否则体检会误报、还会误导人把它从产物里剔掉 → jieba 直接崩。
DEV_BUT_RUNTIME = ("setuptools",)


def _norm(name: str) -> str:
    """PEP 503 归一化。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _req_names(path: Path) -> set[str]:
    """解析 requirements*.txt / .lock 里的包名（跳过注释、选项、hash 行）。"""
    names: set[str] = set()
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "\\")):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            names.add(_norm(m.group(1)))
    return names


def installed_names() -> set[str]:
    out: set[str] = set()
    for d in distributions():
        try:
            nm = d.metadata["Name"]
        except Exception:  # noqa: BLE001  元数据损坏的发行包跳过（不该影响体检）
            continue
        if nm:
            out.add(_norm(nm))
    return out


def dev_only_names() -> set[str]:
    dev = _req_names(REPO / "requirements-dev.txt") | {_norm(x) for x in ALWAYS_SUSPECT}
    runtime = (_req_names(REPO / "requirements.txt")
               | _req_names(REPO / "requirements.lock")
               | {_norm(x) for x in DEV_BUT_RUNTIME})
    return dev - runtime


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    argv = list(argv if argv is not None else sys.argv[1:])
    allow_dirty = "--allow-dirty" in argv

    target = dev_only_names()
    present = sorted(target & installed_names())
    print(f"=== 构建环境体检（解释器：{sys.executable}）===")
    print(f"    dev 专用依赖 {len(target)} 个；当前环境命中 {len(present)} 个")
    if not present:
        print("[通过] 当前环境未安装测试/开发专用依赖，可以安全构建。")
        return 0

    print(f"[{'警告' if allow_dirty else '失败'}] 当前环境装了测试/开发专用依赖：{'、'.join(present)}")
    print("        在这样的环境里构建，PyInstaller 可能把它们连带打进安装包")
    print("        （0.10.3 产物混入 attrs/email-validator/itsdangerous 就是这个原因）。")
    print()
    print("  推荐做法——用干净虚拟环境构建：")
    print("    python -m venv .buildenv")
    print("    .buildenv\\Scripts\\pip install --require-hashes -r requirements.lock")
    print("    .buildenv\\Scripts\\pip install pyinstaller")
    print("    set MEDKIT_BUILD_PYTHON=%CD%\\.buildenv\\Scripts\\python.exe")
    print("    再跑 pack\\build.bat")
    print()
    print("  若确要在此环境临时构建（产物可能不纯净，出包后必须核对 check-package）：")
    print("    python pack\\check-build-env.py --allow-dirty")
    return 0 if allow_dirty else 1


if __name__ == "__main__":
    raise SystemExit(main())
