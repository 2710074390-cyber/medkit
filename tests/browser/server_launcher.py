"""MedKit 浏览器测试启动器（独立子进程）：隔离配置目录 + 启动 uvicorn。

为什么用独立子进程而不是同进程线程：
medkit 的核心模块（config / db / library / review / explain / tutor / state）都在
import 时把 `cfg.CONFIG_DIR` 派生的常量绑定到模块级变量（如 LIBRARY_DIR、DB_PATH、
OCR_JOB_DIR、PRESETS_DIR 等）。若在同进程先 import medkit 再改 config，这些模块常量
早已指向真实 ~/.medkit。所以真正的隔离必须在「import medkit.main 之前」重设 config 常量，
这用子进程最干净（父进程的 monkeypatch 只作用于父进程，互不干扰）。

用法（由 tests/browser/conftest.py 调用）：
    python tests/browser/server_launcher.py <port> <home_dir>

- <port>     ：随机空闲端口（仅监听 127.0.0.1 回环）。
- <home_dir> ：本测试的隔离配置根目录（CONFIG_DIR / CONFIG_FILE / PROMPTS_DIR_USER /
              PRESETS_DIR / projects_dir 全部重定向到这里，绝不触碰真实 ~/.medkit）。

启动完成后进入阻塞（uvicorn.Server.run()），由父进程探测 HTTP 200 判定就绪。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    port = int(sys.argv[1])
    home = sys.argv[2]

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # 关键顺序：先重设 config 常量，再 import medkit.main（及其全部依赖模块）。
    from medkit.core import config as cfg

    home_path = Path(home)
    cfg.CONFIG_DIR = home_path
    cfg.CONFIG_FILE = home_path / "config.json"
    cfg.PROMPTS_DIR_USER = home_path / "prompts"
    cfg.PRESETS_DIR = home_path / "presets"
    cfg.DEFAULTS["projects_dir"] = str(home_path / "projects")

    # 预置一份带 API Key 的配置：让前端「首启欢迎向导」不弹出（它仅在未配置 Key 时出现）。
    # 只写测试隔离目录，绝不触碰真实 ~/.medkit。dummy key 不会用于任何 LLM 调用。
    home_path.mkdir(parents=True, exist_ok=True)
    if not cfg.CONFIG_FILE.exists():
        cfg.CONFIG_FILE.write_text(
            json.dumps({
                "provider": "deepseek",
                "api_key": "sk-browser-test-dummy-not-a-real-key",
                "projects_dir": str(home_path / "projects"),
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    # 端口 / 不开浏览器（lifespan 在运行时读这两个环境变量）。
    os.environ["MEDKIT_PORT"] = str(port)
    os.environ["MEDKIT_NO_BROWSER"] = "1"

    import uvicorn

    from medkit.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
