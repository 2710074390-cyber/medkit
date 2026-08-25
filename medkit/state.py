"""共享运行时状态（S2 拆分自 main.py）：项目运行锁 / OCR 任务表 / 记账占位。

只放「可变全局状态」；行为逻辑放 routers/*。测试可 import 此处直接操作。
"""

import threading

from .core import config as cfg

RUNNING: dict[str, threading.Event] = {}          # pid → 取消 Event（生成中）
RUN_LOCK = threading.Lock()

OCR_JOBS: dict[str, dict] = {}                    # job_id → job dict
OCR_LOCK = threading.Lock()
OCR_SEM = threading.Semaphore(2)                  # 同时并行 OCR 数上限（mineru 限频保护）
OCR_JOB_DIR = cfg.CONFIG_DIR / "ocr"
