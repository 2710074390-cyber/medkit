"""B3 回归（R8+W 修复批次）：checkpoint 断点语义与删除竞态。

覆盖：
- **S2-29**：损坏 checkpoint 必须**先备份再告警**，不得静默从头重跑（否则用户在不知情下重付 token）。
- **S2-31**：空题切片**不得**记入 `done_sids`——否则续跑永久跳过该切片，最终题数少于配额。
- **S2-30**：续跑时必须在 run.log 说明「断点只覆盖出题阶段」。
- **S2-32**：删除项目必须与启动端点共用 RUN_LOCK（否则存在 TOCTOU 窗口）。
- **S3-22**：上次异常中断（stage 非正常结束态）必须留一行可查痕迹。
"""

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import orchestrator as orch  # noqa: E402


@pytest.fixture()
def iso_cfg(tmp_path, monkeypatch):
    """隔离配置目录 + projects_dir 指向 tmp（与 test_pipeline_offline 同款）。"""
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    orig_load = cfgmod.load

    def _load():
        c = orig_load()
        c["projects_dir"] = str(tmp_path / "projects")
        return c

    monkeypatch.setattr(cfgmod, "load", _load)
    return tmp_path


def _mk_project(tmp_path: Path, pid: str = "p1", stage: str = "generating",
                with_progress: bool = False) -> tuple[Path, Path]:
    base = tmp_path / "projects" / pid
    (base / "中间产物").mkdir(parents=True, exist_ok=True)
    meta_path = base / "meta.json"
    meta_path.write_text(json.dumps({"pid": pid, "stage": stage, "seed": 42, "quota": []}),
                         encoding="utf-8")
    if with_progress:
        (base / "progress.json").write_text("{}", encoding="utf-8")
    return base, meta_path


def _log_text(base: Path) -> str:
    p = base / "run.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------------------------------------------------------------- S2-29

def test_corrupt_checkpoint_backed_up_and_logged(tmp_path):
    """截断的 checkpoint.json → 生成 .corrupt-<ts>.bak 且 run.log 有告警，返回空断点。"""
    base = tmp_path / "proj"
    (base / "中间产物").mkdir(parents=True)
    ckpt = base / "中间产物" / "checkpoint.json"
    ckpt.write_text('{"done_sids": ["S001"], "questions": [', encoding="utf-8")   # 截断

    done, qs = orch._load_checkpoint(base)

    assert done == set() and qs == []
    baks = list((base / "中间产物").glob("checkpoint.json.corrupt-*.bak"))
    assert baks, "损坏的 checkpoint 必须留证据（.corrupt-<ts>.bak）"
    assert not ckpt.exists(), "损坏文件应已改名，避免下次继续读到坏文件"
    log = _log_text(base)
    assert "断点文件损坏" in log, "必须在 run.log 留下可查的告警"
    assert "从头出题" in log


def test_valid_checkpoint_still_loads(tmp_path):
    """对照组：正常 checkpoint 照常读出，且不产生 .corrupt 备份。"""
    base = tmp_path / "proj"
    (base / "中间产物").mkdir(parents=True)
    (base / "中间产物" / "checkpoint.json").write_text(
        json.dumps({"done_sids": ["S001", "S002"], "questions": [{"id": "Q001", "sid": "S001"}]}),
        encoding="utf-8")
    done, qs = orch._load_checkpoint(base)
    assert done == {"S001", "S002"} and len(qs) == 1
    assert not list((base / "中间产物").glob("*.corrupt-*.bak"))


# ---------------------------------------------------------------- S2-31 / S2-30

def _run_generate(tmp_path, monkeypatch, pid: str, empty_sid: str, concurrency: int):
    """用假 generate_slice 跑真实 _stage_generate（空切片返回 []）。"""
    import medkit.agents.medgen as mg

    def fake_generate(client, subject, exam, slice_, count, ratios, teacher_text, **kw):
        sid = str(slice_.get("sid"))
        if sid == empty_sid:
            return [], None                      # 模拟该切片一次空返回
        return ([{"type": "A1", "bloom": "记忆", "question": f"{sid} 的题？",
                  "options": ["a", "b", "c", "d", "e"], "answer": "A",
                  "analysis": f"解析【源:切片{sid}】", "sid": sid}], None)

    monkeypatch.setattr(mg, "generate_slice", fake_generate)
    monkeypatch.setattr(orch, "PIPELINE_CONCURRENCY", concurrency)

    base, meta_path = _mk_project(tmp_path, pid)
    slices = {"S001": {"sid": "S001", "title": "一", "text": "t1"},
              "S002": {"sid": "S002", "title": "二", "text": "t2"}}
    quota = [{"sid": "S001", "count": 2}, {"sid": "S002", "count": 2}]
    questions, cancelled, _bad, done = orch._stage_generate(
        base=base, meta_path=meta_path, cancel=threading.Event(), quota=quota,
        slice_by_sid=slices, gen_client=object(), subject="儿科", exam="期末",
        ratios={"A1": 100}, teacher_text="", requirements="", knobs={}, bloom={},
        web_materials_text="", web_ref_quota=0, exam_text="", extra_text="",
        syllabus_text="", image_sections=[])
    return base, done, questions, cancelled


@pytest.mark.parametrize("concurrency", [1, 3])
def test_empty_slice_not_marked_done(tmp_path, monkeypatch, concurrency):
    """空题切片不得进 done_sids（串行与并发两路都要），且要有可查告警。"""
    base, done, questions, _c = _run_generate(tmp_path, monkeypatch, "p_empty", "S002", concurrency)

    assert "S001" in done, "正常切片应记为完成"
    assert "S002" not in done, "空题切片记完成 = 续跑永久缺口（S2-31 回归）"
    ckpt = json.loads((base / "中间产物" / "checkpoint.json").read_text(encoding="utf-8"))
    assert "S002" not in ckpt["done_sids"], "落盘内容同样不得含空切片"
    assert "S002" in _log_text(base), "必须留下「未产出任何题目」的可查痕迹"
    assert len(questions) == 1, "只应包含 S001 产出的题"


def test_resume_declares_checkpoint_scope(tmp_path, monkeypatch):
    """续跑时必须在 run.log 说明断点只覆盖出题阶段（S2-30）。"""
    base, meta_path = _mk_project(tmp_path, "p_scope")
    (base / "中间产物" / "checkpoint.json").write_text(
        json.dumps({"done_sids": ["S001"], "questions": [{"id": "Q001", "sid": "S001"}]}),
        encoding="utf-8")
    import medkit.agents.medgen as mg

    monkeypatch.setattr(mg, "generate_slice",
                        lambda *a, **kw: ([], None))          # S002 也空 → 只跑一轮
    monkeypatch.setattr(orch, "PIPELINE_CONCURRENCY", 1)
    orch._stage_generate(
        base=base, meta_path=meta_path, cancel=threading.Event(),
        quota=[{"sid": "S001", "count": 1}, {"sid": "S002", "count": 1}],
        slice_by_sid={"S001": {"sid": "S001", "title": "一", "text": "t"},
                      "S002": {"sid": "S002", "title": "二", "text": "t"}},
        gen_client=object(), subject="儿科", exam="期末", ratios={}, teacher_text="",
        requirements="", knobs={}, bloom={}, web_materials_text="", web_ref_quota=0,
        exam_text="", extra_text="", syllabus_text="", image_sections=[])
    log = _log_text(base)
    assert "发现断点" in log
    assert "断点仅覆盖" in log, "必须明示断点范围，否则用户不知道下游会重跑（S2-30）"


# ---------------------------------------------------------------- S3-22

def test_crash_detected_and_logged(iso_cfg, monkeypatch):
    """stage 停在非正常结束态 + 有 progress.json → run.log 留下「异常中断」痕迹。"""
    base, _meta = _mk_project(iso_cfg, "p_crash", stage="generating", with_progress=True)
    (base / "slices.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(orch, "_stage_websearch", lambda **kw: ({}, "", False))
    monkeypatch.setattr(orch, "_stage_generate", lambda **kw: ([], True, [], set()))

    res = orch._run_project_impl("p_crash", overrides={
        "gen": object(), "review": object(), "fix": object(), "qc": object()})

    assert res["stage"] == "cancelled"
    assert "检测到上次异常中断" in _log_text(base), "崩溃后必须留下可查痕迹（S3-22）"


# ---------------------------------------------------------------- S2-32

def test_delete_project_checks_running_under_run_lock(iso_cfg, monkeypatch):
    """RUNNING 检查与删除必须**同处 RUN_LOCK 内**（S2-32）。

    注意：**不要用时序推断**（「线程没结束 ⇒ 被锁挡住」）——本机沙箱把 `shutil.rmtree`
    换成走回收站的子进程调用（`sitecustomize._safe_shutil_rmtree → _try_trash_via_binary`），
    线程本来就会卡在那里，结论会假阳性。这里直接对锁的**持有状态**下断言。
    """
    from medkit.routers import projects as proj_router

    base = iso_cfg / "projects" / "p_lock"
    base.mkdir(parents=True)
    (base / "meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(proj_router.cfg, "load",
                        lambda: {"projects_dir": str(iso_cfg / "projects")}, raising=False)

    class SpyLock:
        def __init__(self):
            self.entries = 0
            self.held = False

        def __enter__(self):
            self.entries += 1
            self.held = True
            return self

        def __exit__(self, *a):
            self.held = False
            return False

    spy = SpyLock()
    monkeypatch.setattr(proj_router, "RUN_LOCK", spy)

    seen = {"held_during_rmtree": None, "rmtree_calls": 0}
    real_rmtree = __import__("shutil").rmtree

    def _rmtree(path, *a, **kw):
        seen["rmtree_calls"] += 1
        seen["held_during_rmtree"] = spy.held
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(proj_router.shutil, "rmtree", _rmtree)

    proj_router.delete_project("p_lock")

    assert spy.entries == 1, "删除端点必须获取 RUN_LOCK（S2-32 回归）"
    assert seen["rmtree_calls"] == 1
    assert seen["held_during_rmtree"] is True, \
        "删除必须发生在锁内——否则与启动端点之间仍有 TOCTOU 窗口"
    assert spy.held is False, "退出时应已释放锁"
