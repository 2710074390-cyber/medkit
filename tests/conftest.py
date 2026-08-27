"""测试全局隔离（S0）：学习库 SQL 底座默认指向临时目录，防止测试触碰真实 ~/.medkit。

背景：medkit.db 建立（SQL 模式）后，若测试只 monkeypatch 各模块的文件常量而
未隔离 `DB_FILE`，`_store_is_sql()` 会判定 SQL 模式并写真实用户库（本会话曾踩坑：
一次失败回归把真实库从 1/4/1/1 条污染到 16/11/3/6 条，已用 `.pre-db-import-*.bak` 恢复）。
本 autouse fixture 统一把 db 底座与四域模块全部重定向到本测试的 tmp_path：
DB 不存在 → 各模块自动回落 JSON 路径（既有 174+ 测试行为零变化）。
"""
from __future__ import annotations

import pytest

from medkit.core import db as dbs
from medkit.core import explain as expl
from medkit.core import library as lib
from medkit.core import review as rev
from medkit.core import tutor as tut


@pytest.fixture(autouse=True)
def _isolate_medkit_store(tmp_path, monkeypatch):
    store_dir = tmp_path / "library"
    db_file = store_dir / "medkit.db"
    monkeypatch.setattr(dbs, "LIBRARY_DIR", store_dir)
    monkeypatch.setattr(dbs, "DB_PATH", db_file)
    for mod in (lib, rev, expl, tut):
        monkeypatch.setattr(mod, "DB_FILE", db_file)
    dbs.reset_conn()
    return store_dir
