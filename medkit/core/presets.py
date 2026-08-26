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
        "desc": "考前 1~2 周冲刺：高频考点优先、记忆/理解占 8 成，A1 直问为主（检索练习强化）",
        "payload": {
            "exam": "期末", "target": 100,
            "ratios": {"A1": 50, "A2": 30, "B1": 10, "X": 10},
            "bloom": {"记忆": 40, "理解": 40, "应用": 15, "创造": 5},
            "knobs": {"difficulty": "basic", "analysis_style": "examkey", "stem_style": "direct"},
            "requirements": "严格按教师重点出题；优先覆盖数值、诊断标准、首选药；每题解析末尾加一行【易错点】；不出现教材未提及的超纲内容。",
        },
    },
    {
        "id": "kaoyan_boost", "name": "考研西综强化", "builtin": True,
        "desc": "贴近西综真题：病例题 45%、应用层 35%，详尽机制解析（考点→机制→鉴别）",
        "payload": {
            "exam": "考研西综", "target": 100,
            "ratios": {"A1": 35, "A2": 45, "B1": 10, "X": 10},
            "bloom": {"记忆": 25, "理解": 35, "应用": 35, "创造": 5},
            "knobs": {"difficulty": "clinical", "analysis_style": "detailed", "stem_style": "narrative"},
            "requirements": "A2 题干给完整病例（主诉+现病史+关键查体），问题限定「诊断/首选检查/治疗」；数值题给出标准阈值与单位；解析按「考点→机制→易错」三段展开，末尾附一行记忆锚点。",
        },
    },
    {
        "id": "licheng_sprint", "name": "执医冲刺", "builtin": True,
        "desc": "执医风格模拟卷：病例题过半、应用层近半，速记解析 + 病例叙事题干",
        "payload": {
            "exam": "执业医师", "target": 100,
            "ratios": {"A1": 30, "A2": 50, "B1": 10, "X": 10},
            "bloom": {"记忆": 20, "理解": 30, "应用": 45, "创造": 5},
            "knobs": {"difficulty": "clinical", "analysis_style": "snappy", "stem_style": "narrative"},
            "requirements": "题干为完整病例（主诉+现病史+查体+辅检），问题限定诊断/首选检查/治疗方案；干扰项为同系统常见误诊；解析末尾一行【考点速记】。",
        },
    },
    {
        "id": "gap_killer", "name": "查漏歼灭（二轮）", "builtin": True,
        "desc": "错题重练/二刷：挑战难度 + 多选加量，鉴别对比解析（间隔重复强化）",
        "payload": {
            "exam": "错题重练", "target": 60,
            "ratios": {"A1": 25, "A2": 40, "B1": 15, "X": 20},
            "bloom": {"记忆": 15, "理解": 25, "应用": 45, "创造": 15},
            "knobs": {"difficulty": "challenge", "analysis_style": "compare", "stem_style": "data"},
            "requirements": "聚焦易混疾病/药物/检验指标的横向对比；X 型题干扰项须来自同一系统；解析以「最易混淆对象→关键区别→记忆口诀」呈现；数值题给临界值与边界分析。",
        },
    },
    {
        "id": "teaching_bank", "name": "教研自命题（教学版）", "builtin": True,
        "desc": "教师命题/课堂测验：认知层级均衡覆盖，教学型解析含干扰项设计意图",
        "payload": {
            "exam": "教学测验", "target": 50,
            "ratios": {"A1": 40, "A2": 35, "B1": 15, "X": 10},
            "bloom": {"记忆": 30, "理解": 30, "应用": 30, "创造": 10},
            "knobs": {"difficulty": "basic", "analysis_style": "teaching", "stem_style": "staged"},
            "requirements": "按教学大纲均衡覆盖各章节；解析结论先行，再逐项说明每个干扰项的设计意图；渐进披露题干分阶段给出临床信息，考察诊疗决策的更新能力。",
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
