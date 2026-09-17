"""B7 回归（R8+W）：P1 剩余六项——医学门禁三条 + 并发双计费 + 跨站 GET + 脱敏三旁路。

覆盖：
- **S2-7**：答案键字母越界（`answer="F"` 而只有 A~E）必须被门禁拦下（原只验非空/单字母）。
- **S2-8**：重复选项必须被拦下（原无任何门禁覆盖）。
- **S2-9**：溯源除「切片 ID 是否存在」外，还要核对「知识点 ↔ 切片是否对得上」。
- **S2-5**：**非流式** `tutor/start` 必须真正**持有**在飞锁（原为窥视式 → 并发双建会话双扣费）。
- **S2-6**：GET/HEAD 也要挡跨站触发（`Sec-Fetch-Site: cross-site`），否则任意网页可用
  `<img src="http://127.0.0.1:4880/api/...">` 触发带副作用的 GET。
- **SEC-REDACT**：脱敏防线三处旁路——回显末枝 / run.log 写盘 / 正则覆盖面。
"""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import dedupe  # noqa: E402
from medkit.core import errors as errs  # noqa: E402
from medkit.gates import options_check, trace_check  # noqa: E402


def _q(**kw) -> dict:
    base = {"id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "生长发育",
            "question": "下列哪项正确？", "options": ["甲", "乙", "丙", "丁", "戊"],
            "answer": "A", "analysis": "解析【源:切片S001】", "sid": "S001"}
    base.update(kw)
    return base


def _codes(q: dict) -> set[str]:
    return {i["code"] for i in options_check.check_question(q, "Q001")}


# ---------------------------------------------------------------- S2-7

def test_answer_letter_out_of_range_fails():
    """S2-7：answer='F' 而只有 A~E → R15 fail（原全程无人拦，渲染后无正确项可勾）。"""
    codes = _codes(_q(answer="F"))
    assert "R15" in codes, f"答案键越界未被拦下：{codes}"


def test_x_type_answer_letter_out_of_range_fails():
    """X 型多答案：任一字母越界即 fail。"""
    codes = _codes(_q(type="X", answer="AF"))
    assert "R15" in codes


def test_valid_answer_not_flagged():
    """对照组：范围内答案不得误报。"""
    assert "R15" not in _codes(_q(answer="E"))
    assert "R15" not in _codes(_q(type="X", answer="AE"))


# ---------------------------------------------------------------- S2-8

def test_duplicate_options_fail():
    """S2-8：重复选项 → R16 fail（原无任何门禁覆盖）。"""
    codes = _codes(_q(options=["甲", "乙", "甲", "丁", "戊"]))
    assert "R16" in codes, f"重复选项未被拦下：{codes}"


def test_duplicate_options_whitespace_normalized():
    """归一化后相同也算重复（'甲 ' 与 '甲'）。"""
    codes = _codes(_q(options=["甲", "乙", " 甲 ", "丁", "戊"]))
    assert "R16" in codes


def test_distinct_options_not_flagged():
    assert "R16" not in _codes(_q())


# ---------------------------------------------------------------- S2-9

def test_trace_mismatched_slice_warns():
    """S2-9：切片 ID 真实存在但与本题知识点对不上 → warn。"""
    qs = [_q(subtopic="新生儿黄疸")]
    r = trace_check.check_trace(qs, {"S001"}, slice_texts={"S001": "生长发育有三个高峰。"})
    warns = [i for i in r["issues"] if i["severity"] == "warn"]
    assert warns and "找不到本题知识点" in warns[0]["reason"]


def test_trace_aligned_slice_clean():
    """对照组：知识点能在切片里找到 → 无告警。"""
    qs = [_q(subtopic="生长发育")]
    r = trace_check.check_trace(qs, {"S001"}, slice_texts={"S001": "生长发育有三个高峰。"})
    assert r["issues"] == []


def test_trace_without_slice_texts_keeps_old_behavior():
    """不传 slice_texts → 行为与原先一致（向后兼容，只验 ID 存在）。"""
    qs = [_q(subtopic="完全不相干的知识点")]
    r = trace_check.check_trace(qs, {"S001"})
    assert r["issues"] == []


# ---------------------------------------------------------------- S2-5

def test_tutor_guard_holds_lock_and_releases():
    """S2-5：非流式 tutor/start 的守卫必须**真正持有**在飞锁，而不是窥视。

    窥视版（原实现）下 `is_active` 恒为 False → 并发第二次提交照样进端点 → 双建会话双扣费。
    """
    from medkit.routers.library import TutorStartBody, _tutor_guard, _tutor_key

    body = TutorStartBody(subject="儿科", kp_name="生长发育")
    key = _tutor_key(body)
    dedupe.end(key)                      # 清场
    gen = _tutor_guard(body)
    next(gen)                            # 模拟请求进入
    try:
        assert dedupe.is_active(key) is True, "非流式端点必须持锁（窥视版会返回 False）"
        with pytest.raises(HTTPException) as ei:
            next(_tutor_guard(body))     # 并发第二个请求
        assert ei.value.status_code == 409
    finally:
        gen.close()                      # 模拟请求结束 → finally 释放
    assert dedupe.is_active(key) is False, "请求结束后必须释放锁"


def test_tutor_start_endpoint_uses_holding_guard():
    """S2-5 接线级：**非流式** tutor/start 必须挂持锁守卫 `_tutor_guard`。

    只测守卫本体不够——端点若仍挂着窥视版 `_tutor_start_guard`，并发双扣费照旧。
    """
    import inspect

    from medkit.routers import library as lib

    src = inspect.getsource(lib.tutor_start)
    assert "Depends(_tutor_guard)" in src, "非流式 tutor/start 必须用持锁守卫（S2-5）"
    assert "Depends(_tutor_start_guard)" not in src


def test_tutor_stream_still_uses_peek_guard():
    """流式端点仍用窥视守卫（持锁由 gen() 负责）——避免同一请求自锁。"""
    import inspect

    from medkit.routers import library as lib

    src = inspect.getsource(lib.tutor_start_stream)
    assert "_tutor_start_guard" in src, "流式端点应继续用窥视守卫"
    assert "_tutor_guard(" not in src.replace("_tutor_start_guard(", ""), "流式端点不得改用持锁守卫"


# ---------------------------------------------------------------- S2-6

def test_cross_site_get_is_blocked():
    """S2-6：带 `Sec-Fetch-Site: cross-site` 的 GET 必须 403（跨站 <img> 触发面）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    c = TestClient(app, base_url="http://127.0.0.1")
    blocked = c.get("/api/data/summary", headers={"Sec-Fetch-Site": "cross-site"})
    assert blocked.status_code == 403, "跨站 GET 未被拦下"
    assert "cross-site" in blocked.text


def test_same_origin_get_still_works():
    """对照组：同源 GET 正常放行（`Sec-Fetch-Site: same-origin`）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.get("/api/data/summary", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- SEC-REDACT

def test_redact_masks_mineru_and_jwt():
    """③ 正则覆盖面：`mr-` 与 JWT 形态也要掩码（原只遮 sk-）。"""
    assert "mr-" not in errs.redact("MinerU 失败：mr-abcdef1234567890 无效")
    assert "eyJhbGciOi" not in errs.redact(
        "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef")
    masked = errs.redact("key sk-abcdef123456 无效")
    assert "sk-abcdef123456" not in masked and "sk-***" in masked


def test_redact_masks_registered_secret():
    """③ 动态掩码：非标准形态的**实际**密钥（如智谱 xxx.yyyy）经登记后被掩码。"""
    secret = "abc12345.defgh67890"
    errs.register_secret(secret)
    assert secret not in errs.redact(f"请求失败：api_key {secret} 被拒")
    assert "***" in errs.redact(f"请求失败：api_key {secret} 被拒")


def test_log_project_masks_secret(tmp_path):
    """② run.log 写盘前必须脱敏（该文件会被打进 exports 备份 zip）。"""
    from medkit.routers._common import _log_project

    secret = "sk-livekey1234567890"
    _log_project(tmp_path, f"LLM 调用失败：Authorization: Bearer {secret}")
    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert secret not in text, "run.log 泄漏了密钥"
    assert "sk-***" in text


def test_llm_and_search_hints_are_redacted():
    """① 两处「原始异常串」末枝必须过 redact（它们以 200 JSON 直出，不走统一错误出口）。"""
    from medkit.core.llm import LLMClient
    from medkit.routers.search import _search_error_hint

    masked_llm = LLMClient._test_error_hint(RuntimeError("boom sk-abcdef123456"))
    assert "sk-abcdef123456" not in masked_llm
    masked_search = _search_error_hint(RuntimeError("boom sk-abcdef123456"))
    assert "sk-abcdef123456" not in masked_search
