#!/usr/bin/env python
"""S2-17（R8+W）：给 `requirements.lock` 生成 `--hash=sha256:` 钉版（标准库，仅维护者/CI 用）。

为什么需要：原 lock 只钉版本不钉哈希——同一个 `==x.y.z` 若被上游「重新上传」（或中间人投毒），
构建仍会静默接受，锁文件的完整性保证是不完整的。

做法：对 lock 里每个 `name==version`，查 PyPI JSON API 取该版本的**全部发布文件** sha256
（wheel/sdist、各平台各一份）——**必须列全平台**，否则在别的平台安装时 `--require-hashes`
会因「该平台产物没有已知哈希」而失败。

用法：
    python pack/gen-lock-hashes.py            # 重新生成 requirements.lock（含哈希）
    python pack/gen-lock-hashes.py --check    # 只校验：现有 lock 的哈希是否与 PyPI 一致（不写文件）

退出码：0=成功 / 1=失败（网络异常、版本不存在、或 --check 发现不一致）。
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "requirements.lock"
API = "https://pypi.org/pypi/{name}/{ver}/json"
_REQ = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)==(?P<ver>[^\s#]+)")


def _hashes_for(name: str, ver: str) -> list[str]:
    url = API.format(name=name, ver=ver)
    with urllib.request.urlopen(url, timeout=30) as r:   # noqa: S310  固定 https 主机
        data = json.loads(r.read().decode("utf-8"))
    out = []
    for f in data.get("urls", []):
        h = (f.get("digests") or {}).get("sha256")
        if h:
            out.append(h)
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    argv = list(argv if argv is not None else sys.argv[1:])
    check_only = "--check" in argv

    text = LOCK.read_text(encoding="utf-8")
    head = [ln for ln in text.splitlines() if ln.startswith("#") or not ln.strip()]
    reqs: list[tuple[str, str]] = []
    for ln in text.splitlines():
        m = _REQ.match(ln.strip())
        if m:
            reqs.append((m.group("name"), m.group("ver")))
    if not reqs:
        print("[失败] requirements.lock 里没有可解析的 name==version 行")
        return 1

    lines = list(head)
    bad: list[str] = []
    for name, ver in reqs:
        try:
            hs = _hashes_for(name, ver)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            bad.append(f"{name}=={ver}（{e}）")
            continue
        if not hs:
            bad.append(f"{name}=={ver}（PyPI 无该版本文件）")
            continue
        lines.append(f"{name}=={ver} \\")
        for i, h in enumerate(hs):
            sep = " \\" if i < len(hs) - 1 else ""
            lines.append(f"    --hash=sha256:{h}{sep}")
        print(f"  {name}=={ver}: {len(hs)} 个哈希")

    if bad:
        print("[失败] 以下条目无法取哈希，未写入（保持原文件不动）：")
        for b in bad:
            print("  -", b)
        return 1

    new_text = "\n".join(lines) + "\n"
    if check_only:
        if new_text == text:
            print(f"[通过] requirements.lock 的哈希与 PyPI 一致（{len(reqs)} 项）")
            return 0
        print("[失败] requirements.lock 的哈希与 PyPI 不一致——请重新运行本脚本生成")
        return 1

    LOCK.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"[完成] 已写入 {LOCK}（{len(reqs)} 项，含全平台哈希）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
