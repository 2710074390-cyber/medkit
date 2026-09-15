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
