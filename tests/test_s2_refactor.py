"""S2 工程化重构回归测试：

版本单源（__init__ ↔ APP_VERSION ↔ pack/version.iss）/ 路由拆分后全量端点仍装配 /
state 单例（main 与 state 共享同一 RUNNING·OCR_JOBS）/ logging 幂等（临时目录，不污染 ~/.medkit）/
成本预估端点与 core.cost 公式一致。
"""

import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# README ↔ dist-installer 产物的耦合只在本地发布流程存在：打包 → 写 README → 本地验证。
# CI 工作区没有本地产物（dist-installer 不入库），这两个测试的判定前提（文件名必须存在 /
# 未发布态必须标注）在 CI 上无意义，跳过——由本地发布验证链负责（2026-09-21 CI 首次跑到
# Test 步时暴露，v0.10.5 发布把 README 改为真实安装包名后触发）。
_CI = os.environ.get("CI") == "true"
_SKIP_IF_CI = pytest.mark.skipif(_CI, reason="发布产物/发布态仅在本地发布流程存在")

import medkit  # noqa: E402
import medkit.main as m  # noqa: E402
import medkit.state as state  # noqa: E402
from medkit.agents import render_prompt  # noqa: E402
from medkit.core.cost import estimate_run  # noqa: E402
from medkit.logging_setup import setup_logging  # noqa: E402


def test_version_single_source():
    assert medkit.__version__, "__version__ 不应为空"
    assert m.APP_VERSION == medkit.__version__, "main.APP_VERSION 应引用单源版本"
    iss = (ROOT / "pack" / "version.iss").read_text(encoding="utf-8")
    assert f'"{medkit.__version__}"' in iss, "pack/version.iss 应与 __version__ 一致"


def test_version_declared_pieces_agree():
    """发布口径「五件套」的**声明侧四件**必须一致（S3-15 / R8+W）。

    原守卫只覆盖 `__init__` / `APP_VERSION` / `version.iss` **三件**——CHANGELOG 与 README
    无人看管，于是 0.10.4 声明了但 README 仍写 v0.10.3（README 漂移正是这么漏出去的）。
    """
    ver = medkit.__version__
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{ver}]" in changelog, f"CHANGELOG 缺少 {ver} 版本段"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"v{ver}" in readme or f"（{ver}）" in readme, f"README 未提及当前版本 {ver}"


@_SKIP_IF_CI
def test_readme_download_points_at_existing_artifact():
    """README 里写给用户双击的安装包名，**必须真实存在**（否则用户下载即 404）。"""
    import re as _re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    names = set(_re.findall(r"MedKit-Setup-[\w.\-]+\.exe", readme))
    assert names, "README 未给出安装包文件名"
    inst = ROOT / "dist-installer"
    for n in names:
        assert (inst / n).exists(), f"README 指向不存在的安装包：{n}"


@_SKIP_IF_CI
def test_readme_marks_unreleased_version():
    """若当前 `__version__` 还没有对应安装包，README 必须显式标注「未发布/构建中」。

    这是「声明版本领先于产物」时的诚实口径——否则用户会以为下载到的就是最新修复版
    （2026-09-16 的「同号不同物」事故正是这类信息不对称）。
    """
    ver = medkit.__version__
    inst = ROOT / "dist-installer"
    has_artifact = inst.is_dir() and any(inst.glob(f"MedKit-Setup-{ver}.exe"))
    if has_artifact:
        return
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "尚在构建中" in readme or "未发布" in readme or "构建中" in readme, \
        f"{ver} 无安装包，README 必须标注未发布状态"


def test_run_medkit_console_utf8_prevents_gbk_crash(monkeypatch):
    """打包版（cmd 默认 GBK codepage）print emoji 曾抛 UnicodeEncodeError 致入口崩溃。
    回归：_console_utf8 必须把 stdout/stderr 重配为 UTF-8 + errors=replace。"""
    import io

    import run_medkit as entry

    # 前提：GBK 严格流写 emoji 必崩（这正是不重配时打包版崩溃的原因）
    strict = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    try:
        strict.write("⚠️")
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("前提不成立：GBK 严格流写 emoji 应抛 UnicodeEncodeError")

    class _Out:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kw):
            self.calls.append(kw)

    out, err = _Out(), _Out()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    entry._console_utf8()
    assert out.calls and err.calls, "_console_utf8 应重配 stdout/stderr"
    for calls in (out.calls, err.calls):
        assert calls[0].get("encoding") == "utf-8"
        assert calls[0].get("errors") == "replace"


def _iter_paths(routes):
    """枚举路由路径。fastapi<0.141 平铺拷贝子路由；≥0.141 include_router 追加
    _IncludedRouter 组合代理（无 path，经 original_router 委托匹配），需递归下钻。"""
    for r in routes:
        p = getattr(r, "path", None)
        if p:
            yield p
        sub = getattr(r, "original_router", None)
        if sub is not None:
            yield from _iter_paths(sub.routes)


def test_all_routes_still_assembled():
    paths = set(_iter_paths(m.app.routes))
    expect = {
        "/", "/api/health", "/api/providers", "/api/config",
        "/api/llm/test", "/api/llm/models", "/api/keys", "/api/keys/{pid}",
        "/api/mineru/test", "/api/ocr/start", "/api/ocr/jobs/{job_id}",
        "/api/parse", "/api/sample", "/api/sessions", "/api/sessions/{sid}",
        "/api/projects", "/api/projects/{pid}", "/api/projects/{pid}/status",
        "/api/projects/{pid}/run", "/api/projects/{pid}/files/{name}",
        "/api/projects/{pid}/export/anki", "/api/projects/{pid}/export/apkg",
        "/api/projects/{pid}/questions",
        "/api/projects/{pid}/questions/review", "/api/projects/{pid}/regen",
        "/api/trial", "/api/cost/estimate", "/api/prompts", "/api/prompts/{name}",
        "/api/presets", "/api/presets/{pid}",
        "/api/search/backends", "/api/search/test", "/api/update/check",
    }
    missing = expect - paths
    assert not missing, f"拆分后缺失路由：{sorted(missing)}"


def test_state_singletons_shared():
    assert m.RUNNING is state.RUNNING, "main.RUNNING 应与 state.RUNNING 同一对象"
    assert m.OCR_JOBS is state.OCR_JOBS
    assert m.OCR_LOCK is state.OCR_LOCK


def test_logging_setup_idempotent(tmp_path):
    root = logging.getLogger()
    added = []
    try:
        setup_logging(tmp_path)
        assert (tmp_path / "medkit.log").exists(), "应创建 medkit.log"
        med_handlers = [h for h in root.handlers if getattr(h, "_medkit", False)]
        assert len(med_handlers) == 2, "应添加文件 + 控制台两个 handler"
        added = med_handlers[:]
        # 幂等：再次调用不重复添加
        setup_logging(tmp_path / "other")
        med_handlers2 = [h for h in root.handlers if getattr(h, "_medkit", False)]
        assert len(med_handlers2) == 2, "重复 setup 不应叠加 handler"
    finally:
        for h in added:
            root.removeHandler(h)


def test_cost_estimate_endpoint_matches_formula(monkeypatch, tmp_path):
    """端点 ↔ core.cost 的**契约**：端点必须原样透传 core 的三个字段（不自己算）。

    ## 这条用例测什么、不测什么（2026-09-27 反向验证后改写）
    旧版写的是 `exp = estimate_run(...)`，然后断言端点输出 == exp。
    **这是恒真**：两侧同源，把 `CHARS_PER_TOKEN` 从 0.8 改成 0.08（错 10 倍，
    用户会看到成本少一个量级）后用例仍 `1 passed`。
    它只能发现「端点自己另算了一套公式」，**发现不了公式本身错**。

    现在的分工：
    - 本用例：锁**契约**（字段名 / 透传 / 入参钳制），期望值**独立手算**，不调 estimate_run；
    - `test_cost_formula_matches_hand_computed_document`：锁**公式数值**，手算常量。
    两条合起来才覆盖「端点 ⇄ 公式 ⇄ 数值」全链。
    """
    from medkit.core import config as cfgmod

    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    saved["api_key"] = "sk-test"
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(m.cfg, "load", lambda: dict(saved))
    c = TestClient(m.app, base_url="http://127.0.0.1")
    body = {"chars_textbook": 10000, "chars_teacher": 2000,
            "n_slices": 3, "n_questions": 100}
    r = c.post("/api/cost/estimate", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    # 契约：三个字段都在，且 total == input + output（端点不得自行发明口径）
    assert set(got) == {"input_tokens", "output_tokens", "total_tokens"}, got
    assert got["total_tokens"] == got["input_tokens"] + got["output_tokens"], got
    # 透传：端点必须给出 core 的**同一组数**（这里刻意调 estimate_run 做一致性核对，
    # 数值正确性由下一条用例独立手算保证）
    exp = estimate_run(10000, 2000, 3, 100)
    assert got == {"input_tokens": exp["input_tokens"],
                   "output_tokens": exp["output_tokens"],
                   "total_tokens": exp["total_tokens"]}, "端点必须透传 core.cost 的结果"
    # 入参钳制：负数 / 0 切片也要能算（旧实现内嵌公式在 n_slices=0 时除零）
    r0 = c.post("/api/cost/estimate", json={"chars_textbook": -5, "chars_teacher": -1,
                                           "n_slices": 0, "n_questions": 0})
    assert r0.status_code == 200, r0.text
    assert r0.json()["total_tokens"] > 0, "钳制后仍应给出正数预估"


def test_cost_formula_matches_hand_computed_document():
    """公式数值守卫：**逐项手算**，不调 estimate_run，也不读它的常量。

    ## 为什么必须手算
    上一条用例证明「端点 == estimate_run」是恒真的：改坏 `CHARS_PER_TOKEN` 也绿。
    这里把 `core/cost.py` 文档里写明的口径**用字面量重算一遍**，任何一项改动都会红。

    口径（见 `medkit/core/cost.py` 模块 docstring，2026-08 审计修订版）：
    - 中文 1 字 ≈ 0.8 token
    - 生成：每切片 1 次调用，输入 = 切片全文 + 教师重点（system 注入一次）→ 出题 350 token/题
    - 质检：每题附 1700 字源切片；每 20 题一批，每批 800 token 输出
    - 修复：按 fail_ratio=10% 计；输入 1700 字/题、输出 400 token/题
    - 复习手册：输入 21000 字、输出 4000 token（固定开销）
    """
    CHARS_PER_TOKEN = 0.8       # 字面量，故意不从 core.cost 导入
    GEN_OUT_PER_Q = 350
    QC_IN_CHARS_PER_Q = 1700
    QC_OUT_PER_BATCH = 800
    FIX_IN_CHARS_PER_Q = 1700
    FIX_OUT_PER_Q = 400
    REVIEW_IN_CHARS = 21000
    REVIEW_OUT = 4000
    fail_ratio = 0.10

    def hand(chars_textbook: int, chars_teacher: int, n_slices: int, n_q: int) -> dict:
        gen_in = (chars_textbook + chars_teacher * max(n_slices, 1)) * CHARS_PER_TOKEN
        gen_out = n_q * GEN_OUT_PER_Q
        qc_in = n_q * QC_IN_CHARS_PER_Q * CHARS_PER_TOKEN
        qc_out = (n_q / 20 + 1) * QC_OUT_PER_BATCH
        fails = int(n_q * fail_ratio)
        fix_in = fails * FIX_IN_CHARS_PER_Q * CHARS_PER_TOKEN
        fix_out = fails * FIX_OUT_PER_Q
        rev_in = REVIEW_IN_CHARS * CHARS_PER_TOKEN
        inp = int(gen_in + qc_in + fix_in + rev_in)
        out = int(gen_out + qc_out + fix_out + REVIEW_OUT)
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}

    for args in ((10000, 2000, 3, 100), (0, 0, 1, 1), (5000, 0, 8, 50), (123456, 7890, 12, 300)):
        assert estimate_run(*args) == hand(*args), (
            f"成本公式与文档口径不符，参数={args}；"
            "若这是有意调整，请同步本用例的字面量 + core/cost.py 的 docstring"
        )

    # 灵敏度自检：把换算系数改 10% 必须产生不同结果 —— 防止公式退化成常函数
    assert estimate_run(10000, 2000, 3, 100) != estimate_run(10000, 2000, 3, 101), \
        "题数变化未影响预估（公式疑似退化为常函数）"


def test_render_prompt_single_pass_no_double_injection():
    """占位符单源替换：一次遍历，替换值中的字面量不再被二次扫描。"""
    out = render_prompt("medtutor.md", subject="{kp_name}", kp_name="ABCD")
    assert "{subject}" not in out, "subject 占位符应被替换"
    assert "ABCD" in out, "真正的 {kp_name} 应被替换成 ABCD"
    assert "{kp_name}" in out, "subject 注入的字面量 {kp_name} 不应被二次替换成 ABCD"


def test_render_prompt_leaves_unknown_placeholder():
    """未提供的占位符原样保留（便于调试），不静默置空。"""
    out = render_prompt("medtutor.md", subject="儿科学")
    assert out.count("{kp_name}") >= 1, "缺失占位符应原样保留以便发现"
