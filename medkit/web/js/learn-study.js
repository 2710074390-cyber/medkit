/* exported C, ERR_LABEL, LEARN_COLORS, LEARN_ORDER, _libCache, _mkBatchSetBusy, _siteImportItems, _subjMgrBusy, a, acc, acts, addMistakeRaw, allCard, allChecked, appliedSubject, arcs, arr, badgeNote, bar, blob, body, box, btn, btnAll, byName, c, card, cells, ch, chk, cnt, cs, cur, cw, cwBanner, d, dBanner, dash, dashChapterWeak, dashDonut, dashLegend, dashMetrics, dashWeakRows, data, del, detail, ds, el, ext, f, fd, fillMkSubjectSelect, fr, groups, grp, head, healLibrary, id, ids, items, key, kind, kp, kps, learnRecAction, list, loadDashboard, loadLibrary, loadOverview, loadStudy, loadStudySubjects, loc, m, meta, mime, mkBatchBusy, mkBatchDel, mkBatchExport, mkBatchFile, mkBatchLearn, mkBatchPick, mkClearSel, mkDel, mkDetailTgl, mkGroupHTML, mkInvert, mkLearn, mkOcrFile, mkOcrPick, mkPurgeSameCards, mkRowHTML, mkScopeChange, mkSelected, mkShowAll, mkShowAllFn, mkSiteFile, mkSiteImport, mkSubject, mkToggleAllVisible, mkToggleGroup, mkToggleRow, n, name, o, onlyUn, parts, pct, r, recs, renderDashboard, renderLibrary, renderLibraryCurrent, renderMasteryDashboard, rows, scope, scoped, sel, shown, sl, stages, stats, subj, subject, subjectDelete, subjectMgrOpen, subs, syncMkScope, t, tag, text, tot, total, updateByQueue, updateMkToolbar, url */
/* ---- 掌握度驾驶舱：指标卡 + 状态分布环图 + 弱项清单 + 最弱章节 ---- */
const LEARN_COLORS = { weak: "#f87171", shaky: "#fbbf24", solid: "#34d399", mastered: "#4ade80" };
const LEARN_ORDER = ["weak", "shaky", "solid", "mastered"];
function dashDonut(stats) {
  const total = stats.total_knowledge || 0;
  if (!total) {
    return `<svg viewBox="0 0 120 120" width="132" height="132" style="display:block"><circle cx="60" cy="60" r="48" fill="none" stroke="var(--card3)" stroke-width="14"/><text x="60" y="63" text-anchor="middle" font-size="13" fill="var(--dim)">无数据</text></svg>`;
  }
  const C = 2 * Math.PI * 48;
  const arcs = [];
  let acc = 0;
  for (const s of LEARN_ORDER) {
    const c = stats[s] || 0;
    if (!c) continue;
    const fr = c / total;
    const sl = Math.max(fr * C - 2, 0.6);
    arcs.push(`<circle cx="60" cy="60" r="48" fill="none" stroke="${LEARN_COLORS[s]}" stroke-width="14"
      stroke-dasharray="${sl} ${C - sl}" stroke-dashoffset="${-(acc * C + 2)}" transform="rotate(-90 60 60)"/>`);
    acc += fr;
  }
  const pct = Math.round(((stats.solid || 0) + (stats.mastered || 0)) / total * 100);
  return `<svg viewBox="0 0 120 120" width="132" height="132" style="display:block">
    ${arcs.join("")}
    <text x="60" y="57" text-anchor="middle" font-size="26" font-weight="700" fill="var(--text)" font-variant-numeric="tabular-nums">${pct}%</text>
    <text x="60" y="74" text-anchor="middle" font-size="9" fill="var(--dim)">掌握率</text>
  </svg>`;
}
function dashMetrics(stats) {
  const cells = [["total_knowledge", "知识点"], ["weak", "薄弱"], ["shaky", "需复习"], ["solid", "较熟练"], ["mastered", "已掌握"]];
  return `<div class="dash-metrics">` + cells.map(([k, l]) =>
    `<div class="dash-metric"><b style="${k === "weak" ? "color:#f87171" : ""}">${esc(stats[k] || 0)}</b><span>${l}</span></div>`).join("") + `</div>`;
}
function dashLegend(stats) {
  return `<div class="dash-legend">` + LEARN_ORDER.map(s =>
    `<div style="display:flex;align-items:center;gap:8px"><i style="width:11px;height:11px;border-radius:50%;background:${LEARN_COLORS[s]};flex:none"></i><span style="flex:1;color:var(--dim);font-size:12px">${LEARN_STATE[s]}</span><b style="font-variant-numeric:tabular-nums">${stats[s] || 0}</b></div>`).join("") + `</div>`;
}
function dashWeakRows(kps) {
  const list = kps.filter(k => (k.miss || 0) > 0 || k.state === "weak" || k.state === "shaky")
    .sort((a, b) => (b.priority || 0) - (a.priority || 0)).slice(0, 6);
  if (!list.length) return `<div class="hint">暂无薄弱点，先把错题收进来。</div>`;
  return list.map(k => {
    const tot = k.attempts || 0, c = k.correct || 0, m = k.miss || 0, den = tot || 1;
    const cs = Math.round(c / den * 100), ms = Math.round(m / den * 100);
    return `<div style="padding:7px 0;border-bottom:1px dashed var(--line)">
      <div style="display:flex;align-items:center;gap:10px">
        <span style="flex:1;color:var(--text);font-size:12.5px;min-width:0">${esc(k.name)}</span>
        ${learnChip(k.state)}
        <span class="hint" style="font-size:11px;white-space:nowrap">优先 ${Math.round((k.priority || 0) * 100)}</span>
        <span style="display:flex;gap:4px;flex:none">
          <button class="mini-btn primary" style="padding:2px 8px" onclick="learnRecAction(this)" data-kind="explain" data-subject="${esc(k.subject || "")}" data-name="${esc(k.name)}">讲解</button>
          <button class="mini-btn" style="padding:2px 8px" onclick="learnRecAction(this)" data-kind="tutor" data-subject="${esc(k.subject || "")}" data-name="${esc(k.name)}">提问</button>
        </span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:5px">
        <div class="stack-bar"><i class="ok" style="width:${cs}%"></i><i class="miss" style="width:${ms}%"></i></div>
        <span class="hint" style="font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums">对 ${c}/${tot}</span>
        <button class="mini-btn" style="padding:2px 8px;margin-left:auto" onclick="learnRecAction(this)" data-kind="queue" data-subject="${esc(k.subject || "")}" data-name="${esc(k.name)}">铺卡</button>
      </div>
    </div>`;
  }).join("");
}
function dashChapterWeak(kps) {
  const grp = {};
  kps.filter(k => (k.miss || 0) > 0).forEach(k => {
    const key = [k.subject, k.chapter].filter(Boolean).join("·") || "未分类";
    grp[key] = grp[key] || { miss: 0, n: 0 };
    grp[key].miss += k.miss || 0; grp[key].n++;
  });
  const rows = Object.entries(grp).sort((a, b) => b[1].miss - a[1].miss).slice(0, 3);
  if (!rows.length) return "";
  return `<h3 style="font-size:12.5px;color:var(--dim);margin:12px 0 4px;font-weight:600">最弱章节（按错题量）</h3>` + rows.map(([name, g]) =>
    `<div style="display:flex;align-items:center;gap:10px;padding:4px 0"><span style="flex:1;color:var(--text);font-size:12px">${esc(name)}</span><span class="hint" style="font-size:11px;white-space:nowrap">${g.n} 点 · ${g.miss} 错</span></div>`).join("");
}
function renderMasteryDashboard(kps, stats) {
  return dashMetrics(stats)
    + `<div class="dash">${dashDonut(stats)}<div><div style="font-size:12px;color:var(--text);margin-bottom:6px">知识点状态分布</div>${dashLegend(stats)}<div class="hint" style="margin-top:8px;font-size:11px">掌握率 = (较熟练 + 已掌握) / 全部</div></div></div>`
    + `<div class="dash-weak"><h3>薄弱点清单（按优先级）</h3>${dashWeakRows(kps)}</div>`
    + dashChapterWeak(kps);
}
// 学习闭环总览（掌握度 + 复习 SM-2 + 提问式 MedTutor）
/* C1：概览统一加载——顶部诊断/推荐/错题列表与「学习闭环总览」共用同一科目范围，避免口径分裂 */
async function loadOverview(subject) {
  subject = subject || "";
  try {
    const subs = (await cachedSubjects()).subjects || [];
    const sel = $("dash_subject");
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">全部科目</option>' +
        subs.slice().sort().map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
      if (cur && subs.includes(cur)) sel.value = cur;
    }
    // R3-22：错题本作用域跟随概览科目过滤（另有独立下拉可切回全部科目）
    mkSubject = subject;
    fillMkSubjectSelect();
    const [d, mk, my, recm] = await Promise.all([
      api("/api/library/dashboard?subject=" + encodeURIComponent(subject)),
      api("/api/library/mistakes"),
      api("/api/library/mastery?subject=" + encodeURIComponent(subject)),
      api("/api/library/recommend?limit=6&subject=" + encodeURIComponent(subject)),
    ]);
    renderDashboard(d);
    // D-14/R3-22：错题全量进缓存，作用域过滤在渲染层做（错题本可独立切科目）
    renderLibrary(mk.mistakes || [], my, recm.recommend || []);
  } catch (e) {
    $("dash_scope").textContent = "汇总失败";
    $("dash_loop").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
async function loadDashboard(keepSubject) {
  try {
    const subs = (await cachedSubjects()).subjects || [];
    const sel = $("dash_subject");
    // D-08：重建下拉前记录原选中值——仍存在则恢复选中，否则回「全部科目」
    const cur = keepSubject !== undefined ? keepSubject : (sel ? sel.value : "");
    sel.innerHTML = '<option value="">全部科目</option>' +
      subs.slice().sort().map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    if (cur && subs.includes(cur)) sel.value = cur;
    const subject = sel.value;
    const d = await api("/api/library/dashboard?subject=" + encodeURIComponent(subject));
    renderDashboard(d);
  } catch (e) {
    $("dash_scope").textContent = "汇总失败";
    $("dash_loop").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
function renderDashboard(d) {
  const m = d.mastery || {}, r = d.review || {}, t = d.tutor || {}, loop = d.loop || {};
  $("dash_scope").textContent = `${d.subject_label} · 闭环 ${loop.mastered || 0}/${m.total_knowledge || 0} 已掌握`;
  updateLearnBadges(d);
  // 闭环流转：错题沉淀 → 讲解产物 → 提问会话 → 复习在册 → 已掌握
  const stages = [
    ["错题沉淀", loop.mistakes || 0, ""], ["讲解产物", loop.explains || 0, ""],
    ["提问会话", loop.tutor || 0, ""], ["复习在册", loop.review || 0, ""],
    ["已掌握", loop.mastered || 0, "mastered"],
  ];
  const dBanner = (d.corrupted || 0) > 0
    ? `<div class="databanner"><span>⚠ 检测到 <b>${d.corrupted}</b> 条历史记录存在编码损坏（显示为 ????，多为早期导入所致）</span>
       <button class="mini-btn primary" onclick="healLibrary()">一键修复（先备份）</button>
       <span class="hint">可逆的自动还原；不可逆的仅做标记，不删除数据</span></div>`
    : "";
  // NX-03（R-2）：契约告警可见化——最近一轮生成有输出未通过契约校验
  const cw = d.contract_warnings || {};
  const cwBanner = (cw.total || 0) > 0
    ? `<div class="databanner"><span>⚠ 最近一轮生成有 <b>${cw.total}</b> 条输出未通过契约校验`
      + `（${Object.entries(cw.by_subject || {}).map(([k, v]) => `${esc(k)} ${v}`).join("、") || "未分类"}）`
      + `——不影响最终门禁兜底，详见项目「质检报告」与「人工复核清单」</span></div>`
    : "";
  const badgeNote = ((r.due || 0) || (t.in_progress || 0))
    ? `<div class="databanner" style="border-color:var(--accent2)"><span>🔴 侧栏红点来源：<b>刷题</b>＝今日到期复习 ${r.due || 0} 张；<b>学习中心</b>＝进行中提问 ${t.in_progress || 0} 场。悬停红点可看明细，点击直达对应视图。</span></div>`
    : "";
  $("dash_loop").innerHTML = badgeNote + dBanner + cwBanner + '<div class="loop-flow">' + stages.map(([k, v, cls], i) =>
      (i ? '<div class="loop-arrow">→</div>' : "") +
      `<div class="loop-node ${cls}"><b>${v}</b><span>${k}</span></div>`).join("") +
    '</div>' +
    '<div class="dashmr">' +
      `<div class="dmr good"><div class="dm-top"><b>${m.mastered_rate || 0}%</b><em>掌握率</em></div>` +
      `<div class="dm-sub">${m.total_knowledge || 0} 个知识点 · ${m.weak || 0} 薄弱 / ${m.shaky || 0} 需复习` +
      `<span class="bar"><i style="width:${m.mastered_rate || 0}%"></i></span></div></div>` +
      `<div class="dmr warn"><div class="dm-top"><b>${r.due || 0}</b><em>今日到期复习</em></div>` +
      `<div class="dm-sub">${r.total || 0} 张在册 · ${r.new || 0} 新 / ${r.review || 0} 复习中</div></div>` +
      `<div class="dmr acc"><div class="dm-top"><b>${t.in_progress || 0}</b><em>进行中提问会话</em></div>` +
      `<div class="dm-sub">共 ${t.total || 0} 场 · 已答 ${t.answered_rounds || 0} 轮</div></div>` +
    '</div>';
  // C22：近期活动时间线（讲解/复习打卡/提问作答；来自知识点 history 聚合）
  const acts = d.recent || [];
  $("dash_loop").innerHTML += acts.length
    ? `<div class="dash-act"><h3>近期活动</h3>` + acts.map(a => {
        const t = String(a.t || "").slice(5, 16).replace("T", " ");
        return `<div class="act-row"><span class="act-t">${esc(t)}</span><b>${esc(a.label)}</b>
          <span class="act-note">${esc(a.kp_name || "")}${a.subject ? " · " + esc(a.subject) : ""}${a.note ? "（" + esc(a.note) + "）" : ""}</span></div>`;
      }).join("") + `</div>`
    : "";
}

async function loadLibrary() {
  invalidateLearnCache();   // 学习中心刷新 = 全量刷新（子视图内交互走 30s 缓存）
  try {
    loadOverview($("dash_subject") ? $("dash_subject").value : "");   // C1：顶部与闭环同口径
    loadExplainCtx(appliedSubject());          // M3：同步刷新科目 / 知识点 / 讲解产物
    loadTutorCtx();                            // M4：同步刷新提问式学习的科目 / 知识点 / 会话
    // v0.8.1：复习计划（loadReviewCtx）已迁入「刷题」tab，由 loadStudy 触发
  } catch (e) {
    $("learn_kp").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
/* ---- ② 刷题 tab（v0.8.1）：科目卡片 + 今日到期复习（SM-2 复习卡 + FSRS 记忆卡） ---- */
function loadStudy() {
  loadReviewCtx(rvSubject || "");
  loadStudySubjects();
}
async function loadStudySubjects() {
  const box = $("study_subjects");
  if (!box) return;
  try {
    const r = await api("/api/library/subjects");
    const subs = r.subjects || [];
    const byName = {};
    (r.stats || []).forEach(s => { byName[s.subject] = s; });
    const card = (s, st) => `
      <button class="subj-card${rvSubject === s ? " on" : ""}" data-subject="${esc(s)}" onclick="loadReviewCtx(this)" title="只看「${esc(s)}」的到期复习">
        <div class="subj-avatar">${esc((s || "未分类").slice(0, 1))}</div>
        <div class="subj-main">
          <div class="subj-name">${esc(s)}</div>
          <div class="subj-row"><span>今日到期 <b>${st.review_due || 0}</b></span><span>总卡 <b>${st.review_total || 0}</b></span></div>
          <div class="subj-row"><span>错题 <b>${st.mistakes || 0}</b></span><span>掌握率 <b>${st.mastered_rate || 0}%</b></span></div>
        </div>
      </button>`;
    const allCard = `
      <button class="subj-card${rvSubject === "" ? " on" : ""}" onclick="loadReviewCtx('')" title="查看全部科目的到期复习">
        <div class="subj-avatar all">📚</div>
        <div class="subj-main">
          <div class="subj-name">全部科目</div>
          <div class="subj-row"><span>共 <b>${subs.length}</b> 个科目</span><span>点卡片按科过滤</span></div>
        </div>
      </button>`;
    box.innerHTML = subs.length
      ? `<div class="subj-grid">` + allCard + subs.map(s => card(s, byName[s] || {})).join("") + `</div>`
      : `<div class="hint">暂无科目——先在错题本导入错题，或去「题库」生成题目</div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
/* WP-4：科目管理弹层（列表 + 各科统计 + 删除，删除前自动备份） */
let _subjMgrBusy = false;
async function subjectMgrOpen() {
  if (_subjMgrBusy) return;
  _subjMgrBusy = true;
  try {
    const r = await api("/api/library/subjects");
    const stats = r.stats || [];
    const body = !stats.length
      ? `<div class="hint">暂无科目——先在错题本导入错题，或去「题库」生成题目。</div>`
      : `<div class="subj-mgr">` + stats.map(s => `
        <div class="subj-mgr-row">
          <span class="subj-mgr-avatar">${esc((s.subject || "未分类").slice(0, 1))}</span>
          <span class="grow"><b>${esc(s.subject)}</b>
            <small class="hint">错题 ${s.mistakes || 0} · 知识点 ${s.knowledge || 0} · 复习卡 ${s.review_total || 0}</small>
          </span>
          <button class="mini-btn danger" data-subj="${esc(s.subject)}" onclick="subjectDelete(this)">删除</button>
        </div>`).join("") + `</div>
        <div class="hint" style="margin-top:8px">删除前会自动导出完整备份 JSON；删除后该科错题、知识点、复习卡、记忆卡、讲解与提问会话一并移除。</div>`;
    confirmModal("科目管理", body, "关闭", () => loadStudy(), false);
  } catch (e) {
    toast(e.message, false);
  } finally { _subjMgrBusy = false; }
}
async function subjectDelete(btn) {
  const subject = (btn && btn.dataset.subj) || "";
  if (!subject) return;
  confirmModal("删除科目？",
    `<p>将删除科目 <b>${esc(subject)}</b> 的：错题、知识点掌握度、复习卡、记忆卡、讲解产物、提问会话。<br>
     删除前会自动导出完整备份到本地 exports 目录，删除后需手动恢复。</p>`,
    "导出并删除", async () => {
      try {
        const r = await api("/api/library/subjects/delete",
          { method: "POST", body: JSON.stringify({ subject }) });
        const d = r.deleted || {};
        toast(`已删除「${subject}」：错题 ${d.mistakes || 0} · 知识点 ${d.knowledge || 0} · 复习卡 ${d.review_cards || 0} · 记忆卡 ${d.memory_cards || 0} · 会话 ${d.sessions || 0} · 讲解 ${d.explains || 0}`);
        if (typeof rvSubject !== "undefined" && rvSubject === subject) rvSubject = "";
        loadStudy();       // 刷题 tab：科目卡片 + 复习计划刷新
        loadLibrary();     // 学习中心：概览/dashboard/badge 刷新
        subjectMgrOpen();  // 弹出已刷新的科目管理列表
      } catch (e) { toast(e.message, false); }
    });
}
function appliedSubject() {
  const el = $("exp_subject");
  return el && el.value ? el.value : "";
}
/* C17：错题本「只看未掌握」筛选——缓存最近一次数据，勾选即重渲染（已掌握=归档标记，可隐藏） */
let _libCache = { mistakes: [], my: null, recm: [] };
let mkSubject = "";       // R3-22：错题本作用域（跟随概览科目过滤，可独立切回全部科目）
let mkShowAll = false;     // D-14：错题分块渲染（首屏 100 条 + 「加载全部」按钮）
let mkSelected = new Set();  // WP-5：错题多选集合（id）
function renderLibrary(mistakes, my, recm) {
  _libCache = { mistakes: mistakes || [], my: my || null, recm: recm || [] };
  mkShowAll = false;       // 新数据到达 → 回到分块首屏
  mkSelected.clear();      // WP-5：数据刷新后清空多选
  renderLibraryCurrent();
}
function renderLibraryCurrent() {
  const { mistakes, my, recm } = _libCache;
  const chk = document.getElementById("mk_filter_unlearned");
  const onlyUn = !!(chk && chk.checked);
  // R3-22：作用域过滤（概览科目口径 + 错题本独立下拉）；D-14：分块渲染
  const scoped = mistakes.filter(m => !mkSubject || m.subject === mkSubject);
  const list = onlyUn ? scoped.filter(m => !m.learned) : scoped;
  syncMkScope();
  const stats = (my && my.stats) || {};
  $("learn_stats").innerHTML = `共 <b>${stats.total_knowledge || 0}</b> 个知识点 · 薄弱 <b style="color:#f87171">${stats.weak || 0}</b> · 错题 <b>${stats.total_mistakes || 0}</b>`;
  // 薄弱点诊断 + 待学优先级
  const kps = (my && my.knowledge) || [];
  $("learn_kp").innerHTML = kps.length
      ? renderMasteryDashboard(kps, stats)
      : `<div class="hint">还没有知识点。加入错题后，这里会自动按「错题次数 + 距上次失败」给出掌握度。</div>`;
  const recs = recm || [];
  $("learn_reco").innerHTML = recs.length ? recs.map(r => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px dashed var(--line);flex-wrap:wrap">
      <span style="flex:1;min-width:150px;color:var(--text);font-size:13px">${esc(r.name)}</span>
      ${learnChip(r.state)}
      <span class="hint" style="font-size:11.5px;white-space:nowrap">优先度 ${Math.round((r.priority || 0) * 100)}</span>
      <span style="display:flex;gap:5px">
        <button class="mini-btn primary" onclick="learnRecAction(this)" data-kind="explain" data-subject="${esc(r.subject || "")}" data-name="${esc(r.name)}">→ 讲解</button>
        <button class="mini-btn" onclick="learnRecAction(this)" data-kind="tutor" data-subject="${esc(r.subject || "")}" data-name="${esc(r.name)}">→ 提问</button>
        <button class="mini-btn" onclick="learnRecAction(this)" data-kind="queue" data-subject="${esc(r.subject || "")}" data-name="${esc(r.name)}">铺卡</button>
      </span>
    </div>`).join("") : `<div class="hint">暂无薄弱点，先把错题收进来。</div>`;

  // 错题本（增强版：→讲解 / →提问 / 详情展开 / 已掌握 / 删除）
  const shown = mkShowAll ? list : list.slice(0, 100);   // D-14：首屏约 100 条
  // R4-21：明示「全选可见」实际选中范围（首屏 N/共 M），未加载全部时不再误导
  const btnAll = $("btn_mk_all");
  if (btnAll) btnAll.textContent = list.length > shown.length
    ? `全选已加载（${shown.length}/${list.length}）` : "全选全部";
  updateMkToolbar(list);   // WP-5：工具栏随筛选/数据刷新
  $("learn_mk_count").textContent = `${list.length} 道`
    + (onlyUn && list.length !== scoped.length ? `（未掌握 ${list.length} / ${scoped.length}）` : "");
  if (!list.length) {
    $("learn_mk").innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
      <div class="sub">${mistakes.length
        ? (onlyUn && scoped.length
          ? `当前筛选下没有错题（已掌握 ${scoped.length} 道被隐藏）<br>取消勾选「只看未掌握」可查看全部`
          : `「${mkSubject || "全部科目"}」下暂无错题<br>切到其他科目或先入库错题`)
        : `错题本还是空的<br>粘贴一道错题入库，或点「拍题(图片 OCR)」「批量导入(JSON)」`}</div>
    </div>`;
  } else {
    $("learn_mk").innerHTML = mkGroupHTML(shown) +
      (list.length > shown.length
        ? `<div class="btns" style="justify-content:center;margin-top:10px"><button class="act gray" onclick="mkShowAllFn()">加载全部（共 ${list.length} 道）</button></div>`
        : "");
  }
}
function mkShowAllFn() { mkShowAll = true; renderLibraryCurrent(); }
window.mkShowAllFn = mkShowAllFn;
/* WP-5：错题本多选/分组/批量操作 */
function mkToggleRow(cb) {
  const id = cb && cb.dataset.id;
  if (!id) return;
  if (cb.checked) mkSelected.add(id); else mkSelected.delete(id);
  updateMkToolbar();
  const ds = cb.closest("details");
  if (ds) {
    const head = ds.querySelector(".mk-group-count");
    if (head) head.textContent = ds.querySelectorAll(".mkck").length + " 道";
  }
}
function mkToggleGroup(cb) {
  const ds = cb.closest("details");
  if (!ds) return;
  ds.querySelectorAll(".mkck").forEach(c => {
    c.checked = cb.checked;
    if (cb.checked) mkSelected.add(c.dataset.id); else mkSelected.delete(c.dataset.id);
  });
  updateMkToolbar();
  const head = ds.querySelector(".mk-group-count");
  if (head) head.textContent = ds.querySelectorAll(".mkck").length + " 道";
}
function mkToggleAllVisible() {
  document.querySelectorAll("#learn_mk .mkck").forEach(c => {
    c.checked = true; mkSelected.add(c.dataset.id);
  });
  updateMkToolbar();
}
function mkInvert() {
  document.querySelectorAll("#learn_mk .mkck").forEach(c => {
    c.checked = !c.checked;
    if (c.checked) mkSelected.add(c.dataset.id); else mkSelected.delete(c.dataset.id);
  });
  updateMkToolbar();
}
function mkClearSel() {
  mkSelected.clear();
  document.querySelectorAll("#learn_mk .mkck").forEach(c => { c.checked = false; });
  updateMkToolbar();
}
function updateMkToolbar(list) {
  const bar = $("mk_bar");
  if (!bar) return;
  const total = list ? list.length : document.querySelectorAll("#learn_mk .mk-row").length;
  bar.style.display = total ? "flex" : "none";
  const cnt = $("mk_sel_count");
  if (cnt) cnt.textContent = "已选 " + mkSelected.size;
  const del = $("btn_mk_batch_del");
  if (del) del.disabled = !mkSelected.size;
}
function mkGroupHTML(list) {
  const groups = {};
  list.forEach(mm => {
    const key = [mm.subject || "未分类", mm.chapter || "未分章",
                 (mm.know_tags || [])[0] || mm.topic || "未分组"].join("§");
    (groups[key] = groups[key] || []).push(mm);
  });
  return Object.keys(groups).map(key => {
    const arr = groups[key];
    const parts = key.split("§");
    const subj = parts[0] || "未分类", ch = parts[1] || "未分章", tag = parts[2] || "未分组";
    const allChecked = arr.every(mm => mkSelected.has(mm.id));
    return `<details class="mk-group" open>
      <summary class="mk-group-head">
        <span class="mk-group-title">${esc(subj)}<small>${esc(ch)}</small></span>
        <span class="mk-group-tag">${esc(tag)}</span>
        <span class="mk-group-count">${arr.length} 道</span>
        <label class="chk mk-group-sel" onclick="event.stopPropagation()">
          <input type="checkbox" ${allChecked ? "checked" : ""} onchange="mkToggleGroup(this)"> 本组全选
        </label>
      </summary>
      <div class="mk-group-body">${arr.map(mm => mkRowHTML(mm)).join("")}</div>
    </details>`;
  }).join("");
}
/* R4-22：错题批处理在途互斥——连点不再重复发请求（与站内其它批量「禁用」范式一致） */
let mkBatchBusy = false;
function _mkBatchSetBusy(on) {
  mkBatchBusy = on;
  document.querySelectorAll("#mk_bar button").forEach(b => { b.disabled = on; });
}
async function mkBatchDel() {
  if (mkBatchBusy) return;
  if (!mkSelected.size) { toast("请先勾选错题", false); return; }
  const n = mkSelected.size;
  confirmModal(`批量删除 ${n} 道错题？`,
    `<p style="margin:0;color:var(--dim)">删除前会自动导出所选错题 JSON 备份（~/.medkit/exports/），删除后对应知识点掌握度刷新。<br>
     <span class="hint">已生成的讲解 / 提问会话 / 复习卡 / 记忆卡会保留。</span></p>`,
    "导出并删除", async () => {
      try {
        _mkBatchSetBusy(true);
        const ids = [...mkSelected];
        const r = await api("/api/library/mistakes/batch-delete",
          { method: "POST", body: JSON.stringify({ ids }) });
        toast(`已删除 ${r.deleted || 0} 道` + (r.backup ? "（已备份）" : ""));
        mkSelected.clear();
        loadLibrary();
      } catch (e) { toast(e.message, false); }
      finally { _mkBatchSetBusy(false); }
    });
}
async function mkBatchLearn(learned) {
  if (mkBatchBusy) return;
  if (!mkSelected.size) { toast("请先勾选错题", false); return; }
  try {
    _mkBatchSetBusy(true);
    const ids = [...mkSelected];
    const r = await api("/api/library/mistakes/batch-learn",
      { method: "POST", body: JSON.stringify({ ids, learned }) });
    toast(`${learned ? "已标记" : "已取消"}已掌握 ${r.updated || 0} 道`);
    mkSelected.clear();
    loadLibrary();
  } catch (e) { toast(e.message, false); }
  finally { _mkBatchSetBusy(false); }
}
async function mkBatchExport(fmt) {
  if (mkBatchBusy) return;
  if (!mkSelected.size) { toast("请先勾选错题", false); return; }
  try {
    _mkBatchSetBusy(true);
    const r = await api("/api/library/mistakes/batch-export",
      { method: "POST", body: JSON.stringify({ ids: [...mkSelected], format: fmt }) });
    const mime = fmt === "md" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8";
    const blob = new Blob([r.data || ""], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = r.filename || ("medkit_mistakes_export." + fmt);
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast(`已导出 ${mkSelected.size} 道（${r.filename || fmt}）`);
  } catch (e) { toast(e.message, false); }
  finally { _mkBatchSetBusy(false); }
}
window.mkToggleRow = mkToggleRow; window.mkToggleGroup = mkToggleGroup;
window.mkToggleAllVisible = mkToggleAllVisible; window.mkInvert = mkInvert;
window.mkClearSel = mkClearSel; window.mkBatchDel = mkBatchDel;
window.mkBatchLearn = mkBatchLearn; window.mkBatchExport = mkBatchExport;

/* R3-22：错题本科目下拉——选项随科目集刷新，切换与概览过滤联动 */
function fillMkSubjectSelect() {
  const sel = $("mk_subject");
  if (!sel) return;
  const cur = sel.value || mkSubject || "";
  const subs = (LEARN_CACHE.subjects && LEARN_CACHE.subjects.subjects) || [];
  sel.innerHTML = '<option value="">全部科目</option>' +
    subs.slice().sort().map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  if (cur && subs.includes(cur)) sel.value = cur;
}
function mkScopeChange(v) {
  mkSubject = v || "";
  // R4-23：先用缓存即时按新科目重渲染列表（syncMkScope 在 renderLibraryCurrent 内）——
  // 避免「标签已切、列表还是旧态」的短暂错位；异步刷新数据随后跟上
  renderLibraryCurrent();
  // 与概览过滤联动：按新作用域刷新概览数据（与切换 dash_subject 行为一致）
  if (typeof loadOverview === "function") { loadOverview(mkSubject); return; }
}
function syncMkScope() {
  const scope = $("learn_mk_scope");
  if (scope) scope.textContent = mkSubject ? "科目：" + mkSubject : "";
  const dash = $("dash_subject");
  if (dash && dash.value !== mkSubject) dash.value = mkSubject;
}
window.mkScopeChange = mkScopeChange;
window.renderLibraryCurrent = renderLibraryCurrent;
const ERR_LABEL = { concept_gap: "概念缺失", confusion: "易混", calculation: "计算失误", misread: "误读题干", reasoning: "推理断链", unanswered: "未作答", unknown: "未标注" };
function mkRowHTML(mm) {
  const meta = [];
  const loc = [mm.subject, mm.chapter, mm.topic].filter(Boolean).join(" · ");
  if (loc) meta.push(`<span class="mk-tag">${esc(loc)}</span>`);
  (mm.know_tags || []).forEach(t => meta.push(`<span class="mk-tag">${esc(t)}</span>`));
  meta.push(`<span class="mk-tag">答错 ${mm.miss_count || 1} 次</span>`);
  if (mm.error_reason) meta.push(`<span class="mk-tag" style="color:var(--warn);border-color:var(--warn)">${esc(ERR_LABEL[mm.error_reason] || mm.error_reason)}</span>`);
  if (mm.learned) meta.push(`<span class="mk-tag" style="color:var(--good);border-color:var(--good)">已掌握</span>`);
  if (mm.source === "paper") meta.push(`<span class="mk-tag" style="color:var(--info);border-color:var(--info)">押题卷</span>`);
  if (mm.data_broken) meta.push(`<span class="mk-tag" style="color:var(--bad);border-color:var(--bad)">数据损坏</span>`);
  const detail = `
    ${mm.case_stem ? `<div><b>案例</b>：${esc(mm.case_stem)}</div>` : ""}
    ${mm.image_ref && mm.source_ref && mm.source_ref.pid ? `<div><b>图</b>：<img src="/api/projects/${esc(mm.source_ref.pid)}/assets/${esc(mm.image_ref)}" style="max-width:320px;max-height:240px;border-radius:8px;border:1px solid var(--line);display:block;margin:6px 0" onerror="this.remove()"></div>` : ""}
    ${(mm.options || []).length ? `<div><b>选项</b>：${mm.options.map((o, i) => `${"ABCDEF"[i] || i + 1}. ${esc(o)}`).join("　")}</div>` : ""}
    ${mm.answer ? `<div class="ans">✓ 答案：${esc(mm.answer)}</div>` : ""}
    ${mm.user_answer ? `<div><b>我的作答</b>：${esc(mm.user_answer)}</div>` : ""}
    ${mm.analysis ? `<div><b>解析</b>：${hlKw(mm.analysis)}</div>` : ""}`;
  const kp = (mm.know_tags || [])[0] || mm.topic || "";
  return `<div class="mk-row">
    <label class="mkck-wrap" title="选择该题">
      <input type="checkbox" class="mkck" data-id="${esc(mm.id)}" ${mkSelected.has(mm.id) ? "checked" : ""} onchange="mkToggleRow(this)">
    </label>
    <div class="mk-main">
      <div class="mk-q" data-id="${esc(mm.id)}" onclick="mkDetailTgl(this)" title="点击展开详情">${esc(mm.question || "(无题干)")}</div>
      <div class="mk-meta">${meta.join("")}</div>
      <div class="mk-detail" id="mkd_${esc(mm.id)}">${detail || '<div class="hint">（无更多详情）</div>'}</div>
    </div>
    <div class="mk-actions">
      ${kp ? `<button class="mini-btn primary" onclick="learnRecAction(this)" data-kind="explain" data-subject="${esc(mm.subject || "")}" data-name="${esc(kp)}">→ 讲解</button>
      <button class="mini-btn" onclick="learnRecAction(this)" data-kind="tutor" data-subject="${esc(mm.subject || "")}" data-name="${esc(kp)}">→ 提问</button>` : ""}
      ${mm.learned ? "" : `<button class="act" style="padding:5px 11px;font-size:12px" data-id="${esc(mm.id)}" onclick="mkLearn(this,true)">已掌握</button>`}
      ${mm.learned && kp ? `<button class="act gray" style="padding:5px 11px;font-size:12px" data-subject="${esc(mm.subject || "")}" data-kp="${esc(kp)}" onclick="mkPurgeSameCards(this)">清同名卡</button>` : ""}
      <button class="act gray" style="padding:5px 11px;font-size:12px;color:#f87171" data-id="${esc(mm.id)}" onclick="mkDel(this)">删除</button>
    </div>
  </div>`;
}
function mkDetailTgl(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  const d = $("mkd_" + id);
  if (d) d.classList.toggle("open");
}
/* 学习中心推荐/错题行动作：讲解 / 提问 / 铺卡（复用既有流程，先定位到对应视图）
   R3-02：按钮经 onclick="learnRecAction(this)" + data-kind/data-subject/data-name 传参——
   知识点名含英文撇号（Hodgkin's 等）不再击穿行内 JS；程序化调用仍可用旧签名。 */
async function learnRecAction(btnOrKind, subject, kpName) {
  if (btnOrKind && typeof btnOrKind === "object" && btnOrKind.dataset) {
    const d = btnOrKind.dataset;
    subject = d.subject || "";
    kpName = d.name || "";
    btnOrKind = d.kind || "";
  }
  const kind = btnOrKind;
  if (!kpName) { toast("该记录缺少知识点，无法定位", false); return; }
  try {
    if (kind === "explain") {
      showLearnView("explain");
      if (subject && $("exp_subject")) $("exp_subject").value = subject;
      await loadExplainCtx(subject);
      if (![...$("exp_kp").options].some(o => o.value === kpName)) {
        const o = document.createElement("option");
        o.value = o.textContent = kpName; o.dataset.subject = subject || "";
        $("exp_kp").appendChild(o);
      }
      $("exp_kp").value = kpName;
      await expGenerate();
    } else if (kind === "tutor") {
      showLearnView("tutor");
      if (subject && $("tu_subject")) $("tu_subject").value = subject;
      await loadTutorCtx(subject);   // R3-23：重建下拉后保留原科目
      if (![...$("tu_kp").options].some(o => o.value === kpName)) {
        const o = document.createElement("option");
        o.value = o.textContent = kpName; o.dataset.subject = subject || "";
        $("tu_kp").appendChild(o);
      }
      $("tu_kp").value = kpName;
      await tutorStart();
    } else if (kind === "queue") {
      await api("/api/library/review/queue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: subject || "", kp_name: kpName }) });
      toast(`「${kpName}」已排入复习队列`);
      loadReviewCtx(subject);
      updateByQueue();
    }
  } catch (e) { toast(e.message, false); }
}
async function updateByQueue() {
  try { const d = await api("/api/library/dashboard"); updateLearnBadges(d); } catch (e) { /* ignore */ }
}
/* 数据卫生：一键修复学习库乱码（备份 → 还原 → 标记） */
async function healLibrary() {
  try {
    const r = await api("/api/library/maintenance/heal", { method: "POST" });
    toast(`修复完成：还原 ${r.healed || 0} 条 · 标记损坏 ${r.flagged || 0} 条` + ((r.backups || []).length ? "（原文件已备份）" : ""));
    await loadLibrary();
  } catch (e) { toast(e.message, false); }
}
window.mkDetailTgl = mkDetailTgl; window.learnRecAction = learnRecAction; window.healLibrary = healLibrary;
async function mkLearn(idOrEl, learned) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 (id, learned)
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  try {
    await api(`/api/library/mistakes/${id}/learn`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ learned: learned }) });
    // R3-27：明确告知「仅归档标记」——同名复习卡/记忆卡仍在到期队列（R4-24：可用「清同名卡」移出）
    toast(learned ? "已标记为已掌握：仅归档标记。同名卡可点「清同名卡」移出队列"
                  : "已取消已掌握标记"); loadLibrary();
  } catch (e) { toast(e.message, false); }
}
/* R4-24：标记「已掌握」后的显式清理——移出同名复习卡/记忆卡（带确认，不静默删）
   RV1：双签名——内联传 this（data-subject/data-kp），程序化调用传 (subject, kpName) */
async function mkPurgeSameCards(subjectOrEl, kpName) {
  let subject = subjectOrEl;
  if (subjectOrEl && typeof subjectOrEl === "object" && subjectOrEl.dataset) {
    subject = subjectOrEl.dataset.subject || "";
    kpName = subjectOrEl.dataset.kp || "";
  }
  confirmModal("移出同名卡？",
    `<p style="margin:0;color:var(--dim)">将移出 <b>${esc(kpName)}</b> 的同名复习卡 / 记忆卡（${esc(subject || "未分类")}）。<br>
     <span class="hint">删除后该卡的 SM-2/FSRS 排期数据一并清除；确认知识点已掌握时再操作。</span></p>`,
    "移出同名卡", async () => {
      try {
        const r = await api("/api/library/review/purge-same", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subject: subject || "", kp_name: kpName }) });
        toast(`已移出复习卡 ${r.review_removed || 0} · 记忆卡 ${r.memory_removed || 0}`);
        await Promise.all([loadReviewCtx(rvSubject), loadLibrary()]);
      } catch (e) { toast(e.message, false); }
    }, false);
}
window.mkPurgeSameCards = mkPurgeSameCards;
async function mkDel(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  confirmModal("删除错题", `<p style="margin:0;color:var(--dim)">确定删除这道错题吗？对应知识点掌握度会随之刷新。<br>
    <span class="hint">已生成的讲解 / 提问会话 / 复习卡 / 记忆卡会<b>保留</b>；如不再需要请到对应视图删除。</span></p>`,
    "删除", async () => {
      try { await api("/api/library/mistakes/" + id, { method: "DELETE" }); toast("已删除"); loadLibrary(); }
      catch (e) { toast(e.message, false); }
    });
}
async function addMistakeRaw() {
  const text = $("mk_text").value.trim();
  if (!text) { toast("请先粘贴或输入错题内容", false); $("mk_text").focus(); return; }
  const tag = $("mk_tag").value.trim();
  const ch = $("mk_chapter").value.trim();
  const body = { question: text, know_tags: tag ? [tag] : [] };
  if (ch) { const p = ch.split(/[·|,，]/); body.chapter = (p[0] || "").trim(); body.topic = (p[1] || "").trim(); }
  try {
    await api("/api/library/mistakes/import-text", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("已入库并计入掌握度");
    $("mk_text").value = "";
    loadLibrary();
  } catch (e) { toast(e.message, false); }
}
function mkOcrPick() { $("mk_image").click(); }
async function mkOcrFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const btn = $("btn_mk_ocr"); const old = btn.textContent;
  btn.textContent = "识别中…"; btn.disabled = true;
  try {
    const r = await api("/api/library/mistakes/import-image", { method: "POST", body: fd });
    $("mk_text").value = (r.text || "").trim();
    toast("识别完成，请检查后点「入库错题」");
  } catch (e) { toast(e.message, false); }
  finally { btn.textContent = old; btn.disabled = false; input.value = ""; }
}
async function _siteImportItems(items) {
  if (!items || !items.length) throw new Error("未找到 items 数组");
  const r = await api("/api/library/mistakes/import-export", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }) });
  toast(`站点数据导入：新增 ${r.added || 0} · 更新 ${r.updated || 0} · 跳过 ${r.skipped || 0}`
    + (r.errors && r.errors.length ? `（${esc(r.errors[0])}…）` : ""));
  loadLibrary();
  return r;
}
function mkSiteImport() { $("mk_site_file").click(); }
async function mkSiteFile(input) {
  const f = input.files && input.files[0];
  input.value = "";
  if (!f) return;
  let data;
  try { data = JSON.parse(await f.text()); } catch (e) { toast("站点 JSON 解析失败", false); return; }
  const items = Array.isArray(data) ? data : (data.items || []);
  const btn = $("btn_mk_site"); const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "导入中…"; }
  try { await _siteImportItems(items); }
  catch (e) { toast(e.message, false); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}
function mkBatchPick() { $("mk_json").click(); }
async function mkBatchFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const btn = $("btn_mk_batch"); const old = btn.textContent;
  // C11：批量导入单请求——禁用按钮 + 进度文案（防连点重复导入）
  btn.disabled = true; btn.textContent = "导入中…";
  const name = (f.name || "").toLowerCase();
  const ext = name.includes(".") ? name.split(".").pop() : "txt";
  try {
    if (ext === "json") {
      // WP-11：站点导出 JSON（含 items 数组）走 import-export 幂等导入；
      // 其余 JSON 保持旧 import-file 本地解析器（options:[{label,text}] 等官方结构）
      let data = null;
      try { data = JSON.parse(await f.text()); } catch (e) { data = null; }
      const items = data ? (Array.isArray(data) ? data : (data.items || null)) : null;
      if (Array.isArray(items) && items.length) {
        await _siteImportItems(items);
      } else {
        const fd = new FormData();
        fd.append("file", f);
        const r = await api("/api/library/mistakes/import-file", { method: "POST", body: fd });
        toast(`已批量入库 ${r.added || 0} 道（共 ${r.total || 0} 条，跳过 ${r.skipped || 0} 条）`);
        loadLibrary();
      }
    } else {
      // csv / md / txt：统一走本地解析导入（多字段/多格式归一化）
      const fd = new FormData();
      fd.append("file", f);
      const r = await api("/api/library/mistakes/import-file", { method: "POST", body: fd });
      toast(`已导入 ${r.added} 道（解析 ${r.total}，跳过 ${r.skipped}）`);
      loadLibrary();
    }
  } catch (e) { toast(`批量导入失败：${e.message}`, false); }
  finally { input.value = ""; btn.disabled = false; btn.textContent = old; }
}
window.mkLearn = mkLearn; window.mkDel = mkDel; window.mkOcrPick = mkOcrPick;
window.mkBatchPick = mkBatchPick;
