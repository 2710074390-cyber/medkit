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


def _version_tuple(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    parts: list[int] = []
    for seg in v.split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    lt, ct = _version_tuple(latest), _version_tuple(current)
    n = max(len(lt), len(ct))
    lt += (0,) * (n - len(lt))
    ct += (0,) * (n - len(ct))
    return lt > ct


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
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "notes": notes[:NOTES_LIMIT] or None,
        "published_at": data.get("published_at"),
    }
