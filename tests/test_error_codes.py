"""错误码枚举测试（2026-09-20 审查 P3）：

- ErrorCode 取值与历史线上字符串完全一致（前端按文本展示，不允许改名）；
- str 混入：可 json.dumps、可与旧字符串直接比较；
- main 的异常处理器接线正确：LLM/Search/MinerU → 502，Pipeline → 500，兜底 → 500；
- 错误体经脱敏（不回显密钥）。
"""
from __future__ import annotations

import json

import pytest

import medkit.main as m
from medkit.core.error_codes import ErrorCode
from medkit.core.llm import LLMError
from medkit.core.mineru import MinerUError
from medkit.core.orchestrator import PipelineError
from medkit.core.websearch import SearchError


def test_error_code_values_match_legacy_wire_strings():
    assert ErrorCode.LLM_ERROR.value == "LLM_ERROR"
    assert ErrorCode.SEARCH_ERROR.value == "SEARCH_ERROR"
    assert ErrorCode.MINERU_ERROR.value == "MINERU_ERROR"
    assert ErrorCode.PIPELINE_ERROR.value == "PIPELINE_ERROR"
    assert ErrorCode.INTERNAL_ERROR.value == "INTERNAL_ERROR"
    # str 混入兼容：枚举成员可直接当字符串序列化/比较
    assert ErrorCode.LLM_ERROR == "LLM_ERROR"
    assert json.dumps({"error_code": ErrorCode.LLM_ERROR}) == '{"error_code": "LLM_ERROR"}'


@pytest.mark.parametrize(("exc", "status", "code"), [
    (LLMError("boom"), 502, ErrorCode.LLM_ERROR),
    (SearchError("boom"), 502, ErrorCode.SEARCH_ERROR),
    (MinerUError("boom"), 502, ErrorCode.MINERU_ERROR),
    (PipelineError("boom"), 500, ErrorCode.PIPELINE_ERROR),
])
def test_registered_exception_handlers(exc, status, code):
    """经 app 实际注册的处理器走一遍（不新造路由，避免污染共享 app 的路由表）。"""
    handler = m.app.exception_handlers.get(type(exc))
    assert handler is not None, f"未注册 {type(exc).__name__} 处理器"
    resp = handler(None, exc)
    assert resp.status_code == status
    body = json.loads(resp.body)
    assert body["error_code"] == code.value


def test_err_response_redacts_secret():
    resp = m._err_response(502, LLMError("调用失败 sk-abcdef1234567890"), ErrorCode.LLM_ERROR)
    body = json.loads(resp.body)
    assert body["error_code"] == "LLM_ERROR"
    assert "sk-abcdef1234567890" not in body["detail"]


def test_unhandled_exception_handler_returns_structured_500(run_coro):
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/_boom",
             "headers": [], "query_string": b"", "app": m.app}
    resp = run_coro(m._unhandled_exception(Request(scope), RuntimeError("炸了")))
    assert resp.status_code == 500
    body = json.loads(resp.body)
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "服务器内部错误" in body["detail"]
