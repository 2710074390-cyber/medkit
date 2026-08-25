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


if __name__ == "__main__":
    port = pick_port()
    os.environ["MEDKIT_PORT"] = str(port)
    print(f"MedKit v{__version__} · 服务地址 http://127.0.0.1:{port}  （端口被占自动回退 4881-4889；关闭此窗口即退出）")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
