"""编排器：五阶段管线（后台线程运行）。

generating → gate1 → qc → fixing → reviewing → render → done
产物：中间产物/questions_raw.json · 质检报告/质检报告.json · 最终产物/* · 追溯日志.md
进度：每阶段写 run.log（追加）+ stage.json + progress.json；前端轮询 status。

U1/U2（2026-08 审计）：可取消（Event）+ 断点续跑（每切片落 checkpoint.json）——
取消≠丢弃：已生成题目保留，重新运行自动从断点继续。
U3：切片出题 ThreadPoolExecutor(≤3) 并发（结果按配额顺序回填，id 稳定）。
U5：结束时把实际 usage（token）与估算成本写入 meta。
U6：查重门禁（n-gram Jaccard >0.8 → warn → MedFix 改写）。
"""

import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..agents import medfix, medgen, medqc, medreview
from ..core import config as cfg
from ..core import usage
from ..core import websearch as ws
from ..core.config import resolve_key
from ..gates import bloom_check, dedup_check, options_check, trace_check
from ..render import qbank_html, review_html

B1_WEIGHT_REDIST = {"A1": 0.5, "A2": 0.4, "X": 0.1}  # B1 未支持时配额再分配
FIX_ROUNDS_GATE = 2
PAPER_DEFAULT = 50
PIPELINE_CONCURRENCY = 3  # 切片出题并发（DeepSeek 等限额友好）
TEACHER_CHAR_LIMIT = medgen.TEACHER_CHAR_LIMIT  # S2：单源常量（medgen.py 定义，管线与 trial 共用）

RENDER_MAX_OPTIONS = 6  # 渲染前终检上限（qbank_html LETTERS=10 保底防 IndexError，超限题剔除）


def _render_precheck(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """渲染前终检（D2）：仍超限/缺字段的题剔除出产物 + 记入人工复核清单。

    返回 (kept, dropped)：dropped 的元素带 _drop_reasons 字段（仅供清单使用）。
    """
    from ..gates.options_check import ALLOWED_BLOOM, ALLOWED_TYPES

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for q in questions:
        reasons: list[str] = []
        if str(q.get("type", "")) not in ALLOWED_TYPES:
            reasons.append(f"题型非法：「{q.get('type')}」")
        if str(q.get("bloom", "")) not in ALLOWED_BLOOM:
            reasons.append(f"bloom 非法：「{q.get('bloom')}」")
        if not str(q.get("question", "")).strip():
            reasons.append("题干为空")
        opts = [o for o in (q.get("options") or []) if isinstance(o, str) and o.strip()]
        if not opts:
            reasons.append("选项缺失")
        elif len(opts) > RENDER_MAX_OPTIONS:
            reasons.append(f"选项数 {len(opts)} > {RENDER_MAX_OPTIONS}（渲染上限）")
        if not str(q.get("answer", "")).strip():
            reasons.append("答案缺失")
        if reasons:
            dropped.append({**q, "_drop_reasons": reasons})
        else:
            kept.append(q)
    return kept, dropped


def _append_review_list(base: Path, dropped: list[dict[str, Any]]) -> None:
    """把渲染前剔除的题追加进人工复核清单.md（与网络冲突清单并存，不覆盖）。"""
    section = ["", "## 渲染前剔除（不产出，待人工复核）", "",
               "> 下列题目在门禁修复轮用尽后仍不满足渲染契约，已从产物中剔除，人工修正后可加回：", ""]
    for q in dropped:
        section.append(f"- **{q.get('id', '?')}** · {q.get('type', '')}型 · "
                       f"{q.get('subtopic', '')}：{'；'.join(q.get('_drop_reasons', []))}")
        section.append(f"  题面：{str(q.get('question', ''))[:120]}")
    target = base / "人工复核清单.md"
    pre = target.read_text(encoding="utf-8") + "\n" if target.exists() else ""
    target.write_text(pre + "\n".join(section) + "\n", encoding="utf-8")


class PipelineError(Exception):
    pass


class PipelineCancelled(Exception):
    """用户取消（保留断点与已生成题目）。"""


def _log(proj_dir: Path, msg: str) -> None:
    with open(proj_dir / "run.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")


def _write_json_atomic(path: Path, data: Any) -> None:
    """原子写 JSON（唯一临时名 + 共享冲突重试；实现收敛于 fsutil，v0.5）。"""
    from .fsutil import write_json_atomic

    write_json_atomic(path, data)


_PROG_LOCK = threading.Lock()


def _set_stage(proj_dir: Path, meta_path: Path, stage: str, msg: str) -> None:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["stage"] = stage
    _write_json_atomic(meta_path, meta)
    (proj_dir / "stage.json").write_text(
        json.dumps({"stage": stage, "msg": msg, "updated": datetime.now().isoformat()}),
        encoding="utf-8")
    _log(proj_dir, msg)


def _set_progress(proj_dir: Path, stage: str, done: int, total: int, detail: str = "") -> None:
    pct = round(done / total * 100) if total else (100 if stage == "done" else 0)
    with _PROG_LOCK:  # 并发切片各自写进度 → 串行化
        _write_json_atomic(proj_dir / "progress.json", {
            "stage": stage, "done": done, "total": total, "pct": pct,
            "detail": detail, "updated": datetime.now().isoformat()})


def _effective_ratios(ratios: dict[str, int]) -> dict[str, int]:
    """B1 尚未支持 → 按权重并入 A1/A2/X（批次备注记录）。"""
    out = dict(ratios)
    b1 = out.pop("B1", 0)
    if b1:
        for k, w in B1_WEIGHT_REDIST.items():
            out[k] = out.get(k, 0) + round(b1 * w)
    return {k: v for k, v in out.items() if v > 0}


def _sample_paper(questions: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """按模块+Bloom 分层取样（组卷）。"""
    n = max(10, min(n, len(questions)))
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for q in questions:
        by_key.setdefault((q.get("sid", ""), q.get("bloom", "")), []).append(q)
    for v in by_key.values():
        random.shuffle(v)
    keys = sorted(by_key.keys())
    picked: list[dict[str, Any]] = []
    i = 0
    while len(picked) < n and keys:
        for k in keys:
            bucket = by_key[k]
            if bucket and len(picked) < n:
                picked.append(bucket.pop(0))
        i += 1
        if i > 200:
            break
    return picked[:n]


def _load_checkpoint(base: Path) -> tuple[set[str], list[dict[str, Any]]]:
    ckpt = base / "中间产物" / "checkpoint.json"
    if not ckpt.exists():
        return set(), []
    try:
        data = json.loads(ckpt.read_text(encoding="utf-8"))
        return set(data.get("done_sids", [])), data.get("questions", [])
    except Exception:  # noqa: BLE001
        return set(), []


def _save_checkpoint(base: Path, done_sids: set[str], questions: list[dict[str, Any]]) -> None:
    _write_json_atomic(base / "中间产物" / "checkpoint.json", {
        "done_sids": sorted(done_sids), "questions": questions,
        "updated": datetime.now().isoformat()})


def run_project(pid: str, seed: Optional[int] = None, overrides: Optional[dict[str, Any]] = None,
                cancel: Optional[threading.Event] = None) -> dict[str, Any]:
    """同步执行整条管线；overrides 用于测试注入 Fake client；cancel 用于取消。

    returns {"stage", "questions", "qc_decision", ...}
    U5（v0.5）：按次上下文记账 — 本次 run 独立账本（trial/regen 不再串账），结束即还原。
    """
    token = usage.activate()
    try:
        return _run_project_impl(pid, seed, overrides, cancel)
    finally:
        usage.deactivate(token)


def _run_project_impl(pid: str, seed: Optional[int] = None,
                      overrides: Optional[dict[str, Any]] = None,
                      cancel: Optional[threading.Event] = None) -> dict[str, Any]:
    cancel = cancel or threading.Event()
    base = Path(cfg.load()["projects_dir"]) / pid
    meta_path = base / "meta.json"
    if not meta_path.exists():
        raise PipelineError("项目不存在")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("stage") == "done":
        return {"stage": "done", "questions": meta.get("final_count", 0), "resumed": True}
    if seed is None:
        seed = int(meta.get("seed") or 42)
    random.seed(seed)

    slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    textbook_slices = [s for s in slices if s.get("role") == "textbook"]
    teacher_slices = [s for s in slices if s.get("role") == "teacher"]
    teacher_text = "\n".join(s.get("text", "") for s in teacher_slices)
    slice_by_sid = {s["sid"]: s for s in textbook_slices}
    text_by_sid = {sid: s.get("text", "") for sid, s in slice_by_sid.items()}
    known_sids = set(slice_by_sid)

    gen_client = (overrides or {}).get("gen") or medgen.make_client()
    qc_client = (overrides or {}).get("qc") or medqc.make_client()
    fix_client = (overrides or {}).get("fix") or medfix.make_client()
    rev_client = (overrides or {}).get("review") or medreview.make_client()

    subject = meta.get("subject", "")
    exam = meta.get("exam", "期末")
    toggles = meta.get("toggles", {})
    ratios = _effective_ratios(meta.get("ratios", {}))
    quota = meta.get("quota", [])
    requirements = meta.get("requirements", "")     # 可玩性 1A
    knobs = meta.get("knobs", {})                   # 可玩性 2A
    bloom = meta.get("bloom") or None               # 可玩性 2B（空 → 默认）
    bloom_target = None
    if isinstance(bloom, dict) and sum(int(v or 0) for v in bloom.values()) > 0:
        bloom_target = {k: float(v) / 100.0 for k, v in bloom.items() if v}
    web_enabled = bool(meta.get("web_search"))      # §5.4
    web_ref_quota = int(meta.get("web_ref_quota") or 0)

    if len(teacher_text) > TEACHER_CHAR_LIMIT:
        _log(base, f"⚠️ 教师重点过长（{len(teacher_text)} 字），仅前 {TEACHER_CHAR_LIMIT} 字参与考点锚定")

    # ---------------- ⓪ 多轮网络检索（§5.4，默认关；同项目缓存）
    web_materials_text = ""
    web_materials: list[dict[str, Any]] = []
    if web_enabled:
        ckpt_file = base / "网络参考素材.json"
        inc_flag = base / "网络参考素材.incomplete"  # F2（v0.5）：取消留下的不完整标记
        if ckpt_file.exists() and not inc_flag.exists():  # 同项目缓存：重复批跑不再扣费
            try:
                web_materials = json.loads(ckpt_file.read_text(encoding="utf-8"))
                web_materials_text = ws.digest_for_prompt(web_materials)
                _log(base, f"  网络检索：使用项目缓存（{len(web_materials)} 条）")
            except Exception:  # noqa: BLE001
                web_materials = []
        elif inc_flag.exists():
            _log(base, "  网络检索：上一轮被取消（结果不完整）→ 续跑重新检索")
        if not web_materials:
            _set_stage(base, meta_path, "websearch", "⓪ 多轮网络检索（考纲/真题/指南）…")
            _set_progress(base, "websearch", 0, 1, "检索中…（首次约 1~3 分钟）")
            ws_cfg = cfg.load().get("web_search", {}) or {}
            backend = ws.resolve_backend(meta.get("web_backend", "auto") or "auto",
                                         cfg.load().get("provider", "deepseek"),
                                         resolve_key(ws_cfg.get("api_key", "")))
            chapter = next((s.get("title", "") for s in textbook_slices), "")
            keywords = teacher_text[:500]
            if backend == "manual":
                materials = ws.parse_manual(meta.get("web_manual_text", ""))
                logs_list = [f"手动粘贴素材 {len(materials)} 条"]
                err_list: list[str] = []
            else:
                if cancel.is_set():
                    _set_stage(base, meta_path, "cancelled", "⏹ 已取消（未开始生成）")
                    return {"stage": "cancelled", "questions": 0, "partial": True}
                res_search = ws.run_search_rounds(
                    gen_client, subject, chapter, keywords, backend,
                    api_key=resolve_key(ws_cfg.get("api_key", "")),
                    model=cfg.load().get("model_gen", ""),
                    slices_digest="\n\n".join(
                        f"【{s.get('title','')}】\n{s.get('text','')[:600]}"
                        for s in textbook_slices[:4]),
                    cancel=cancel)
                materials = res_search["materials"]
                logs_list = res_search["logs"]
                err_list = res_search["errors"]
                for e in err_list:
                    _log(base, f"  {e}")
            for ln in logs_list:
                _log(base, f"  {ln}")
            web_materials = materials
            web_materials_text = ws.digest_for_prompt(materials)
            (base / "网络参考素材.json").write_text(
                json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8")
            if cancel.is_set():
                # F2：检索中途取消 → 落盘结果标记 incomplete，续跑将重新检索而非复用残缺结果
                inc_flag.write_text("incomplete", encoding="utf-8")
                _set_stage(base, meta_path, "cancelled",
                           "⏹ 已取消（网络检索未完成；续跑将重新检索）")
                return {"stage": "cancelled", "questions": 0, "partial": True}
            inc_flag.unlink(missing_ok=True)
            conflicts = [m for m in materials if m.get("conflict")]
            if conflicts:
                lines = ["# 人工复核清单（网络检索冲突项）", "",
                         "> 下列网络素材与教材切片结论/数值直接矛盾，已**标记不自动改写**；"
                         "引用题不得以其为正确答案依据：", ""]
                for m in conflicts:
                    lines.append(f"- {m.get('title', '')} · {m.get('url', '')}\n"
                                 f"  {m.get('snippet', '')[:200]}")
                (base / "人工复核清单.md").write_text("\n".join(lines), encoding="utf-8")
                _log(base, f"  ⚠️ {len(conflicts)} 条素材与教材冲突 → 人工复核清单.md")
            _log(base, f"  网络检索完成：{len(materials)} 条素材（引用配额 {web_ref_quota}%）")

    # ---------------- ① MedGen 出题（并发 + 断点续跑 + 可取消）
    _set_stage(base, meta_path, "generating", "① MedGen 出题（按章节切片并发）…")
    done_sids, done_questions = _load_checkpoint(base)
    if done_sids:
        _log(base, f"  发现断点：已完成 {len(done_sids)} 个切片（{len(done_questions)} 题），跳过继续…")
    # 断点题目先并入（新完成的切片会覆盖/追加）
    ckpt_lock = threading.Lock()
    result_by_sid: dict[str, list[dict[str, Any]]] = {}
    for q in done_questions:
        result_by_sid.setdefault(q.get("sid", ""), []).append(q)

    items = [(item, int(item.get("count", 0)), item.get("sid", ""))
             for item in quota]
    items = [(it, cnt, sid) for it, cnt, sid in items if cnt > 0 and sid in slice_by_sid]
    # 预分配 id 区间（保持稳定编号；断点恢复后编号不变）
    id_ranges: list[tuple[int, int]] = []
    start = 1
    for _, cnt, _ in items:
        id_ranges.append((start, start + cnt - 1))
        start += cnt

    total_slices = len(items)
    progress_done = len(done_sids)

    def gen_one(idx: int, item: dict[str, Any], cnt: int, sid: str,
                start_id: int) -> tuple[str, list[dict[str, Any]]]:
        if cancel.is_set():
            raise PipelineCancelled()
        _set_progress(base, "generating", progress_done, total_slices,
                      f"{slice_by_sid[sid].get('title', '')[:24]}（{cnt} 题）")
        _log(base, f"  切片 {sid}（{slice_by_sid[sid].get('title', '')[:20]}）出题 {cnt} 题…")
        qs, _ = medgen.generate_slice(
            gen_client, subject, exam, slice_by_sid[sid], cnt, ratios, teacher_text,
            ids_start=start_id, requirements=requirements, knobs=knobs, bloom=bloom,
            web_materials=web_materials_text, web_quota=web_ref_quota)
        for i, q in enumerate(qs):
            q["id"] = f"Q{start_id + i:03d}"
        if len(qs) < cnt:
            _log(base, f"  ⚠️ 切片 {sid} 期望 {cnt} 题实得 {len(qs)} 题（已补足尝试，仍不足）")
        if cancel.is_set():
            raise PipelineCancelled()
        return sid, qs

    cancelled_midway = False
    try:
        if total_slices:
            if PIPELINE_CONCURRENCY <= 1:
                for idx, (item, cnt, sid) in enumerate(items):
                    if cancel.is_set():
                        raise PipelineCancelled()
                    sid_r, qs = gen_one(idx, item, cnt, sid, id_ranges[idx][0])
                    with ckpt_lock:
                        done_sids.add(sid_r)
                        result_by_sid[sid_r] = qs
                        progress_done = len(done_sids)
                        _save_checkpoint(base, done_sids, [q for v in result_by_sid.values() for q in v])
            else:
                with ThreadPoolExecutor(max_workers=PIPELINE_CONCURRENCY) as ex:
                    futures = {}
                    for idx, (item, cnt, sid) in enumerate(items):
                        if sid in done_sids:
                            continue
                        futures[ex.submit(gen_one, idx, item, cnt, sid, id_ranges[idx][0])] = sid
                    for fut in as_completed(futures):
                        sid_r, qs = fut.result()  # PipelineCancelled 会向上抛
                        with ckpt_lock:
                            done_sids.add(sid_r)
                            result_by_sid[sid_r] = qs
                            progress_done = len(done_sids)
                            _save_checkpoint(base, done_sids,
                                             [q for v in result_by_sid.values() for q in v])
                        _set_progress(base, "generating", progress_done, total_slices,
                                      f"已完成 {progress_done}/{total_slices} 切片")
    except PipelineCancelled:
        cancelled_midway = True
    except Exception as e:  # noqa: BLE001
        raise PipelineError(f"出题阶段失败：{e}") from e

    # 断点恢复：全部切片已完成 → 题目直接来自 checkpoint
    questions = []
    for _idx, (_item, _cnt, sid) in enumerate(items):
        questions.extend(result_by_sid.get(sid, []))
    if not questions and done_questions:
        questions = done_questions
    _set_progress(base, "generating", progress_done, total_slices,
                  "出题完成" if not cancelled_midway else "已取消")

    if cancelled_midway:
        _save_checkpoint(base, done_sids, questions)
        (base / "中间产物").mkdir(exist_ok=True)
        if questions:
            (base / "中间产物" / "questions_raw.json").write_text(
                json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
        _set_stage(base, meta_path, "cancelled",
                   f"⏹ 已取消：已生成 {len(questions)} 题并保留断点，可再次「开始生成」续跑")
        return {"stage": "cancelled", "questions": len(questions), "partial": True}

    if not questions:
        raise PipelineError("出题为空：请检查 API Key / 模型 / 素材")
    if questions and len(questions) < meta.get("target", 1):
        _log(base, f"  ⚠️ 题数不足：共 {len(questions)}/{meta.get('target')} 题（模型补足尝试后仍缺）")
    (base / "中间产物").mkdir(exist_ok=True)
    (base / "中间产物" / "questions_raw.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(base, f"  出题完成：{len(questions)} 题")

    # ---------------- ② 门禁①（选项/Bloom/溯源/查重 自动修复循环）
    for round_i in range(1, FIX_ROUNDS_GATE + 2):
        _set_stage(base, meta_path, "gate1", f"② 门禁① 第 {round_i} 轮…")
        _set_progress(base, "gate1", round_i - 1, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮")
        gate = {
            "options": options_check.check_all(questions),
            "bloom": bloom_check.check_bloom(questions, bloom_target),
            "trace": trace_check.check_trace(questions, known_sids),
            "dup": dedup_check.check_dup(questions),
        }
        dup_issues = [x for x in gate["dup"]["issues"] if x.get("severity") in ("fail", "warn")]
        all_issues = (gate["options"]["issues"] + gate["bloom"]["issues"]
                      + gate["trace"]["issues"] + dup_issues)
        fails = [x for x in all_issues if x["severity"] == "fail"]
        (base / "质检报告").mkdir(exist_ok=True)
        (base / "质检报告" / f"gate1_round{round_i}.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
        if not fails and not dup_issues:
            _log(base, f"  门禁① 通过（warn {len(all_issues)} 条）")
            break
        to_fix = fails + dup_issues
        if round_i <= FIX_ROUNDS_GATE:
            _log(base, f"  门禁① fails={len(fails)} dup={len(dup_issues)} → MedFix 修复第 {round_i} 轮…")
            fixed = medfix.fix_questions(fix_client, questions, to_fix, text_by_sid)
            by_id = {q["id"]: q for q in questions}
            for fq in fixed["fixed"]:
                if fq.get("id") in by_id:
                    by_id[fq["id"]] = fq
            questions = list(by_id.values())
        else:
            _log(base, f"  门禁① 修复轮次用尽，仍剩 {len(fails)} fail → 保留并转人工复核清单")
            break
    (base / "中间产物" / "questions_gate1.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------- ③ MedQC 质检（并发批次）
    if cancel.is_set():
        _set_stage(base, meta_path, "cancelled", "⏹ 已取消（题目已保留）")
        _save_checkpoint(base, done_sids, questions)
        return {"stage": "cancelled", "questions": len(questions), "partial": True}
    _set_stage(base, meta_path, "qc", "③ MedQC 质检（LLM-as-judge 并行分批）…")
    _set_progress(base, "qc", 0, (len(questions) + medqc.BATCH_SIZE - 1) // medqc.BATCH_SIZE,
                  "质检中…")
    qc_report = medqc.qc_batch(qc_client, questions, text_by_sid)
    (base / "质检报告" / "质检报告.json").write_text(
        json.dumps(qc_report, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(base, f"  QC score={qc_report['score']} decision={qc_report['gate_decision']}"
               f" issues={len(qc_report['issues'])}")
    if qc_report["gate_decision"] == "BLOCKED":
        _set_stage(base, meta_path, "fixing", "④ MedFix（质检 BLOCKED → 定向修复）…")
        _set_progress(base, "fixing", 0, 1, "定向修复中…")
        fixed = medfix.fix_questions(fix_client, questions,
                                     qc_report["issues"], text_by_sid)
        by_id = {q["id"]: q for q in questions}
        for fq in fixed["fixed"]:
            if fq.get("id") in by_id:
                by_id[fq["id"]] = fq
        questions = list(by_id.values())
        _log(base, f"  修复 {len(fixed['fixed'])} 题")
        # 修复后终检：门禁① 快速复核（不再 QC，成本控制）
        gate = {
            "options": options_check.check_all(questions),
            "bloom": bloom_check.check_bloom(questions, bloom_target),
            "trace": trace_check.check_trace(questions, known_sids),
            "dup": dedup_check.check_dup(questions),
        }
        (base / "质检报告" / "gate1_final.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
        trace_md = "\n".join(f"- {t['q_id']}: {t['reason']}" for t in fixed["trace"]) or "无修复项"
    else:
        trace_md = "质检通过（PASS / PASS_WITH_FIXES），无需修复。"
    (base / "最终产物").mkdir(exist_ok=True)
    (base / "最终产物" / "追溯日志.md").write_text(trace_md, encoding="utf-8")

    # ---------------- ④ 最终题库落盘
    _set_stage(base, meta_path, "finalizing", "④ 汇总题库…")
    for i, q in enumerate(questions):
        q.setdefault("id", f"Q{i + 1:03d}")
    # 渲染前终检（D2）：修复轮用尽仍超限/缺字段的题剔除出产物 + 人工复核清单
    questions, dropped_list = _render_precheck(questions)
    if dropped_list:
        _log(base, f"  ⚠️ 渲染前终检剔除 {len(dropped_list)} 题 → 人工复核清单.md")
        _append_review_list(base, dropped_list)
    (base / "最终产物").mkdir(exist_ok=True)
    (base / "最终产物" / "questions_final.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(base, f"  最终题库 {len(questions)} 题")

    # ---------------- ⑤ MedReview 复习手册
    review_md = ""
    if toggles.get("review", True):
        _set_stage(base, meta_path, "reviewing", "⑤ MedReview 复习手册生成…")
        _set_progress(base, "reviewing", 0, 1, "生成复习手册…")
        if cancel.is_set():
            _set_stage(base, meta_path, "cancelled", "⏹ 已取消（题目已保留）")
            return {"stage": "cancelled", "questions": len(questions), "partial": True}
        review_md = medreview.generate_review(
            rev_client, subject, exam, questions, teacher_text,
            "\n\n".join(f"【{s.get('title','')}】\n{s.get('text','')[:1200]}" for s in textbook_slices))
        (base / "最终产物" / "复习手册.md").write_text(review_md, encoding="utf-8")

    # ---------------- ⑥ 渲染产物
    _set_stage(base, meta_path, "rendering", "⑥ 渲染产物…")
    _set_progress(base, "rendering", 0, 1, "生成 HTML…")
    qbank_md = qbank_html.export_md(questions, f"{subject} 题库")
    qbank_html_text = qbank_html.export_html(questions, f"{subject} 题库")
    (base / "最终产物" / "qbank.md").write_text(qbank_md, encoding="utf-8")
    (base / "最终产物" / "qbank.html").write_text(qbank_html_text, encoding="utf-8")
    rendered = ["qbank.md", "qbank.html"]
    if toggles.get("paper", True):
        paper_qs = _sample_paper(questions, min(PAPER_DEFAULT, len(questions)))
        (base / "最终产物" / "押题卷.html").write_text(
            qbank_html.export_paper_html(paper_qs, f"{subject} 押题卷"), encoding="utf-8")
        rendered.append("押题卷.html")
    if review_md and toggles.get("review", True):
        (base / "最终产物" / "复习手册.html").write_text(
            review_html.review_to_html(review_md, f"{subject} 复习手册"), encoding="utf-8")
        rendered.append("复习手册.html")
    # Anki 导出（U8，随产物生成，项目目录留档 + 详情页可下载）
    anki_txt = qbank_html.export_anki(questions, f"{subject} 题库")
    (base / "最终产物" / "anki_export.txt").write_text(anki_txt, encoding="utf-8")
    rendered.append("anki_export.txt")

    # ---------------- ⑦ 收尾：usage 记账 + 状态
    snap = usage.snapshot()
    from .providers import get_provider as _gp  # noqa: E402
    price = (_gp(cfg.load().get("provider", "")) or {}).get("price")
    est_cost = usage.estimate_cost_cny(snap["prompt_tokens"], snap["completion_tokens"], price)
    if snap["prompt_tokens"]:
        _log(base, f"  💰 本次实际消耗：输入 {snap['prompt_tokens']} token + "
                   f"输出 {snap['completion_tokens']} token"
                   + (f" ≈ ¥{est_cost:.2f}" if est_cost is not None else ""))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["stage"] = "done"
    meta["final_count"] = len(questions)
    meta["usage"] = {**snap, "est_cost_cny": round(est_cost, 2) if est_cost is not None else None,
                     "price_unit": "元/1M token（官网为准）"}
    _write_json_atomic(meta_path, meta)
    (base / "stage.json").write_text(
        json.dumps({"stage": "done", "msg": "✅ 全部产物生成完成",
                    "updated": datetime.now().isoformat()}), encoding="utf-8")
    _set_progress_clear(base)
    _log(base, "✅ 全部产物生成完成")
    return {"stage": "done", "questions": len(questions),
            "qc_decision": qc_report["gate_decision"], "rendered": rendered}


def _set_progress_clear(base: Path) -> None:
    try:
        (base / "progress.json").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
