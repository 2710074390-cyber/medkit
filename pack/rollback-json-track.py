"""把学习库从 SQLite 轨回滚到 JSON 轨（运维脚本，非应用功能）。

背景（2026-09-16 R7/V-13 取证）：
- ADR-006 §4「回滚方案」写的是「库级：`db.downgrade_to(0)` + 恢复 `.pre-db-*.bak` 原文件名 →
  回到 JSON 模式」。但**按此执行回不到 JSON 模式**：`library._store_is_sql()` 的判据是
  `DB_FILE.exists()`，`downgrade_to(0)` 只是 DROP 表、**db 文件仍在** → 域模块依旧走 SQL 轨，
  读到的却是空表（数据看起来又「全空」了）。
- 且 `db.downgrade_to()` 全仓零调用方、也没有任何可执行入口 —— 文档里的回滚路径实际不可执行。
- 自 v0.10.3 起，启动路径会自动 `migrate()` + `import_from_json()`（ADR-006 迁移路径 §3），
  所以**回滚能力必须真实可用**。

本脚本实现真正可用的回滚序列（顺序不能换）：
  ① 备份并**移走** `medkit.db` / `-wal` / `-shm`（不删除；`_store_is_sql()` 因此返回 False）；
  ② 把 `*.pre-db-*.bak` 恢复回原文件名（已存在同名活文件则跳过，绝不覆盖）；
  ③ 回查：确认库域已回落到 JSON 轨且能读到数据。

⚠️ 执行前必须**关闭 MedKit**（运行中的进程持有 db 连接与单实例锁）。

用法：
    python pack/rollback-json-track.py                 # 只打印计划（默认 dry-run）
    python pack/rollback-json-track.py --yes           # 真正执行
    python pack/rollback-json-track.py --home DIR      # 指定配置根（默认 ~/.medkit）
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 备份后缀形态：migrate 的 `<name>.pre-db-<ts>.bak` / import_from_json 的
# `<name>.pre-db-import-<ts>-<uuid4>.bak`
BAK_MARK = ".pre-db-"


def _live_name(bak: Path) -> str | None:
    """由备份文件名反推原始活文件名；不是本脚本认识的备份形态 → None。"""
    name = bak.name
    i = name.find(BAK_MARK)
    if i <= 0 or not name.endswith(".bak"):
        return None
    return name[:i]


def main() -> int:
    ap = argparse.ArgumentParser(description="学习库 SQLite → JSON 轨回滚")
    ap.add_argument("--yes", action="store_true", help="真正执行（缺省只打印计划）")
    ap.add_argument("--home", default=str(Path.home() / ".medkit"),
                    help="配置根目录（默认 ~/.medkit）")
    args = ap.parse_args()

    lib = Path(args.home) / "library"
    if not lib.is_dir():
        print(f"[跳过] 未找到库目录：{lib}")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_files = [p for p in (lib / "medkit.db", lib / "medkit.db-wal", lib / "medkit.db-shm")
                if p.exists()]
    baks = sorted(p for p in lib.glob(f"*{BAK_MARK}*.bak") if _live_name(p))

    print(f"库目录：{lib}")
    print(f"\n① 移走数据库（共 {len(db_files)} 个文件 → 保留为 medkit.db.rollback-{ts}*）")
    for p in db_files:
        print(f"   {p.name} → {p.name}.rollback-{ts}")
    if not db_files:
        print("   （无 db 文件，本就处于 JSON 轨）")

    print(f"\n② 恢复 JSON 活文件（共 {len(baks)} 个备份）")
    restores: list[tuple[Path, Path]] = []
    seen: dict[str, Path] = {}
    for b in baks:
        live = _live_name(b) or ""
        target = b.with_name(live)
        if live in seen:
            print(f"   [跳过] {target.name} 已有更早的备份 {seen[live].name} 作为恢复源")
            continue
        if target.exists():
            print(f"   [跳过] 活文件已存在，不覆盖：{target.name}")
            continue
        seen[live] = b
        restores.append((b, target))
        print(f"   {b.name} → {target.name}")
    if not restores:
        print("   （无需恢复）")

    if not args.yes:
        print("\n[dry-run] 未做任何改动。确认无误后加 --yes 执行。")
        return 0

    moved: list[tuple[Path, Path]] = []
    try:
        for p in db_files:
            dst = p.with_name(f"{p.name}.rollback-{ts}")
            p.rename(dst)
            moved.append((p, dst))
        for src, dst in restores:
            if dst.exists():            # 执行期复查（计划后可能有并发写入）
                print(f"   [跳过] {dst.name} 已存在，不覆盖")
                continue
            shutil.copy2(src, dst)
    except OSError as e:
        print(f"\n[失败] {e}\n已完成的移动：{[(a.name, b.name) for a, b in moved]}")
        return 1

    # ③ 回查：库域是否真的回落 JSON 轨、数据是否读得到
    print("\n③ 回查")
    try:
        from medkit.core import db as dbs
        from medkit.core import library as lib_mod
        dbs.reset_conn()
        if lib_mod._store_is_sql(lib_mod.MISTAKES_FILE):
            print("   ❌ 仍判定为 SQL 轨（db 文件未被移走？）")
            return 1
        print(f"   ✅ 已回落 JSON 轨 · 错题 {len(lib_mod.list_mistakes())} 条 · "
              f"知识点 {len(lib_mod.list_knowledge())} 条")
    except Exception as e:  # noqa: BLE001  回查失败不影响回滚本身
        print(f"   ⚠️ 回查未完成（{e}）——请启动 MedKit 目视确认错题本有数据")

    print(f"\n回滚完成。数据库保留为 medkit.db.rollback-{ts}（未删除，可反悔）。")
    print("如需再切回 SQL 轨：直接启动 MedKit（启动路径会 migrate + 幂等补导）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
