"""AI 调用本地指标（2026-10-29 P0）的可证伪行为。

守三条：
1. **本地、不报原文**：记录里没有 question/answer 字段，只有耗时/依据/剔除/失败；
2. **样本不足不下结论**：<10 次时 `insufficient=true`，不拿 1 次算出「100% 有依据」；
3. **坏行不丢全部**：一行 JSON 损坏只跳过它，其余照常汇总。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from medkit import main as m
from medkit.core import ai_metrics


def test_record_does_not_store_content():
    rec = ai_metrics.record("ask", ms=120, grounded=True, cites=2, dropped=1, tools=3)
    assert set(rec) == {"at", "kind", "ms", "grounded", "cites", "dropped",
                        "stopped", "error", "tools"}
    text = ai_metrics.metrics_path().read_text(encoding="utf-8")
    assert "question" not in text and "answer" not in text


def test_summary_flags_insufficient_sample():
    for _ in range(3):
        ai_metrics.record("ask", ms=100, grounded=True, cites=1)
    s = ai_metrics.summary()
    assert s["calls"] == 3
    assert s["insufficient"] is True, "3 次样本不许给出结论性比率"
    assert s["grounded_rate"] == 1.0   # 数值仍给，但调用方必须看 insufficient


def test_summary_counts_stop_error_and_latency():
    for i in range(12):
        ai_metrics.record("ask", ms=100 + i * 10, grounded=(i % 2 == 0),
                          stopped=(i == 0), error=("超时" if i == 1 else ""))
    s = ai_metrics.summary()
    assert s["calls"] == 12 and s["insufficient"] is False
    assert s["stopped"] == 1 and s["errors"] == 1
    assert s["avg_ms"] == 155
    assert s["p95_ms"] >= s["avg_ms"]


def test_bad_line_does_not_drop_everything():
    ai_metrics.record("ask", ms=50, grounded=True)
    p = ai_metrics.metrics_path()
    p.write_text(p.read_text(encoding="utf-8") + "{不是 JSON}\n", encoding="utf-8")
    assert len(ai_metrics.rows()) == 1


def test_clear_removes_file():
    ai_metrics.record("ask", ms=10)
    assert ai_metrics.clear() == 1
    assert not ai_metrics.metrics_path().exists()


def test_metrics_endpoints():
    ai_metrics.record("ask", ms=80, grounded=True, cites=1)
    client = TestClient(m.app, base_url="http://127.0.0.1")
    r = client.get("/api/vault/metrics", params={"days": 7})
    assert r.status_code == 200 and r.json()["calls"] == 1
    r = client.delete("/api/vault/metrics")
    assert r.status_code == 200 and r.json()["removed"] == 1
