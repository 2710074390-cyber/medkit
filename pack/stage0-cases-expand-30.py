# -*- coding: utf-8 -*-
"""把题样从 20 道扩充到 30 道（对齐《总纲》§3.4 的 30 题口径）。

设计约束（全部由原 20 道实测反推，脚本会断言）：
  - 5 个选项，形如 "A. xxx"
  - user_answer != answer（这是错题集，不是答对集）
  - confidence ∈ {2, 3}
  - my_reasoning 长度 28~45 字
  - human_tag ∈ ANALYSIS_TAGS 的 6 类

补题方向：原 20 道里 诊断学/药理学/病理生理学/医学微生物学 各仅 1 道，
新增 10 道向这四个学科倾斜，同时把 6 类标签的计数拉平。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "pack" / "stage0_cases.json"

NEW = [
    {
        "id": "S21",
        "subject": "诊断学",
        "chapter": "体格检查",
        "topic": "肺部检查",
        "question": "大叶性肺炎实变期，患侧肺部叩诊音为",
        "options": [
            "A. 清音",
            "B. 浊音或实音",
            "C. 过清音",
            "D. 鼓音",
            "E. 空瓮音",
        ],
        "user_answer": "D",
        "answer": "B",
        "confidence": 2,
        "my_reasoning": "肺炎是炎症，里面应该有液体和气体混合，叩诊发空像敲鼓一样，所以选鼓音。",
        "human_tag": "概念偷换",
    },
    {
        "id": "S22",
        "subject": "诊断学",
        "chapter": "体格检查",
        "topic": "腹部检查",
        "question": "正常成人肝下缘在右锁骨中线上的位置是",
        "options": [
            "A. 肋缘下 1~2 cm",
            "B. 肋缘下 3~4 cm",
            "C. 肋缘下不能触及",
            "D. 剑突下 5 cm",
            "E. 平脐水平",
        ],
        "user_answer": "A",
        "answer": "C",
        "confidence": 3,
        "my_reasoning": "肝脏是人体最大的实质器官，这么大一块肯定能摸到边缘，一般就在肋缘下一点点，选A。",
        "human_tag": "记忆偏差",
    },
    {
        "id": "S23",
        "subject": "药理学",
        "chapter": "抗菌药物",
        "topic": "青霉素类",
        "question": "青霉素过敏性休克首选的抢救药物是",
        "options": [
            "A. 地塞米松",
            "B. 肾上腺素",
            "C. 异丙嗪",
            "D. 葡萄糖酸钙",
            "E. 去甲肾上腺素",
        ],
        "user_answer": "A",
        "answer": "B",
        "confidence": 2,
        "my_reasoning": "过敏性休克是过敏反应，激素抗过敏效果最强，地塞米松又是长效的，抢救应该首选它。",
        "human_tag": "机制混淆",
    },
    {
        "id": "S24",
        "subject": "药理学",
        "chapter": "心血管药物",
        "topic": "硝酸甘油",
        "question": "硝酸甘油缓解心绞痛的主要机制是",
        "options": [
            "A. 减慢心率，降低心肌收缩力",
            "B. 扩张静脉，减少回心血量，降低前负荷",
            "C. 阻断β受体，降低心肌耗氧",
            "D. 直接扩张冠状动脉，增加心肌供血",
            "E. 抑制血小板聚集，防止血栓形成",
        ],
        "user_answer": "D",
        "answer": "B",
        "confidence": 2,
        "my_reasoning": "心绞痛就是冠脉狭窄导致心肌缺血，那硝酸甘油的作用当然是直接扩张冠状动脉让血过去，选D。",
        "human_tag": "机制混淆",
    },
    {
        "id": "S25",
        "subject": "病理生理学",
        "chapter": "水电解质代谢紊乱",
        "topic": "低钾血症",
        "question": "低钾血症时骨骼肌的表现是",
        "options": [
            "A. 肌无力，甚至弛缓性麻痹",
            "B. 肌强直，腱反射亢进",
            "C. 肌无力，但腱反射亢进",
            "D. 肌强直，腱反射消失",
            "E. 无明显肌力改变",
        ],
        "user_answer": "B",
        "answer": "A",
        "confidence": 2,
        "my_reasoning": "血钾低说明细胞内钾相对多了，细胞更兴奋，肌肉当然收缩得更强，所以应该是肌强直。",
        "human_tag": "推理跳步",
    },
    {
        "id": "S26",
        "subject": "病理生理学",
        "chapter": "酸碱平衡紊乱",
        "topic": "代谢性酸中毒",
        "question": "代谢性酸中毒时机体的代偿调节主要依靠",
        "options": [
            "A. 肺通气量增加，排出 CO2",
            "B. 肾小管排 H+ 减少",
            "C. 红细胞内碳酸酐酶活性降低",
            "D. 组织细胞摄取 H+ 减少",
            "E. 血浆蛋白缓冲作用增强",
        ],
        "user_answer": "E",
        "answer": "A",
        "confidence": 2,
        "my_reasoning": "酸中毒就是酸多了，血浆蛋白是重要的缓冲系统，增强缓冲就能中和掉多余的酸，选E。",
        "human_tag": "知识盲区",
    },
    {
        "id": "S27",
        "subject": "医学微生物学",
        "chapter": "细菌的感染与免疫",
        "topic": "外毒素与内毒素",
        "question": "关于内毒素的叙述，正确的是",
        "options": [
            "A. 主要由革兰阳性菌产生",
            "B. 化学成分为蛋白质，不耐热",
            "C. 化学成分为脂多糖，耐热",
            "D. 经甲醛处理可制成类毒素",
            "E. 毒性强，具有高度特异性",
        ],
        "user_answer": "D",
        "answer": "C",
        "confidence": 3,
        "my_reasoning": "内毒素也是一种毒素，毒素一般都能用甲醛脱毒做成类毒素来打疫苗，所以应该选D。",
        "human_tag": "概念偷换",
    },
    {
        "id": "S28",
        "subject": "医学微生物学",
        "chapter": "病毒的基本性状",
        "topic": "病毒的结构",
        "question": "病毒的基本结构单位是",
        "options": [
            "A. 核衣壳",
            "B. 包膜",
            "C. 刺突",
            "D. 质粒",
            "E. 中介体",
        ],
        "user_answer": "B",
        "answer": "A",
        "confidence": 2,
        "my_reasoning": "病毒能致病主要是靠外面的包膜去吸附细胞，包膜没了病毒就死了，所以基本结构应该是包膜。",
        "human_tag": "知识盲区",
    },
    {
        "id": "S29",
        "subject": "外科学",
        "chapter": "体液代谢失调",
        "topic": "等渗性缺水",
        "question": "等渗性缺水时，机体最常出现的表现是",
        "options": [
            "A. 口渴明显，尿量减少",
            "B. 口渴不明显，尿量减少，血压下降",
            "C. 口渴明显，尿量正常",
            "D. 无口渴，尿量增多",
            "E. 口渴明显，血压升高",
        ],
        "user_answer": "A",
        "answer": "B",
        "confidence": 2,
        "my_reasoning": "缺水就是脱水，脱水肯定口渴、尿少，这是最典型的表现，不管哪种缺水都应该是这样。",
        "human_tag": "审题失误",
    },
    {
        "id": "S30",
        "subject": "生物化学",
        "chapter": "氨基酸代谢",
        "topic": "血氨的来源与去路",
        "question": "体内氨的主要去路是",
        "options": [
            "A. 合成尿素经肾排出",
            "B. 合成谷氨酰胺",
            "C. 重新合成氨基酸",
            "D. 合成嘌呤嘧啶",
            "E. 直接由肺呼出",
        ],
        "user_answer": "E",
        "answer": "A",
        "confidence": 3,
        "my_reasoning": "氨是气体啊，有刺激性气味，那在体内多了当然是跟着呼吸从肺排出去，所以选E。",
        "human_tag": "推理跳步",
    },
]

# 原 20 道的既有约束（脚本会断言新题也对齐）
EXPECT = {
    "n_options": 5,
    "reasoning_min": 28,
    "reasoning_max": 45,
    "confidence": {2, 3},
}


def _tag_pool():
    """从 schema 里取权威的 6 类标签，避免手写字面量漂移。"""
    sys.path.insert(0, str(ROOT))
    from medkit.core import schema as schema_mod

    return set(schema_mod.ANALYSIS_TAGS)


def _assert_case(c: dict, tags: set) -> list:
    """返回该题的问题清单（空 = 合规）。"""
    problems = []
    for k in ("id", "subject", "chapter", "topic", "question",
              "options", "user_answer", "answer", "confidence",
              "my_reasoning", "human_tag"):
        if k not in c:
            problems.append("缺字段 %s" % k)
    if len(c.get("options") or []) != EXPECT["n_options"]:
        problems.append("选项数 != %d" % EXPECT["n_options"])
    # 选项必须形如 "A. xxx"，且字母连续
    opts = c.get("options") or []
    want = "ABCDE"[: len(opts)]
    for i, o in enumerate(opts):
        if not o.startswith(want[i] + "."):
            problems.append("选项 %d 前缀不符：%r" % (i + 1, o[:8]))
    if c.get("user_answer") == c.get("answer"):
        problems.append("user_answer == answer（这是错题集）")
    if c.get("user_answer") not in want or c.get("answer") not in want:
        problems.append("答案字母越界")
    if c.get("confidence") not in EXPECT["confidence"]:
        problems.append("confidence 不在 %s" % EXPECT["confidence"])
    rl = len(c.get("my_reasoning") or "")
    if not (EXPECT["reasoning_min"] <= rl <= EXPECT["reasoning_max"]):
        problems.append("my_reasoning 长度 %d 不在 %d~%d"
                        % (rl, EXPECT["reasoning_min"], EXPECT["reasoning_max"]))
    if c.get("human_tag") not in tags:
        problems.append("human_tag %r 不在 %s" % (c.get("human_tag"), sorted(tags)))
    return problems


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if "--check" in argv:
        cases = json.loads(CASES.read_text(encoding="utf-8"))
    else:
        existing = json.loads(CASES.read_text(encoding="utf-8"))
        have = {c["id"] for c in existing}
        add = [c for c in NEW if c["id"] not in have]
        cases = existing + add
        CASES.write_bytes(
            (json.dumps(cases, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        print("已写入 %d 道（原有 %d + 新增 %d）" % (len(cases), len(existing), len(add)))

    tags = _tag_pool()
    bad = 0
    for c in cases:
        p = _assert_case(c, tags)
        if p:
            bad += 1
            print("[FAIL] %s: %s" % (c.get("id"), "; ".join(p)))
    ids = [c["id"] for c in cases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        bad += 1
        print("[FAIL] id 重复: %s" % sorted(dup))

    from collections import Counter

    print()
    print("总数: %d" % len(cases))
    print("学科: %s" % dict(Counter(c["subject"] for c in cases)))
    print("标签: %s" % dict(Counter(c["human_tag"] for c in cases)))
    print()
    print("校验: %s" % ("全部通过" if not bad else "%d 处不合规" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
