"""MedKit 入口：启动本地服务（浏览器由服务启动完成后自动打开）。

S5（2026-08 审计）：启动前 socket 探测 4880~4889，端口被占时自动回退到空闲端口，
并把实际端口经 MEDKIT_PORT 传给 main（Host 校验与浏览器打开联动）。

打包说明：PyInstaller 需要直接 import app 对象（而非字符串导入），
保证 medkit.main 及其依赖被静态分析捕获。
"""
import os
import socket

import uvicorn

from medkit import __version__
from medkit.main import app

DEFAULT_PORT = 4880
MAX_PORT = 4889


def pick_port(start: int = DEFAULT_PORT, end: int = MAX_PORT) -> int:
    """返回第一个空闲端口；全部占用则返回 DEFAULT_PORT（由 uvicorn 报错提示）。"""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return DEFAULT_PORT


def _busy(port: int) -> bool:
    """该端口当前是否已被占用（用于区分「4880 空闲」与「全部端口被占」）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _acquire_instance_lock() -> object | None:
    """单实例锁：~/.medkit/app.lock 独占锁；已有实例运行 → 返回 None。

    非 Windows / 锁不可用环境返回 None 交由调用方区分（此处 None 即「已占用」，
    仅在锁机制可用时才判定占用；不可用（异常）返回 None 且不阻塞——由 caller 判断）。
    """
    try:
        from medkit.core import config as _cfg

        p = _cfg.CONFIG_DIR / "app.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a+b")
        try:
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        except OSError:
            f.close()
            return None
    except (ImportError, AttributeError):
        return object()  # 锁机制不可用（非 Windows）→ 放行，不做单实例强制
    except Exception:  # noqa: BLE001  其他锁异常（沙箱/权限）→ 放行，不阻塞启动
        return object()


if __name__ == "__main__":
    # 单实例：防止重复双击起第二个进程共享 ~/.medkit 数据（并发写风险）
    inst_lock = _acquire_instance_lock()
    if inst_lock is None:
        print("⚠️ MedKit 已在运行（单实例锁 ~/.medkit/app.lock）。")
        print("   请切换到已打开的 MedKit 窗口；若确认没有实例，请删除该锁文件后重试。")
        try:
            input("按回车键退出…")
        except EOFError:
            pass
        raise SystemExit(1)
    port = pick_port()
    os.environ["MEDKIT_PORT"] = str(port)
    # S5 补丁（2026-08 审查）：全部端口被占时给可读提示而不是 uvicorn 裸抛 traceback
    if _busy(port):
        print("⚠️ 端口 4880~4889 均已被其他程序占用，无法启动。")
        print("   请关闭占用的程序（或任务管理器结束相关进程）后重试。")
        try:
            input("按回车键退出…")
        except EOFError:
            pass
        raise SystemExit(1)
    print(f"MedKit v{__version__} · 服务地址 http://127.0.0.1:{port}  （端口被占自动回退 4881-4889；关闭此窗口即退出）")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
