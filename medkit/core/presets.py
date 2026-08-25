"""配置预设（可玩性 2C）：内置三套 + 用户自建（~/.medkit/presets/）。

预设 = JSON 快照：{id, name, desc, payload{exam,target,ratios,bloom,knobs,requirements}}
内置不可删；用户预设可增删；分享 = 导出/导入 JSON 文件（纯前端）。
"""

import json
import time
from typing import Any

from . import config as cfg

BUILTINS: list[dict[str, Any]] = [
    {
        "id": "final_speedrun", "name": "期末速通", "builtin": True,
        "desc": "高频考点优先，基础题为主，适合考前一周",
        "payload": {
            "exam": "期末", "target": 100,
            "ratios": {"A1": 50, "A2": 30, "B1": 10, "X": 10},
            "bloom": {"记忆": 40, "理解": 40, "应用": 15, "创造": 5},
            "knobs": {"difficulty": "basic", "analysis_style": "snappy"},
            "requirements": "优先覆盖教师重点中的数值与诊断标准",
        },
    },
    {
        "id": "kaoyan_boost", "name": "考研西综强化", "builtin": True,
        "desc": "应用占比高 + 临床综合难度，贴近真题风格",
        "payload": {
            "exam": "考研西综", "target": 100,
            "ratios": {"A1": 35, "A2": 45, "B1": 10, "X": 10},
            "bloom": {"记忆": 25, "理解": 35, "应用": 35, "创造": 5},
            "knobs": {"difficulty": "clinical", "analysis_style": "detailed", "stem_style": "narrative"},
            "requirements": "每道 A2 题给出病例与鉴别要点；数值题给出标准阈值",
        },
    },
    {
        "id": "licheng_sprint", "name": "执医冲刺", "builtin": True,
        "desc": "病例题占比高，题干叙事化，贴合执业医师风格",
        "payload": {
            "exam": "执业医师", "target": 100,
            "ratios": {"A1": 30, "A2": 50, "B1": 10, "X": 10},
            "bloom": {"记忆": 20, "理解": 30, "应用": 45, "创造": 5},
            "knobs": {"difficulty": "clinical", "analysis_style": "snappy", "stem_style": "narrative"},
            "requirements": "题干为完整病例（主诉+查体+辅检），问题为诊断/首选检查/治疗",
        },
    },
]


def list_presets() -> dict[str, list[dict[str, Any]]]:
    customs = []
    if cfg.PRESETS_DIR.exists():
        for p in sorted(cfg.PRESETS_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                d.setdefault("builtin", False)
                customs.append(d)
            except Exception:  # noqa: BLE001
                continue
    return {"builtins": BUILTINS, "customs": customs}


def save_preset(name: str, desc: str, payload: dict[str, Any]) -> dict[str, Any]:
    cfg.PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    pid = f"u{int(time.time() * 1000)}"
    data = {"id": pid, "name": (name or "未命名").strip()[:30], "desc": (desc or "").strip()[:80],
            "payload": payload, "builtin": False}
    (cfg.PRESETS_DIR / f"{pid}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def delete_preset(pid: str) -> bool:
    """返回是否删除成功；内置预设与无效 id 返回 False。"""
    if any(b["id"] == pid for b in BUILTINS):
        return False
    f = cfg.PRESETS_DIR / f"{pid}.json"
    if f.exists():
        f.unlink()
        return True
    return False
