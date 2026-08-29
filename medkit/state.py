"""共享运行时状态（S2 拆分自 main.py）：项目运行锁 / OCR 任务表 / 记账占位。

只放「可变全局状态」；行为逻辑放 routers/*。测试可 import 此处直接操作。
"""

import threading

from .core import config as cfg

RUNNING: dict[str, threading.Event] = {}          # pid → 取消 Event（生成中）
RUN_LOCK = threading.Lock()
CANCELLING: dict[str, bool] = {}                     # R3-09：pid → 已请求取消、尚未停稳（前端展示「正在取消中…」）

OCR_JOBS: dict[str, dict] = {}                    # job_id → job dict
OCR_LOCK = threading.Lock()
OCR_SEM = threading.Semaphore(2)                  # 同时并行 OCR 数上限（mineru 限频保护）
OCR_JOB_DIR = cfg.CONFIG_DIR / "ocr"


# ---------------------------------------------------------------- Feature flags（IMP-02）
# 回滚矩阵 §7 第一行：任一 WP 功能 → 独立 feature flag 关闭即整体下线（无需回滚 commit）。
# 读 ~/.medkit/config.json 的 features 节；缺省全 True 保持现状兼容。
FLAGS: dict[str, bool] = {}                       # 进程内覆盖（测试注入 / 临时关闭）；空 = 全部跟随 config


def flag(name: str) -> bool:
    """查询 WP 级 feature flag。优先 FLAGS 覆盖；否则读 config.json `features.{name}`，缺省 True。"""
    if name in FLAGS:
        return FLAGS[name]
    try:
        feats = (cfg.load().get("features") or {})
        return bool(feats.get(name, True))
    except Exception:  # noqa: BLE001  配置损坏等异常一律放行（开=现状兼容，不该因 flags 拖垮功能）
        return True
