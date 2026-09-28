"""阶段 0 题样导入器（pack/stage0-export-cases.py）的准入与字段映射测试。

为什么要有这些用例
------------------
导入器是「把真实错题变成闸门题样」的唯一入口。它的两个失效方式都不会报错：
  1. **该跳过的没跳过**（如把「答案与考生所答相同」的记录当成错题）→ 闸门拿答对题
     去测归因质量，分母被污染；
  2. **该搬运的字段没搬**（如漏了 error_tag / my_reasoning）→ 脚本跑得通，
     但对照基准是空的，那一维的结论无意义。

故本文件断言的是**准入的计数与原因**、以及**字段映射的逐项对应**，
而不是「脚本能跑完」。

## 为什么端到端那半段不再 `pytest.skip`（2026-09-29 修）

`test_end_to_end_against_isolated_store` 原先在"隔离库落库失败"时走 skip，
理由是"环境相关"。但这一段是**唯一**在真实写入路径（`library.add_mistake`）
上验证字段名与产品一致的地方；一旦它变成 skip，
字段映射那半段就永远处于"未被验证"状态，而报告仍显示全绿
（skip 与 pass 在 CI 里退出码相同）。故改为**断言**：
落库必须成功、计数必须等于 5。
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "stage0_export_cases", ROOT / "pack" / "stage0-export-cases.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _rec(**kw):
    """构造一条错题记录（默认值 = 完全合规）。"""
    base = {
        "id": "m-1",
        "subject": "生理学",
        "chapter": "血液循环",
        "topic": "心脏泵血功能",
        "question": "心肌不产生强直收缩的原因是",
        "options": ["A. a", "B. b", "C. c", "D. d", "E. e"],
        "answer": "B",
        "user_answer": "E",
        "confidence": 3,
        "my_reasoning": "我记得心肌肌浆网不发达，所以不能持续收缩，选E。",
        "error_tag": "机制混淆",
    }
    base.update(kw)
    return base


# --------------------------------------------------------------- 准入：该放行的


def test_fully_valid_record_is_accepted():
    m = _load()
    assert m._skip_reason(_rec()) == ""


# --------------------------------------------------------------- 准入：该拦的


def test_rejects_when_answer_equals_user_answer():
    """这是错题集——答对的题不能进来，否则分母被污染。"""
    m = _load()
    why = m._skip_reason(_rec(user_answer="B"))
    assert "相同" in why


def test_rejects_when_human_tag_missing():
    """人工归因是对照基准，缺了就无法判 AI 对不对。"""
    m = _load()
    assert "error_tag" in m._skip_reason(_rec(error_tag=""))


def test_rejects_when_reasoning_missing():
    """归因必须依附原话；空推理只能靠答案偏离方向猜，质量不同，须隔离。"""
    m = _load()
    assert "my_reasoning" in m._skip_reason(_rec(my_reasoning=""))


def test_rejects_when_correct_answer_missing():
    m = _load()
    assert "正确答案" in m._skip_reason(_rec(answer=""))


def test_rejects_when_user_answer_missing():
    m = _load()
    assert "考生答案" in m._skip_reason(_rec(user_answer=""))


def test_rejects_when_question_missing():
    m = _load()
    assert "题干" in m._skip_reason(_rec(question="   "))


# --------------------------------------------------------------- 字段映射


def test_field_mapping_uses_human_tag_not_ai_tag():
    """human_tag 必须取自 error_tag（人工），**不能**取 ai_error_tag。

    取错的话「与人工标签一致率」就变成「AI 与自己一致率」，
    恒等于 100%，且看起来完全正常——这是最隐蔽的一种失效。
    """
    m = _load()
    rec = _rec(error_tag="审题失误", ai_error_tag="知识盲区")
    case = m._to_case(rec)
    assert case["human_tag"] == "审题失误"


def test_field_mapping_carries_all_contract_fields():
    m = _load()
    case = m._to_case(_rec())
    for k in ("id", "subject", "chapter", "topic", "question", "options",
              "answer", "user_answer", "confidence", "my_reasoning", "human_tag"):
        assert k in case, "缺字段 %s" % k
    assert case["answer"] == "B"
    assert case["user_answer"] == "E"
    assert case["confidence"] == 3
    assert len(case["options"]) == 5


def test_field_mapping_preserves_confidence_type():
    """置信度必须是数值（下游会做 1-5 区间判断），不能被 str() 掉。"""
    m = _load()
    assert m._to_case(_rec(confidence=2))["confidence"] == 2
    assert m._to_case(_rec(confidence=None))["confidence"] is None


def test_field_mapping_strips_whitespace():
    m = _load()
    case = m._to_case(_rec(subject="  生理学  ", answer=" B "))
    assert case["subject"] == "生理学"
    assert case["answer"] == "B"


# --------------------------------------------------------------- --include-dup-answer 开关


def test_include_dup_answer_switch_only_relaxes_that_one_rule():
    """打开开关只应放行「答案相同」这一条，其余规则仍须生效。

    若实现成「跳过所有校验」，会静默放进缺归因/缺推理的记录。
    """
    m = _load()
    # 该开关在 main 里生效，这里的判据是：_skip_reason 本身仍拦「答案相同」
    assert "相同" in m._skip_reason(_rec(user_answer="B"))
    # 其余规则与此开关无关
    assert "error_tag" in m._skip_reason(_rec(error_tag=""))
    assert "my_reasoning" in m._skip_reason(_rec(my_reasoning=""))


# --------------------------------------------------------------- 端到端：跑真库（隔离 HOME）


def test_end_to_end_against_isolated_store(tmp_path, monkeypatch):
    """把 5 条记录经产品自身的 `add_mistake` 落进隔离 HOME，再跑体检，核对计数。

    走真实写入路径（而不是手写 SQL）是为了保证**字段名与产品一致**——
    否则测试会因为自己编的字段名而通过。
    """
    import subprocess
    import sys

    home = tmp_path / "home"
    (home / ".medkit" / "library").mkdir(parents=True)

    fixture = [
        _rec(id="m-ok"),
        _rec(id="m-dup", user_answer="B"),
        _rec(id="m-notag", error_tag=""),
        _rec(id="m-noreason", my_reasoning=""),
        _rec(id="m-noans", answer=""),
    ]
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": str(ROOT),
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    code = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "from medkit.core import library\n"
        "for r in json.loads(%r):\n"
        "    library.add_mistake(r)\n"
        "print(library.count_mistakes())\n"
    ) % (str(ROOT), json.dumps(fixture, ensure_ascii=False))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", cwd=str(ROOT))
    # 不许 skip：这是"在产品真实写入路径上落库"，失败即代表 add_mistake
    # 与隔离 HOME 的组合坏了——若放过，端到端那半段就永远测不到。
    # （2026-09-29 修：原为 pytest.skip，见本文件头部说明。）
    assert r.returncode == 0, (
        "隔离库落库失败（`library.add_mistake` 在隔离 HOME 下 rc=%s）：\n%s"
        % (r.returncode, (r.stderr or "")[-800:])
    )
    assert (r.stdout or "").strip() == "5", (
        "落库后 count_mistakes() 期望 5，实得 %r（stdout）" % (r.stdout or "").strip()
    )

    out = tmp_path / "real.json"
    r2 = subprocess.run(
        [sys.executable, str(ROOT / "pack" / "stage0-export-cases.py"),
         "--out", str(out)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        errors="ignore", cwd=str(ROOT))
    assert r2.returncode == 0, (r2.stderr or "")[-500:]
    cases = json.loads(out.read_text(encoding="utf-8"))
    assert len(cases) == 1, "5 条里应只有 1 条合规，实得 %d" % len(cases)
    assert cases[0]["id"] == "m-ok"
    assert cases[0]["human_tag"] == "机制混淆"


def test_end_to_end_empty_store_writes_nothing(tmp_path):
    """空库必须**不产出文件**并给出指引，而不是写出一个空数组。

    写出 `[]` 会让下游 `--cases` 静默跑 0 题、报「0/0」——
    看起来像"跑过了"，实则什么都没测。
    """
    import os
    import subprocess
    import sys

    home = tmp_path / "home"
    (home / ".medkit" / "library").mkdir(parents=True)
    out = tmp_path / "should_not_exist.json"
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": str(ROOT),
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    r = subprocess.run(
        [sys.executable, str(ROOT / "pack" / "stage0-export-cases.py"),
         "--out", str(out)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        errors="ignore", cwd=str(ROOT))
    assert r.returncode == 1, "空库应以非零退出提示用户"
    assert not out.exists(), "空库不得产出文件（否则下游会静默跑 0 题）"
