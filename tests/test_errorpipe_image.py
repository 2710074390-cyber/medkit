"""EP-01 图像录入（错题图片 → 结构化字段）测试。

覆盖四层：
1. **能力判定**（`vision.looks_like_vision` / `capability`）：正面判据 + 保守方向；
2. **路由编排**（`vision.plan` / `extract` / `iter_extract`）：视觉优先、失败降级 OCR、
   两者皆败必须明确失败、流式与非流式结果必须一致；
3. **红线**（`vision.sanitize`）：无出处的 `answer` / `user_answer` 一律丢弃；
4. **路由层 + 结构守卫**：TestClient 打 `/api/errors/image/*` 与 `/api/errors/intake/image`；
   并断言「闸门字段只能由用户提供」（契约无该字段 + 端点签名必须收 Form）。

隔离沿用项目既有做法（monkeypatch 模块级路径常量 / 注入假客户端），
**不发起任何真实网络调用**、不触碰真实 `~/.medkit`。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.config as cfgmod  # noqa: E402
import medkit.core.db as dbs  # noqa: E402
import medkit.core.error_events as ev  # noqa: E402
import medkit.core.errorpipe as ep  # noqa: E402
import medkit.core.kpid as kpid  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.core.vision as vision  # noqa: E402
import medkit.main as m  # noqa: E402
from medkit.core.schema import ErrorImageExtract  # noqa: E402

ERRORS_ROUTER = ROOT / "medkit" / "routers" / "errors.py"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64          # 只需魔数正确（本层不做真实解码）
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
NOT_AN_IMAGE = b"this is plain text, not an image"

GOOD_RAW = json.dumps({
    "question": "3. 患儿男，6 岁，发热咳嗽 5 天，双肺闻及中细湿啰音。最可能的诊断是",
    "options": ["A. 急性支气管炎", "B. 支气管肺炎"],
    "answer": "B",
    "user_answer": "A",
    "analysis": "双肺固定中细湿啰音是肺炎的典型体征。",
    "subject": "儿科学",
    "chapter": "",
    "topic": "",
    "answer_from_image": True,
    "user_answer_from_image": True,
    "legible": True,
    "uncertain": [],
    "notes": "",
}, ensure_ascii=False)


# ==================================================================== 夹具
@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """库域与 kp/流水全部指到临时目录（与 test_errorpipe 同口径）。"""
    libd = tmp_path / "library"
    libd.mkdir()
    for mod in (lib, dbs, kpid, ev):
        monkeypatch.setattr(mod, "LIBRARY_DIR", libd, raising=False)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db", raising=False)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json", raising=False)
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json", raising=False)
    dbs.reset_conn()
    yield tmp_path
    dbs.reset_conn()


@pytest.fixture()
def cfg(monkeypatch):
    """隔离配置读取（`vision.capability()` / `plan()` 都经 `config.load()`）。"""
    data: dict[str, Any] = {"model_gen": "deepseek-v4-flash", "model_qc": "",
                            "mineru": {"api_key": "", "auto_ocr": True}}
    monkeypatch.setattr(cfgmod, "load", lambda: dict(data))
    return data


@pytest.fixture()
def client(isolated):
    # base_url 必须 127.0.0.1：main.py 的 Host 校验中间件会 403 掉默认 testserver
    return TestClient(m.app, base_url="http://127.0.0.1")


class FakeVisionClient:
    """假 LLM 客户端：`chat` 返回原始 JSON 文本；`chat_stream` 分块吐同一文本。"""

    def __init__(self, raw: str = GOOD_RAW, *, fail: bool = False, chunk: int = 16):
        self.raw = raw
        self.fail = fail
        self.chunk = chunk
        self.chat_calls: list[dict[str, Any]] = []
        self.stream_calls = 0

    def chat(self, messages, temperature=0.7, json_mode=False, max_tokens=None):
        self.chat_calls.append({"messages": messages, "json_mode": json_mode})
        if self.fail:
            raise RuntimeError("模拟视觉模型故障")
        return self.raw

    def chat_stream(self, messages, temperature=0.7, max_tokens=None):
        self.stream_calls += 1
        if self.fail:
            raise RuntimeError("模拟视觉模型故障")
        for i in range(0, len(self.raw), self.chunk):
            yield {"delta": self.raw[i:i + self.chunk], "usage": None, "canceled": False}


def _ocr_stub(calls: list[dict[str, Any]], *, question: str = "OCR 题干",
              fail: bool = False):
    def _f(data, mime, **kw):
        calls.append({"size": len(data), "mime": mime, "kwargs": kw})
        if fail:
            raise ValueError("模拟 OCR 故障")
        return {"fields": ErrorImageExtract(
            question=question, options=["A. 甲"], answer="B", answer_from_image=True,
            legible=True, notes="ocr").model_dump(),
            "raw_text": f"{question}\nA. 甲\n答案：B"}
    return _f


# ==================================================================== 1. 能力判定
VISION_NAMES = (
    "deepseek-v4-flash-vision-exp", "qwen-vl-max", "qwen2.5-vl-72b", "qwen3-vl",
    "glm-4v-plus", "glm-4.5v", "glm-5v", "claude-sonnet-4", "claude-3.5-sonnet",
    "gemini-2.0-flash", "gpt-4o", "gpt-4.1-mini", "gpt-5", "step-1v-8k",
    "internvl2-8b", "minicpm-v-2.6", "doubao-1.5-vision-pro",
)
NON_VISION_NAMES = (
    "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "glm-5.3",
    "qwen-plus", "qwen-max", "kimi-k2-thinking", "moonshot-v1-8k",
    "eval-model", "vlsi-parser", "my-custom-endpoint", "",
)


@pytest.mark.parametrize("name", VISION_NAMES)
def test_vision_hint_hits_known_vision_models(name):
    assert vision.looks_like_vision(name) is True, f"{name} 应判为支持视觉"


@pytest.mark.parametrize("name", NON_VISION_NAMES)
def test_vision_hint_misses_non_vision_models(name):
    """**保守方向**：认不出即 False。

    这条比上一条更重要——判错的代价不对称：把不支持视觉的模型判成支持，
    用户会拿到一个 400 报错且不知道该换模型还是该配 OCR；反过来的代价只是多走一次 OCR。
    故 `vlsi-parser`（含 `vl` 但不是词边界）与 `eval-model` 都必须判 False。
    """
    assert vision.looks_like_vision(name) is False, f"{name} 不应判为支持视觉"


def test_capability_preferred_matches_plan(cfg):
    """`capability().preferred` 必须与 `plan("auto")[0]` 一致（单一口径，不许两处各判）。"""
    for model, expect in (("qwen-vl-max", "vision"), ("deepseek-v4-flash", "ocr")):
        cfg["model_gen"] = model
        cap = vision.capability(cfg)
        assert cap["preferred"] == expect, (model, cap)
        assert vision.plan("auto", cfg=cfg)[0] == expect


def test_capability_reports_ocr_mode(cfg):
    assert vision.capability(cfg)["ocr_mode"] == "agent"      # 无 Token → 免 Token 模式
    cfg["mineru"]["api_key"] = "plain-key"
    assert vision.capability(cfg)["ocr_mode"] == "v4"


# ==================================================================== 2. 路由编排
def test_plan_matrix():
    """`plan()` 是纯函数，直接把整张表钉死（可读、可注入验证）。"""
    vision_cfg = {"model_gen": "qwen-vl-max"}
    plain_cfg = {"model_gen": "deepseek-v4-flash"}
    assert vision.plan("auto", cfg=vision_cfg) == ["vision", "ocr"]
    assert vision.plan("auto", cfg=plain_cfg) == ["ocr"]
    assert vision.plan("vision", cfg=plain_cfg) == ["vision", "ocr"]   # 强制视觉，失败仍降级
    assert vision.plan("ocr", cfg=vision_cfg) == ["ocr"]
    # 超过视觉单图上限 → 视觉被跳过（不是报错）
    big = vision.MAX_VISION_BYTES + 1
    assert vision.plan("auto", cfg=vision_cfg, size=big) == ["ocr"]


def test_extract_prefers_vision_and_never_touches_ocr(monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    ocr_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub(ocr_calls))
    cli = FakeVisionClient()

    res = vision.extract(PNG, client=cli, cfg=cfg)
    assert res["ok"] is True and res["via"] == "vision"
    assert res["fields"]["answer"] == "B"
    assert ocr_calls == [], "视觉成功时不应触碰 OCR（白花钱 + 白等）"
    assert cli.chat_calls and cli.chat_calls[0]["json_mode"] is True, "应先试 json_mode"


def test_extract_falls_back_to_ocr_when_vision_fails(monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    ocr_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub(ocr_calls, question="降级题干"))

    res = vision.extract(PNG, client=FakeVisionClient(fail=True), cfg=cfg)
    assert res["ok"] is True and res["via"] == "ocr"
    assert res["fields"]["question"] == "降级题干"
    assert [a["via"] for a in res["attempts"]] == ["vision", "ocr"]
    assert res["attempts"][0]["ok"] is False and res["attempts"][1]["ok"] is True
    assert any("降级" in w for w in res["warnings"]), res["warnings"]


def test_extract_nonvision_model_never_calls_llm(monkeypatch, cfg):
    """非视觉模型 → 直接 OCR，**一次 LLM 都不该调**（否则等于白烧钱还必然失败）。"""
    cfg["model_gen"] = "deepseek-v4-flash"
    ocr_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub(ocr_calls))
    cli = FakeVisionClient(fail=True)   # 一旦被调用就抛错 → 用例会红

    res = vision.extract(PNG, client=cli, cfg=cfg)
    assert res["via"] == "ocr" and res["ok"] is True
    assert cli.chat_calls == [] and cli.stream_calls == 0
    assert [a["via"] for a in res["attempts"]] == ["ocr"]


def test_extract_skips_vision_when_image_too_large(monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    ocr_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub(ocr_calls))
    cli = FakeVisionClient()
    big = PNG + b"\x00" * (vision.MAX_VISION_BYTES + 1)

    res = vision.extract(big, client=cli, cfg=cfg)
    assert res["via"] == "ocr"
    assert cli.chat_calls == [], "超限图不该发给视觉模型"
    assert any("超过视觉" in w for w in res["warnings"]), res["warnings"]


def test_extract_both_fail_is_loud_not_fake_success(monkeypatch, cfg):
    """两条路都失败 → `ok=False` 且带原因；**绝不能**返回"成功但字段全空"。"""
    cfg["model_gen"] = "qwen-vl-max"
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub([], fail=True))

    res = vision.extract(PNG, client=FakeVisionClient(fail=True), cfg=cfg)
    assert res["ok"] is False
    assert res["via"] == "" and res["fields"] == {}
    assert res["error"], "失败必须带原因，不许静默"
    assert [a["ok"] for a in res["attempts"]] == [False, False]


def test_extract_rejects_non_image_without_calling_any_channel(monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    ocr_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub(ocr_calls))
    cli = FakeVisionClient()

    res = vision.extract(NOT_AN_IMAGE, client=cli, cfg=cfg)
    assert res["ok"] is False and "图片" in res["error"]
    assert ocr_calls == [] and cli.chat_calls == []


def test_extract_matches_stream_result(monkeypatch, cfg):
    """`stream=True` 与 `stream=False` **两条分支不得漂移**。

    ⚠️ 这条判据的**能力边界要写清**（否则会被高估）：`extract()` 本身是
    `iter_extract()` 的消费者，所以它守的**不是**「两份独立实现互相印证」——
    那种说法是错的（本项目已有一处「同一真源两侧各取一次值相比 = 恒真」的教训）。
    它真正守的是：`iter_extract` 内部那两个分支
    （`stream=True` 走 `stream_vision` 逐块解析 / `stream=False` 走 `extract_vision`
    带 json_mode）**产出同一份结果**。两者用不同的调用与解析路径，
    任一边漏加 warning、丢 `raw_text`、少记 `attempts`，这里就会红
    （注入实证见 `.workbuddy-ai/tmp/diag_vision_guards.py` 的 parity 用例）。
    """
    cfg["model_gen"] = "qwen-vl-max"
    keys = ("ok", "via", "fields", "raw_text", "warnings", "attempts", "error")

    cli1 = FakeVisionClient()
    nonstream = vision.extract(PNG, client=cli1, cfg=cfg)

    cli2 = FakeVisionClient()
    events = list(vision.iter_extract(PNG, stream=True, client=cli2, cfg=cfg))
    assert events[0]["type"] == "stage" and events[-1]["type"] == "result"
    assert any(e["type"] == "delta" for e in events), "视觉流式路径必须吐 delta"
    streamed = events[-1]

    assert {k: nonstream[k] for k in keys} == {k: streamed[k] for k in keys}


def test_iter_extract_emits_error_less_result_on_failure(monkeypatch, cfg):
    cfg["model_gen"] = "deepseek-v4-flash"
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub([], fail=True))
    events = list(vision.iter_extract(PNG, cfg=cfg))
    assert events[-1]["type"] == "result" and events[-1]["ok"] is False


# ==================================================================== 3. 红线
def test_extract_answer_requires_provenance():
    """**红线**：没有「来自图片」声明的 answer / user_answer 一律丢弃。"""
    kept, w = vision.sanitize({"answer": "B", "answer_from_image": True})
    assert kept["answer"] == "B" and w == []

    dropped, w2 = vision.sanitize({"answer": "B", "answer_from_image": False})
    assert dropped["answer"] == "" and len(w2) == 1 and "出处" in w2[0]

    d2, w3 = vision.sanitize({"user_answer": "A", "user_answer_from_image": False})
    assert d2["user_answer"] == "" and len(w3) == 1


def test_extract_provenance_survives_end_to_end(monkeypatch, cfg):
    """模型声称有答案但没标出处 → 走完整编排后 answer 仍必须为空。"""
    cfg["model_gen"] = "qwen-vl-max"
    raw = json.dumps({"question": "题干", "answer": "B", "answer_from_image": False,
                      "legible": True}, ensure_ascii=False)
    res = vision.extract(PNG, client=FakeVisionClient(raw), cfg=cfg)
    assert res["ok"] is True
    assert res["fields"]["answer"] == ""
    assert any("出处" in w for w in res["warnings"])


# ==================================================================== 4. 契约
def test_contract_rejects_legible_but_empty_question():
    # 用具体异常类型（pydantic 的 ValidationError）而不是裸 Exception：
    # 裸 Exception 会把"构造函数拼错参数名"也当成"不变式生效"
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ErrorImageExtract(question="", legible=True)


def test_contract_bool_does_not_trust_truthy_strings():
    """`bool("false") is True` —— 必须显式解析，否则「图里没答案」会被读成「有答案」。"""
    e = ErrorImageExtract(question="q", answer_from_image="false", legible="true")
    assert e.answer_from_image is False
    assert e.legible is True
    assert ErrorImageExtract(question="q", answer_from_image="true").answer_from_image is True


def test_contract_has_no_gate_fields():
    """**红线（结构）**：识别契约**不得**出现 confidence / my_reasoning。

    这两个字段只能由用户在**看答案前**亲手填（《总纲》§3.4）。一旦模型能产出它们，
    校准数据就变成了故事——故在契约层就把通道关掉（不是靠提示词自觉）。
    """
    fields = set(ErrorImageExtract.model_fields)
    assert "confidence" not in fields
    assert "my_reasoning" not in fields


# ==================================================================== 5. 路由层
def test_capability_endpoint(client, cfg):
    r = client.get("/api/errors/image/capability")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"vision", "preferred", "ocr_mode", "reason", "max_vision_bytes"}
    assert body["preferred"] in ("vision", "ocr")


def test_extract_endpoint(client, monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    monkeypatch.setattr(vision, "extract", lambda data, prefer="auto": {
        "ok": True, "via": "vision", "fields": {"question": "q"}, "raw_text": "",
        "warnings": [], "attempts": [], "error": ""})
    r = client.post("/api/errors/image/extract",
                    files={"file": ("q.png", PNG, "image/png")})
    assert r.status_code == 200
    assert r.json()["via"] == "vision"


def test_extract_endpoint_rejects_empty_and_bad_prefer(client):
    r = client.post("/api/errors/image/extract",
                    files={"file": ("q.png", b"", "image/png")})
    assert r.status_code == 400 and "空" in r.json()["detail"]
    r2 = client.post("/api/errors/image/extract",
                     files={"file": ("q.png", PNG, "image/png")},
                     data={"prefer": "magic"})
    assert r2.status_code == 400 and "prefer" in r2.json()["detail"]


def _sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        ev, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev and data:
            out.append((ev, json.loads(data)))
    return out


def test_extract_stream_endpoint_emits_stage_delta_result(client, monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    # 端点内部经 `agents.get_client("gen")` 造客户端 → 换成假的（**不发真实网络请求**）
    import medkit.agents as agents
    monkeypatch.setattr(agents, "get_client", lambda role="gen", cancel=None: FakeVisionClient())

    r = client.post("/api/errors/image/extract/stream",
                    files={"file": ("q.png", PNG, "image/png")})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = _sse_events(r.text)
    kinds = [e for e, _ in evs]
    assert kinds[0] == "stage"
    assert "delta" in kinds, "视觉路径必须把模型原始输出流出来"
    assert kinds[-1] == "result"
    assert evs[-1][1]["ok"] is True and evs[-1][1]["via"] == "vision"
    # 流式拼回来的 JSON 必须能还原出与整段一致的字段
    assert evs[-1][1]["fields"]["answer"] == "B"


def test_extract_stream_endpoint_error_frame_on_recognition_failure(client, monkeypatch, cfg):
    """识别失败时**必须有 error 帧**——否则前端只看到连接断掉，无从判断原因。"""
    cfg["model_gen"] = "qwen-vl-max"
    import medkit.agents as agents
    monkeypatch.setattr(agents, "get_client",
                        lambda role="gen", cancel=None: FakeVisionClient(fail=True))
    monkeypatch.setattr(vision, "extract_ocr", _ocr_stub([], fail=True))

    r = client.post("/api/errors/image/extract/stream",
                    files={"file": ("q.png", PNG, "image/png")})
    evs = _sse_events(r.text)
    assert evs[-1][1]["ok"] is False
    assert any(e == "result" for e, _ in evs)


def test_intake_image_endpoint_runs_pipeline(client, monkeypatch, cfg):
    cfg["model_gen"] = "qwen-vl-max"
    monkeypatch.setattr(vision, "iter_extract", lambda data, **kw: iter([
        {"type": "stage", "via": "vision", "label": "读图…"},
        {"type": "result", "ok": True, "via": "vision",
         "fields": {"question": "题干内容", "options": ["A. 甲", "B. 乙"],
                    "answer": "B", "user_answer": "A", "analysis": "",
                    "subject": "儿科学", "chapter": "", "topic": "",
                    "answer_from_image": True, "user_answer_from_image": True,
                    "legible": True, "uncertain": [], "notes": ""},
         "raw_text": "", "warnings": [], "attempts": [], "error": ""},
    ]))

    r = client.post("/api/errors/intake/image",
                    files={"file": ("q.png", PNG, "image/png")},
                    data={"confidence": "3", "my_reasoning": "觉得是支气管炎",
                          "attribute": "0"})
    assert r.status_code == 200
    evs = _sse_events(r.text)
    kinds = [e for e, _ in evs]
    assert "fields" in kinds and kinds[-1] == "done"
    done = evs[-1][1]
    assert done["gate_ok"] is True
    card = done["card"]
    assert card["question"] == "题干内容"
    assert card["confidence"] == 3
    assert card["my_reasoning"] == "觉得是支气管炎"
    # correct 由「答案 vs 考生作答」派生（B vs A → False），不是凭空默认
    assert card["correct"] is False
    # 真的落库了
    assert any(c["id"] == card["id"] for c in lib.list_mistakes())


def test_intake_image_without_gate_fields_still_saves_but_skips_attribution(
        client, monkeypatch, cfg):
    """**图片入口不放宽闸门**：没填 confidence → 只入库，不跑 AI 归因，且明确告知。"""
    cfg["model_gen"] = "deepseek-v4-flash"
    called: list[str] = []
    monkeypatch.setattr(vision, "iter_extract", lambda data, **kw: iter([
        {"type": "result", "ok": True, "via": "ocr",
         "fields": {"question": "题干内容", "options": [], "answer": "B",
                    "user_answer": "", "analysis": "", "subject": "", "chapter": "",
                    "topic": "", "answer_from_image": True,
                    "user_answer_from_image": False, "legible": True,
                    "uncertain": [], "notes": ""},
         "raw_text": "", "warnings": [], "attempts": [], "error": ""},
    ]))
    monkeypatch.setattr(ep, "attribute_one",
                        lambda rec, client=None: called.append("attr") or {
                            "ok": True, "analysis": {}, "error": ""})

    r = client.post("/api/errors/intake/image",
                    files={"file": ("q.png", PNG, "image/png")},
                    data={"attribute": "1"})   # 故意不填 confidence / my_reasoning
    evs = _sse_events(r.text)
    done = evs[-1][1]
    assert done["gate_ok"] is False
    assert done["attributed"] is False
    assert called == [], "未过闸门却调了 LLM 归因 = 红线被绕过"
    assert any("闸门" in w for w in done["warnings"]), done["warnings"]


def test_intake_image_emits_error_frame_when_recognition_fails(client, monkeypatch, cfg):
    monkeypatch.setattr(vision, "iter_extract", lambda data, **kw: iter([
        {"type": "result", "ok": False, "via": "", "fields": {}, "raw_text": "",
         "warnings": [], "attempts": [{"via": "ocr", "ok": False, "error": "超时"}],
         "error": "识别失败（没有可用的识别通道）"},
    ]))
    r = client.post("/api/errors/intake/image",
                    files={"file": ("q.png", PNG, "image/png")},
                    data={"confidence": "3", "my_reasoning": "x"})
    evs = _sse_events(r.text)
    kinds = [e for e, _ in evs]
    assert kinds[-1] == "error"
    assert "识别失败" in evs[-1][1]["msg"]
    assert lib.list_mistakes() == [], "识别失败不应留下半条记录"


# ==================================================================== 6. 结构守卫
# 全部图像端点（与真身装饰器**双向核对**，见 test_image_endpoint_list_matches_router）
IMAGE_ENDPOINTS = ("image_capability", "image_extract",
                   "image_extract_stream", "intake_image")
# 其中**接受 `prefer` 入参**的那些（`image_capability` 是只读能力探测，没有该参数）。
# 这份清单本身也要与真身核对（见 test_prefer_endpoints_match_signatures）。
PREFER_ENDPOINTS = ("image_extract", "image_extract_stream", "intake_image")


def _router_functions() -> dict[str, Any]:
    """取 `routers/errors.py` 的模块级函数（**同步与异步都要**：端点是 `async def`）。"""
    tree = ast.parse(ERRORS_ROUTER.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _image_endpoints_from_router() -> set[str]:
    """从装饰器里**扫出**所有图像端点函数名（路径含 `image`）。

    为什么不只手写一个 tuple：手写清单与真身脱钩——新加一个图像端点却忘了登记，
    守卫照样绿（方向 A「扫描面有洞」）。这里按**路径**扫，再与手写清单双向核对：
    真身有而清单没有 ⇒ 那个端点的 `prefer` 校验与闸门入参无人守。
    """
    tree = ast.parse(ERRORS_ROUTER.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == "router"):
                continue
            if not (dec.args and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                continue
            if "image" in dec.args[0].value:
                found.add(node.name)
    return found


def test_parametrize_sources_are_not_empty():
    """**元守卫（必须非参数化）**：本文件三条 parametrize 的来源清单都不能为空。

    pytest 对空 parametrize 的处理是「收集一个 `[NOTSET]` 用例并 **SKIP**」——
    输出 `N passed, 1 skipped` 看着完全正常，实则是**用例消失**（比假绿更隐蔽）。
    而 parametrize 里**没法断言非空**（空集合根本不进函数体），故必须另起一条。
    """
    for name, seq in (("VISION_NAMES", VISION_NAMES),
                      ("NON_VISION_NAMES", NON_VISION_NAMES),
                      ("IMAGE_ENDPOINTS", IMAGE_ENDPOINTS),
                      ("PREFER_ENDPOINTS", PREFER_ENDPOINTS)):
        assert len(seq) >= 3, f"{name} 只剩 {len(seq)} 项——下面的 parametrize 会静默消失"
    # ⚠️ 刻意**不**断言「不含空项」：`NON_VISION_NAMES` 里的 `""` 是**有意保留**的样本
    #（"模型未配置" → 必须判不支持视觉）。把空串当违规 = 对合法样本假红，
    # 而假红会逼人删掉这条元守卫（方向 C）。
    for name, seq in (("VISION_NAMES", VISION_NAMES),
                      ("NON_VISION_NAMES", NON_VISION_NAMES)):
        assert sum(1 for x in seq if str(x).strip()) >= 3, f"{name} 的非空样本不足 3 个"
    # 成员检查（独立于上面的下限：下限可能被一起改小，成员不会）
    for must in ("deepseek-v4-flash-vision-exp", "qwen-vl-max", "claude-sonnet-4"):
        assert must in VISION_NAMES, f"VISION_NAMES 缺关键样本 {must}"
    for must in ("deepseek-v4-flash", "qwen-plus", "eval-model", ""):
        assert must in NON_VISION_NAMES, f"NON_VISION_NAMES 缺关键样本 {must!r}"


def test_image_endpoint_list_matches_router():
    """手写清单必须与真身装饰器**双向相等**（新增图像端点忘登记 ⇒ 红）。

    这条守卫第一次跑就抓到 `IMAGE_ENDPOINTS` 漏了 `image_capability`
    ——手写清单与真身脱钩正是方向 A 的「扫描面有洞」。
    """
    actual = _image_endpoints_from_router()
    declared = set(IMAGE_ENDPOINTS)
    assert actual == declared, (
        f"图像端点清单与 `routers/errors.py` 的装饰器不一致：\n"
        f"  真身有而清单没有 = {sorted(actual - declared)}\n"
        f"  清单有而真身没有 = {sorted(declared - actual)}\n"
        "（新增图像端点必须同步 `IMAGE_ENDPOINTS`——否则它的 prefer 校验与闸门入参无人守）")


def test_prefer_endpoints_match_signatures():
    """`PREFER_ENDPOINTS` 必须等于「图像端点里**真的收了 `prefer` 形参**的那些」。

    双向：漏登记 ⇒ 该端点的 `_check_prefer` 无人守；多登记 ⇒ 会对着没有该参数的
    端点跑参数校验用例（红得没道理）。两边都由真身签名判定，不靠人记。
    """
    fns = _router_functions()
    with_prefer = {name for name in IMAGE_ENDPOINTS
                   if "prefer" in {a.arg for a in fns[name].args.args + fns[name].args.kwonlyargs}}
    assert with_prefer == set(PREFER_ENDPOINTS), (
        f"接受 `prefer` 的图像端点 = {sorted(with_prefer)}，"
        f"而 PREFER_ENDPOINTS = {sorted(PREFER_ENDPOINTS)}")
    assert set(PREFER_ENDPOINTS) <= set(IMAGE_ENDPOINTS)


@pytest.mark.parametrize("fn_name", PREFER_ENDPOINTS)
def test_image_endpoints_validate_prefer(fn_name):
    """每个图像端点都必须调用 `_check_prefer`（**调用点真的调了**，不是"函数存在"）。

    用 AST 而不是子串：注释/文档串里写 `_check_prefer` 不算数（本项目 R20 的教训）。
    """
    fn = _router_functions()[fn_name]
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_check_prefer"]
    assert calls, f"{fn_name} 未校验 prefer —— 非法取值会被静默当成 auto"


def test_intake_image_requires_gate_fields_as_form_inputs():
    """闸门字段必须是**请求入参**（用户提供），不能来自识别结果。

    判据取 AST 参数表而不是文本匹配：`confidence` 这个词在本文件里出现多次
    （`_payload_from_fields` 的参数、`ep.gate_ok` 的说明…），子串断言会说不出
    「到底是谁的参数」（本项目 R18/R19 的魔数与同源教训）。
    """
    fn = _router_functions()["intake_image"]
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    assert {"confidence", "my_reasoning", "attribute", "prefer"} <= params, (
        "intake_image 必须接收 confidence / my_reasoning / attribute / prefer 作为请求参数——"
        "否则图片入口就成了绕过闸门的通道")
    # 默认值为空串：必填由前端保证，**后端不替用户编一个值**
    # （FastAPI 里形如 `confidence: str = Form("")` → AST 上是 `Call(Form, [Constant("")])`）
    defaults = fn.args.defaults
    named = fn.args.args[len(fn.args.args) - len(defaults):] if defaults else []
    by_name = {a.arg: d for a, d in zip(named, defaults, strict=False)}

    def _literal(node: Any) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Call) and node.args:
            return _literal(node.args[0])
        return object()   # 非字面量 → 与 "" 必然不等，用例会红并打印形态

    for key in ("confidence", "my_reasoning"):
        assert _literal(by_name.get(key)) == "", (
            f"{key} 的默认值必须是空串（未填 = 未过闸门），实际 {by_name.get(key)!r}")


def test_image_endpoint_payload_takes_gate_fields_from_args_not_fields():
    """`_payload_from_fields` 的 confidence / my_reasoning **必须来自形参**。

    这条守的是"以后有人图省事，把 `fields.get('confidence')` 接上去"——
    那会让模型（或 OCR 文本里的"把握程度"字样）间接代填闸门字段。
    """
    fn = _router_functions()["_payload_from_fields"]
    assert {"confidence", "my_reasoning"} <= {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    # 函数体内不得出现对 fields 取 confidence / my_reasoning 的下标或 .get
    bad: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value in ("confidence", "my_reasoning"):
            bad.append(str(node.slice.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value in ("confidence", "my_reasoning"):
            bad.append(str(node.args[0].value))
    assert bad == [], f"_payload_from_fields 不得从识别结果里取闸门字段：{bad}"


def test_prompt_forbids_inferring_answer():
    """提示词必须明文禁止推断答案（红线在 prompt 侧的落点，与契约/清洗三层齐备）。

    判据刻意**不绑具体措辞**（方向 H：绑书写格式 ⇒ 等价改写即假红 ⇒ 逼人删守卫）：
    - 禁令用「否定词 + 推断动词 + 答案」的**形态**匹配（换句话把同一条禁令说清楚保持绿）；
    - 出处标记查的是**契约字段名**（`answer_from_image` 等，稳定标识符而非散文）。
    删掉整条禁令 / 删掉某个出处字段 ⇒ 红。

    ⚠️ 能力边界：它只证明「提示词里写了这条规矩」，**不证明模型会遵守**。
    后者由契约（`answer_from_image` 默认 False）+ `vision.sanitize` 的行为用例把关。
    """
    text = (ROOT / "medkit" / "prompts" / "error_image_extract.md").read_text(encoding="utf-8")
    flat = "".join(text.split())        # 去空白：换行/缩进/空格不影响判定
    assert re.search(r"(不要|禁止|不得|切勿)[^。]{0,16}(给出|推断|猜|编|臆造)[^。]{0,16}答案", flat), \
        "提示词缺少「禁止推断/给出答案」的明文禁令"
    for field in ("answer_from_image", "user_answer_from_image", "legible", "uncertain"):
        assert field in text, f"提示词未要求模型填写 `{field}`（契约字段名必须出现）"
