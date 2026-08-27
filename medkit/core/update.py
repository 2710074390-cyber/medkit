"""v0.6：内置更新检查（GitHub Releases，仅提醒 + 跳转下载页）。

- check() 请求 GitHub API releases/latest，与本地 __version__ 数值化比较
- 任何网络/解析异常一律优雅降级（has_update=False + error="network"），绝不抛出
- 不携带任何用户数据，仅访问 api.github.com
"""

from typing import Any

import httpx

from .. import __version__

GITHUB_REPO = "2710074390-cyber/medkit"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
NOTES_LIMIT = 800


import re as _re

_PRE_RE = _re.compile(r"[-+]?([a-zA-Z]+)[-._]?(\d*)")
_PRE_RANK = {"dev": 0, "alpha": 1, "beta": 2, "preview": 3, "rc": 4,
             "test": 4, "v": 5}


def _version_parts(v: str) -> tuple[tuple[int, ...], tuple[str, int] | None]:
    """把版本拆成 (数值部分, 预览后缀)。``0.8.0-rc.1`` → ((0,8,0), ("rc",1))。

    A10：此前首个非数字即截断（0.8.0-rc.1 被折叠成 0.8.0），预览版永不提示。
    现在后缀参与比较：正式版 > 任何预览版；预览版之间按 dev<alpha<beta<rc 排序。
    """
    v = v.strip().lstrip("vV")
    m = _PRE_RE.search(v)
    if m:
        nums_str = v[: m.start()]
        pre: tuple[str, int] | None = (m.group(1).lower(), int(m.group(2) or 0))
    else:
        nums_str = v
        pre = None
    parts: list[int] = []
    for seg in nums_str.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return (tuple(parts) if parts else (0,)), pre


def _version_tuple(v: str) -> tuple[int, ...]:
    """纯数值部分（兼容旧调用方/测试）。"""
    return _version_parts(v)[0]


def _pre_rank(pre: tuple[str, int] | None) -> tuple[int, int]:
    """预览后缀排序键：(-1,0) = 正式版（最高）；否则 (类型序/100, 序号)。"""
    if pre is None:
        return (10, 0)
    return (_PRE_RANK.get(pre[0], 5), pre[1])


def is_newer(latest: str, current: str) -> bool:
    lt, lp = _version_parts(latest)
    ct, cp = _version_parts(current)
    n = max(len(lt), len(ct))
    lt += (0,) * (n - len(lt))
    ct += (0,) * (n - len(ct))
    if lt != ct:
        return lt > ct
    return _pre_rank(lp) > _pre_rank(cp)


def is_prerelease(v: str) -> bool:
    """版本是否带预览后缀（alpha/beta/rc/dev/preview…）。"""
    return _version_parts(v)[1] is not None


def check(timeout: float = 8.0) -> dict[str, Any]:
    current = __version__
    try:
        r = httpx.get(RELEASES_API, timeout=timeout, follow_redirects=True,
                      headers={"Accept": "application/vnd.github+json",
                               "User-Agent": f"MedKit/{current}"})
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 —— 网络/限流/无 Release(404) 都视为检查失败
        return {"current": current, "latest": None, "has_update": False,
                "html_url": RELEASES_PAGE, "notes": None, "error": "network"}
    latest = (data.get("tag_name") or "").strip().lstrip("vV")
    notes = (data.get("body") or "").strip()
    return {
        "current": current,
        "latest": latest or None,
        "has_update": bool(latest) and is_newer(latest, current),
        "prerelease": bool(latest) and is_prerelease(latest),
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "notes": notes[:NOTES_LIMIT] or None,
        "published_at": data.get("published_at"),
    }
