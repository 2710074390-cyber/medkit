/* exported appliedSubject, healLibrary, loadDashboard, loadLibrary, loadOverview, updateByQueue */
/* ---- 掌握度驾驶舱：指标卡 + 状态分布环图 + 弱项清单 + 最弱章节 ---- */
const LEARN_COLORS = { weak: "var(--bad)", shaky: "var(--warn)", solid: "var(--viz-2)", mastered: "var(--good)" };
const LEARN_ORDER = ["weak", "shaky", "solid", "mastered"];
function dashDonut(stats) {
  const total = stats.total_knowledge || 0;
  if (!total) {
    return `<svg viewBox="0 0 120 120" width="132" height="132" style="display:block"><circle cx="60" cy="60" r="48" fill="none" stroke="var(--surface-3)" stroke-width="14"/><text x="60" y="63" text-anchor="middle" font-size="13" fill="var(--text-2)">无数据</text></svg>`;
  }
  const C = 2 * Math.PI * 48;
  const arcs = [];
  let acc = 0;
  for (const s of LEARN_ORDER) {
    const c = stats[s] || 0;
    if (!c) continue;
    const fr = c / total;
    const sl = Math.max(fr * C - 2, 0.6);
    arcs.push(`<circle cx="60" cy="60" r="48" fill="none" style="stroke:${LEARN_COLORS[s]}" stroke-width="14"
      stroke-dasharray="${sl} ${C - sl}" stroke-dashoffset="${-(acc * C + 2)}" transform="rotate(-90 60 60)"/>`);
    acc += fr;
  }
  const pct = Math.round(((stats.solid || 0) + (stats.mastered || 0)) / total * 100);
  return `<svg viewBox="0 0 120 120" width="132" height="132" style="display:block">
    ${arcs.join("")}
    <text x="60" y="57" text-anchor="middle" font-size="26" font-weight="700" fill="var(--text-1)" font-variant-numeric="tabular-nums">${pct}%</text>
    <text x="60" y="74" text-anchor="middle" font-size="9" fill="var(--text-2)">掌握率</text>
  </svg>`;
}
function dashMetrics(stats) {
  const cells = [["total_knowledge", "知识点"], ["weak", "薄弱"], ["shaky", "需复习"], ["solid", "较熟练"], ["mastered", "已掌握"]];
  return `<div class="dash-metrics">` + cells.map(([k, l]) =>
    `<div class="dash-metric"><b style="${k === "weak" ? "color:var(--bad)" : ""}">${esc(stats[k] || 0)}</b><span>${l}</span></div>`).join("") + `</div>`;
}
function dashLegend(stats) {
  return `<div class="dash-legend">` + LEARN_ORDER.map(s =>
    `<div style="display:flex;align-items:center;gap:8px"><i style="width:11px;height:11px;border-radius:50%;background:${LEARN_COLORS[s]};flex:none"></i><span style="flex:1;color:var(--text-2);font-size:var(--fs-2)">${LEARN_STATE[s]}</span><b style="font-variant-numeric:tabular-nums">${stats[s] || 0}</b></div>`).join("") + `</div>`;
}
function dashWeakRows(kps) {
  const list = kps.filter(k => (k.miss || 0) > 0 || k.state === "weak" || k.state === "shaky")
    .sort((a, b) => (b.priority || 0) - (a.priority || 0)).slice(0, 6);
  if (!list.length) return `<div class="hint">暂无薄弱点，先把错题收进来。</div>`;
  return list.map(k => {
    const tot = k.attempts || 0, c = k.correct || 0, m = k.miss || 0, den = tot || 1;
    const cs = Math.round(c / den * 100), ms = Math.round(m / den * 100);
    return `<div style="padding:7px 0;border-bottom:1px dashed var(--line-hair)">
      <div style="display:flex;align-items:center;gap:10px">
        <span style="flex:1;color:var(--text-1);font-size:var(--fs-2);min-width:0">${esc(k.name)}</span>
        ${learnChip(k.state)}
        <span class="hint" style="font-size:var(--fs-1);white-space:nowrap">优先 ${Math.round((k.priority || 0) * 100)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:5px">
        <div class="stack-bar"><i class="ok" style="width:${cs}%"></i><i class="miss" style="width:${ms}%"></i></div>
        <span class="hint" style="font-size:var(--fs-1);white-space:nowrap;font-variant-numeric:tabular-nums">对 ${c}/${tot}</span>
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
  return `<h3 style="font-size:var(--fs-2);color:var(--text-2);margin:12px 0 4px;font-weight:600">最弱章节（按错题量）</h3>` + rows.map(([name, g]) =>
    `<div style="display:flex;align-items:center;gap:10px;padding:4px 0"><span style="flex:1;color:var(--text-1);font-size:var(--fs-2)">${esc(name)}</span><span class="hint" style="font-size:var(--fs-1);white-space:nowrap">${g.n} 点 · ${g.miss} 错</span></div>`).join("");
}
function renderMasteryDashboard(kps, stats) {
  return dashMetrics(stats)
    + `<div class="dash">${dashDonut(stats)}<div><div style="font-size:var(--fs-2);color:var(--text-1);margin-bottom:6px">知识点状态分布</div>${dashLegend(stats)}<div class="hint" style="margin-top:8px;font-size:var(--fs-1)">掌握率 = (较熟练 + 已掌握) / 全部</div></div></div>`
    + `<div class="dash-weak"><h3>薄弱点清单（按优先级）</h3>${dashWeakRows(kps)}</div>`
    + dashChapterWeak(kps);
}
/* C1：概览统一加载——顶部「闭环总览 + 薄弱点诊断 + 推荐」共用同一科目范围 */
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
    const [d, my, recm] = await Promise.all([
      api("/api/library/dashboard?subject=" + encodeURIComponent(subject)),
      api("/api/library/mastery?subject=" + encodeURIComponent(subject)),
      api("/api/library/recommend?limit=6&subject=" + encodeURIComponent(subject)),
    ]);
    renderDashboard(d);
    renderLibrary(my, recm.recommend || []);
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
  const m = d.mastery || {}, loop = d.loop || {};
  $("dash_scope").textContent = `${d.subject_label} · 闭环 ${loop.mastered || 0}/${m.total_knowledge || 0} 已掌握`;
  updateLearnBadges();
  // 闭环流转：错题沉淀 → 已掌握
  const stages = [
    ["错题沉淀", loop.mistakes || 0, ""], ["已掌握", loop.mastered || 0, "mastered"],
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
  $("dash_loop").innerHTML = dBanner + cwBanner + '<div class="loop-flow">' + stages.map(([k, v, cls], i) =>
      (i ? '<div class="loop-arrow">→</div>' : "") +
      `<div class="loop-node ${cls}"><b>${v}</b><span>${k}</span></div>`).join("") +
    '</div>' +
    '<div class="dashmr">' +
      `<div class="dmr good"><div class="dm-top"><b>${m.mastered_rate || 0}%</b><em>掌握率</em></div>` +
      `<div class="dm-sub">${m.total_knowledge || 0} 个知识点 · ${m.weak || 0} 薄弱 / ${m.shaky || 0} 需复习` +
      `<span class="bar"><i style="width:${m.mastered_rate || 0}%"></i></span></div></div>` +
    '</div>';
  // C22：近期活动时间线（来自知识点 history 聚合）
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
  } catch (e) {
    $("learn_kp").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
function appliedSubject() {
  const el = $("exp_subject");
  return el && el.value ? el.value : "";
}
/* 概览下半区：薄弱点诊断 + 待学优先级（错题列表已由「错题本」视图统一承载） */
function renderLibrary(my, recm) {
  const stats = (my && my.stats) || {};
  $("learn_stats").innerHTML = `共 <b>${stats.total_knowledge || 0}</b> 个知识点 · 薄弱 <b style="color:var(--bad)">${stats.weak || 0}</b> · 错题 <b>${stats.total_mistakes || 0}</b>`;
  const kps = (my && my.knowledge) || [];
  $("learn_kp").innerHTML = kps.length
      ? renderMasteryDashboard(kps, stats)
      : `<div class="hint">还没有知识点。错题入库后，这里会按「错题次数 + 距上次失败」给出掌握度。</div>`;
  const recs = recm || [];
  $("learn_reco").innerHTML = recs.length ? recs.map(r => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px dashed var(--line-hair);flex-wrap:wrap">
      <span style="flex:1;min-width:150px;color:var(--text-1);font-size:var(--fs-2)">${esc(r.name)}</span>
      ${learnChip(r.state)}
      <span class="hint" style="font-size:var(--fs-1);white-space:nowrap">优先度 ${Math.round((r.priority || 0) * 100)}</span>
    </div>`).join("") : `<div class="hint">暂无薄弱点，先把错题收进来。</div>`;
}
async function updateByQueue() {
  try { await api("/api/library/dashboard"); updateLearnBadges(); } catch (e) { /* ignore */ }
}
/* 数据卫生：一键修复学习库乱码（备份 → 还原 → 标记） */
async function healLibrary() {
  try {
    const r = await api("/api/library/maintenance/heal", { method: "POST" });
    toast(`修复完成：还原 ${r.healed || 0} 条 · 标记损坏 ${r.flagged || 0} 条` + ((r.backups || []).length ? "（原文件已备份）" : ""));
    await loadLibrary();
  } catch (e) { toast(e.message, false); }
}
window.healLibrary = healLibrary;
