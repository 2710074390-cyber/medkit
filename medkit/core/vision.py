"""错题图片 → 结构化字段（EP-01 图像录入）· 识别层。

## 这个模块解决什么

`errorpipe` 的 P1 只吃**结构化 dict**，但考生的错题往往只有一张截图。
本模块是「图片 → 结构化 dict」的那一段，产出直接喂给 `errorpipe.normalize_card()`。

## 两条识别路径（**原生视觉优先，OCR 兜底**）

```
                    ┌─ ① 原生视觉模型 ──┐  图直接进多模态 messages（image_url + base64）
图片 bytes ─────────┤                   ├─→ ErrorImageExtract 契约 → 结构化字段
                    └─ ② MinerU OCR ───┘  图 → Markdown → library.parse_question_text
```

- **优先 ①**：一次调用同时完成"认字"与"分字段"，且模型能理解版式（题干/选项/答案
  各在哪一块），错选标注也能识别。前提是当前 `model_gen` 支持图像输入。
- **兜底 ②**：模型不支持视觉时，用 MinerU 把图转成 Markdown（该能力项目早已接入），
  再用**本地规则**（`library.parse_question_text`，零 LLM）拆字段。
  这条路**完全不需要模型支持视觉**，断网也能用（MinerU agent 模式免 Token）。

## 为什么"不支持视觉"要判得保守

`looks_like_vision()` 是**正面**判据：模型名里出现已知视觉标记才算支持。
认不出来 ⇒ 判「不支持」⇒ 走 OCR。这个方向是刻意选的：

- 反了（认不出就当支持）的代价是**用户传一张图，得到一个 400 报错**，
  而用户无从知道该换个模型还是该去配 OCR；
- 现在这个方向最坏只是「多走一次 OCR」，而且 `prefer="vision"` 允许用户**强制**
  走视觉路径（覆盖自动判定），所以未知但实际支持的模型并没有被堵死。

## 红线（《总纲》§3.2）在本模块的落点

`answer` / `user_answer` **只有在模型声明「这是我在图上看到的」时才被采用**
（`answer_from_image` / `user_answer_from_image`）。声明缺失即丢弃该字段，
并在 `warnings` 里说明。理由：AI 推断出来的答案一旦写进 `mistakes.answer`，
整条校准链就建立在一个假事实上——比留空危险得多。
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from . import config as _cfg
from . import errors as _errs
from .schema import ErrorImageExtract

# 视觉路径的体积上限：base64 会再膨胀 ~33%，多数视觉端点单图上限在 5~10MB。
# 超限不报错，而是**自动改走 OCR**（MinerU 能吃 20MB），并留一条 warning——
# 这比让用户在"压缩图片"和"看不懂的报错"之间二选一友好得多。
MAX_VISION_BYTES = 8 * 1024 * 1024

# 支持的图片类型（与 routers/_common.IMAGE_SUFFIXES 同集合；此处按魔数自判，不依赖扩展名）
_MAGIC_MIME: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"BM", "image/bmp"),
    (b"RIFF", "image/webp"),        # RIFF 容器；webp 的 WEBP 标记在第 8 字节
)
_MIME_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg",
                "image/bmp": ".bmp", "image/webp": ".webp"}

# 视觉能力标记（**正面清单**）。命中任一即判"支持图像输入"。
#
# 维护约定：新增一家服务商时**只加不删**；这里不是"排除名单"，认不出只是退回 OCR，
# 不会造成功能不可用，所以宁可保守也不要为了"猜得准"塞进 `o1` 这类会误伤的短串
# （`o1` 作为子串会命中任意含 "o1" 的名字）。
VISION_MODEL_HINTS: tuple[str, ...] = (
    "vision",        # deepseek-v4-flash-vision-exp / doubao-vision / abab6.5-vision
    "claude",        # claude-3.5-sonnet / claude-sonnet-4
    "gemini",        # gemini-1.5-pro / gemini-2.0-flash
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5",
    "glm-4v", "glm-4.5v", "glm-5v",
    "step-1v",
    "internvl",
    "minicpm-v",
)
# `vl` 单独用**词边界**匹配：qwen-vl-plus / qwen3-vl / qwen2.5-vl 命中，
# 而 `eval` / `vlsi` 这类误伤被挡住。
_VL_RE = re.compile(r"(?:^|[-_.])vl(?:$|[-_.])")


def looks_like_vision(model: str) -> bool:
    """模型名是否带视觉标记（**正面判据**，认不出即 False → 走 OCR）。"""
    m = str(model or "").strip().lower()
    if not m:
        return False
    if any(h in m for h in VISION_MODEL_HINTS):
        return True
    return bool(_VL_RE.search(m))


def sniff_mime(data: bytes) -> str:
    """按魔数判图片类型；认不出返回空串（调用方据此拒绝）。**不信扩展名。**"""
    for magic, mime in _MAGIC_MIME:
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    return ""


def ocr_mode(cfg: Optional[dict[str, Any]] = None) -> str:
    """MinerU 模式：有 Token = `v4`（精准）；无 Token = `agent`（免 Token，IP 限频）。"""
    c = cfg if cfg is not None else _cfg.load()
    key = _cfg.resolve_key(str((c.get("mineru") or {}).get("api_key") or ""))
    return "v4" if key else "agent"


def capability(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """当前配置下的识别能力（供前端横幅与端点自检）。

    返回的 `preferred` 就是 `prefer="auto"` 会走的那条路——前端直接展示它，
    不做二次判断（避免前后端各判一套、口径分裂）。
    """
    c = cfg if cfg is not None else _cfg.load()
    model = str(c.get("model_gen") or "")
    vision = looks_like_vision(model)
    mode = ocr_mode(c)
    return {
        "vision": vision,
        "model": model,
        "preferred": "vision" if vision else "ocr",
        "reason": (f"模型 {model} 带视觉标记，可直接读图" if vision
                   else f"模型 {model or '（未配置）'} 未带视觉标记——"
                        "无法确认支持图像输入，将走 OCR 兜底"),
        # OCR 永远在（agent 模式免 Token）。但它是**联网**服务，离线时会失败——
        # 这一点由 `needs_network` 明说，而不是让用户以为"本地兜底一定可用"。
        "ocr_mode": mode,
        "ocr_label": "MinerU 精准解析（已配置 Token）" if mode == "v4"
                     else "MinerU 轻量解析（免 Token，按 IP 限频）",
        "ocr_needs_network": True,
        "max_vision_bytes": MAX_VISION_BYTES,
    }


# --------------------------------------------------------------------- 视觉路径
def _image_part(data: bytes, mime: str) -> dict[str, Any]:
    """OpenAI 兼容的多模态图片分片（data URL + base64）。"""
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _messages(data: bytes, mime: str) -> list[dict[str, Any]]:
    from ..agents import render_prompt

    return [
        {"role": "system", "content": render_prompt("error_image_extract.md")},
        {"role": "user", "content": [
            {"type": "text", "text": "请转录这张错题图片上的信息，按约定输出 JSON。"},
            _image_part(data, mime),
        ]},
    ]


def _parse(raw: str) -> ErrorImageExtract:
    from .llm import _extract_json

    return ErrorImageExtract.model_validate(_extract_json(raw))


def extract_vision(client: Any, data: bytes, mime: str,
                   progress: Optional[Callable[[str, str], None]] = None) -> dict[str, Any]:
    """视觉模型读图 → 结构化字段。返回 `{fields, raw_text}`（**不吞异常**）。

    `json_mode` 先试、失败退回纯文本：部分视觉端点不支持 `response_format`，
    而提示词本身已强制"只输出 JSON"，退回后 `_extract_json` 仍能剥出结构。
    """
    from .llm import LLMError

    if progress:
        progress("vision", "调用原生视觉模型读图…")
    msgs = _messages(data, mime)
    try:
        raw = client.chat(msgs, temperature=0.0, json_mode=True, max_tokens=2500)
    except LLMError:
        raw = client.chat(msgs, temperature=0.0, json_mode=False, max_tokens=2500)
    fields = _parse(raw)
    return {"fields": fields.model_dump(), "raw_text": raw}


def stream_vision(client: Any, data: bytes, mime: str,
                  progress: Optional[Callable[[str, str], None]] = None) -> Iterator[dict[str, Any]]:
    """流式版：逐块 yield `{"type": "delta", "text": ...}`，最后 yield `{"type": "result", ...}`。

    为什么值得单独做一条流式路径：视觉模型读一张医学题图常要 5~20 秒，
    全程只有一个转圈动画会让人以为卡死。把模型的原始输出**边到边显**，
    用户能看到"它在认真读图"，这是成熟客户端（Cherry Studio / LobeChat）的标准做法。
    """
    if progress:
        progress("vision", "调用原生视觉模型读图…")
    parts: list[str] = []
    for chunk in client.chat_stream(_messages(data, mime), temperature=0.0, max_tokens=2500):
        if chunk.get("canceled"):
            yield {"type": "canceled"}
            return
        delta = chunk.get("delta") or ""
        if delta:
            parts.append(delta)
            yield {"type": "delta", "text": delta}
    raw = "".join(parts)
    if not raw.strip():
        raise ValueError("视觉模型返回为空")
    fields = _parse(raw)
    yield {"type": "result", "fields": fields.model_dump(), "raw_text": raw}


# --------------------------------------------------------------------- OCR 路径
def extract_ocr(data: bytes, mime: str, *,
                cfg: Optional[dict[str, Any]] = None,
                progress: Optional[Callable[[str, str], None]] = None,
                cancel: Optional[Any] = None) -> dict[str, Any]:
    """MinerU OCR → Markdown → **本地规则**拆字段（零 LLM）。

    刻意不在 OCR 之后再调一次模型来"结构化"：`library.parse_question_text` 已经把
    题干/选项/答案/解析拆好了，再花一次 token 只为了得到同样的结构，
    既慢又引入新的失败面，还违背项目「离线优先」的取向。
    """
    from . import library as lib
    from .mineru import MinerUClient, MinerUError

    c = cfg if cfg is not None else _cfg.load()
    key = _cfg.resolve_key(str((c.get("mineru") or {}).get("api_key") or ""))
    suffix = _MIME_SUFFIX.get(mime, ".png")
    if progress:
        progress("ocr", "提交 MinerU OCR（联网，可能需要数秒到数十秒）…")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp_path = tmp.name
    try:
        markdown = MinerUClient(key).extract(
            tmp_path,
            progress=(lambda label: progress("ocr", label)) if progress else None,
            cancel=cancel)
    except MinerUError as e:
        raise ValueError(f"OCR 识别失败：{e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    text = (markdown or "").strip()
    if not text:
        raise ValueError("OCR 未识别出任何文字（图片可能过暗/过小/非题目）")
    parsed = lib.parse_question_text(text)
    fields = ErrorImageExtract(
        question=str(parsed.get("question") or ""),
        options=[str(o) for o in (parsed.get("options") or [])],
        answer=str(parsed.get("answer") or ""),
        # OCR 的文本**就是图片内容本身**，故出处标记为真（不是模型推断）
        answer_from_image=bool(parsed.get("answer")),
        analysis=str(parsed.get("analysis") or ""),
        legible=bool(str(parsed.get("question") or "").strip()),
        # OCR 只能认字，认不出"这属于哪一科"——如实标为不确定，让人工补
        uncertain=["subject", "chapter", "topic"],
        notes="经 MinerU OCR 识别（本地规则拆字段）",
    ).model_dump()
    return {"fields": fields, "raw_text": text}


# --------------------------------------------------------------------- 编排
def sanitize(fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """红线落点：无出处的 `answer` / `user_answer` 一律丢弃。返回 (清洗后字段, 警告)。"""
    out = dict(fields or {})
    warnings: list[str] = []
    for field, flag in (("answer", "answer_from_image"),
                        ("user_answer", "user_answer_from_image")):
        if str(out.get(field) or "").strip() and not out.get(flag):
            out[field] = ""
            warnings.append(
                f"{field} 没有「来自图片」的出处声明，已丢弃（不采用模型推断的答案）")
    return out, warnings


def plan(prefer: str = "auto", *, cfg: Optional[dict[str, Any]] = None,
         size: int = 0) -> list[str]:
    """决定尝试顺序。返回 `["vision", "ocr"]` 这类列表，**可被守卫直接断言**。

    - `prefer="ocr"` → 只用 OCR；
    - `prefer="vision"` → 视觉优先，失败仍降级 OCR（"降级"是兜底的本义）；
    - `prefer="auto"` → 模型支持视觉则视觉优先，否则 OCR 优先；
    - 图大于 `MAX_VISION_BYTES` → 视觉被跳过（**不是报错**），直接 OCR。
    """
    capable = capability(cfg)["vision"] if prefer == "auto" else True
    if prefer == "ocr":
        return ["ocr"]
    if size and size > MAX_VISION_BYTES:
        return ["ocr"] if prefer == "auto" else ["vision", "ocr"]
    if prefer == "auto":
        return ["vision", "ocr"] if capable else ["ocr"]
    return ["vision", "ocr"]


def iter_extract(data: bytes, *, prefer: str = "auto", stream: bool = False,
                 client: Any = None, cfg: Optional[dict[str, Any]] = None,
                 progress: Optional[Callable[[str, str], None]] = None,
                 cancel: Optional[Any] = None) -> Iterator[dict[str, Any]]:
    """识别编排的**唯一实现**：依次 yield 事件，最后一个必然是 `{"type": "result"}`。

    事件类型：
    - `stage`  —— 准备尝试某条路径（`via` / `label`），供前端显示进度；
    - `delta`  —— 视觉模型的原始输出增量（仅 `stream=True` 时）；
    - `result` —— 终态。字段与 :func:`extract` 的返回体完全一致。

    为什么让非流式 API 也走这里（`extract()` 就是本生成器的消费者）：
    两条独立的编排实现一定会漂移（一边加了降级、另一边忘了），
    而"`extract()` 的返回 == `iter_extract()` 的 result 事件"是一条**可断言**的性质
    （守卫 `test_extract_matches_stream_result` 用同一个假 client 跑两遍比对）。
    """
    mime = sniff_mime(data)
    if not mime:
        yield {"type": "result", "ok": False, "via": "", "fields": {}, "raw_text": "",
               "warnings": [], "attempts": [],
               "error": "不是可识别的图片（仅支持 PNG/JPEG/WebP/BMP）"}
        return
    if not data.strip():
        yield {"type": "result", "ok": False, "via": "", "fields": {}, "raw_text": "",
               "warnings": [], "attempts": [],
               "error": "图片为空（0 字节）——请重新拍照或截图"}
        return

    warnings: list[str] = []
    attempts: list[dict[str, Any]] = []
    order = plan(prefer, cfg=cfg, size=len(data))
    if len(data) > MAX_VISION_BYTES and (prefer == "vision" or capability(cfg)["vision"]):
        # 只要"本来会用视觉"就说明原因——否则用户看着一张 12MB 的图走 OCR，
        # 完全不知道是体积被挡了（`plan()` 已经把 vision 摘掉了，事后看不出痕迹）。
        warnings.append(
            f"图片 {len(data) // (1024 * 1024)}MB 超过视觉模型单图上限"
            f"（{MAX_VISION_BYTES // (1024 * 1024)}MB），已改用 OCR 识别")

    last_err = ""
    for via in order:
        if via == "vision" and len(data) > MAX_VISION_BYTES:
            attempts.append({"via": "vision", "ok": False, "error": "超过视觉单图上限，已跳过"})
            continue
        label = ("原生视觉模型读图…" if via == "vision" else "OCR 识别中（联网）…")
        yield {"type": "stage", "via": via, "label": label}
        # 显式标注：三条赋值分支（流式视觉 / 非流式视觉 / OCR）返回的 dict 值类型不一，
        # 让 mypy 自行 join 会推成 `Collection[str]` 之类的怪类型（实测报 arg-type）。
        got: dict[str, Any] = {}
        try:
            if via == "vision":
                cli = client
                if cli is None:
                    from ..agents import get_client
                    cli = get_client("gen")
                if stream:
                    fields: dict[str, Any] = {}
                    raw = ""
                    for ev in stream_vision(cli, data, mime, progress):
                        if ev["type"] == "delta":
                            yield ev
                        elif ev["type"] == "canceled":
                            yield {"type": "result", "ok": False, "via": "", "fields": {},
                                   "raw_text": "", "warnings": warnings, "attempts": attempts,
                                   "canceled": True, "error": "已取消"}
                            return
                        else:
                            fields, raw = ev["fields"], ev["raw_text"]
                    got = {"fields": fields, "raw_text": raw}
                else:
                    got = extract_vision(cli, data, mime, progress)
            else:
                got = extract_ocr(data, mime, cfg=cfg, progress=progress, cancel=cancel)
        except Exception as e:  # noqa: BLE001  单条路径失败要降级，不能中断编排
            last_err = str(e) or type(e).__name__
            attempts.append({"via": via, "ok": False, "error": last_err})
            _errs.record("vision.extract", f"{via} 识别失败", e=e)
            continue

        fields, dropped = sanitize(got["fields"])
        warnings.extend(dropped)
        if not str(fields.get("question") or "").strip() and fields.get("legible"):
            # 契约已保证 legible⇒question 非空，这里再挡一次"空题干入库"
            last_err = "识别结果没有题干"
            attempts.append({"via": via, "ok": False, "error": last_err})
            continue
        attempts.append({"via": via, "ok": True, "error": ""})
        if len(attempts) > 1:
            warnings.append(f"已从 {attempts[0]['via']} 降级到 {via}（原因见 attempts）")
        yield {"type": "result", "ok": True, "via": via, "fields": fields,
               "raw_text": got.get("raw_text") or "", "warnings": warnings,
               "attempts": attempts, "error": ""}
        return

    yield {"type": "result", "ok": False, "via": "", "fields": {}, "raw_text": "",
           "warnings": warnings, "attempts": attempts,
           "error": last_err or "识别失败（没有可用的识别通道）"}


_RESULT_KEYS = ("ok", "via", "fields", "raw_text", "warnings", "attempts", "error")


def extract(data: bytes, *, prefer: str = "auto",
            client: Any = None, cfg: Optional[dict[str, Any]] = None,
            progress: Optional[Callable[[str, str], None]] = None,
            cancel: Optional[Any] = None) -> dict[str, Any]:
    """图片 → 结构化字段（非流式）。返回 `{ok, via, fields, raw_text, warnings, error, attempts}`。

    **fail-soft 但不静默**：两条路都失败时 `ok=False` 且 `error` 是**最后一条**失败原因
    （前一条记在 `attempts` 里），绝不返回"看起来成功但字段全空"的结果。
    """
    out: dict[str, Any] = {}
    for ev in iter_extract(data, prefer=prefer, stream=False, client=client,
                           cfg=cfg, progress=progress, cancel=cancel):
        if ev.get("type") == "result":
            out = ev
    return {k: out.get(k) for k in _RESULT_KEYS}


__all__ = [
    "MAX_VISION_BYTES",
    "VISION_MODEL_HINTS",
    "capability",
    "extract",
    "extract_ocr",
    "extract_vision",
    "iter_extract",
    "looks_like_vision",
    "ocr_mode",
    "plan",
    "sanitize",
    "sniff_mime",
    "stream_vision",
]
