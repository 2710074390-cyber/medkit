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
