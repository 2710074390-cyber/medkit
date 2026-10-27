"""后端审查改进的回归守卫（2026-10-22）。

对应 `docs/后端审查报告_2026-10-22.md` 的三项落地，每项都配「反向可证伪」用例：

1. **422 校验错误统一体**：FastAPI 默认把 `detail` 返回成**数组**（pydantic 错误列表），
   而前端 `api()` 用 `new Error(j.detail)` 消费 —— 数组会渲染成乱码/`[object Object]`，
   用户看到的是英文内部字段路径。现在统一为 `{detail: str, error_code, fields}`。
2. **上传读取带硬上限**：`await file.read()` 会把整个上传体先 materialize 进内存，
   "声明 200MB 上限"拦不住一个 2GB 上传先吃掉 2GB。改为分块读取、超限即中止。
3. **数据统计不再全表加载**：`/api/data/summary` 原为拿几个数字把错题/知识点整表
   `SELECT *` + 逐行 `json.loads`，且目录体积走了 6 遍 `rglob`。现走 COUNT/GROUP BY
   与单次遍历。

每个检测器都带「能命中才算数」的自证：见 `test_detectors_can_actually_hit`。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.exceptions import RequestValidationError

from medkit.core import config as cfg
from medkit.core import library as lib

ROUTERS = Path(__file__).resolve().parents[1] / "medkit" / "routers"


# ---------------------------------------------------------------- 1. 422 统一体
def test_validation_error_returns_unified_envelope():
    """参数类型错误 → 422 且 detail 是**字符串**（不是 pydantic 的数组）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.get("/api/library/recommend", params={"limit": "abc"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert isinstance(body["detail"], str), f"detail 仍是 {type(body['detail']).__name__}"
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["fields"], "缺少机器可读的 fields（前端无法做行内定位）"
    assert body["fields"][0]["field"] == "limit"
    assert "需为整数" in body["detail"], body["detail"]


def test_validation_detail_maps_and_falls_back():
    """已知错误类型 → 中文；未知类型 → 通用文案（不得漏成英文原文）。"""
    from medkit.main import _validation_detail

    known = RequestValidationError([
        {"type": "int_parsing", "loc": ("query", "limit"), "msg": "Input should be …",
         "input": "abc"},
    ])
    detail, fields = _validation_detail(known)
    assert "limit" in detail and "需为整数" in detail
    assert fields == [{"field": "limit", "msg": "需为整数"}]

    unknown = RequestValidationError([
        {"type": "some_future_type", "loc": ("body", "items", 0, "name"), "msg": "boom"},
    ])
    detail2, fields2 = _validation_detail(unknown)
    assert "格式不正确" in detail2
    assert fields2[0]["field"] == "items[0].name", fields2


def test_detectors_can_actually_hit():
    """元守卫：`_field_label` 必须真的去掉前缀、保留下标（否则上面那条等于空跑）。"""
    from medkit.main import _field_label

    assert _field_label(("query", "limit")) == "limit"
    assert _field_label(("body", "items", 0, "name")) == "items[0].name"
    assert _field_label(()) == "请求"


# ---------------------------------------------------------------- 2. 上传读取上限
class _FakeUpload:
    """最小 UploadFile 替身：记录每次 read 的分块大小，便于断言"没有整块读"。"""

    def __init__(self, data: bytes, declared_size: int | None = None,
                 filename: str = "x.png") -> None:
        self._buf = data
        self._pos = 0
        self.filename = filename
        self.reads: list[int] = []
        if declared_size is not None:
            self.size = declared_size

    async def read(self, n: int = -1) -> bytes:
        self.reads.append(n)
        if self._pos >= len(self._buf):
            return b""
        end = len(self._buf) if n < 0 else self._pos + n
        chunk = self._buf[self._pos:end]
        self._pos += len(chunk)
        return chunk


def _read_capped(fake, limit, msg="过大"):
    from medkit.routers._common import read_upload_capped
    return asyncio.run(read_upload_capped(fake, limit, msg))


def test_declared_size_over_limit_aborts_before_reading():
    """multipart 解析出的 size 超限 → 一块都不读（最常见的超限场景）。"""
    from fastapi import HTTPException

    fake = _FakeUpload(b"x" * 100, declared_size=10_000)
    with pytest.raises(HTTPException) as ei:
        _read_capped(fake, 1000)
    assert ei.value.status_code == 400
    assert fake.reads == [], f"超限仍读了 {len(fake.reads)} 次"


def test_chunked_read_stops_at_limit():
    """没有 size 声明时按块读：超限立即中止，读入量不超过 limit + 1 块。"""
    from fastapi import HTTPException

    from medkit.routers._common import _UPLOAD_CHUNK

    fake = _FakeUpload(b"y" * (_UPLOAD_CHUNK * 3), declared_size=None)
    with pytest.raises(HTTPException):
        _read_capped(fake, _UPLOAD_CHUNK)          # 限 1 块，实际 3 块
    assert len(fake.reads) == 2, f"应在第 2 块就中止，实际读了 {len(fake.reads)} 次"


def test_under_limit_returns_exact_bytes():
    """正常路径必须逐字节等价（分块拼接不得改变内容）。"""
    payload = b"hello" * 1000
    fake = _FakeUpload(payload, declared_size=len(payload))
    assert _read_capped(fake, 1_000_000) == payload


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """去掉注释与字符串字面量后的 (行号, 代码) —— 文档里引用旧写法不该被当成违规。"""
    import tokenize

    kept: dict[int, list[str]] = {}
    with tokenize.open(path) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            kept.setdefault(tok.start[0], []).append(tok.string)
    return [(n, " ".join(kept[n])) for n in sorted(kept)]


def test_no_router_reads_whole_upload_without_cap():
    """静态守卫：routers 里不得再出现裸 `await x.read()`（回到"先吃内存后判上限"）。"""
    offenders: list[str] = []
    for p in sorted(ROUTERS.glob("*.py")):
        for i, code in _code_lines(p):
            if re.search(r"await\s+\w+\.read\(\s*\)", code):
                offenders.append(f"{p.name}:{i}: {code[:70]}")
    assert not offenders, (
        "以下位置仍整块读取上传体（应改用 read_upload_capped）：\n  " + "\n  ".join(offenders))


def test_capped_read_is_actually_wired_into_routers():
    """元守卫：静态守卫的扫描面必须真的覆盖到调用点（否则上一条恒绿）。"""
    wired = 0
    for p in sorted(ROUTERS.glob("*.py")):
        wired += p.read_text(encoding="utf-8").count("read_upload_capped(")
    # 定义处 1 次 + 各调用点；当前 9 个上传端点
    assert wired >= 9, f"只有 {wired} 处引用 read_upload_capped，疑似有端点漏改"


# ---------------------------------------------------------------- 3. 数据统计
def _touch(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z" * size)


def test_data_summary_uses_counts_not_full_table_loads(monkeypatch):
    """统计面板不得为了几个数字整表加载（monkeypatch 成"一调就炸"）。"""
    from medkit.routers import data as rdata

    def _boom(*_a, **_k):
        raise AssertionError("data_summary 仍在全量加载数据表")

    monkeypatch.setattr(lib, "list_mistakes", _boom)
    monkeypatch.setattr(lib, "list_knowledge", _boom)

    out = rdata.data_summary()
    assert out["counts"]["mistakes"] == 0
    assert out["counts"]["knowledge"] == 0
    assert "sessions" in out["counts"], "缺少素材会话计数（前端会显示 undefined）"
    assert "review_cards" not in out["counts"], "复习卡域已删除，不该再返回该键"


def test_data_summary_sizes_from_single_pass(monkeypatch, tmp_path):
    """单次遍历的归集结果必须与逐目录计算一致。"""
    from medkit.routers import data as rdata

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setitem(cfg.DEFAULTS, "projects_dir", str(tmp_path / "projects"))
    (tmp_path / "config.json").write_text('{"note": "' + "x" * 10 + '"}', encoding="utf-8")
    cfg_bytes = (tmp_path / "config.json").stat().st_size
    _touch(tmp_path / "presets" / "a.json", 20)
    _touch(tmp_path / "library" / "b.json", 30)
    _touch(tmp_path / "prompts" / "c.md", 40)
    _touch(tmp_path / "projects" / "p1" / "meta.json", 50)

    out = rdata.data_summary()
    assert out["size"]["config"] == cfg_bytes
    assert out["size"]["presets"] == 20
    assert out["size"]["library"] == 30
    assert out["size"]["prompts"] == 40
    assert out["size"]["projects"] == 50
    assert out["size"]["total"] == cfg_bytes + 20 + 30 + 40 + 50, out["size"]
    assert out["counts"]["projects"] == 1


def test_data_summary_reports_backup_retention_surface(monkeypatch, tmp_path):
    """备份只增不删 → 必须把「总份数 / 总占用」暴露出来（可见才可治理，不自动删用户备份）。"""
    from medkit.routers import data as rdata

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setitem(cfg.DEFAULTS, "projects_dir", str(tmp_path / "projects"))
    bdir = tmp_path / "exports" / "backups"
    for i in range(10):
        _touch(bdir / f"medkit-backup-2026100{i}-000000.zip", 100)

    out = rdata.data_summary()
    assert out["backups_count"] == 10
    assert out["backups_total"] == 1000
    assert len(out["backups"]) == 8, "回执只保留最近 8 份"


# ---------------------------------------------------------------- 4. 图片 OCR 临时文件（P0 回归）
def test_import_image_returns_text_and_cleans_up(monkeypatch, run_coro):
    """P0 回归：`NamedTemporaryFile(delete=False)` 未关闭就 unlink → Windows 抛 WinError 32。

    该异常发生在 `finally` 里，会把**成功**的 OCR 结果变成 500，并永久留下临时文件。
    旧测试只覆盖「体积超限」分支（那时临时文件还没创建），成功路径从未被执行到。
    """
    import medkit.core.mineru as mineru_mod
    import medkit.routers.library as lib_router

    cfg.CONFIG_FILE.write_text(json.dumps({"mineru": {"api_key": "k"}}), encoding="utf-8")

    seen: dict[str, str] = {}

    class _StubClient:
        def __init__(self, key: str) -> None:
            seen["key"] = key

        def mode(self) -> str:
            return "precision"

        def extract(self, path: str) -> str:
            seen["path"] = path
            assert Path(path).exists(), "OCR 期间临时文件必须还在"
            return "# 识别结果"

    monkeypatch.setattr(mineru_mod, "MinerUClient", _StubClient)

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    out = run_coro(lib_router.import_image(_FakeUpload(png, filename="x.png")))

    assert out["ok"] is True
    assert out["text"] == "# 识别结果"
    assert "path" in seen
    assert not Path(seen["path"]).exists(), f"临时文件未清理：{seen['path']}"
