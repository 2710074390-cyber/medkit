"""W7 守卫：错误清单必须跨重启可回查（2026-10-02 二轮审计）。

背景：`core/errors.py` 的 `RECENT` 是**进程内缓冲**，重启即清空 ⇒ 用户「出问题→重启」
后无法回查「上次为何失败」。现在 `record()` 追加 JSONL 落盘，`snapshot()` 合并磁盘历史。

判据：
1. `record()` 必须落盘（读回磁盘能拿到该条）。
2. `snapshot()` 必须包含磁盘独有项（模拟重启：清空内存 → 仍能查到）。
3. 磁盘清单有上限（防无限增长），超限裁剪后仍保留最近条目。
4. 脱敏必须生效（落盘的 msg 不含明文密钥）。

运行：`pytest tests/test_persistent_errors.py -q`（零网络）
"""
from __future__ import annotations

import json

import pytest

from medkit.core import errors as errs


@pytest.fixture(autouse=True)
def _isolated_errors_log(tmp_path, monkeypatch):
    """把错误清单钉到 tmp（不碰真实 ~/.medkit）。"""
    monkeypatch.setenv("MEDKIT_ERRORS_LOG", str(tmp_path / "errors.jsonl"))
    errs.reset()
    yield
    errs.reset()


def test_record_persists_to_disk(tmp_path):
    errs.record("LLM_ERROR", "调用失败", model="deepseek")
    path = tmp_path / "errors.jsonl"
    assert path.exists(), "record() 未落盘"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[-1]["code"] == "LLM_ERROR"
    assert rows[-1]["ctx"]["model"] == "deepseek"


def test_snapshot_survives_restart(tmp_path):
    """模拟重启：写一条 → 清空内存 → snapshot 仍能查到（磁盘历史）。"""
    errs.record("GATE1", "门禁失败")
    # 模拟重启：内存态清空（进程重启等价），但磁盘文件保留
    errs.RECENT.clear()
    errs.COUNTS.clear()

    snap = errs.snapshot(limit=50)
    codes = [e["code"] for e in snap.get("recent", [])]
    assert "GATE1" in codes, f"重启后查不到历史错误：{codes}"
    assert snap.get("disk_only"), "snapshot 未标记磁盘独有项"


def test_disk_only_not_duplicated(tmp_path):
    """内存里已有的条目不应在 disk_only 里重复出现。"""
    errs.record("DUP_CHECK", "只记一次")
    snap = errs.snapshot(limit=50)
    disk_only_codes = [e["code"] for e in snap.get("disk_only", [])]
    assert "DUP_CHECK" not in disk_only_codes, "内存已有项被重复计入 disk_only"


def test_disk_trim_keeps_recent(tmp_path, monkeypatch):
    """磁盘清单超上限时裁剪，且保留**最近**条目（不是最早）。"""
    monkeypatch.setattr(errs, "_MAX_DISK_LINES", 5)
    for i in range(12):
        errs.record("BULK", f"第{i}条")
    rows = [json.loads(ln) for ln in (tmp_path / "errors.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert len(rows) <= 5, f"未裁剪：{len(rows)} 行"
    assert rows[-1]["msg"].endswith("第11条"), "裁剪后未保留最近条目"


def test_disk_write_redacts_secret(tmp_path):
    """落盘的 msg/ctx 必须已脱敏（明文密钥不得进磁盘）。"""
    errs.record("KEY_LEAK", "认证失败 sk-abcdef123456")
    raw = (tmp_path / "errors.jsonl").read_text(encoding="utf-8")
    assert "sk-abcdef123456" not in raw, "明文密钥落盘"
    assert "sk-***" in raw, "未按 sk-*** 掩码"


def test_corrupt_disk_line_does_not_crash(tmp_path):
    """磁盘文件含损坏行 → 只跳过该行，不抛异常。"""
    path = tmp_path / "errors.jsonl"
    path.write_text('{"code":"OK","msg":"好","t":"x"}\n不是JSON\n', encoding="utf-8")
    errs.RECENT.clear()
    snap = errs.snapshot(limit=50)
    assert any(e.get("code") == "OK" for e in snap["recent"]), "损坏行影响了正常行读取"
