"""B5 回归（R8+W 修复批次）：供应链一致性守卫。

覆盖：
- **S2-23**：`requirements.lock` 必须与「从 `requirements.txt` 实际推导出的运行时依赖闭包」
  **完全一致**（锁文件随升级漂移是原报告点出的缺口；这里把它变成可自动判定的断言）。
- **S2-24**：`starlette` 必须 ≥ 1.3.1（CVE-2026-54283 修复线），防止被误降级回去。
- **S2-22**：`THIRD_PARTY_NOTICES.md` 的闭包条目数必须与 lock 条目数一致
  （原缺口：漏了 `uvicorn[standard]` 的 4 个 extras）。
"""

import importlib.metadata as md
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name as cn

ROOT = Path(__file__).resolve().parents[1]


def _direct_requirements() -> list[str]:
    out: list[str] = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def _lock_names() -> dict[str, str]:
    """返回 {归一化包名: 版本}。"""
    out: dict[str, str] = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, ver = line.split("==", 1)
        out[cn(name.split("[")[0])] = ver.strip()
    return out


def _applies(req: Requirement, extras: frozenset[str]) -> bool:
    """该依赖在「当前已请求的 extras」下是否生效（处理 `extra == "standard"` 这类 marker）。"""
    m = req.marker
    if m is None:
        return True
    return any(m.evaluate({"extra": e}) for e in (extras or frozenset({""})))


def _runtime_closure() -> set[str]:
    """从 requirements.txt 的直接依赖出发，按已装元数据推导传递闭包（含 extras）。"""
    names: set[str] = set()
    seen: set[tuple[str, frozenset[str]]] = set()
    stack: list[tuple[str, frozenset[str]]] = []
    for spec in _direct_requirements():
        r = Requirement(spec)
        stack.append((r.name, frozenset(r.extras)))
    while stack:
        name, extras = stack.pop()
        key = (cn(name), extras)
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        names.add(cn(name))
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except Exception:  # noqa: BLE001  元数据里有非法条目时跳过
                continue
            if _applies(req, extras):
                stack.append((req.name, frozenset(req.extras)))
    return names


def test_lock_matches_runtime_closure():
    """S2-23：锁文件 = 实际闭包（少一个 → 产物缺依赖；多一个 → 锁文件漂移）。"""
    lock = _lock_names()
    closure = _runtime_closure()
    assert closure, "闭包推导为空——requirements.txt 或环境异常"
    missing = sorted(closure - set(lock))
    extra = sorted(set(lock) - closure)
    assert not missing, f"lock 漏了实际闭包里的包：{missing}（升级依赖后未同步锁文件）"
    assert not extra, f"lock 含闭包外的包：{extra}（锁文件漂移）"


def test_starlette_meets_cve_fix_line():
    """S2-24：starlette 必须 ≥ 1.3.1（CVE-2026-54283 修复版本），防止误降级。"""
    ver = _lock_names().get("starlette")
    assert ver, "lock 里没有 starlette"
    parts = tuple(int(x) for x in re.findall(r"\d+", ver)[:3])
    assert parts >= (1, 3, 1), f"starlette {ver} 低于 CVE-2026-54283 修复线 1.3.1"


def test_notices_closure_count_matches_lock():
    """S2-22：notices 闭包条目数 == lock 条目数（原缺口：漏 4 个 uvicorn[standard] extras）。"""
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    section = text.split("## 运行时依赖闭包", 1)
    assert len(section) == 2, "notices 缺少「运行时依赖闭包」小节"
    body = section[1].split("## ", 1)[0]
    rows = re.findall(r"^\|\s*([A-Za-z0-9_.\-]+)\s*\|\s*([0-9][^|]*)\|", body, re.M)
    names = {cn(n) for n, _v in rows}
    lock = _lock_names()
    missing = sorted(set(lock) - names)
    assert not missing, f"notices 闭包漏了：{missing}"
    assert len(names) == len(lock), f"notices 条目数 {len(names)} ≠ lock 条目数 {len(lock)}"


@pytest.mark.parametrize("pkg", ["httptools", "python-dotenv", "watchfiles", "websockets"])
def test_uvicorn_standard_extras_are_declared(pkg):
    """S2-22 的具体缺口：这 4 个 extras 必须同时出现在 lock 与 notices 里。"""
    assert pkg in _lock_names(), f"{pkg} 不在 lock 闭包里"
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert re.search(rf"^\|\s*{re.escape(pkg)}\s*\|", text, re.M | re.I), \
        f"{pkg} 不在 THIRD_PARTY_NOTICES 闭包表里"
