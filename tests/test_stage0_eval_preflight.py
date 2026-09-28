# -*- coding: utf-8 -*-
"""`pack/stage0-attribution-eval.py` 的题样闸门守卫。

这个脚本是**唯一会花钱**的一环。它此前对题样零校验——实测把
「答案越界 / 考生答案越界 / confidence 非数值 / human_tag 无效」的坏题样喂进去，
它照样跑完、退出 0、产出一份看起来正常的报告（2026-09-29 实测）。

根因值得记住：`tag_hit` 只做「相等则计数」，**无效标签的表现是"不命中"而非报错**
→ 坏题样会**静默产出假分数**。这类"不报错的功能失效"比崩溃危险。

所以闸门的关键判据不是「能不能发现坏题样」，而是
**「拒绝发生在 `make_client()` 之前」**——那才是"没花掉额度"的证明。
本文件用假 client 记录调用，把它变成可证伪的断言。
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL = ROOT / "pack" / "stage0-attribution-eval.py"


@pytest.fixture
def mod():
    """加载评测脚本（不执行 main）。"""
    spec = importlib.util.spec_from_file_location("stage0_eval_mod", EVAL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def spy(mod, tmp_path, monkeypatch):
    """把 make_client 换成探针。

    `called=False` 是"拦在花钱之前"的证据；若为 True 说明已经越过闸门。
    """
    state = {"called": False}

    class _FakeClient:
        pass

    def _make(*a, **k):
        state["called"] = True
        return _FakeClient()

    monkeypatch.setattr(mod.agent, "make_client", _make)
    # analyze 一旦被调用即失败，确保不会有真实网络请求
    monkeypatch.setattr(mod.agent, "analyze",
                        lambda c, r: (_ for _ in ()).throw(mod.LLMError("FAKE")))
    monkeypatch.setattr(mod, "OUT_ROOT", tmp_path / "out")
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return state


def _good_case(**kw):
    c = {
        "id": "G1", "subject": "", "chapter": "", "topic": "",
        "question": "好题样",
        "options": ["A. 甲", "B. 乙", "C. 丙", "D. 丁", "E. 戊"],
        "answer": "C", "user_answer": "A", "confidence": 3,
        "my_reasoning": "我记成了甲。", "human_tag": "知识盲区",
    }
    c.update(kw)
    return c


def _run(mod, monkeypatch, cases, tmp_path, extra=None):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    argv = ["prog", "--cases", str(p)] + (extra or [])
    monkeypatch.setattr(sys, "argv", argv)
    try:
        return mod.main()
    except SystemExit as e:
        return e.code


# ------------------------------------------------------------ 核心判据

def test_bad_case_rejected_before_spending_money(mod, spy, monkeypatch, tmp_path):
    """**本文件最重要的一条**：坏题样必须被拒，且 `make_client` 未被调用。

    只断言"返回 2"是不够的——返回 2 也可能发生在跑完一轮之后。
    `spy["called"] is False` 才是"一个 token 都没花"的证明。
    """
    bad = _good_case(options=["A. 甲", "B. 乙", "C. 丙"],
                     answer="E", user_answer="Z", human_tag="不存在的标签")
    rc = _run(mod, monkeypatch, [bad], tmp_path)
    assert rc == 2
    assert spy["called"] is False, "坏题样竟然走到了建客户端——闸门形同虚设"


def test_good_case_passes_gate_and_reaches_client(mod, spy, monkeypatch, tmp_path):
    """反向：好题样不能被误拦（否则闸门会逼人绕过它）。"""
    rc = _run(mod, monkeypatch, [_good_case()], tmp_path)
    assert rc == 0
    assert spy["called"] is True, "好题样被误拦——闸门过严，会逼人绕过"


@pytest.mark.parametrize("bad_kw,what", [
    # 注意：`_good_case` 默认 5 个选项（A~E），所以 "E" 是**合法**答案。
    # 越界必须用缩选项或 F 之上的字母——否则测的是"闸门没拦住合法的值"。
    ({"options": ["A. 甲", "B. 乙", "C. 丙"], "answer": "E"}, "答案超出选项范围"),
    ({"options": ["A. 甲", "B. 乙", "C. 丙"], "answer": "C", "user_answer": "E"},
     "考生答案超出选项范围"),
    ({"human_tag": "粗心"}, "无效标签"),
    ({"answer": "A", "user_answer": "A"}, "答案==考生答案"),
    ({"options": ["A. 甲", "B. 乙", "D. 丁"]}, "选项前缀断档"),
])
def test_each_hard_problem_blocks_gate(mod, spy, monkeypatch, tmp_path,
                                       bad_kw, what):
    """逐条硬问题都要能单独拦住（防"只挡了最简单那一种"）。"""
    cases = [_good_case(), _good_case(id="G2", **bad_kw)]
    cases[1]["question"] = "另一道题，避免题干重复干扰"
    rc = _run(mod, monkeypatch, cases, tmp_path)
    assert rc == 2, "%s 未被拦住" % what
    assert spy["called"] is False


def test_soft_problem_does_not_block(mod, spy, monkeypatch, tmp_path):
    """软问题（真实错题常见）不能挡人——只打印。"""
    rc = _run(mod, monkeypatch, [_good_case(my_reasoning="")], tmp_path)
    assert rc == 0
    assert spy["called"] is True


def test_skip_preflight_flag_bypasses_gate(mod, spy, monkeypatch, tmp_path):
    """逃生口：`--skip-preflight` 存在且确实绕过。

    留这个口子是为了"明知数据有问题但要复现现象"的场景；
    但如果它悄悄失效，用户会以为绕过了其实没有。
    """
    bad = _good_case(options=["A. 甲", "B. 乙", "C. 丙"], answer="E")
    rc = _run(mod, monkeypatch, [bad], tmp_path, extra=["--skip-preflight"])
    assert rc == 0
    assert spy["called"] is True


# ------------------------------------------------------------ 单源

def test_preflight_reuses_checker_single_source(mod):
    """判据必须来自 `stage0-cases-check.py`，不能另写一份。

    两处各写一份判据 = 迟早漂移；漂移的表现是"预检说没事、闸门说有事"
    （或反之），用户会不信任两边。
    """
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_chk", ROOT / "pack" / "stage0-cases-check.py")
    chk = ilu.module_from_spec(spec)
    spec.loader.exec_module(chk)

    tags = chk._tags()
    # 同一个坏例子，两个入口必须给出一致的硬问题集合
    bad = _good_case(options=["A. 甲", "B. 乙", "C. 丙"], answer="E",
                     human_tag="粗心")
    hard_from_checker = set(chk.check_case(bad, tags)[0])
    from_eval = set(mod._preflight([bad]))
    assert len(hard_from_checker) == len(from_eval)
    assert all(any(h in e for e in from_eval) for h in hard_from_checker), \
        (hard_from_checker, from_eval)


def test_preflight_returns_empty_for_clean_set(mod):
    assert mod._preflight([_good_case()]) == []


# ------------------------------------------------------------ 真实题样

def test_repo_case_file_passes_gate(mod, tmp_path):
    """仓库自带的 30 题题样必须能过闸门（否则主线流程根本跑不动）。"""
    cases = json.loads((ROOT / "pack" / "stage0_cases.json").read_text(encoding="utf-8"))
    assert mod._preflight(cases) == []
