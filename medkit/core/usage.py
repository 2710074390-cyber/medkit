"""用量记账（U5）：全局累计 LLM usage，供 run.log / meta.json 记录实际成本。

LLMClient.chat 每次拿到 OpenAI usage 后调用 add()；管线结束时快照写入 meta。
线程安全（并发切片/QC 批次共用）。
"""

import threading

_LOCK = threading.Lock()
_STATE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}


def reset() -> None:
    with _LOCK:
        _STATE["prompt_tokens"] = 0
        _STATE["completion_tokens"] = 0


def add(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    with _LOCK:
        _STATE["prompt_tokens"] += int(prompt_tokens or 0)
        _STATE["completion_tokens"] += int(completion_tokens or 0)


def snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_STATE)


def estimate_cost_cny(tokens_in: int, tokens_out: int,
                      price: dict[str, float] | None) -> float | None:
    """按服务商单价（元 / 1M token，以官网为准）折算人民币；无价格表 → None。"""
    if not price:
        return None
    return tokens_in / 1e6 * price.get("input", 0.0) + tokens_out / 1e6 * price.get("output", 0.0)
