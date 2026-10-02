"""W4 守卫：LLM 端点 `base_url` 安全校验（拦 SSRF，但**不误伤** Ollama/内网网关）。

背景（2026-10-02 二轮审计 W4）：`base_url` 由用户自由填写并直接外呼。指向云元数据服务
（`169.254.169.254` / `metadata.google.internal`）时，本机会代其请求并把响应写进产物 ——
经典 SSRF（云上偷临时凭证）。

**本守卫的关键在于「两向都要对」**（否则就是「排除式判据」的两种病）：
- 不安全目标 → **必须拦**（正面断言，不能只测「正常地址能过」）；
- **正当用法 → 必须放行**：本机 Ollama `127.0.0.1:11434`、内网网关 `10.x` / `192.168.x`
  —— 产品文档明确支持（`providers.py:68`），一刀切封私网会把正当功能封死。

另配**结构守卫**：保存路径与两个「测试连接」端点都必须调用校验（AST 判 `ast.Call`），
防「加了函数但没人调」——这正是「只测函数本体 ≠ 测了防线」。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from medkit.core import config as cfg

_ROUTER_SRC = Path(__file__).resolve().parents[1] / "medkit" / "routers" / "config.py"


# ------------------------------------------------------------------ 行为：不安全目标必须拦
@pytest.mark.parametrize("bad", [
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254",
    "169.254.169.254",                       # 无 scheme 形态
    "http://metadata.google.internal/computeMetadata/v1/",
    "https://metadata.goog/",
    "http://[fe80::1]:11434/v1",             # IPv6 链路本地
])
def test_blocks_metadata_and_link_local(bad: str) -> None:
    err = cfg.endpoint_safety_error(bad)
    assert err, f"未拦下不安全端点：{bad}"
    assert isinstance(err, str) and err


# ------------------------------------------------------------------ 行为：正当用法必须放行
@pytest.mark.parametrize("ok", [
    "http://127.0.0.1:11434/v1",             # 本机 Ollama（产品明确支持）
    "http://localhost:11434/v1",
    "http://192.168.1.50:8000/v1",           # 内网网关
    "http://10.0.0.7:1234/v1",
    "https://api.deepseek.com",
    "https://open.bigmodel.cn/api/paas/v4",
    "",                                       # 空（由调用方各自的必填校验处理）
    "http://[::1]:11434/v1",                  # IPv6 回环
])
def test_allows_legit_endpoints(ok: str) -> None:
    assert cfg.endpoint_safety_error(ok) is None, f"误伤了正当端点：{ok}"


def test_does_not_block_plain_lan_10_range() -> None:
    """10.0.0.0/8 与 172.16/12 不得被误判为链路本地（两者常被混淆）。"""
    for host in ("10.1.2.3", "172.16.5.5", "172.31.255.1"):
        assert cfg.endpoint_safety_error(f"http://{host}:8000/v1") is None, host


# ------------------------------------------------------------------ 结构：调用点真的调了
def _calls_in(fn_ast) -> list[str]:
    out: list[str] = []
    for node in ast.walk(fn_ast):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.append(f.attr)
            elif isinstance(f, ast.Name):
                out.append(f.id)
    return out


def _fn(tree, name):
    return next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
        None,
    )


@pytest.mark.parametrize("fn_name", ["put_config", "llm_test", "llm_models"])
def test_endpoint_check_is_actually_called(fn_name: str) -> None:
    """三个会外呼/保存 base_url 的入口都必须调用 `endpoint_safety_error`。"""
    tree = ast.parse(_ROUTER_SRC.read_text(encoding="utf-8"))
    fn = _fn(tree, fn_name)
    assert fn is not None, f"未找到 {fn_name}"
    assert "endpoint_safety_error" in _calls_in(fn), \
        f"{fn_name} 未调用端点安全校验（加了函数但没接上）"


def test_metadata_hosts_is_single_source() -> None:
    """已知元数据主机名单必须集中（防后续分叉成多份）。"""
    assert isinstance(cfg._METADATA_HOSTS, frozenset)
    assert "169.254.169.254" in cfg._METADATA_HOSTS
