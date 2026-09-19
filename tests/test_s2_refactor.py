"""S2 工程化重构回归测试：

版本单源（__init__ ↔ APP_VERSION ↔ pack/version.iss）/ 路由拆分后全量端点仍装配 /
state 单例（main 与 state 共享同一 RUNNING·OCR_JOBS）/ logging 幂等（临时目录，不污染 ~/.medkit）/
成本预估端点与 core.cost 公式一致。
"""

import logging
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def test_readme_download_points_at_existing_artifact():
    """README 里写给用户双击的安装包名，**必须真实存在**（否则用户下载即 404）。"""
    import re as _re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    names = set(_re.findall(r"MedKit-Setup-[\w.\-]+\.exe", readme))
    assert names, "README 未给出安装包文件名"
    inst = ROOT / "dist-installer"
    for n in names:
        assert (inst / n).exists(), f"README 指向不存在的安装包：{n}"


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
    saved = dict(__import__("medkit.core.config", fromlist=["x"]).DEFAULTS)
    from medkit.core import config as cfgmod

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
    exp = estimate_run(10000, 2000, 3, 100)
    assert got == {"input_tokens": exp["input_tokens"],
                   "output_tokens": exp["output_tokens"],
                   "total_tokens": exp["total_tokens"]}, "前端成本公式必须与 core.cost 同源"


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
