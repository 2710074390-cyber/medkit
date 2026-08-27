"""P1 迭代2 离线管线测试：FakeLLM 注入 orchestrator，全链路（含门禁修复循环）跑通。

运行：python -m pytest tests/test_pipeline_offline.py -q（或 python tests/test_pipeline_offline.py）
不发起任何真实 LLM 调用。

S0（2026-08-25）：原本 main() 内嵌套用例改为模块级 test_ 函数（pytest 收集数为 0 的问题），
配置目录改为每次用例独立临时目录（不再写真实 ~/.medkit）。
"""

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core.orchestrator import run_project  # noqa: E402


class FakeLLM:
    """按消息内容返回对应阶段的 canned 输出；捕获 system 供注入断言（可玩性）。"""

    def __init__(self, role: str):
        self.role = role
        self.calls = 0
        self.systems: list[str] = []

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.systems.append(messages[0] and messages[0].get("content", ""))
        if self.role == "review":
            return ("# 儿科复习手册\n\n## 一、考点速记表\n\n| 考点 | 核心要点 |\n|---|--|\n| 生长发育 | 两个高峰 |\n\n"
                    "## 三、临床思维路径\n\n1. 症状→鉴别\n")
        raise AssertionError(f"{self.role} 不应调用 chat()")

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        self.systems.append(messages[0] and messages[0].get("content", ""))
        content = "\n".join(m.get("content", "") for m in messages)
        if self.role == "gen":
            n = 12 if "本切片题数：12 题" in content else 8
            qs = []
            for i in range(n):
                # 固定坏题：截断选项 + 无溯源 + bloom 非法（确保触发门禁修复循环）
                bad = (i == 0)
                qs.append({
                    "type": "A1" if bad else "A1", "bloom": "未知" if bad else ("记忆" if i % 2 == 0 else "理解"),
                    "subtopic": "生长发育规律",
                    "question": f"关于生长发育，下列正确的是{i}？",
                    "options": ["支气管哮" if bad else f"选项{i}A", "选项B", "选项C", "选项D", "选项E"],
                    "answer": "B",
                    "analysis": "解析无溯源" if bad else f"机制解析{i}。【源:切片S001】",
                })
            return {"questions": qs}
        if self.role == "qc":
            return {"score": 82, "gate_decision": "PASS_WITH_FIXES",
                    "issues": [{"q_id": "Q001", "code": "D18", "severity": "warn",
                                "reason": "轻微", "suggest": "无"}],
                    "summary": "整体良好。"}
        if self.role == "fix":
            return {"questions": [{
                "id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "生长发育规律",
                "question": "关于生长发育规律，正确的是？",
                "options": ["选项A", "选项B", "选项C", "选项D", "选项E"],
                "answer": "B", "analysis": "修复后解析。【源:切片S001】"}]}
        raise AssertionError(f"未知角色 {self.role}")


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """每次用例隔离配置目录：替换 CONFIG_DIR 及其派生路径 + projects_dir 指向 tmp。

    参照 test_api.py 的 TMP_DIR 模式；test_ 完成后 monkeypatch 自动还原，目录由 pytest 清理。
    """
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    orig_load = cfgmod.load

    def _isolated_load():
        cfg = orig_load()
        cfg["projects_dir"] = str(tmp_path / "projects")
        return cfg

    monkeypatch.setattr(cfgmod, "load", _isolated_load)
    return tmp_path


def build_project(pid: str = "_pipeline_test") -> str:
    """在临时 projects 目录建一个样例项目（切片+配额），返回 pid。"""
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    slices = [
        {"sid": "S001", "title": "第一章 生长发育", "text": "生长发育有三个高峰：婴儿期、青春期……"
         "出生体重3.25kg，1岁10kg。", "role": "textbook"},
        {"sid": "S002", "title": "第二章 儿童营养", "text": "能量需求110kcal/kg，母乳SIgA……",
         "role": "textbook"},
        {"sid": "T001", "title": "教师重点", "text": "生长发育 体重 3.25kg 辅食 由少到多", "role": "teacher"},
    ]
    (tmp / "slices.json").write_text(json.dumps(slices, ensure_ascii=False), encoding="utf-8")
    from medkit.core.quota import allocate  # noqa: E402
    quota = [{**q, "title": ("第一章 生长发育" if q["sid"] == "S001" else "第二章 儿童营养")}
             for q in allocate([s for s in slices if s["role"] == "textbook"],
                               "\n".join(s["text"] for s in slices[2:]), 20)]
    meta = {"pid": pid, "subject": "儿科学（离线测试）", "exam": "期末", "target": 20,
            "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
            "toggles": {"qbank": True, "paper": True, "review": True},
            "stage": "quota", "quota": quota, "seed": 42,
            "created": "2026-08-25T00:00:00"}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return pid


def _overrides() -> dict:
    return {"gen": FakeLLM("gen"), "qc": FakeLLM("qc"),
            "fix": FakeLLM("fix"), "review": FakeLLM("review")}


def test_orchestrator_end_to_end(isolated_cfg):
    """FakeLLM 全链路（含门禁修复循环）：出题→门禁→质检→渲染，产物齐全。"""
    pid = build_project()
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid

    res = run_project(pid, overrides=_overrides())
    assert res["stage"] == "done", res
    assert res["questions"] >= 15, res
    # 门禁修复循环后，坏题 Q001 应被修复
    final = json.loads((tmp / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    q1 = next(q for q in final if q["id"] == "Q001")
    assert "【源:切片S001】" in q1["analysis"], "Q001 修复后应补齐溯源"
    assert len(q1["options"]) == 5
    # 产物齐全
    for name in ("qbank.md", "qbank.html", "押题卷.html", "复习手册.md", "复习手册.html",
                 "追溯日志.md", "anki_export.txt"):
        assert (tmp / "最终产物" / name).exists(), f"缺产物 {name}"
    # S3：.apkg 真包随管线生成
    assert any(p.suffix == ".apkg" for p in (tmp / "最终产物").iterdir()), "缺 .apkg 产物"
    # 质检报告
    qc = json.loads((tmp / "质检报告" / "质检报告.json").read_text(encoding="utf-8"))
    assert qc["gate_decision"] == "PASS_WITH_FIXES"


def test_resume_from_checkpoint(isolated_cfg, monkeypatch):
    """U2 断点续跑：手动构造 checkpoint（S001 已完成）→ 重跑应跳过 S001 并沿用题目。"""
    import medkit.agents.medgen as mg
    called: list[str] = []
    orig_gen = mg.generate_slice

    def spy(client, subject, exam, slice_, count, ratios, teacher_text,
            ids_start=1, requirements="", knobs=None, bloom=None,
            web_materials="", web_quota=0, exam_text="", extra_text="",
            syllabus_text="", image_sections=""):
        called.append(str(slice_.get("sid", "")))
        return orig_gen(client, subject, exam, slice_, count, ratios, teacher_text,
                        ids_start=ids_start, requirements=requirements, knobs=knobs,
                        bloom=bloom, web_materials=web_materials, web_quota=web_quota,
                        exam_text=exam_text, extra_text=extra_text,
                        syllabus_text=syllabus_text, image_sections=image_sections)

    monkeypatch.setattr(mg, "generate_slice", spy)
    pid2 = build_project("_resume_test")
    tmp2 = Path(cfgmod.CONFIG_DIR) / "projects" / pid2
    quota = json.loads((tmp2 / "meta.json").read_text(encoding="utf-8"))["quota"]
    # 与 orchestrator 相同的预分配编号规则：S001 是第一条
    c1 = quota[0]["count"]
    n_ck = min(2, c1)
    ck_qs = []
    for i in range(n_ck):
        ck_qs.append({
            "id": f"Q{i + 1:03d}", "type": "A1",
            "bloom": "记忆" if i % 2 == 0 else "理解", "subtopic": "生长发育",
            "question": f"关于生长发育规律，正确的是（断点题{i}）？",
            "options": ["甲型正确", "乙型正确", "丙型正确", "丁型正确", "戊型正确"],
            "answer": "A", "analysis": f"断点题解析{i}。【源:切片S001】", "sid": "S001",
            "module": "第一章 生长发育"})
    (tmp2 / "中间产物").mkdir(exist_ok=True)
    (tmp2 / "中间产物" / "checkpoint.json").write_text(json.dumps(
        {"done_sids": ["S001"], "questions": ck_qs}, ensure_ascii=False), encoding="utf-8")
    res = run_project(pid2, overrides=_overrides())
    assert res["stage"] == "done", res
    assert "S001" not in called, f"断点切片 S001 不应重跑（实际调用：{called}）"
    assert called and "S002" in called, "未继续生成未完成切片"
    final = json.loads(
        (tmp2 / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    assert res["questions"] == len(final) >= n_ck + 1
    # 断点题被 MedFix 重写也允许；但 S001 的题目总数不应超过断点题数量
    s001_cnt = sum(1 for q in final if q.get("sid") == "S001")
    assert s001_cnt <= n_ck, f"S001 题目被重复生成：{s001_cnt} > {n_ck}"


def test_cancel_early_and_resume(isolated_cfg):
    """U1 取消：预置 cancel Event → 管线返回 cancelled 且保留断点；再跑续完。"""
    import threading
    pid3 = build_project("_cancel_test")
    tmp3 = Path(cfgmod.CONFIG_DIR) / "projects" / pid3
    ev = threading.Event()
    ev.set()
    res = run_project(pid3, cancel=ev, overrides=_overrides())
    assert res["stage"] == "cancelled", res
    meta = json.loads((tmp3 / "meta.json").read_text(encoding="utf-8"))
    assert meta["stage"] == "cancelled"
    res2 = run_project(pid3, overrides=_overrides())
    assert res2["stage"] == "done", res2


def test_gates_rules():
    """门禁规则：选项/溯源/Bloom/查重（默认配比零回归）。"""
    from medkit.gates.bloom_check import check_bloom
    from medkit.gates.options_check import check_all
    from medkit.gates.trace_check import check_trace
    bad = [{"id": "Q1", "type": "A1", "bloom": "理解", "question": "题",
            "options": ["支气管哮", "哮喘", "C型", "D型", "E型"], "answer": "A",
            "analysis": "无"}]
    r = check_all(bad)
    assert any(x["code"] in ("R7", "R8", "R13", "F2") for x in r["issues"]), r["issues"]
    assert any(x["code"] == "R1" for x in check_all(
        [dict(bad[0], options=["a", "b"])])["issues"])
    b = check_bloom([{"bloom": v} for v in ["记忆"] * 50 + ["理解"]] * 6)
    assert b["fail_count"] >= 1, "记忆 90%+ 应触发 Bloom fail"
    # 可玩性 2B：自定义配比参数化（默认行为零回归）
    b2 = check_bloom([{"bloom": "记忆"}] * 5 + [{"bloom": "理解"}] * 5,
                     {"记忆": 0.5, "理解": 0.5, "应用": 0.0, "创造": 0.0})
    assert b2["fail_count"] == 0, f"50/50 对自定义目标应通过：{b2['issues']}"
    t = check_trace([{"id": "X", "analysis": "……【源:切片S999】"}], {"S001"})
    assert t["fail_count"] == 1


def test_playability_inject(isolated_cfg):
    """迭代1/2：附加要求 + 旋钮 + Bloom 自定义 → system 末尾注入。"""
    pid = build_project("_play_test")
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
    meta["requirements"] = "多出计算题，解析末尾给易错点"
    meta["knobs"] = {"difficulty": "clinical", "analysis_style": "snappy"}
    meta["bloom"] = {"记忆": 40, "理解": 40, "应用": 15, "创造": 5}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    gen = FakeLLM("gen")
    res = run_project(pid, overrides={
        "gen": gen, "qc": FakeLLM("qc"), "fix": FakeLLM("fix"), "review": FakeLLM("review")})
    assert res["stage"] == "done", res
    sys0 = gen.systems[0]
    assert "多出计算题" in sys0, "附加要求应注入 system"
    assert sys0.rstrip().endswith("易错点"), "注入位置应在 system 末尾"
    assert "难度定位：临床综合题" in sys0, "旋钮片段应注入"
    assert "40% / 40% / 15% / 5%" in sys0, "Bloom 自定义配比应替换占位符"


def test_websearch_rounds_offline():
    """§5.4 离线：多轮循环（注入 search_fn）+ conflict 标记 + manual 解析。"""
    from medkit.core import websearch as wsmod

    class QC:
        def chat_json(self, messages, **kwargs):
            user = messages[-1].get("content", "")
            if "网络检索素材" in user:
                return {"conflict": [0]}
            if "已检索结果" in user:
                return {"queries": ["补充检索词"]}
            return {"queries": ["考纲查询", "真题查询", "指南查询"]}

    def fn(q: str) -> list[dict]:
        return [{"title": q, "url": f"https://a.example.com/{q}",
                 "snippet": f"内容 {q}（标准值 3.25kg）"}]

    res = wsmod.run_search_rounds(QC(), "儿科学", "生长发育", "体重 3.25kg",
                                  "bocha", search_fn=fn,
                                  slices_digest="教材切片：体重3.25kg")
    assert len(res["materials"]) >= 3, res["materials"]
    assert any(m.get("conflict") for m in res["materials"]), "应标记冲突"
    assert "https://a.example.com" in wsmod.digest_for_prompt(res["materials"])
    manual = wsmod.parse_manual("标题一 https://x.example.com/a\n任意一段文字，可作素材")
    assert len(manual) == 2
    assert wsmod.parse_manual("https://www.bilibili.com/video/1") == [], "视频站应被过滤"


def test_websearch_pipeline(isolated_cfg, monkeypatch):
    """§5.4 接入管线：检索结果落盘 + 参考素材注入 + conflict 复核清单。"""
    import medkit.core.orchestrator as orch
    pid = build_project("_web_pipe_test")
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
    meta["web_search"] = True
    meta["web_ref_quota"] = 10
    meta["web_backend"] = "bocha"
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    fake_materials = [
        {"title": "指南A", "url": "https://guide.example.com/a", "snippet": "标准值 3.25kg", "round": 1},
        {"title": "冲突B", "url": "https://x.example.com/b", "snippet": "出生体重 4kg", "round": 2,
         "conflict": True},
    ]
    monkeypatch.setattr(orch.ws, "run_search_rounds",
                        lambda *a, **k: {"materials": fake_materials,
                                         "logs": ["fake log"], "errors": []})
    gen = FakeLLM("gen")
    res = run_project(pid, overrides={
        "gen": gen, "qc": FakeLLM("qc"), "fix": FakeLLM("fix"),
        "review": FakeLLM("review")})
    assert res["stage"] == "done", res
    mats = json.loads((tmp / "网络参考素材.json").read_text(encoding="utf-8"))
    assert len(mats) == 2 and mats[1].get("conflict") is True
    sys0 = gen.systems[0]
    assert "参考素材" in sys0 and "guide.example.com" in sys0, "素材应注入 system"
    assert (tmp / "人工复核清单.md").exists(), "conflict 应生成复核清单"


def test_render_precheck_drop_e2e(isolated_cfg):
    """D2 全链路：门禁修复轮用尽后仍超限的题（选项数 >6）→ 剔除出产物 + 人工复核清单。"""
    import re

    class GenWithOverLimit(FakeLLM):
        def __init__(self):
            super().__init__("gen")

        def chat_json(self, messages, **kwargs):
            s = super().chat_json(messages, **kwargs)
            # 把本批最后一道题改成 7 个选项（超渲染上限；id 由 orchestrator 生成后分配）
            if len(s["questions"]) >= 6:
                q = s["questions"][-1]
                q["options"] = ["超额A", "超额B", "超额C", "超额D", "超额E", "超额F", "超额G"]
            return s

    pid = build_project("_drop_test")
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    res = run_project(pid, overrides={
        "gen": GenWithOverLimit(), "qc": FakeLLM("qc"),
        "fix": FakeLLM("fix"), "review": FakeLLM("review")})
    assert res["stage"] == "done", res
    final = json.loads((tmp / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    assert all(len(q.get("options", [])) <= 6 for q in final), "产物中不应保留超限题"
    review = (tmp / "人工复核清单.md").read_text(encoding="utf-8")
    assert "渲染前剔除" in review and "选项数 7 > 6" in review, "超限原因应写入复核清单"
    assert re.search(r"\*\*Q\d{3}\*\*", review), "复核清单应记录被剔除题目的 id"
    # 其余产物正常生成
    for name in ("qbank.md", "qbank.html", "押题卷.html", "复习手册.md", "anki_export.txt"):
        assert (tmp / "最终产物" / name).exists(), f"缺产物 {name}"


def test_websearch_cancel_marks_incomplete(isolated_cfg, monkeypatch):
    """F2：检索中途取消 → 落盘标记 incomplete；续跑重新检索不复用残缺结果。"""
    import medkit.core.orchestrator as orch

    pid = build_project("_web_cancel_test")
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
    meta["web_search"] = True
    meta["web_backend"] = "bocha"
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    ev = threading.Event()

    def partial_search(*a, **k):
        ev.set()  # 检索进行中被取消
        return {"materials": [{"title": "部分结果", "url": "https://x.example.com/a",
                               "snippet": "s"}], "logs": [], "errors": []}

    monkeypatch.setattr(orch.ws, "run_search_rounds", partial_search)
    res = run_project(pid, cancel=ev, overrides=_overrides())
    assert res["stage"] == "cancelled", res
    assert (tmp / "网络参考素材.incomplete").exists(), "取消后应留下 incomplete 标记"
    assert (tmp / "网络参考素材.json").exists(), "残缺结果已落盘（但标记不完整）"

    calls = []

    def fresh_search(*a, **k):
        calls.append(1)
        return {"materials": [], "logs": ["重新检索"], "errors": []}

    monkeypatch.setattr(orch.ws, "run_search_rounds", fresh_search)
    res2 = run_project(pid, overrides=_overrides())  # 续跑（新 cancel Event）
    assert res2["stage"] == "done", res2
    assert calls, "续跑应重新检索（不得复用残缺缓存）"
    assert not (tmp / "网络参考素材.incomplete").exists(), "完整检索后应清除标记"


def test_case_group_full_chain(isolated_cfg):
    """S3：FakeLLM 产出案例组（A3）+ B1 选项组 → 门禁 → QC → 渲染全链路（D1 扁平结构）。"""
    class GenWithCases(FakeLLM):
        def __init__(self):
            super().__init__("gen")

        def chat_json(self, messages, **kwargs):
            s = super().chat_json(messages, **kwargs)
            qs = s["questions"]
            if len(qs) >= 12:  # 首个切片（12 题）：前 3 道为案例组，第 10 道为 B1 组
                stem = "患儿男，3岁，发热3天，皮疹1天，口唇干裂，结膜充血，精神差…"
                subs = ["该患儿最可能的诊断是？", "该患儿首选检查是？", "该患儿的致病病原最可能是？"]
                for j in range(3):
                    qs[j].update({
                        "type": "A3", "case_id": "C001", "case_order": j + 1,
                        "case_stem": stem, "group_kind": "case",
                        "question": subs[j], "bloom": "理解",
                        "analysis": f"案例解析{j}。【源:切片S001】"})
                qs[9].update({
                    "type": "B1", "options": [], "group_kind": "option_group",
                    "group": {"options": ["支原体", "肺炎链球菌", "腺病毒",
                                          "呼吸道合胞病毒", "金黄色葡萄球菌"]},
                    "question": "上呼吸道感染最常见的病原？", "bloom": "记忆",
                    "analysis": "B1 解析。【源:切片S001】"})
            return s

    pid = build_project("_case_test")
    tmp = Path(cfgmod.CONFIG_DIR) / "projects" / pid
    res = run_project(pid, overrides={
        "gen": GenWithCases(), "qc": FakeLLM("qc"),
        "fix": FakeLLM("fix"), "review": FakeLLM("review")})
    assert res["stage"] == "done", res
    final = json.loads((tmp / "最终产物" / "questions_final.json").read_text(encoding="utf-8"))
    cases = [q for q in final if q.get("case_id") == "C001"]
    assert len(cases) == 3, f"案例组应保留 3 道子题：{len(cases)}"
    assert all(q["group_kind"] == "case" and q["case_stem"] for q in cases), "D1：扁平+冗余 case_stem"
    assert sorted(q["case_order"] for q in cases) == [1, 2, 3]
    assert all(q["type"] == "A3" for q in cases), "MedFix 合并策略应保留原题型"
    b1 = [q for q in final if q.get("group_kind") == "option_group"]
    assert b1 and b1[0]["group"]["options"] and b1[0]["options"] == [], "B1：共享选项在 group"
    # 渲染：MD 案例题干只出现一次 + 选项组；HTML 组折叠；押题卷分组呈现+分组判分
    md = (tmp / "最终产物" / "qbank.md").read_text(encoding="utf-8")
    assert md.count("患儿男，3岁") == 1, "MD 案例题干应只出现一次（组标题）"
    assert "🧩 选项组" in md and "支原体" in md
    html = (tmp / "最终产物" / "qbank.html").read_text(encoding="utf-8")
    assert "案例 C001" in html
    assert html.count("<b>案例题干</b>：患儿男，3岁") == 1, "案例题干可见处应只出现一次（折叠）"
    paper = (tmp / "最终产物" / "押题卷.html").read_text(encoding="utf-8")
    assert "casebar" in paper and "分组判分" in paper, "押题卷应分组呈现 + 分组判分"
    # .apkg 产物与案例前缀
    apkg = next((p for p in (tmp / "最终产物").iterdir() if p.suffix == ".apkg"), None)
    assert apkg is not None, "案例题也应产出 .apkg"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
