"""在**隔离的 HOME** 下冒烟启动 MedKit 绿色版，验证产物真正可运行。

为什么必须隔离 HOME：
  2026-09-28 我直接启动解包产物做冒烟，它读了真实 `~/.medkit/`，
  `main._lifespan` 里的 `dbs.migrate()` 就地把你真实的库从 v7 升到 v8。
  零数据丢失（有自动备份），但这本可避免 —— **任何启动会写用户数据目录的
  程序的冒烟测试，都必须先把「家」指到临时目录**。

本脚本做三件事：
  1. 建临时 HOME，把 USERPROFILE / HOME / MEDKIT_LOG_DIR 全部指向它；
  2. 启动 `MedKit.exe`，轮询 http://127.0.0.1:<port>/ 直到就绪或超时；
  3. 打印就绪状态与临时目录内容，然后**优雅终止**（terminate + 等待 + kill 兜底）。

用法：
    python smoke_run_isolated.py <MedKit.exe 路径> [端口]

退出码：0 = 冒烟通过；1 = 未就绪/启动失败。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_PORT = 4880
READY_TIMEOUT_S = 60.0


def build_env(home: pathlib.Path, port: int) -> dict[str, str]:
    """构造把「家」整体重定向到 `home` 的子进程环境。

    `MEDKIT_PORT` 是**唯一的端口入口**（`main._local_port()` 读它，缺省 4880）——
    新进程不带这个变量时永远听 4880，传命令行参数是没用的（2026-09-28 踩过）。
    """
    env = dict(os.environ)
    env["USERPROFILE"] = str(home)          # Windows：expanduser("~") 读这个
    env["HOME"] = str(home)                 # 兼容 POSIX
    env["MEDKIT_LOG_DIR"] = str(home / "logs")
    env["MEDKIT_PORT"] = str(port)
    # 剥掉可能继承到的真实路径类变量，避免绕过隔离
    for k in ("MEDKIT_HOME", "MEDKIT_DATA_DIR"):
        env.pop(k, None)
    return env


def wait_ready_any(ports: range, timeout: float) -> tuple[bool, str, int | None]:
    """在 `ports` 区间里轮询 `/api/health`，返回 (是否就绪, 详情, 命中端口)。

    关键事实（2026-09-28 踩了两轮才搞清）：`MEDKIT_PORT` 是**出口不是入口**——
    `run_medkit.py` 的 `pick_port()` 硬扫 4880~4889 找到第一个空闲端口，然后
    `os.environ["MEDKIT_PORT"] = str(port)` **覆盖**外部传入值。所以：
      - 想让某个进程听特定端口是做不到的（除非改源码）；
      - 冒烟必须**扫整个区间**去发现它到底听了哪个端口。

    另两个已踩的坑：
      ① 必须绕开代理（环境里有 `http_proxy=127.0.0.1:62171`，会回 502 误导判断）；
      ② 探活端点用 `/api/health`，不要用根路径。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_err = "（未尝试）"
    while time.monotonic() < deadline:
        for port in ports:
            url = f"http://127.0.0.1:{port}/api/health"
            try:
                with opener.open(url, timeout=1.5) as resp:
                    body = resp.read(8192).decode("utf-8", "replace")
                    return True, f"HTTP {resp.status} @ :{port}；响应：{body[:200]!r}", port
            except urllib.error.HTTPError as e:      # 有响应即算活着
                return True, f"HTTP {e.code} @ :{port}（有响应即视为已就绪）", port
            except Exception as e:  # noqa: BLE001  任何异常都只作「这个端口还没起」
                last_err = f":{port} {type(e).__name__}: {e}"
        time.sleep(1.0)
    return False, f"超时 {timeout:.0f}s，最后错误：{last_err}", None


def main(argv: list[str]) -> int:
    # Windows 控制台常为 cp1252 而本脚本输出中文——强制 UTF-8，避免 UnicodeEncodeError（R6-11 CI 实证）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    if len(argv) < 2:
        print(__doc__)
        return 2
    exe = pathlib.Path(argv[1]).resolve()
    port = int(argv[2]) if len(argv) > 2 else DEFAULT_PORT
    keep_home = "--keep-home" in argv

    if not exe.exists():
        print(f"[失败] 找不到可执行文件：{exe}")
        return 1

    home = pathlib.Path(tempfile.mkdtemp(prefix="medkit-smoke-home-"))
    print(f"隔离 HOME = {home}")
    print(f"目标 exe  = {exe}")
    print(f"端口      = {port}")
    print()

    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [str(exe)],
            env=build_env(home, port),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(exe.parent),
        )
        print(f"已启动 PID={proc.pid}，在 127.0.0.1:4880-4889 区间等待就绪…")
        ok, detail, hit_port = wait_ready_any(range(4880, 4890), READY_TIMEOUT_S)
        print(f"[{'通过' if ok else '失败'}] 就绪探测：{detail}")
        if hit_port is not None:
            print(f"命中端口：{hit_port}（与服务自身 pick_port() 的选择一致）")

        print()
        print("隔离 HOME 内生成的内容（证明它确实没有写真实目录）：")
        for root, _dirs, files in os.walk(home):
            rel = pathlib.Path(root).relative_to(home)
            for f in sorted(files)[:40]:
                p = pathlib.Path(root) / f
                print(f"    {rel / f}  ({p.stat().st_size} bytes)")

        log = home / "logs" / "medkit.log"
        if log.exists():
            print()
            print(f"--- {log.relative_to(home)} 内容 ---")
            print(log.read_text(encoding="utf-8", errors="replace").strip()[:3000] or "（空）")
        return 0 if ok else 1
    finally:
        if proc is not None and proc.poll() is None:
            print()
            print("优雅终止：terminate → 等待 5s → kill 兜底")
            proc.terminate()
            try:
                proc.wait(timeout=5)
                print("  已退出（terminate 生效）")
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                print("  已退出（kill 兜底生效）")
        if keep_home:
            print(f"[保留] 隔离 HOME 未清理：{home}（--keep-home）")
        else:
            shutil.rmtree(home, ignore_errors=True)
            print(f"已清理隔离 HOME：{home}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
