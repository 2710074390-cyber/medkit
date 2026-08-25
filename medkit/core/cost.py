"""成本预估（U5）：出题前给用户「预计消耗 X 万 token · 约 ¥Y」。

口径（2026-08 审计修订）：
- 中文 1 字 ≈ 0.8 token（DeepSeek 实测约 0.6~1.0，取中间偏保守）
- 生成阶段：每个切片 1 次调用，输入 = 切片全文 + 教师重点（system 注入一次）
- 质检：每题附 1500 字源切片 → n 题 ≈ n×1700 字输入
- 修复：按 fail 估算（默认 10% 题量，最多 2+1 轮）
- 输出：出题 ≈ 350 token/题 + 质检/复习固定开销
所有价格以所选服务商官网为准，本估算只用于决策参考。
"""

from typing import Optional

from .providers import PROVIDERS

CHARS_PER_TOKEN = 0.8
GEN_OUT_PER_Q = 350
QC_IN_CHARS_PER_Q = 1700
QC_OUT_PER_BATCH = 800
FIX_IN_CHARS_PER_Q = 1700
FIX_OUT_PER_Q = 400
REVIEW_IN_CHARS = 21000
REVIEW_OUT_CHARS_TOK = 4000


def estimate_run(chars_textbook: int, chars_teacher: int, n_slices: int,
                 n_questions: int, fail_ratio: float = 0.10) -> dict[str, int]:
    """返回 {input_tokens, output_tokens, total_tokens}（粗估，仅供决策）。"""
    gen_in = (chars_textbook + chars_teacher * max(n_slices, 1)) * CHARS_PER_TOKEN
    gen_out = n_questions * GEN_OUT_PER_Q
    qc_in = n_questions * QC_IN_CHARS_PER_Q * CHARS_PER_TOKEN
    qc_out = (n_questions / 20 + 1) * QC_OUT_PER_BATCH
    fails = int(n_questions * fail_ratio)
    fix_in = fails * FIX_IN_CHARS_PER_Q * CHARS_PER_TOKEN
    fix_out = fails * FIX_OUT_PER_Q
    rev_in = REVIEW_IN_CHARS * CHARS_PER_TOKEN
    rev_out = REVIEW_OUT_CHARS_TOK
    inp = int(gen_in + qc_in + fix_in + rev_in)
    out = int(gen_out + qc_out + fix_out + rev_out)
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def estimate_cny(provider_id: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    for p in PROVIDERS:
        if p["id"] == provider_id:
            price = p.get("price") or {}
            return (tokens_in / 1e6 * price.get("input", 0.0)
                    + tokens_out / 1e6 * price.get("output", 0.0))
    return None


def format_estimate(tokens: int, cny: Optional[float], model: str = "",
                    provider_name: str = "") -> str:
    parts = [f"预计消耗约 {tokens / 10000:.1f} 万 token"]
    if cny is not None:
        parts.append(f"约 ¥{cny:.2f}")
    if provider_name:
        parts.append(f"（{provider_name}"
                     + (f" {model}" if model else "") + " 参考价，以官网为准）")
    else:
        parts.append("（价格以所选服务商官网为准）")
    return " · ".join(parts)
