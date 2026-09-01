"""routers 共享工具（S2 拆分自 main.py）：路径消毒 / meta 容错 / 原子写 / 切片分析 / 常量。"""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .. import state
from ..core import config as cfg
from ..core import extract as ex
from ..core.cost import CHARS_PER_TOKEN  # 单源：见 core/cost.py
from ..core.fsutil import write_json_atomic
from ..core.slice import slice_text


# ---------------------------------------------------------------- Feature flags（IMP-02）
def require_flag(name: str) -> None:
    """WP 级 feature flag 守卫：config.json `features` 节为 false 时整组接口 404（统一异常体）。

    用法：路由函数首行 `require_flag("syllabus")`。关闭 = 服务端整体下线，前端入口同批隐藏。
    """
    if not state.flag(name):
        raise HTTPException(404, f"功能「{name}」已在服务端禁用")


# ---------------------------------------------------------------- 路径与 meta
def _safe_pid(pid: str) -> str:
    """S3：pid 只允许单段安全字符，禁止 .. / \\ 等路径逃逸。"""
    if pid in {"", ".", ".."} or "/" in pid or "\\" in pid:
        raise HTTPException(400, "非法项目 ID")
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]+", pid):
        raise HTTPException(400, "非法项目 ID")
    return pid


def proj_dir(pid: str) -> Path:
    return Path(cfg.load()["projects_dir"]) / pid


def _read_meta_checked(base: Path) -> dict[str, Any]:
    """A5：meta.json 损坏 → 422（提示可删除重建），不再 500。"""
    p = base / "meta.json"
    if not p.exists():
        raise HTTPException(404, "项目不存在")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(
            422, "项目元数据损坏（可能因中途断电写坏）；可删除该项目后重新生成")
    # R4-14：解析成功但内容不是 dict（[]/字符串/数字等）→ 同样按损坏 422，
    # 否则调用方 .get(...) 抛 AttributeError → 全局 500（OWASP：对外读入强制类型校验）
    if not isinstance(data, dict):
        raise HTTPException(422, "项目元数据损坏（内容异常）；可删除该项目后重新生成")
    return data


def _write_meta_atomic(base: Path, meta: dict[str, Any]) -> None:
    """原子写统一走 fsutil（唯一 tmp 名 + 重试；旧实现固定 meta.json.tmp 无重试）。"""
    write_json_atomic(base / "meta.json", meta)


def _log_project(base: Path, msg: str) -> None:
    try:
        with open(base / "run.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 素材解析（共享）
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB（对齐 MinerU 精准 API 上限）
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TEXT_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"} | IMAGE_SUFFIXES

STAGE_LABELS = {
    "websearch": "网络检索中", "parsing": "解析素材", "quota": "配额已分配",
    "generating": "出题中",
    "gate1": "门禁①", "qc": "质检中", "fixing": "修复中",
    "finalizing": "汇总题库", "reviewing": "复习手册生成中",
    "rendering": "渲染产物", "done": "已完成", "error": "出错（见日志）",
    "cancelled": "已取消（可继续生成）",
}
STAGE_LABELS_DEFAULT: dict[str, Any] = STAGE_LABELS  # 兼容别名（不变化）


def _analyze_slices(slices: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """切片质量体检：章节结构 / 内容量 / token 估算。"""
    warnings: list[str] = []
    chapter_like = [s for s in slices if s["title"]]
    if len(chapter_like) < 2:
        warnings.append("未检测到章节标题（第X章 / 一、 / 1.1 / ##），将按全文整体切片；"
                        "建议使用带章节标题的教材版本，可自动按章分配题数")
    total_chars = sum(b["chars"] for b in blocks)
    if total_chars < 800:
        warnings.append("内容较短（疑似节选），出题素材不足时建议补充完整章节")
    if any(b.get("label", "").startswith("P") for b in blocks) and len(blocks) > 30:
        warnings.append("PDF 页数较多：建议只上传目标章节对应的页码范围/文件，降低输入成本")
    return {
        "chars": total_chars,
        "est_tokens": int(total_chars * CHARS_PER_TOKEN),
        "slice_count": len(slices),  # 契约字段（前端渲染依赖）
        "warnings": warnings,
        "slices": [{"sid": s["sid"], "title": s["title"],
                    "chars": len(s["text"]),
                    "text": s["text"],
                    "preview": s["text"][:120].replace("\n", " ")}
                   for s in slices],  # 全量返回（本地回环，无网络开销）
    }


def _mineru_to_result(name: str, markdown: str, via: str) -> dict[str, Any]:
    """MinerU OCR 结果 → 与本地解析同构的 result。"""
    text = markdown or ""
    blocks = [{"index": 0, "label": "MinerU-OCR", "text": text, "chars": len(text)}]
    slices = slice_text(blocks)
    info = _analyze_slices(slices, blocks)
    return {"name": name, "ok": True, "via": via,
            "via_note": "MinerU OCR 识别完成，已自动加入输入", **info}


def _parse_bytes(name: str, data: bytes, suffix: str) -> dict[str, Any]:
    """单文件本地解析（同步，由调用方放入线程池）。返回 result dict。"""
    if len(data) > MAX_FILE_SIZE:
        return {"name": name,
                "error": "文件超过 200 MB。建议按章节拆分成多个文件（也符合“一次一章”的推荐做法）"}
    if suffix in IMAGE_SUFFIXES:
        return {"name": name, "error": "图片文件需要「扫描件自动识别（MinerU OCR）」；"
                                        "开启后将自动识别并加入输入",
                "ocr_needed": True, "ocr_reason": "image"}
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        blocks = ex.extract_text(tmp_path)
        slices = slice_text(blocks)
        info = _analyze_slices(slices, blocks)
        return {"name": name, "ok": True, "via": "local", **info}
    except ex.ExtractError as e:
        return {"name": name, "error": str(e), "ocr_needed": True, "ocr_reason": "scan"}
    finally:
        Path(tmp_path).unlink(missing_ok=True)
