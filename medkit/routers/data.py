"""routers：数据管理区（U-20）。

兑现「我的」tab 副标题承诺的「数据管理」：数据目录路径 / 一键备份 zip / 各块占用统计 /
一键打开目录 / 清空全部（输入确认短语 + 自动备份）。数据全部本机，操作走线程池不阻塞事件循环。

安全边界：
- `backup` 只读源、写「备份产物」目录，不触碰任何活数据 → 非破坏性。
- `clear` 破坏性：必须匹配确认短语；自动先做完整备份；只清「学习数据 + 项目」，保留应用配置。
- `restore`（导入恢复）**不在此轮提供**：运行中覆盖 SQLite 有 WAL/线程缓存一致性问题，需
  重启令牌机制与产品交互定案后再做（见 AGENT_HANDOFF / 优化清单 U-20 段）。
"""

import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..core import config as cfg
from ..core import errors as _errs
from ..core import library as lib

router = APIRouter()

BACKUPS_SUBDIR = ("exports", "backups")
_CLEAR_CONFIRM = "清空全部数据"

# 参与备份/清空的「活数据 + 应用配置」目录白名单（不含 prompts 影子副本：由内置模板可重建）
_INCLUDE_PATHS = ("config.json", "presets", "library", "projects")
# 清空 = 学习数据 + 项目（保留 config.json / presets / prompts）
_CLEAR_PATHS = ("library", "projects")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backups_dir() -> Path:
    return cfg.CONFIG_DIR.joinpath(*BACKUPS_SUBDIR)


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    n = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                n += f.stat().st_size
            except OSError:
                pass
    return n


@router.get("/api/data/summary")
def data_summary() -> dict[str, Any]:
    root = cfg.CONFIG_DIR
    projects_dir = Path(cfg.load()["projects_dir"])
    _size_paths = [("config", "config.json"), ("presets", "presets"), ("library", "library"),
                   ("projects", None), ("prompts", "prompts")]
    size = {}
    for name, rel in _size_paths:
        size[name] = _dir_size(projects_dir) if name == "projects" else _dir_size(root / rel)
    size["total"] = _dir_size(root)

    counts: dict[str, Any] = {"projects": 0}
    if projects_dir.is_dir():
        counts["projects"] = len([d for d in projects_dir.iterdir() if d.is_dir()])
    mistakes = lib.list_mistakes() if (root / "library").exists() else []
    knowledge = lib.list_knowledge() if (root / "library").exists() else []
    counts["mistakes"] = len(mistakes)
    counts["knowledge"] = len(knowledge)
    counts["review_cards"], counts["sessions"], counts["explains"] = 0, 0, 0
    # 复习卡/会话/讲解与 mistakes 同库；DB 存在时从同源计数（与 list_mistakes 同口径）
    try:
        from ..core import review as rev
        from ..core import tutor as tut

        counts["review_cards"] = len(rev.list_cards())
        counts["sessions"] = len(tut.list_sessions())
    except Exception as e:  # noqa: BLE001  U-15 留痕：只读统计失败不 500
        _errs.record("data.summary.counts", "会话/复习卡统计失败（跳过）", e=e)

    by_subject: dict[str, int] = {}
    for m in mistakes:
        s = (m.get("subject") or "未分类").strip() or "未分类"
        by_subject[s] = by_subject.get(s, 0) + 1

    bdir = _backups_dir()
    backups = []
    if bdir.is_dir():
        for f in sorted(bdir.glob("medkit-backup-*.zip"))[-8:]:
            backups.append({"name": f.name, "size": f.stat().st_size,
                            "mtime": f.stat().st_mtime})
    return {
        "dir": str(root),
        "projects_dir": str(projects_dir),
        "exists": root.exists(),
        "backups_dir": str(bdir),
        "size": size,
        "counts": counts,
        "by_subject": by_subject,
        "backups": backups,
    }


def _build_backup_zip(include_projects: bool) -> tuple[str, int]:
    root = cfg.CONFIG_DIR
    ts = _ts()
    name = f"medkit-backup-{ts}" + ("-full" if include_projects else "") + ".zip"
    bdir = _backups_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / name
    items = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _INCLUDE_PATHS:
            if rel == "projects" and not include_projects:
                continue
            src = root / rel
            if not src.exists():
                continue
            if not include_projects and rel == "library":
                # 核心备份：DB 主文件 + 活跃 JSON（跳过产生中的 WAL/损坏改名 bak）
                for f in sorted(src.rglob("*")):
                    if f.is_file() and not f.name.endswith(("-wal", "-shm", ".bak")):
                        zf.write(f, f"library/{f.relative_to(src)}")
                        items += 1
                continue
            if src.is_file():
                zf.write(src, rel)
                items += 1
            else:
                for f in sorted(src.rglob("*")):
                    if f.is_file():
                        zf.write(f, f"{rel}/{f.relative_to(src)}")
                        items += 1
    zf_size = target.stat().st_size
    return str(target), zf_size


@router.post("/api/data/backup")
async def data_backup(body: dict[str, Any] | None = None) -> dict[str, Any]:
    include_projects = bool((body or {}).get("include_projects"))
    try:
        path, size = await asyncio.to_thread(_build_backup_zip, include_projects)
    except Exception as e:  # noqa: BLE001
        _errs.record("data.backup", "备份失败", e=e)
        raise HTTPException(500, "备份失败，请重试或检查磁盘空间")
    return {"ok": True, "file": Path(path).name, "path": path, "size": size,
            "hint": f"已备份到 {path}"}


@router.post("/api/data/open")
async def data_open_dir() -> dict[str, Any]:
    import os

    root = cfg.CONFIG_DIR
    root.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(os.startfile, str(root))
    except Exception as e:  # noqa: BLE001
        _errs.record("data.open_dir", "打开数据目录失败", e=e)
        raise HTTPException(500, f"无法打开数据目录：{str(root)}")
    return {"ok": True, "path": str(root)}


def _clear_all_data() -> list[str]:
    """清空学习数据 + 项目。返回删除的顶层块列表。"""
    root = cfg.CONFIG_DIR
    removed: list[str] = []
    for rel in _CLEAR_PATHS:
        p = root / rel
        if not p.exists():
            continue
        for child in sorted(p.iterdir()):
            try:
                if child.is_dir():
                    for f in sorted(child.rglob("*")):
                        if f.is_file():
                            f.unlink(missing_ok=True)
                    child.rmdir()
                else:
                    child.unlink(missing_ok=True)
            except OSError as e:
                _errs.record("data.clear", f"清理 {child} 失败", e=e)
        if not any(p.iterdir()):
            removed.append(rel)
    return removed


@router.post("/api/data/clear")
async def data_clear(body: dict[str, Any] | None = None) -> dict[str, Any]:
    confirm = str((body or {}).get("confirm") or "")
    if confirm.strip() != _CLEAR_CONFIRM:
        raise HTTPException(400, f"请输入「{_CLEAR_CONFIRM}」以确认清空全部数据")
    # 自动完整备份（含项目）——清空不可逆，先留后路
    try:
        path, size = await asyncio.to_thread(_build_backup_zip, True)
    except Exception as e:  # noqa: BLE001
        _errs.record("data.clear.backup", "清空前自动备份失败——中止清空", e=e)
        raise HTTPException(500, "清空前自动备份失败，已中止，未删除任何数据")
    try:
        removed = await asyncio.to_thread(_clear_all_data)
    except Exception as e:  # noqa: BLE001
        _errs.record("data.clear", "清空数据失败", e=e)
        raise HTTPException(500, "清空失败，数据可能残留，请用上面的备份恢复")
    return {"ok": True, "removed": removed, "backup": {"file": Path(path).name, "path": path},
            "hint": f"已清空学习数据与项目；自动备份存于 {path}（请重启 MedKit 使数据库完全释放）"}
