"""routers：数据管理区（U-20）。

兑现「我的」tab 副标题承诺的「数据管理」：数据目录路径 / 一键备份 zip / 各块占用统计 /
一键打开目录 / 清空全部（输入确认短语 + 自动备份）。数据全部本机，操作走线程池不阻塞事件循环。

安全边界：
- `backup` 只读源、写「备份产物」目录，不触碰任何活数据 → 非破坏性。
- `clear` 破坏性：必须匹配确认短语；自动先做完整备份；清「学习数据 + 项目 + 会话/日志 + exports
  （保留 `exports/backups`）」，保留应用配置（config.json / presets / prompts）；
  未能删除的项**如实回执** `ok=false` + `failed`，绝不谎报成功（S2-13）。
- `restore`（导入恢复）**仍不提供自动端点**（S3-20 / R8+W 复核后维持原结论）：运行中覆盖
  SQLite 有 WAL/线程缓存一致性问题，需**重启令牌机制 + 产品交互定案**后再做。
  本轮只做**安全收口**：`backup` 回执里补 `restore_hint`，把**手动恢复步骤**（退出应用 →
  解压覆盖 → 重启）明确写给用户，使用户可见缺口闭合，而不引入半成品的自动恢复。
  → 自动化前置条件见 `docs/reviews/修复方案_R8+W_2026-09-17.md` 的「未执行」表。
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

# 参与备份的「活数据 + 应用配置」目录白名单（不含 prompts 影子副本：由内置模板可重建）
# S2-14（R8+W）：补 sessions——原实现漏掉素材会话，换机恢复后会话全丢。
_INCLUDE_PATHS = ("config.json", "presets", "library", "projects", "sessions")
# 清空 = 学习数据 + 项目 + 会话/日志（保留 config.json / presets / prompts）
# S2-13（R8+W）：原仅 library/projects，导致 exports/sessions/logs 残留。
_CLEAR_PATHS = ("library", "projects", "sessions", "logs")
# exports 清空时**保留**的子目录：`exports/backups` 是清空前刚生成的自动备份，
# 若一并删除等于亲手毁掉这次操作的安全网（S2-13 落地时的必要修正）。
_CLEAR_KEEP: dict[str, tuple[str, ...]] = {"exports": ("backups",)}


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
    _size_paths: list[tuple[str, str | None]] = [
        ("config", "config.json"), ("presets", "presets"), ("library", "library"),
        ("projects", None), ("prompts", "prompts")]
    size: dict[str, int] = {}
    for name, rel in _size_paths:
        if name == "projects":
            size[name] = _dir_size(projects_dir)
        elif rel is not None:
            size[name] = _dir_size(root / rel)
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
                # 核心备份：DB 主文件 + 活跃 JSON + **回滚点**（S3-18）。
                # 只跳过产生中的 WAL/SHM 与损坏改名 bak（.corrupt-*，无恢复价值）；
                # `.pre-db-*.bak` / `.pre-db-import-*.bak` 是真正的迁移/导入回滚点，
                # 原实现把它们一并排除，导致「换机恢复后失去回滚点」。
                for f in sorted(src.rglob("*")):
                    if not f.is_file():
                        continue
                    if f.name.endswith(("-wal", "-shm")) or ".corrupt-" in f.name:
                        continue
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
            "hint": f"已备份到 {path}",
            # S3-20（R8+W）：应用内**不提供**自动恢复（原因见模块 docstring：运行中覆盖
            # SQLite 有 WAL/连接缓存一致性问题），但把**手动恢复路径**写进回执，
            # 让用户知道备份怎么用——原实现只给「已备份到 X」，用户无从下手。
            "restore_hint": (
                "恢复方式：① 退出 MedKit；② 解压该 zip，把其中的 "
                "config.json / presets / library / projects / sessions "
                "覆盖回数据目录；③ 重新启动。"
                "覆盖前请先把当前数据目录整份另存一份——恢复属不可逆操作。")}


@router.post("/api/data/open")
async def data_open_dir() -> dict[str, Any]:
    import os

    root = cfg.CONFIG_DIR
    root.mkdir(parents=True, exist_ok=True)
    try:
        # Windows-only：os.startfile 不存在于非 Windows typeshed（Linux CI mypy attr-defined 误报）。
        # getattr 规避静态误报；运行时非 Windows 平台显式按「不支持」处理而非静默失败。
        opener = getattr(os, "startfile", None)
        if opener is None:
            raise RuntimeError("os.startfile 不可用（非 Windows 平台）")
        await asyncio.to_thread(opener, str(root))
    except Exception as e:  # noqa: BLE001
        _errs.record("data.open_dir", "打开数据目录失败", e=e)
        raise HTTPException(500, f"无法打开数据目录：{str(root)}")
    return {"ok": True, "path": str(root)}


def _clear_all_data() -> tuple[list[str], list[str]]:
    """清空学习数据 + 项目 + 会话/日志。返回 `(已删除的顶层块, 未能删除的路径)`。

    S2-13（R8+W）：原实现逐 child 吞掉 `OSError` 后**恒报成功**——运行中 SQLite 被占用时
    主库根本删不掉（Windows `WinError 32`），用户却收到 `ok=True`，重启后数据"复活"。
    现把失败项原样交给调用方，由接口如实回执（宁报 partial，不谎报成功）。
    """
    root = cfg.CONFIG_DIR
    removed: list[str] = []
    failed: list[str] = []
    for rel in (*_CLEAR_PATHS, *_CLEAR_KEEP):
        p = root / rel
        if not p.exists():
            continue
        keep = _CLEAR_KEEP.get(rel, ())
        for child in sorted(p.iterdir()):
            if child.name in keep:
                continue
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
                failed.append(str(child))
        if rel in _CLEAR_PATHS and not any(p.iterdir()):
            removed.append(rel)
    return removed, failed


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
        removed, failed = await asyncio.to_thread(_clear_all_data)
    except Exception as e:  # noqa: BLE001
        _errs.record("data.clear", "清空数据失败", e=e)
        raise HTTPException(500, "清空失败，数据可能残留，请用上面的备份恢复")
    backup = {"file": Path(path).name, "path": path}
    if failed:
        # S2-13（R8+W）：不得恒报成功——如实回 partial 并给出可执行指引
        _errs.record("data.clear", f"{len(failed)} 项未能删除（多为 SQLite 仍被占用）")
        names = "、".join(Path(f).name for f in failed[:5])
        more = f" 等 {len(failed)} 项" if len(failed) > 5 else ""
        return {"ok": False, "removed": removed, "failed": failed, "backup": backup,
                "hint": (f"部分数据未能删除（{names}{more}）——通常是数据库仍被本程序占用。"
                         f"请**完全退出 MedKit**（含托盘/黑窗）后重新打开「数据管理」再清空一次。"
                         f"清空前的完整备份已存于 {path}，本次不会丢失数据。")}
    return {"ok": True, "removed": removed, "failed": [], "backup": backup,
            "hint": f"已清空学习数据与项目；自动备份存于 {path}（请重启 MedKit 使数据库完全释放）"}
