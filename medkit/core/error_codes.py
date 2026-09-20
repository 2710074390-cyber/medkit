"""HTTP 边界错误码（2026-09-20 审查 P3：统一散落字符串）。

历史上 ``error_code`` 以字符串字面量分散在 ``main.py`` 的异常处理器与兜底 500 中，
拼写漂移（如 ``LLM_ERR``）不会有任何静态报错。统一为本枚举后：

- **线上 JSON 的 ``error_code`` 取值保持不变**——枚举值即原字符串，前端仅做文本展示
  （``(${j.error_code})``），无需联动；
- ``str`` 混入保证 ``json.dumps``、与旧字符串的 ``==`` 比较均兼容；
- 新增边界错误码时在此集中登记，处理器引用枚举成员。
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """对外结构化错误体中的机器可读错误码。"""

    LLM_ERROR = "LLM_ERROR"                # LLM 调用失败（超时/4xx 模型侧/5xx）→ 502
    SEARCH_ERROR = "SEARCH_ERROR"          # 联网检索后端失败 → 502
    MINERU_ERROR = "MINERU_ERROR"          # MinerU OCR 失败 → 502
    PIPELINE_ERROR = "PIPELINE_ERROR"      # 出题管线内部失败 → 500
    INTERNAL_ERROR = "INTERNAL_ERROR"      # 未捕获异常兜底 → 500
