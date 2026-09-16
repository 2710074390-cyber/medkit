"""U 批次 2 守卫测试（工程品质项：异步阻塞 / 分层 / 迁移 / 异常治理）。

覆盖（对应 `docs/reviews/优化清单与执行记录_2026-09-15.md`）：
- U-05 async 端点内不得直接调用已知阻塞函数（须经 asyncio.to_thread）
- U-09 分层单向：core/agents/render 不得反向导入 routers；路由层不得直写 SQL/迁移
- U-14 迁移 v7：v1 五张表补齐索引
- U-15 异常治理：静默 pass 数量收敛 + 诊断端点存在
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "medkit" / "routers"

# U-05：已知的秒级~分钟级阻塞函数（同步 LLM 调用 / 文件解析 / 大批量 DB / 大文件写盘）
_BLOCKING_CALLS = {
    "import_teacher_file", "import_teacher_file_preview", "add_seed_items",
    "_seed_parse", "parse_import_text", "batch_add", "analyze", "write_bytes",
    "extract_outline",
}


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def test_u05_async_endpoints_have_no_direct_blocking_calls():
    """U-05：async 端点内不得直接调用阻塞函数——必须 `await asyncio.to_thread(...)`。

    说明：`asyncio.to_thread(fn, ...)` 的第 0 参数是**函数对象**（Name/Attribute，非 Call），
    因此本检查只看 async 函数体内的 Call 节点即可区分「裸调用」与「已包裹」。
    """
    problems: list[str] = []
    for p in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for c in ast.walk(node):
                if isinstance(c, ast.Call) and _call_name(c) in _BLOCKING_CALLS:
                    problems.append(
                        f"{p.name}:{c.lineno} async 端点 `{node.name}` 内直接调用阻塞函数 "
                        f"`{_call_name(c)}()`（应经 asyncio.to_thread）")
    assert not problems, "U-05 违规：\n" + "\n".join(problems)


def test_u09_core_does_not_import_routers():
    """U-09：core/agents/render 不得反向导入 routers（分层单向 routers → core）。"""
    problems: list[str] = []
    for sub in ("core", "agents", "render"):
        for p in sorted((ROOT / "medkit" / sub).rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("..routers"):
                    problems.append(f"{p.relative_to(ROOT)}:{node.lineno} → from {node.module}")
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.startswith("medkit.routers"):
                            problems.append(f"{p.relative_to(ROOT)}:{node.lineno} → import {a.name}")
    assert not problems, "U-09 违规（core→routers 反向依赖）：\n" + "\n".join(problems)


def test_u14_v7_indexes_created_and_rollback():
    """U-14：迁移 v7 为 v1 五张表补齐索引（可升级、可回滚）。"""
    from medkit.core import db as dbs

    def idx_names() -> set[str]:
        return {r[0] for r in dbs.get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}

    dbs.reset_conn()
    assert dbs.migrate() == 7, "MIGRATIONS 末位应为 7"
    expected = ("idx_mk_subject_state", "idx_kn_subject_state", "idx_ex_subject",
                "idx_rc_subject_due", "idx_ts_subject_state")
    names = idx_names()
    for idx in expected:
        assert idx in names, f"v7 应创建索引 {idx}"
    # 回滚路径：`downgrade_to` 仅支持整库归零，故直接验证 v7 的 DOWN 语句
    with dbs.tx(write=True) as cur:
        dbs._downgrade_from(cur, 7)
    after = idx_names()
    for idx in expected:
        assert idx not in after, f"v7 回滚应删除索引 {idx}"
    assert dbs.migrate() == 7   # 幂等重升级（IF NOT EXISTS）


def test_u15_silent_pass_converged():
    """U-15：静默 `except Exception: pass` **零容忍**（原 31 处 → 0）。

    V-04：原断言是 `n <= 5`——一个「预算式」阈值，等于允许 5 处静默回归不被发现
    （反向验证实测：注入 1 处仍绿，注入 6 处才红）。现收敛为 0；
    确需吞异常的场景一律走 `core.errors.record/swallow`（留痕 + 计数 + 脱敏）。
    """
    import re as _re
    root = ROOT / "medkit"
    hits: list[str] = []
    for p in root.rglob("*.py"):
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if _re.match(r"^\s*except Exception\b.*:\s*(#.*)?$", ln):
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and lines[j].strip() == "pass":
                    hits.append(f"{p.relative_to(ROOT)}:{i + 1}")
    assert not hits, f"出现静默 except-pass（应为 0）：{hits}"


def test_u15_redact_and_diagnostics_endpoint():
    """U-15：错误留痕前脱敏 + 只读诊断端点可用。"""
    from fastapi.testclient import TestClient

    from medkit.core import errors as errs
    from medkit.main import app

    errs.reset()
    errs.record("TEST_CODE", "出错了 sk-abcdef123456 Authorization: Bearer xyz")
    snap = errs.snapshot()
    assert snap["counts"].get("TEST_CODE") == 1
    assert "sk-abcdef123456" not in snap["recent"][-1]["msg"]
    assert "sk-***" in snap["recent"][-1]["msg"]
    r = TestClient(app, base_url="http://127.0.0.1").get("/api/diagnostics/errors")
    assert r.status_code == 200
    assert "counts" in r.json()
    errs.reset()


def test_u15_llm_error_does_not_echo_model_output():
    """U-15 / R6-19：LLM JSON 解析失败的异常串不得含模型原始输出片段。"""
    import pytest as _pytest

    from medkit.core import llm

    with _pytest.raises(llm.LLMError) as ei:
        llm._extract_json("这不是合法 JSON 的模型原始输出片段")
    assert "这不是合法 JSON" not in str(ei.value)


def test_u10_no_god_function_over_400_lines():
    """U-10：无 ≥400 行的上帝函数（项目自定判据）。

    历史：`_run_project_impl` 699→727 行、`export_paper_html` 440 行。现拆为
    4 个管线阶段函数 + 3 个渲染片段函数，最大函数 <400 行。
    """
    import ast as _ast
    bad: list[tuple[int, str, str]] = []
    for p in sorted((ROOT / "medkit").rglob("*.py")):
        tree = _ast.parse(p.read_text(encoding="utf-8"))
        for n in _ast.walk(tree):
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                ln = n.end_lineno - n.lineno + 1
                if ln >= 400:
                    bad.append((ln, str(p.relative_to(ROOT)), n.name))
    assert not bad, f"存在 ≥400 行函数（应拆分）：{bad}"


def test_u10_pipeline_stages_are_extracted():
    """U-10：管线四阶段已抽为独立函数（可单独测试/局部回滚），且各自 <400 行。"""
    import ast as _ast
    import inspect

    from medkit.core import orchestrator as orch
    for name in ("_stage_websearch", "_stage_generate", "_stage_gate1", "_stage_qc_fix"):
        fn = getattr(orch, name, None)
        assert fn is not None, f"缺少阶段函数 {name}"
        src = inspect.getsource(fn)
        assert len(src.splitlines()) < 400, f"{name} 仍超 400 行"
    # `_run_project_impl` 只做编排：不含内联的 SQL/校验实现细节（按体量粗判）
    impl = inspect.getsource(orch._run_project_impl)
    assert len(impl.splitlines()) < 400, "_run_project_impl 应仅做阶段编排"
    assert _ast.parse(impl)  # 语法健全


def test_u09_routers_have_no_raw_sql_or_migrate():
    """U-09：路由层不得直写 SQL / 调用迁移（SQL 与事务边界归 core）。"""
    problems: list[str] = []
    for p in sorted(ROUTERS.glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            for pat in ("cur.execute(", "dbs.migrate()", "dbs.tx("):
                if pat in s:
                    problems.append(f"{p.name}:{i} → {pat}  （{s[:70]}）")
    assert not problems, "U-09 违规（路由层直写 SQL/迁移）：\n" + "\n".join(problems)
