"""U-24（R6-18）：日志脱敏 RedactingFilter 独立单测。

防线：Logger 一旦把含 Key 的异常串写日志，Key 即随 ~/.medkit/logs 落盘/上屏。
本测试验证 filter 在格式化前掩码 `sk-***` 与 `authorization/api_key: ***`，
并确认无害文本原样通过（不误伤）。
"""

import logging

from medkit.logging_setup import RedactingFilter


def _capture(record: logging.LogRecord) -> str:
    """模拟 handler 格式化：先过 filter，再跑 Formatter。"""
    assert RedactingFilter().filter(record) is True
    return logging.Formatter("%(message)s").format(record)


def test_redacts_sk_key():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "LLM 调用失败：bad request sk-abc123XYZ09", None, None)
    assert "sk-***" in _capture(rec)
    assert "sk-abc123XYZ09" not in _capture(rec)


def test_redacts_authorization_value():
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "请求头 Authorization: Bearer tok-secret-123", None, None))
    assert out == "请求头 Authorization: Bearer ***"


def test_redacts_api_key_equals():
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "config api_key=sk-zzz999888", None, None))
    assert out == "config api_key=***"


def test_keeps_plain_text():
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "正常日志：保存题库 42 道", None, None))
    assert out == "正常日志：保存题库 42 道"


def test_redacts_after_variable_interpolation():
    # %-style args 在 getMessage() 展开后再掩码，值内 Key 也要被盖住
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "上游错误：%s 携带 %s", ("LLM 调用", "sk-0x0x0x111222"), None)
    out = _capture(rec)
    assert "sk-***" in out
    assert "sk-0x0x0x111222" not in out
