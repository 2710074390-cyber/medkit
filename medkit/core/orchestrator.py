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

import contextvars
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..agents import medfix, medgen, medqc, medreview
from ..core import config as cfg
from ..core import db as dbs
from ..core import usage
from ..core import websearch as ws
from ..core.config import resolve_key
from ..gates import bloom_check, dedup_check, options_check, trace_check
from ..render import qbank_html, review_html

FIX_ROUNDS_GATE = 2
PAPER_DEFAULT = 50
PIPELINE_CONCURRENCY = 3  # 切片出题并发（DeepSeek 等限额友好）
TEACHER_CHAR_LIMIT = medgen.TEACHER_CHAR_LIMIT  # S2：单源常量（medgen.py 定义，管线与 trial 共用）

RENDER_MAX_OPTIONS = 6  # 渲染前终检上限（qbank_html LETTERS=10 保底防 IndexError，超限题剔除）

# v0.10.0：统一阶段序列（WP-2 进度模型；前端 STEPS 与此对齐）
PIPELINE_STAGES = ["websearch", "generating", "gate1", "qc", "fixing",
                   "finalizing", "reviewing", "rendering", "done"]


def _render_precheck(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """渲染前终检（D2）：仍超限/缺字段的题剔除出产物 + 记入人工复核清单。

    返回 (kept, dropped)：dropped 的元素带 _drop_reasons 字段（仅供清单使用）。
    S3：B1 选项组用 group.options 判定选项完整性。
    """
    from ..gates.options_check import ALLOWED_BLOOM, ALLOWED_TYPES
    from ..render.qbank_html import _effective_options

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
        opts = [o for o in _effective_options(q) if o.strip()]
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


def _append_contract_review(base: Path, fails: list[dict[str, Any]]) -> None:
    """NX-03：质检契约失败批次追加进人工复核清单.md（D20：自然语言，去技术黑话）。"""
    section = ["", "## 质检判分不可信批次（需人工复核）", "",
               "> 该批题目交给 AI 质检两次，两次判分结果都不符合格式要求，"
               "因此计分未被采纳（题目本身仍保留在最终题库）。"
               "建议打开「质检报告」看一下这几批题，有问题的直接到审核台修改。", ""]
    for f in fails:
        section.append(f"- {f.get('reason', '')}")
    section.append("")
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

# WP-3：子步骤事件流（{project}/substeps.jsonl），保留最近 200 行
_SUBSTEP_LOCK = threading.Lock()
_SUBSTEP_KEEP = 200


def _substep(base: Path, stage: str, step: str, label: str,
             status: str, detail: str = "") -> None:
    """WP-3：追加写一条子步骤事件（status ∈ pending/running/done/failed/retry）。

    写入失败（磁盘只读等）静默跳过——事件流是辅助可视化，不阻断管线。
    """
    record = {"stage": stage, "step": step, "label": label,
              "status": status, "detail": detail,
              "ts": datetime.now().isoformat()}
    with _SUBSTEP_LOCK:
        path = base / "substeps.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > _SUBSTEP_KEEP:
                path.write_text("\n".join(lines[-_SUBSTEP_KEEP:]) + "\n", encoding="utf-8")
        except OSError:
            pass


def _append_manual_section(base: Path, title: str, lines: list[str]) -> None:
    """把子步骤失败/降级说明追加进人工复核清单.md（与既有清单并存不覆盖）。"""
    section = ["", f"## {title}", "", "> 子步骤失败/降级，已记录供人工复核：", ""]
    section.extend(f"- {line}" for line in lines)
    section.append("")
    target = base / "人工复核清单.md"
    pre = target.read_text(encoding="utf-8") + "\n" if target.exists() else ""
    target.write_text(pre + "\n".join(section) + "\n", encoding="utf-8")


def _run_substep(base: Path, stage: str, step: str, label: str, fn,
                 ttl: float = 60, retries: int = 2,
                 detail: str = "", on_fail=None) -> tuple[Any, Optional[Exception]]:
    """WP-3：超时/重试子步骤包装。

    每次尝试：retry → running → fn（守护线程，ttl 秒）→ done/failed；
    超时或异常写 failed 并重试（retry 事件），重试用尽返回 (None, err)，
    调用方按需求降级（写人工复核清单并继续，不中断管线）。
    """
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        if attempt:
            _substep(base, stage, step, label, "retry",
                     f"第 {attempt}/{retries} 次重试（上次：{last_err}）")
        _substep(base, stage, step, label, "running", detail)
        box: dict[str, Any] = {}
        # R3S-03：子步骤在线程执行——在父线程先取上下文，worker 内 run 才带 token 账本
        _ctx = contextvars.copy_context()

        def _target(_box: dict[str, Any] = box, _c: Any = _ctx) -> None:
            try:
                _box["result"] = _c.run(fn)
            except BaseException as e:  # noqa: BLE001  超时线程异常只回传不抛出
                _box["error"] = e

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(ttl)
        if t.is_alive():
            last_err = TimeoutError(f"子步骤超过 {ttl}s 未完成")
            _substep(base, stage, step, label, "failed", str(last_err))
            continue
        err = box.get("error")
        if err is not None:
            last_err = err
            _substep(base, stage, step, label, "failed", str(err)[:300])
            continue
        _substep(base, stage, step, label, "done", detail)
        return box.get("result"), None
    _substep(base, stage, step, label, "failed", f"重试用尽（{retries} 次），降级继续")
    if on_fail is not None:
        try:
            on_fail(last_err)
        except Exception:  # noqa: BLE001  降级回执失败不阻断
            pass
    return None, last_err


def _set_stage(proj_dir: Path, meta_path: Path, stage: str, msg: str) -> None:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["stage"] = stage
    _write_json_atomic(meta_path, meta)
    (proj_dir / "stage.json").write_text(
        json.dumps({"stage": stage, "msg": msg, "updated": datetime.now().isoformat()}),
        encoding="utf-8")
    _log(proj_dir, msg)


def _set_progress(proj_dir: Path, stage: str, done: int, total: int, detail: str = "",
                  sub: str = "", sub_done: int = 0, sub_total: int = 0) -> None:
    """写进度（WP-2）：阶段级 done/total/pct + 子步骤级 sub/sub_done/sub_total。"""
    pct = round(done / total * 100) if total else (100 if stage == "done" else 0)
    with _PROG_LOCK:  # 并发切片各自写进度 → 串行化
        _write_json_atomic(proj_dir / "progress.json", {
            "stage": stage, "done": done, "total": total, "pct": pct,
            "detail": detail, "sub": sub, "sub_done": sub_done, "sub_total": sub_total,
            "updated": datetime.now().isoformat()})


def _effective_ratios(ratios: dict[str, int]) -> dict[str, int]:
    """B1 组题已端到端支持（HC-7 契约 + 门禁/渲染/导出全链路），配额原样直达 MedGen。

    历史：v0.5 曾把 B1 按权重并入 A1/A2/X（B1 未支持时配额再分配）；S3 起渲染/门禁/审核均已适配
    group_kind=option_group，此处只做「去掉零值键」，保证 trial 与正式管线口径一致。
    """
    return {k: v for k, v in dict(ratios).items() if v > 0}


def _sample_paper_exact(questions: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """按模块+Bloom 分层取样（组卷），精确取样 n 个（不做最小 10 的抬升）。

    ME-6：案例组（A3/A4，同 case_id）视为**原子**——子题同进同出，防止子题被拆散
    后丢共享案例题干上下文；B1 选项组子题相互独立（共享选项随子题自带），可单抽。
    B26：补足抽样（N-len(reused)）需要精确数量，不能用抬升后的 _sample_paper。
    """
    n = min(n, len(questions))
    # 1) 案例组原子合并为「组块」
    groups: list[list[dict[str, Any]]] = []
    gidx: dict[str, int] = {}
    for q in questions:
        cid = q.get("case_id")
        if cid and q.get("group_kind") == "case":
            if cid not in gidx:
                gidx[cid] = len(groups)
                groups.append([])
            groups[gidx[cid]].append(q)
        else:
            groups.append([q])
    # 2) 按首个题目 (sid, bloom) 分桶（组随首题桶）
    by_key: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}
    for g in groups:
        by_key.setdefault((g[0].get("sid", ""), g[0].get("bloom", "")), []).append(g)
    for v in by_key.values():
        for g in v:
            random.shuffle(g)
        random.shuffle(v)
    keys = sorted(by_key.keys())
    picked: list[dict[str, Any]] = []
    i = 0
    while len(picked) < n and keys:
        for k in keys:
            bucket = by_key[k]
            # 桶内连续抽取（组为原子）：避免一轮只抽一组导致案例组跨轮被拆散
            while bucket and len(picked) + len(bucket[0]) <= n:
                picked.extend(bucket.pop(0))
        i += 1
        if i > 200:
            break
    return picked[:n]


def _sample_paper(questions: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """组卷入口：保底至少 10 题（押题卷页数体验），再走精确分层取样。"""
    return _sample_paper_exact(questions, max(10, n))


def _save_paper_ids(base: Path, paper_qs: list[dict[str, Any]]) -> None:
    """ME-7：记录押题卷抽样的题目 id —— 审核台「保存并重渲染」据此复用，避免抽样漂移。"""
    try:
        (base / "最终产物" / "paper_ids.json").write_text(
            json.dumps({"ids": [q.get("id") for q in paper_qs]}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:  # 写失败不阻断渲染（仅失去防漂移能力）
        pass


def _gate_image_refs(questions: list[dict[str, Any]],
                     image_sids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """B28：image_ref 门禁——任何 image_ref 不在 image_sids 中都剔除并返回被剔除 id。
    不再要求 image_sids 非空：未传图项目的幻觉 image_ref 同样被拦截（防伪图题零提示）。"""
    dropped = [q.get("id") for q in questions
               if q.get("image_ref") and q.get("image_ref") not in image_sids]
    kept = [q for q in questions
            if not (q.get("image_ref") and q.get("image_ref") not in image_sids)]
    return kept, dropped


def select_paper_stable(saved_ids: list[str], questions: list[dict[str, Any]],
                        n: int = PAPER_DEFAULT) -> list[dict[str, Any]]:
    """ME-7：押题卷抽样防漂移——优先复用上次抽样（仍存在于题库的 id）。
    B26：复用不足时不再整卷重抽——保留已复用题 + 从剩余池补足（抽样 N-len(reused) 追加），
    剔除/重掷场景下已复用题的顺序与成员不被洗牌。"""
    by_id = {q.get("id"): q for q in questions}
    picked = [by_id[i] for i in (saved_ids or []) if i in by_id]
    need = min(n, len(questions))
    if len(picked) < need:
        picked_ids = {q.get("id") for q in picked}
        remaining = [q for q in questions if q.get("id") not in picked_ids]
        if remaining:
            picked = picked + _sample_paper_exact(remaining, need - len(picked))
    return picked


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


def build_image_index(base: Path,
                      slices: Optional[list[dict[str, Any]]] = None) -> tuple[dict[str, Any], str]:
    """WP-04：从 slices.json 重建图像索引与提示词清单（管线初跑与审核台重渲染共用）。

    R3S-02：审核台 _rerender_project 原先不传 image_index → 重渲染后图题全丢图；
    现在两处统一走本函数，渲染层（qbank_html.render_media）缺图时也给占位提示。
    返回 (image_index, image_sections)：image_index = {sid: {"path": Path, "caption": str}}。
    """
    if slices is None:
        try:
            slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  切片损坏 → 空索引（题目保留，渲染给占位）
            slices = []
    image_index: dict[str, Any] = {}
    image_sections = ""
    for _s in slices:
        if _s.get("role") != "image" or not (_s.get("image") or {}).get("path"):
            continue
        _sid = _s.get("sid") or ""
        if not _sid:
            continue
        image_index[_sid] = {"path": base / str(_s["image"]["path"]),
                             "caption": _s.get("text") or _s.get("title") or ""}
        image_sections += f"[{_sid}] {_s.get('text') or _s.get('title') or ''}\n"
    return image_index, image_sections


def _review_slice_digest(slices: list[dict[str, Any]], per_slice: int = 1200,
                         budget: int = 6000) -> str:
    """B32：复习手册教材侧预算按切片顺序轮转分配（每切片每轮至多 per_slice 字，直到预算耗尽）。
    每轮先按「剩余预算 ÷ 还有文字的切片数」给本轮均摊份额（上限 per_slice），再按切片顺序取用——
    切片多时每片都能分到预算，不再「前约 5 切片各 1200 字截到 6000」，保证后面章节也进入手册。"""
    if not slices:
        return ""
    texts = [str(s.get("text") or "") for s in slices]
    out: list[str] = []
    left = budget
    while left > 0:
        active = [i for i, t in enumerate(texts) if t]
        if not active:
            break
        share = max(1, left // len(active)) if len(active) > 1 else left
        share = min(share, per_slice)
        progressed = False
        for i in active:
            if left <= 0:
                break
            take = min(share, len(texts[i]), left)
            out.append(f"【{slices[i].get('title','')}】\n{texts[i][:take]}")
            texts[i] = texts[i][take:]
            left -= take
            progressed = True
        if not progressed:
            break
    return "\n\n".join(out)


def _cancel_out(base: Path, meta_path: Path, done_sids: set[str],
                questions: list[dict[str, Any]], msg: str = "题目已保留") -> dict[str, Any]:
    """B24：后段各阶段取消出口——标 cancelled、落断点、返回部分结果（usage 由外层记账）。"""
    _set_stage(base, meta_path, "cancelled", f"⏹ 已取消（{msg}）")
    _save_checkpoint(base, done_sids, questions)
    return {"stage": "cancelled", "questions": len(questions), "partial": True}


def _record_usage_on_exit(pid: str, *, cancelled: bool) -> None:
    """B27/R3S-03：取消/失败路径也落 usage（已烧 token 不失踪；「费用透明」承诺）。

    只在有实际 token 消耗时写 meta，避免污染从未调用 LLM 的失败（如参数校验错误）。
    """
    try:
        base = Path(cfg.load()["projects_dir"]) / pid
        meta_path = base / "meta.json"
        if not meta_path.exists():
            return
        snap = usage.snapshot()
        if not snap["prompt_tokens"] and not snap["completion_tokens"]:
            return
        from .providers import get_provider as _gp  # noqa: PLC0415
        price = (_gp(cfg.load().get("provider", "")) or {}).get("price")
        est_cost = usage.estimate_cost_cny(snap["prompt_tokens"], snap["completion_tokens"], price)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["usage"] = {**snap, "est_cost_cny": round(est_cost, 2) if est_cost is not None else None,
                         "price_unit": "元/1M token（官网为准）"}
        _write_json_atomic(meta_path, meta)
        label = "取消前" if cancelled else "失败前"
        _log(base, f"  💰 {label}实际消耗：输入 {snap['prompt_tokens']} token + "
                   f"输出 {snap['completion_tokens']} token"
                   + (f" ≈ ¥{est_cost:.2f}" if est_cost is not None else ""))
    except Exception:  # noqa: BLE001  记账失败不掩盖主流程结果
        pass


def run_project(pid: str, seed: Optional[int] = None, overrides: Optional[dict[str, Any]] = None,
                cancel: Optional[threading.Event] = None) -> dict[str, Any]:
    """同步执行整条管线；overrides 用于测试注入 Fake client；cancel 用于取消。

    returns {"stage", "questions", "qc_decision", ...}
    U5（v0.5）：按次上下文记账 — 本次 run 独立账本（trial/regen 不再串账），结束即还原。
    R3S-03/B27：取消与失败路径也 snapshot usage 落 meta——已烧 token 永久可见。
    """
    token = usage.activate()
    try:
        try:
            res = _run_project_impl(pid, seed, overrides, cancel)
        except BaseException:
            _record_usage_on_exit(pid, cancelled=False)
            raise
        if res.get("stage") == "cancelled":
            _record_usage_on_exit(pid, cancelled=True)
        return res
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
    # v0.5.2：自备真题 / 补充资料（可选角色，仅考点/风格校准与补充上下文）
    exam_text = "\n".join(filter(None, (s.get("text", "") for s in slices if s.get("role") == "exam")))
    extra_text = "\n".join(filter(None, (s.get("text", "") for s in slices if s.get("role") == "extra")))
    slice_by_sid = {s["sid"]: s for s in textbook_slices}
    text_by_sid = {sid: s.get("text", "") for sid, s in slice_by_sid.items()}
    known_sids = set(slice_by_sid)

    # R3-09/B24：cancel 下透到 LLM 层（流式读取提前退出，停止后不再烧完整回复）
    gen_client = (overrides or {}).get("gen") or medgen.make_client(cancel=cancel)
    qc_client = (overrides or {}).get("qc") or medqc.make_client(cancel=cancel)
    fix_client = (overrides or {}).get("fix") or medfix.make_client(cancel=cancel)
    rev_client = (overrides or {}).get("review") or medreview.make_client(cancel=cancel)

    subject = meta.get("subject", "")
    exam = meta.get("exam", "期末")
    toggles = meta.get("toggles", {})
    # WP-01/WP-10：大纲锚定注入——教师重点为主（source=teacher），官方 306 仅作补充
    syllabus_text = ""
    official_note = ""
    try:
        from . import syllabus as syl
        if dbs.enabled() and syl.list_subjects():
            syllabus_text = syl.chapter_items_text(subject, limit=800, source="teacher")
            official_quota = int(meta.get("official_quota") or 0)
            if official_quota > 0:
                official_text = syl.chapter_items_text(
                    subject, limit=min(400, official_quota * 10), source="seed")
                if official_text:
                    syllabus_text = (syllabus_text + "\n\n# 官方 306 补充大纲（仅补充，考点以教师重点为准）\n"
                                     + official_text)[:1200]
                    official_note = f" + 官方306补充 {len(official_text)} 字"
    except Exception:  # noqa: BLE001  大纲引擎故障不阻塞出题
        syllabus_text = ""
    if syllabus_text:
        _log(base, f"📋 大纲锚定注入 {len(syllabus_text)} 字（教师重点为主{official_note}，subtopic 对齐考点条目）")
    # WP-04：图像素材（图/表题）——清单注入提示词 + 渲染用索引（R3S-02：与审核台重渲染共用构建函数）
    image_index, image_sections = build_image_index(base, slices)
    if image_sections:
        _log(base, f"🖼 图像素材 {len(image_index)} 个注入（鼓励出图题，image_ref 门禁校验）")
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
    if exam_text:
        cut = "（超长截断）" if len(exam_text) > medgen.EXAM_CHAR_LIMIT else ""
        _log(base, f"📎 自备真题 {len(exam_text)} 字参与考点/风格校准{cut}（严禁照抄原题）")
    if extra_text:
        cut = "（超长截断）" if len(extra_text) > medgen.EXTRA_CHAR_LIMIT else ""
        _log(base, f"📎 自备资料 {len(extra_text)} 字作为补充上下文{cut}（与教材冲突以教材为准）")

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
            _set_progress(base, "websearch", 0, 1, "检索中…（首次约 1~3 分钟）", sub="多轮检索", sub_done=0, sub_total=3)
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
                    cancel=cancel,
                    trusted_only=bool(ws_cfg.get("trusted_only", False)),
                    trusted_domains=ws_cfg.get("trusted_domains") or [])
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
            if err_list:
                _append_manual_section(base, "网络检索失败（已降级继续）",
                                       [str(e) for e in err_list])
                _log(base, f"  ⚠️ 网络检索 {len(err_list)} 项失败 → 人工复核清单.md")
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

    # NX-03（R-2）：软校验契约告警计数（medgen append 线程安全；出题完成后落项目 meta）
    contract_bad: list[int] = []

    def gen_one(idx: int, item: dict[str, Any], cnt: int, sid: str,
                start_id: int) -> tuple[str, list[dict[str, Any]]]:
        if cancel.is_set():
            raise PipelineCancelled()
        _set_progress(base, "generating", progress_done, total_slices,
                      f"{slice_by_sid[sid].get('title', '')[:24]}（{cnt} 题）",
                      sub="切片出题", sub_done=progress_done, sub_total=total_slices)
        _log(base, f"  切片 {sid}（{slice_by_sid[sid].get('title', '')[:20]}）出题 {cnt} 题…")
        qs, _ = medgen.generate_slice(
            gen_client, subject, exam, slice_by_sid[sid], cnt, ratios, teacher_text,
            ids_start=start_id, requirements=requirements, knobs=knobs, bloom=bloom,
            web_materials=web_materials_text, web_quota=web_ref_quota,
            exam_text=exam_text, extra_text=extra_text, syllabus_text=syllabus_text,
            image_sections=image_sections, contract_bad=contract_bad)
        for i, q in enumerate(qs):
            q["id"] = f"Q{start_id + i:03d}"
        if len(qs) < cnt:
            _log(base, f"  ⚠️ 切片 {sid} 期望 {cnt} 题实得 {len(qs)} 题（已补足尝试，仍不足）")
        if cancel.is_set():
            raise PipelineCancelled()
        return sid, qs

    cancelled_midway = False
    pending: Exception | None = None
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
                        # R3S-03：ContextVar 不随 submit 传播——copy_context 把本次 run 的用量账本带进切片线程
                        futures[ex.submit(contextvars.copy_context().run,
                                          gen_one, idx, item, cnt, sid, id_ranges[idx][0])] = sid
                    for fut in as_completed(futures):
                        try:
                            sid_r, qs = fut.result()
                        except PipelineCancelled:
                            pending = PipelineCancelled()
                            break              # 取消：先把已完成切片结果尽量落盘
                        except Exception as e:  # noqa: BLE001
                            pending = e        # 单切片失败：先收起已完成的别家切片再抛
                            break
                        with ckpt_lock:
                            done_sids.add(sid_r)
                            result_by_sid[sid_r] = qs
                            progress_done = len(done_sids)
                            _save_checkpoint(base, done_sids,
                                             [q for v in result_by_sid.values() for q in v])
                        _set_progress(base, "generating", progress_done, total_slices,
                                      f"已完成 {progress_done}/{total_slices} 切片",
                                      sub="切片出题", sub_done=progress_done, sub_total=total_slices)
                # 中途退出/取消：把已完成但尚未写入的切片结果一并落 checkpoint，避免续跑重生成白花钱
                with ckpt_lock:
                    for fut in futures:
                        if not fut.done():
                            continue
                        try:
                            sid_r, qs = fut.result()
                        except Exception:       # noqa: BLE001  已失败切片不回填
                            continue
                        if sid_r not in done_sids:
                            done_sids.add(sid_r)
                            result_by_sid[sid_r] = qs
                            progress_done = len(done_sids)
                    if done_sids:
                        _save_checkpoint(base, done_sids,
                                         [q for v in result_by_sid.values() for q in v])
                if pending is not None:
                    raise pending
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
                  "出题完成" if not cancelled_midway else "已取消",
                  sub="切片出题", sub_done=progress_done, sub_total=total_slices)

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
    # NX-03（R-2）：软校验契约告警计数落项目 meta（学习中心概览卡可见；0 也会覆盖旧值）
    meta["contract_warnings"] = len(contract_bad)
    _write_json_atomic(meta_path, meta)
    if contract_bad:
        _log(base, f"  ⚠️ 本批 {len(contract_bad)} 条输出未通过 QuestionItem 契约"
                   f"（软校验告警，不影响门禁兜底；已记入 meta contract_warnings）")

    # ---------------- ② 门禁①（选项/Bloom/溯源/查重/图像引用 自动修复循环）
    # WP-04+B28：image_ref 必须指向已上传的图片素材；任何不匹配都剔除并记 warning
    # （不再要求 image_sids 非空——未传图项目的幻觉 image_ref 同样拦截，防伪图题零提示）
    image_sids = {s.get("sid") for s in slices if s.get("role") == "image" and s.get("image")}
    _substep(base, "gate1", "image_ref", "图像引用检查", "running",
             f"{len(image_sids)} 个素材")
    questions, dropped_img = _gate_image_refs(questions, image_sids)
    _substep(base, "gate1", "image_ref", "图像引用检查",
             "done" if not dropped_img else "failed",
             f"剔除 {len(dropped_img)} 题" if dropped_img else "通过")
    if dropped_img:
        _log(base, f"  ⚠️ 图像引用门禁：剔除 {len(dropped_img)} 题"
             f"（image_ref 不在素材清单：{'、'.join(str(x) for x in dropped_img[:5])}）")
    for round_i in range(1, FIX_ROUNDS_GATE + 2):
        if cancel.is_set():   # B24：门禁循环轮次间可取消
            return _cancel_out(base, meta_path, done_sids, questions)
        _set_stage(base, meta_path, "gate1", f"② 门禁① 第 {round_i} 轮…")
        _set_progress(base, "gate1", round_i - 1, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮",
                      sub="选项校验", sub_done=0, sub_total=4)
        _opt_issues, opt_err = _run_substep(
            base, "gate1", "options", "选项校验",
            lambda qs=questions: options_check.check_all(qs)["issues"],
            detail=f"第 {round_i} 轮")
        if opt_err:
            _append_manual_section(base, "门禁① 选项校验",
                                   [f"第 {round_i} 轮失败：{opt_err}", "已按无问题继续，建议人工复核。"])
            _opt_issues = []
        _set_progress(base, "gate1", round_i - 1, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮",
                      sub="Bloom 校验", sub_done=1, sub_total=4)
        _bloom_issues, bloom_err = _run_substep(
            base, "gate1", "bloom", "Bloom 校验",
            lambda qs=questions: bloom_check.check_bloom(qs, bloom_target)["issues"],
            detail=f"第 {round_i} 轮")
        if bloom_err:
            _append_manual_section(base, "门禁① Bloom 校验",
                                   [f"第 {round_i} 轮失败：{bloom_err}", "已按无问题继续，建议人工复核。"])
            _bloom_issues = []
        _set_progress(base, "gate1", round_i - 1, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮",
                      sub="溯源回查", sub_done=2, sub_total=4)
        _trace_issues, trace_err = _run_substep(
            base, "gate1", "trace", "溯源回查",
            lambda qs=questions: trace_check.check_trace(qs, known_sids)["issues"],
            detail=f"第 {round_i} 轮")
        if trace_err:
            _append_manual_section(base, "门禁① 溯源回查",
                                   [f"第 {round_i} 轮失败：{trace_err}", "已按无问题继续，建议人工复核。"])
            _trace_issues = []
        _set_progress(base, "gate1", round_i - 1, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮",
                      sub="查重", sub_done=3, sub_total=4)
        _dup, dup_err = _run_substep(
            base, "gate1", "dup", "查重",
            lambda qs=questions: dedup_check.check_dup(qs),
            detail=f"第 {round_i} 轮")
        if dup_err:
            _append_manual_section(base, "门禁① 查重",
                                   [f"第 {round_i} 轮失败：{dup_err}", "已按无问题继续，建议人工复核。"])
            _dup = {"issues": []}
        gate = {
            "options": {"issues": _opt_issues},
            "bloom": {"issues": _bloom_issues},
            "trace": {"issues": _trace_issues},
            "dup": _dup,
        }
        _set_progress(base, "gate1", round_i, FIX_ROUNDS_GATE + 1, f"第 {round_i} 轮完成",
                      sub="门禁检查", sub_done=4, sub_total=4)
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
            if cancel.is_set():   # B24：修复轮开始前可取消（单次修复调用期间的取消由 LLM 层流式退出接管）
                return _cancel_out(base, meta_path, done_sids, questions)
            _log(base, f"  门禁① fails={len(fails)} dup={len(dup_issues)} → MedFix 修复第 {round_i} 轮…")
            for _i, iss in enumerate(to_fix):
                _qid = str(iss.get("q_id") or f"issue{_i + 1}")
                _substep(base, "gate1", f"fix:{_qid}", f"修复 {_qid}", "running",
                         f"第 {round_i} 轮 · {str(iss.get('reason', ''))[:80]}")
            fixed, fix_err = _run_substep(
                base, "gate1", "medfix", "MedFix 批量修复",
                lambda qs=questions, tf=to_fix: medfix.fix_questions(
                    fix_client, qs, tf, text_by_sid),
                ttl=300, retries=1,
                detail=f"第 {round_i} 轮 · {len(to_fix)} 条问题")
            if fix_err:
                _append_manual_section(base, "门禁① MedFix",
                                       [f"第 {round_i} 轮失败：{fix_err}",
                                        "本轮未修复，题目保留待人工复核。"])
                fixed = {"fixed": [], "trace": []}
            fixed = fixed or {"fixed": [], "trace": []}
            _fixed_ids = {fq.get("id") for fq in fixed.get("fixed", [])}
            for _i, iss in enumerate(to_fix):
                _qid = str(iss.get("q_id") or f"issue{_i + 1}")
                _substep(base, "gate1", f"fix:{_qid}", f"修复 {_qid}",
                         "done" if _qid in _fixed_ids else "failed",
                         "已自动修复" if _qid in _fixed_ids else "保留待人工复核")
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
        return _cancel_out(base, meta_path, done_sids, questions)
    _set_stage(base, meta_path, "qc", "③ MedQC 质检（LLM-as-judge 并行分批）…")
    total_batches = (len(questions) + medqc.BATCH_SIZE - 1) // medqc.BATCH_SIZE
    _set_progress(base, "qc", 0, total_batches, "质检中…", sub="LLM 判分", sub_done=0, sub_total=total_batches)

    def _qc_step(done: int, tot: int) -> None:
        """QC 每批完成 → 进度 + substeps.jsonl 每批事件（WP-3）。"""
        _set_progress(base, "qc", done, tot, f"质检中… 第 {done}/{tot} 批",
                      sub="LLM 判分", sub_done=done, sub_total=tot)
        _substep(base, "qc", f"batch{done}", f"质检批次 {done}/{tot}", "done",
                 f"第 {done}/{tot} 批完成")
        if done < tot:
            _substep(base, "qc", f"batch{done + 1}", f"质检批次 {done + 1}/{tot}",
                     "running", "提交中…")

    if total_batches:
        _substep(base, "qc", "batch1", f"质检批次 1/{total_batches}", "running", "提交中…")
    qc_report, qc_err = _run_substep(
        base, "qc", "medqc", "MedQC 质检",
        lambda: medqc.qc_batch(qc_client, questions, text_by_sid,
                               on_progress=_qc_step, cancel=cancel),
        ttl=600, retries=1, detail=f"{total_batches} 批")
    if qc_err:
        _append_manual_section(base, "MedQC 质检",
                               [f"质检失败/超时：{qc_err}",
                                "已按「质检通过」降级继续，建议人工复核质检报告。"])
        qc_report = {"score": 50, "gate_decision": "PASS_WITH_FIXES",
                     "issues": [], "summary": f"质检降级继续：{qc_err}"}
        _set_progress(base, "qc", 0, total_batches, f"质检降级继续：{str(qc_err)[:60]}",
                      sub="LLM 判分", sub_done=0, sub_total=total_batches)
    qc_report = qc_report or {"score": 50, "gate_decision": "PASS_WITH_FIXES",
                              "issues": [], "summary": "质检结果缺失，降级继续"}
    if qc_report.get("cancelled"):
        return _cancel_out(base, meta_path, done_sids, questions)
    (base / "质检报告" / "质检报告.json").write_text(
        json.dumps(qc_report, ensure_ascii=False, indent=2), encoding="utf-8")
    # NX-03（R-2）：契约硬闭环失败批次 → 人工复核清单（与网络冲突/渲染前剔除并存）
    contract_fails = [i for i in qc_report.get("issues", []) if i.get("code") == "QC_CONTRACT"]
    if contract_fails:
        _log(base, f"  ⚠️ {len(contract_fails)} 个质检批次判分不可信（格式两次未过，计分未采纳）"
                   f"→ 人工复核清单.md")
        _append_contract_review(base, contract_fails)
    _log(base, f"  QC score={qc_report['score']} decision={qc_report['gate_decision']}"
               f" issues={len(qc_report['issues'])}")
    if qc_report["gate_decision"] == "BLOCKED":
        if cancel.is_set():   # B24：质检修复阶段可取消
            return _cancel_out(base, meta_path, done_sids, questions)
        _set_stage(base, meta_path, "fixing", "④ MedFix（质检 BLOCKED → 定向修复）…")
        _set_progress(base, "fixing", 0, 1, "定向修复中…（最长约 1~2 分钟）",
                      sub="MedFix", sub_done=0, sub_total=1)
        for _i, iss in enumerate(qc_report["issues"]):
            _qid = str(iss.get("q_id") or f"issue{_i + 1}")
            _substep(base, "fixing", f"fix:{_qid}", f"修复 {_qid}", "running",
                     str(iss.get("reason", ""))[:80])
        fixed, fix_err = _run_substep(
            base, "fixing", "medfix", "MedFix 质检修复",
            lambda: medfix.fix_questions(fix_client, questions,
                                         qc_report["issues"], text_by_sid),
            ttl=300, retries=1, detail=f"{len(qc_report['issues'])} 条问题")
        if fix_err:
            _append_manual_section(base, "MedFix 质检修复",
                                   [f"失败：{fix_err}", "本轮未修复，题目保留待人工复核。"])
            fixed = {"fixed": [], "trace": []}
        fixed = fixed or {"fixed": [], "trace": []}
        _fixed_ids = {fq.get("id") for fq in fixed.get("fixed", [])}
        for _i, iss in enumerate(qc_report["issues"]):
            _qid = str(iss.get("q_id") or f"issue{_i + 1}")
            _substep(base, "fixing", f"fix:{_qid}", f"修复 {_qid}",
                     "done" if _qid in _fixed_ids else "failed",
                     "已自动修复" if _qid in _fixed_ids else "保留待人工复核")
        by_id = {q["id"]: q for q in questions}
        for fq in fixed["fixed"]:
            if fq.get("id") in by_id:
                by_id[fq["id"]] = fq
        questions = list(by_id.values())
        _log(base, f"  修复 {len(fixed['fixed'])} 题")
        _set_progress(base, "fixing", 1, 1, f"修复完成（{len(fixed['fixed'])} 题）",
                      sub="MedFix", sub_done=1, sub_total=1)
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
    if cancel.is_set():   # B24：汇总/终检前可取消（题目仍保留断点）
        return _cancel_out(base, meta_path, done_sids, questions)
    _set_stage(base, meta_path, "finalizing", "④ 汇总题库…")
    _set_progress(base, "finalizing", 0, 1, "汇总题库与终检…",
                  sub="渲染前终检", sub_done=0, sub_total=1)
    for i, q in enumerate(questions):
        q.setdefault("id", f"Q{i + 1:03d}")
    # 渲染前终检（D2）：修复轮用尽仍超限/缺字段的题剔除出产物 + 人工复核清单
    _substep(base, "finalizing", "precheck", "渲染前终检", "running",
             f"{len(questions)} 题")
    questions, dropped_list = _render_precheck(questions)
    _substep(base, "finalizing", "precheck", "渲染前终检",
             "done" if not dropped_list else "failed",
             f"剔除 {len(dropped_list)} 题" if dropped_list else "通过")
    if dropped_list:
        _log(base, f"  ⚠️ 渲染前终检剔除 {len(dropped_list)} 题 → 人工复核清单.md")
        _append_review_list(base, dropped_list)
    # WP-04：已上传图片素材但本批无图题 → 记 visible 提示（前端项目详情展示）
    if image_index and not any(q.get("image_ref") for q in questions):
        _log(base, "  ⚠️ 已有图片素材但本批未产出图题（可稍后重试/加大题量）")
        meta["image_warning"] = True
        _write_json_atomic(meta_path, meta)
    (base / "最终产物").mkdir(exist_ok=True)
    # v0.8.1 真题标注（PRD 6.3.2）：题干/章节命中已确认考频条目 → 写回 source_type/source_year
    # （零 LLM；未确认考频不标注，WP-02 红线）
    try:
        from ..core import realexams as _rex
        _rex.annotate_questions(questions, subject)
    except Exception as _e:  # noqa: BLE001  标注失败不阻断产物落盘
        _log(base, f"  ⚠️ 真题来源标注失败（不影响产物）：{_e}")
    (base / "最终产物" / "questions_final.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_progress(base, "finalizing", 1, 1, "题库已落盘", sub="渲染前终检", sub_done=1, sub_total=1)
    _log(base, f"  最终题库 {len(questions)} 题")

    # ---------------- ⑤ MedReview 复习手册
    review_md = ""
    if toggles.get("review", True):
        _set_stage(base, meta_path, "reviewing", "⑤ MedReview 复习手册生成…")
        _set_progress(base, "reviewing", 0, 1, "生成复习手册…（预计 1~2 分钟）",
                      sub="MedReview", sub_done=0, sub_total=1)
        if cancel.is_set():
            return _cancel_out(base, meta_path, done_sids, questions)
        _substep(base, "reviewing", "medreview", "MedReview 复习手册", "running",
                 "生成中…（预算 6000 字）")
        review_md = medreview.generate_review(
            rev_client, subject, exam, questions, teacher_text,
            _review_slice_digest(textbook_slices))   # B32：6000 预算按切片顺序轮转分配，后面章节也进手册
        _substep(base, "reviewing", "medreview", "MedReview 复习手册", "done",
                 f"{len(review_md)} 字")
        (base / "最终产物" / "复习手册.md").write_text(review_md, encoding="utf-8")
        _set_progress(base, "reviewing", 1, 1, "复习手册已生成",
                      sub="MedReview", sub_done=1, sub_total=1)

    # ---------------- ⑥ 渲染产物
    if cancel.is_set():   # B24：渲染前可取消（题库已落盘、产物可稍后「仅重渲染」补齐）
        return _cancel_out(base, meta_path, done_sids, questions)
    _set_stage(base, meta_path, "rendering", "⑥ 渲染产物…")
    _set_progress(base, "rendering", 0, 1, "生成 HTML…",
                  sub="题库 MD/HTML", sub_done=0, sub_total=5)
    qbank_md = qbank_html.export_md(questions, f"{subject} 题库")
    qbank_html_text = qbank_html.export_html(questions, f"{subject} 题库",
                                             image_index=image_index, pid=pid)
    (base / "最终产物" / "qbank.md").write_text(qbank_md, encoding="utf-8")
    (base / "最终产物" / "qbank.html").write_text(qbank_html_text, encoding="utf-8")
    rendered = ["qbank.md", "qbank.html"]
    _set_progress(base, "rendering", 0, 1, "题库 MD/HTML 完成",
                  sub="题库 MD/HTML", sub_done=1, sub_total=5)
    _substep(base, "rendering", "qbank", "题库 MD/HTML", "done",
             "qbank.md / qbank.html")
    if toggles.get("paper", True):
        _substep(base, "rendering", "paper", "押题卷", "running", "抽样+渲染…")
        paper_qs = _sample_paper(questions, min(PAPER_DEFAULT, len(questions)))
        _save_paper_ids(base, paper_qs)   # ME-7：审核重渲染时防抽样漂移
        _set_progress(base, "rendering", 0, 1, "生成押题卷…",
                      sub="押题卷", sub_done=2, sub_total=5)
        (base / "最终产物" / "押题卷.html").write_text(
            qbank_html.export_paper_html(paper_qs, f"{subject} 押题卷",
                                         pid=pid, subject=subject,
                                         image_index=image_index), encoding="utf-8")
        rendered.append("押题卷.html")
        _substep(base, "rendering", "paper", "押题卷", "done",
                 f"{len(paper_qs)} 题")
    if review_md and toggles.get("review", True):
        _substep(base, "rendering", "review", "复习手册 HTML", "running")
        _set_progress(base, "rendering", 0, 1, "生成复习手册 HTML…",
                      sub="复习手册", sub_done=3, sub_total=5)
        (base / "最终产物" / "复习手册.html").write_text(
            review_html.review_to_html(review_md, f"{subject} 复习手册",
                                       out_dir=base / "最终产物"), encoding="utf-8")
        rendered.append("复习手册.html")
        _substep(base, "rendering", "review", "复习手册 HTML", "done")
    # Anki 导出（U8，随产物生成，项目目录留档 + 详情页可下载）
    _substep(base, "rendering", "anki", "Anki 导出", "running", "txt / apkg")
    _set_progress(base, "rendering", 0, 1, "生成 Anki 导出…",
                  sub="Anki.txt", sub_done=4, sub_total=5)
    anki_txt = qbank_html.export_anki(questions, f"{subject} 题库")
    (base / "最终产物" / "anki_export.txt").write_text(anki_txt, encoding="utf-8")
    rendered.append("anki_export.txt")
    _substep(base, "rendering", "anki", "Anki 导出", "done", "anki_export.txt")
    # S3：.apkg 真包导出（genanki；model/deck id 按项目名稳定哈希）
    try:
        from ..core.fsutil import safe_filename
        from ..render.apkg import export_apkg
        apkg_path = base / "最终产物" / f"{safe_filename(subject)} 题库.apkg"
        _substep(base, "rendering", "apkg", "Anki.apkg", "running", "真包导出…")
        export_apkg(questions, subject, pid, apkg_path)
        rendered.append(apkg_path.name)
        _set_progress(base, "rendering", 1, 1, "产物渲染完成",
                      sub="Anki.apkg", sub_done=5, sub_total=5)
        _substep(base, "rendering", "apkg", "Anki.apkg", "done")
        _log(base, f"  ✅ .apkg 导出：{apkg_path.name}")
    except Exception as e:  # noqa: BLE001  apkg 失败不阻断其余产物
        _log(base, f"  ⚠️ .apkg 导出失败（不影响其余产物）：{e}")

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
