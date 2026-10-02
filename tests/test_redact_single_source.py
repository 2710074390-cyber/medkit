"""W8 守卫：脱敏**单源**（logging_setup 不得自维护第二份模式表）。

背景（2026-10-02 二轮审计 W8）：`logging_setup` 原有一份 `_SCRUB_PATTERNS`，只认
`sk-` / `api_key` / `authorization`；而 `core.errors.redact` 另认 `mr-`（MinerU）、
JWT、以及 `register_secret` 登记的实际密钥明文。两份模式表**漂移** ⇒ 同一密钥经不同
入口（日志 vs 诊断端点）落盘时掩码结果不一致，且新形态只补一处。

本文件两道防线：
1. **结构守卫**：AST 检查 `logging_setup` 不再定义本地脱敏正则表，且 `_scrub`
   真的调用了 `core.errors.redact`（用 AST 判 `ast.Call`，不是文本子串）。
2. **行为守卫（正面断言）**：`mr-xxx` 与一段 JWT 经日志链路必须被掩码——
   断言「被掩码」而非「sk- 被掩码」（后者对旧实现恒真，属假绿）。
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from medkit import logging_setup
from medkit.core import errors

_SRC = Path(__file__).resolve().parents[1] / "medkit" / "logging_setup.py"


# ------------------------------------------------------------------ 结构守卫
def _module_ast() -> ast.Module:
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def _assigned_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_no_local_scrub_pattern_table() -> None:
    """logging_setup 不得再有本地模式表（`_SCRUB_PATTERNS` / `_KEY_RE` 等）。

    存在即视为「第二源」——允许常量其余命名，但含 `SCRUB`/`KEY_RE` 的模块级
    正则表一律禁止（正是漂移的来源）。
    """
    names = {n for n in _assigned_names(_module_ast()) if re.search(r"(SCRUB|KEY_RE|SECRET_RE)", n)}
    assert not names, f"logging_setup 仍自维护脱敏模式表：{sorted(names)}（应复用 errors.redact）"


def test_scrub_calls_errors_redact() -> None:
    """`_scrub` 必须调用 `core.errors.redact`（AST 判 `ast.Call`，非文本子串）。"""
    tree = _module_ast()
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_scrub"),
        None,
    )
    assert fn is not None, "未找到 _scrub 函数"
    calls: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.append(f.id)
            elif isinstance(f, ast.Attribute):
                calls.append(f.attr)
    assert "redact" in calls or "_redact" in calls, f"_scrub 未调用 redact，实际调用：{calls}"


def test_no_local_re_compile_in_logging_setup() -> None:
    """logging_setup 不应再 `re.compile`（脱敏正则归 errors 单源）。"""
    tree = _module_ast()
    compiles = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
    ]
    assert not compiles, "logging_setup 仍自行 re.compile（应移到 errors.redact 单源）"


# ------------------------------------------------------------------ 行为守卫
@pytest.mark.parametrize(
    ("raw", "must_not_appear"),
    [
        ("mineru key is mr-abcdef123456 done", "mr-abcdef123456"),
        ("token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd1234 tail", "eyJhbGciOiJIUzI1NiJ9"),
        ("sk-abcdef1234567890 leaked", "sk-abcdef1234567890"),
        ("api_key: sk_realkey_12345678 end", "sk_realkey_12345678"),
        ("Authorization: Bearer tok_abcdef123456 end", "tok_abcdef123456"),
    ],
)
def test_scrub_masks_non_sk_forms(raw: str, must_not_appear: str) -> None:
    """正面断言：非 `sk-` 形态（mr-/JWT/Bearer）也必须被掩码。"""
    out = logging_setup._scrub(raw)
    assert must_not_appear not in out, f"未掩码：{must_not_appear!r} 仍在 {out!r}"


def test_scrub_masks_registered_secret() -> None:
    """登记的实际密钥明文（智谱 `xxx.yyyy` 这类无前缀形态）也必须被掩码。"""
    secret = "9f8e7d6c5b4a.0123456789abcdef"
    errors.register_secret(secret)
    try:
        out = logging_setup._scrub(f"using {secret} to call")
        assert secret not in out
    finally:
        errors._SECRETS.discard(secret)


def test_scrub_does_not_truncate() -> None:
    """日志链路**只脱敏不截断**（日志需要完整上下文；截断属诊断端点一侧的策略）。"""
    long_text = "x" * 900
    assert len(logging_setup._scrub(long_text)) == 900


def test_redacting_filter_rewrites_record(caplog: pytest.LogCaptureFixture) -> None:
    """端到端：经 logging 链路落盘的 record 已被掩码。"""
    flt = logging_setup.RedactingFilter()
    rec = logging.LogRecord(
        "medkit.test", logging.WARNING, __file__, 1,
        "key %s leaked", ("mr-zzzz9999888877776666",), None,
    )
    assert flt.filter(rec) is True
    rec2 = logging.LogRecord(
        "medkit.test", logging.WARNING, __file__, 1,
        "key %s leaked", ("sk-abcdef1234567890",), None,
    )
    flt.filter(rec2)
    for r in (rec, rec2):
        msg = r.getMessage()
        assert "mr-zzzz9999888877776666" not in msg
        assert "sk-abcdef1234567890" not in msg


def test_redacting_filter_honours_registered_secret() -> None:
    """Filter 走同一单源 ⇒ 登记密钥在日志链路同样被掩码（收敛前不具备的能力）。"""
    secret = "zz11yy22xx33.mm44nn55oo66"
    errors.register_secret(secret)
    try:
        rec = logging.LogRecord(
            "medkit.test", logging.WARNING, __file__, 1, "cfg=%s", (secret,), None,
        )
        logging_setup.RedactingFilter().filter(rec)
        assert secret not in rec.getMessage()
    finally:
        errors._SECRETS.discard(secret)
