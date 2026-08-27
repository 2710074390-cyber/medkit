/* ---- ④ 学习中心（v0.7 M1/M2：错题本 + 掌握度诊断） ---- */
const LEARN_STATE = { weak: "待加强", shaky: "需复习", solid: "较熟练", mastered: "已掌握" };
function learnChip(state) {
  const txt = LEARN_STATE[state] || state || "未知";
  return `<span class="learn-chip ${esc(state || "")}">${txt}</span>`;
}
/* ---- 学习中心子导航：一屏一任务（概览/错题本/讲解产物/提问学习/复习计划） ---- */
function showLearnView(name) {
  document.querySelectorAll("#learnnav button").forEach(b => {
    const on = b.dataset.lv === name;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll("#tab-learn .learnview").forEach(v => {
    v.classList.toggle("show", v.id === "lv-" + name);
  });
  if (name === "syllabus") sylLoad();
  try { sessionStorage.setItem("medkit-learn-view", name); } catch (e) { /* ignore */ }
}
document.querySelectorAll("#learnnav button").forEach(b => {
  b.onclick = () => showLearnView(b.dataset.lv);
});
/* IMP-12①：学习中心子导航 Alt+1..6 直达（与主 tab Ctrl+1..5 同风格；flag 隐藏的 pill 自动跳过） */
const LEARN_ALT_KEYS = ["overview", "mistakes", "explain", "tutor", "review", "syllabus"];
window.addEventListener("keydown", e => {
  if (!e.altKey || e.ctrlKey || e.metaKey || !(e.key >= "1" && e.key <= "6")) return;
  const lv = LEARN_ALT_KEYS[+e.key - 1];
  if (!lv) return;
  const pill = document.querySelector('#learnnav button[data-lv="' + lv + '"]');
  if (!pill || pill.style.display === "none") return;
  e.preventDefault();
  if (!$("tab-learn").classList.contains("show")) showTab("learn");
  showLearnView(lv);
});
/* IMP-08：学习中心子导航 ←/→ 方向键循环（APG tab 模式；隐藏 pill 跳过） */
(function initLearnNavKeys() {
  const nav = document.getElementById("learnnav");
  if (!nav) return;
  nav.addEventListener("keydown", e => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const btns = [...nav.querySelectorAll("button")].filter(b => b.style.display !== "none");
    if (!btns.length) return;
    let i = btns.findIndex(b => b.classList.contains("on"));
    if (i < 0) i = 0;
    i = (i + (e.key === "ArrowRight" ? 1 : -1) + btns.length) % btns.length;
    e.preventDefault();
    btns[i].focus();
    showLearnView(btns[i].dataset.lv);
  });
})();
(function initLearnView() {
  let v = null;
  try { v = sessionStorage.getItem("medkit-learn-view"); } catch (e) { /* ignore */ }
  const ok = ["overview", "mistakes", "explain", "tutor", "review", "syllabus"].includes(v);
  if (ok) showLearnView(v);
})();
/* 侧栏「学习中心」徽章：薄弱 + 需复习 知识点数（>0 显示） */
function setLearnNavBadge(n) {
  const b = document.querySelector('button[data-tab="learn"]');
  if (!b) return;
  let d = b.querySelector(".navbadge");
  if (n > 0) {
    if (!d) { d = document.createElement("span"); d.className = "navbadge"; b.appendChild(d); }
    d.textContent = n > 99 ? "99+" : String(n);
    d.title = "薄弱知识点：" + n + " 个 → 去学习中心";
  } else if (d) d.remove();
}
/* 子导航计数徽章（闭环数据回填） */
function updateLearnBadges(d) {
  const loop = (d && d.loop) || {};
  const nb = (id, v, hot) => { const el = $(id); if (!el) return;
    el.textContent = v > 0 ? v : "";
    el.classList.toggle("hot", !!hot && v > 0); };
  nb("nb_overview", 0);
  nb("nb_mistakes", loop.mistakes || 0, true);
  nb("nb_explain", loop.explains || 0);
  nb("nb_tutor", loop.tutor || 0);
  nb("nb_review", loop.review || 0, (d && d.review && d.review.due) > 0);
  const m = (d && d.mastery) || {};
  setLearnNavBadge((m.weak || 0) + (m.shaky || 0));
}
/* ---- ⑥ 大纲覆盖（WP-01 考试锚定 · 以教师重点为纲） ---- */
let SYL_DRAFTS = [];
let SYL_LOADED = false;
let SYL_STD = "teacher";   // 数据标准：teacher=教师重点(默认) / official=官方大纲 / all=全部
async function sylLoad() {
  const meta = document.getElementById("syl_meta");
  try {
    // 首次打开：同步教师重点（扫项目 teacher 切片，幂等）
    if (!SYL_LOADED) {
      const sr = await api("/api/syllabus/sync-teacher", { method: "POST" });
      if (sr.items) toast(`教师重点同步：${sr.items} 条考点（${sr.projects} 个项目）`);
      SYL_LOADED = true;
    }
    const st = await api("/api/syllabus/status");
    const subs = st.subjects || [];
    const sel = document.getElementById("syl_subject");
    if (sel && sel.options.length <= 1) {
      sel.innerHTML = '<option value="">全部科目</option>' + subs
        .map(s => `<option value="${esc(s.subject)}">${esc(s.subject)}（${s.items} 条目）</option>`).join("");
    }
    if (st.seed && st.seed.exam && meta) meta.textContent = `种子：${st.seed.exam}`;
    if (FEATURES.realexams) rexHeat();
    const t = st.teacher || { items: 0, subjects: [] };
    const hint = document.getElementById("syl_std_hint");
    if (hint) hint.textContent = t.items
      ? `（教师重点：${t.items} 条 · ${t.subjects.join("、")}）`
      : "（暂无教师重点——在「新建课题」上传教师重点后自动同步；也可用「粘贴导入」）";
    sylRender(sel ? sel.value : "");
  } catch (e) { if (meta) meta.textContent = ""; sylFail("加载失败：" + e.message); }
}
function sylSetStd(std) {
  SYL_STD = std;
  document.querySelectorAll("#syl_std .css-pill").forEach(b => {
    const on = b.dataset.std === std;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  const sel = document.getElementById("syl_subject");
  sylRender(sel ? sel.value : "");
}
document.querySelectorAll("#syl_std .css-pill").forEach(b => {
  b.onclick = () => sylSetStd(b.dataset.std);
});
function sylFail(msg) {
  const b = document.getElementById("syl_body");
  if (b) b.innerHTML = `<div class="hint">${esc(msg)}</div>`;
}
async function sylRender(subject) {
  const body = document.getElementById("syl_body");
  const stats = document.getElementById("syl_stats");
  if (!body) return;
  body.innerHTML = '<div class="hint"><span class="spin"></span>计算覆盖度…</div>';
  try {
    const qs = new URLSearchParams();
    if (subject) qs.set("subject", subject);
    qs.set("source", SYL_STD);
    const d = await api("/api/syllabus/coverage?" + qs.toString());
    const t = d.totals || { items: 0, covered: 0, mastered: 0, pending: 0 };
    const pct = t.items ? Math.round((t.covered + t.mastered) / t.items * 100) : 0;
    stats.innerHTML = `<div class="syl-chips">
      <div class="syl-stat"><b>${t.items}</b><div class="hint">总条目</div></div>
      <div class="syl-stat"><b>${t.covered}</b><div class="hint">已覆盖</div></div>
      <div class="syl-stat"><b>${t.mastered}</b><div class="hint">已掌握</div></div>
      <div class="syl-stat"><b>${t.pending}</b><div class="hint">未覆盖</div></div>
      <div class="syl-stat"><b>${pct}%</b><div class="hint">覆盖率</div></div>
    </div>`;
    if (!d.chapters || !d.chapters.length) {
      const stdName = { teacher: "教师重点", seed: "官方大纲", all: "全部标准" }[SYL_STD];
      body.innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
        <div class="sub">暂无${stdName}条目 — ${SYL_STD === "teacher"
          ? '在「新建课题」上传教师重点后自动同步；或先粘贴教师重点考点清单。'
          : SYL_STD === "seed" ? '点「导入内置大纲」加载教材/真题种子，或先粘贴大纲。' : '点「导入内置大纲」或先粘贴大纲。'}</div>
        <button class="act gray" onclick="sylPaste()">粘贴/导入大纲 →</button>
      </div>`;
      return;
    }
    body.innerHTML = d.chapters.map(ch => {
      const items = (ch.items || []).map(it => {
        const s = it.status === "mastered" ? ["已掌握", "mastered"] :
                  it.status === "covered" ? ["已覆盖", "solid"] : ["未覆盖", "pending"];
        return `<div class="syl-item"><span class="learn-chip ${s[1]}">${s[0]}</span>
          <span class="grow"><b>${esc(it.item)}</b>${it.matched ? `<div class="hint">已匹配：${esc(it.matched)}</div>` : ""}</span></div>`;
      }).join("");
      return `<div class="syl-chap">${esc(ch.chapter)} <span class="hint">（覆盖 ${ch.covered + ch.mastered}/${ch.total} · 未覆盖 ${ch.pending}）</span></div>` +
        (items || '<div class="hint" style="margin-left:10px">（无条目，待粘贴）</div>');
    }).join("");
  } catch (e) { sylFail(e.message); }
}
async function sylEnsure() {
  const r = await api("/api/syllabus/ensure", { method: "POST", body: JSON.stringify({ force: false }) });
  toast(`大纲种子导入：新增 ${r.imported} 条（幂等）`);
  sylLoad();
}
function sylPaste() {
  document.getElementById("syl_paste_card").style.display = "";
}
async function sylParse() {
  const text = document.getElementById("syl_paste_text").value;
  const subject = document.getElementById("syl_subject").value || "";
  const r = await api("/api/syllabus/parse", { method: "POST", body: JSON.stringify({ text, subject }) });
  SYL_DRAFTS = r.drafts || [];
  const pv = document.getElementById("syl_paste_preview");
  if (!SYL_DRAFTS.length) {
    pv.innerHTML = `<div class="hint">${esc(r.note || "未识别到条目")}</div>`;
    return;
  }
  pv.innerHTML = `<div class="hint">预览 ${SYL_DRAFTS.length} 条：</div>` +
    SYL_DRAFTS.slice(0, 30).map(d => `<div class="syl-item">
      <span class="learn-chip pending">${esc(d.subject || "?")}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span></div>`).join("") +
    (SYL_DRAFTS.length > 30 ? `<div class="hint">…共 ${SYL_DRAFTS.length} 条（全部入库）</div>` : "");
}
async function sylParseConfirm() {
  if (!SYL_DRAFTS || !SYL_DRAFTS.length) { toast("先解析再确认"); return; }
  const items = SYL_DRAFTS.map(d => ({ subject: d.subject || "未分类", chapter: d.chapter || "", item: d.item }));
  const r = await api("/api/syllabus/confirm", { method: "POST", body: JSON.stringify({ items }) });
  toast(`大纲条目入库：新增 ${r.added} 条`);
  SYL_DRAFTS = [];
  document.getElementById("syl_paste_preview").innerHTML = '<div class="hint">已入库。可继续粘贴或点顶部「刷新」。</div>';
  await sylLoad();   // 等待确认后的刷新完成，避免迟到渲染覆盖后续标准切换
}
async function sylReport() {
  const subject = document.getElementById("syl_subject").value || "";
  const qs = new URLSearchParams();
  if (subject) qs.set("subject", subject);
  qs.set("source", SYL_STD);
  const r = await api("/api/syllabus/report?" + qs.toString());
  downloadText("大纲覆盖报告_" + (subject || "全部") + ".md", r.markdown);
}

/* ---- ⑦ 真题考频（WP-02） ---- */
let REX_DRAFTS = [];
function rexSubject() { return document.getElementById("syl_subject")?.value || ""; }
async function rexAnalyze() {
  const text = document.getElementById("rex_text").value;
  if (!text.trim()) { toast("先粘贴真题文本", false); return; }
  const r = await api("/api/library/realexams/analyze",
    { method: "POST", body: JSON.stringify({ text, subject: rexSubject() }) });
  REX_DRAFTS = r.drafts || [];
  const box = document.getElementById("rex_drafts");
  if (!REX_DRAFTS.length) {
    box.innerHTML = `<div class="hint">未识别到考点命中（${r.stats?.unmatched ?? 0} 句未命中词典）——可先导入大纲种子或粘贴大纲，词典越全频次越准。</div>`;
    toast(`分析完成：命中 0 条（${r.stats?.unmatched ?? 0} 句未匹配）`, false);
    return;
  }
  // IMP-12③：草稿确认区折叠（默认收起，避免长表格把热力表挤出首屏）
  box.innerHTML = `<details class="rex-fold" open><summary>草稿确认（${REX_DRAFTS.length} 条）</summary>
    <div class="hint">来源句子 ${r.stats?.sentences} · 未命中 ${r.stats?.unmatched} ——核实后确认：</div>` +
    REX_DRAFTS.slice(0, 30).map((d, i) => `<div class="syl-item">
      <span class="learn-chip pending">×${d.freq}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")} · ${esc(d.subject || "?")}</div></span>
      <button class="act gray mini" data-skip="${i}">跳过</button></div>`).join("") +
    (REX_DRAFTS.length > 30 ? `<div class="hint">…共 ${REX_DRAFTS.length} 条（确认全部入库）</div>` : "") +
    `<div class="btns" style="margin-top:8px"><button class="act" onclick="rexConfirmAll()">确认全部入库</button>
     <span class="hint" style="align-self:center">未确认不进入任何推荐权重（红线）</span></div></details>`;
  box.querySelectorAll("[data-skip]").forEach(b => b.onclick = () => {
    REX_DRAFTS.splice(+b.dataset.skip, 1);
    rexAnalyzeRender();
  });
}
function rexAnalyzeRender() {
  const box = document.getElementById("rex_drafts");
  if (!REX_DRAFTS.length) { box.innerHTML = '<div class="hint">已清空选择。</div>'; return; }
  document.querySelectorAll("#rex_drafts .syl-item").forEach((el, i) => {
    if (i < REX_DRAFTS.length) { el.querySelector("b").textContent = REX_DRAFTS[i].item; }
  });
}
async function rexConfirmAll() {
  if (!REX_DRAFTS.length) { toast("先分析"); return; }
  const r = await api("/api/library/realexams/confirm",
    { method: "POST", body: JSON.stringify({ items: REX_DRAFTS.slice(0, 200) }) });
  toast(`已确认 ${r.added} 条频次（可重复确认合并）`);
  REX_DRAFTS = [];
  document.getElementById("rex_drafts").innerHTML = '<div class="hint">已确认。热力表见下。</div>';
  const fold = document.getElementById("rex_drafts").closest("details");
  if (fold) fold.open = false;
  rexHeat();
  // IMP-12③：确认后收起草稿并锚定到热力表
  setTimeout(() => { const h = document.getElementById("rex_heat"); if (h) h.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 150);
}
async function rexHeat() {
  const r = await api("/api/library/realexams/freq" + (rexSubject() ? "?subject=" + encodeURIComponent(rexSubject()) : ""));
  const box = document.getElementById("rex_heat");
  document.getElementById("rex_meta").textContent = `累计命中 ${r.total} 次`;
  if (!r.chapters.length) { box.innerHTML = '<div class="hint">暂无已确认频次。</div>'; return; }
  box.innerHTML = `<table class="rex-tab"><thead><tr><th>章节</th><th>频次</th><th>高频条目（前5）</th></tr></thead><tbody>` +
    r.chapters.slice(0, 15).map(ch => `<tr><td>${esc(ch.chapter)}</td><td><b>${ch.freq}</b></td><td>${esc(ch.items.slice(0, 5).map(i => i.item + "×" + i.freq).join(" · "))}</td></tr>`).join("") +
    `</tbody></table>`;
}
function rexFilePick() { document.getElementById("rex_file").click(); }
async function rexFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const r = await api("/api/library/realexams/analyze-file", { method: "POST", body: fd });
  REX_DRAFTS = r.drafts || [];
  document.getElementById("rex_text").value = `（已解析文件 ${f.name}：${r.stats?.sentences ?? 0} 句 · 命中 ${REX_DRAFTS.length} 条）`;
  const box = document.getElementById("rex_drafts");
  box.innerHTML = REX_DRAFTS.length ? `<details class="rex-fold" open><summary>草稿确认（${REX_DRAFTS.length} 条）</summary>
    <div class="hint">文件草稿——核实后确认：</div>
    ${REX_DRAFTS.slice(0, 30).map(d => `<div class="syl-item"><span class="learn-chip pending">×${d.freq}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter)}</div></span></div>`).join("")}
    <div class="btns" style="margin-top:8px"><button class="act" onclick="rexConfirmAll()">确认全部入库</button></div></details>`
    : `<div class="hint">文件解析命中 0 条。</div>`;
  input.value = "";
}
async function rexReport() {
  const qs = rexSubject() ? "?subject=" + encodeURIComponent(rexSubject()) : "";
  const r = await api("/api/library/realexams/report" + qs);
  downloadText("真题高频考点_" + (rexSubject() || "全部") + ".md", r.markdown);
}

/* ---- ⑧ 一键刷薄弱组卷（WP-03） ---- */
async function gapPaper() {
  if (!FEATURES.gap) { toast("该功能已在服务端禁用", false); return; }
  const subject = document.getElementById("dash_subject").value || "";
  if (!subject) { toast("请先在上方选择科目范围", false); return; }
  try {
    const r = await api("/api/library/gap-paper", { method: "POST",
      body: JSON.stringify({ subject, question_count: 50, w_freq: 15 }) });
    if (!r.ok) { toast(r.msg || "无法组卷", false); return; }
    const est = r.est ? `预计消耗约 ${(r.est.total_tokens / 10000).toFixed(1)} 万 token`
      + (r.est.cny ? ` · 约 ¥${r.est.cny.toFixed(2)}（参考价，以官网为准）` : "") : "";
    confirmModal("薄弱组卷就绪",
      `<div class="hint">科目：${esc(subject)} · 共 ${r.plan.total} 题<br>薄弱点：${esc((r.plan.weak_top || []).join("、"))}</div>` +
      `<div class="hint" style="margin-top:8px">${est}</div>` +
      (r.reused ? '<div class="hint" style="margin-top:6px">已复用现有薄弱组卷项目（不重复创建）。</div>' : ""),
      r.reused ? "去查看" : "开始生成", () => {
        if (r.reused) { showTab("mine"); return; }
        api("/api/projects/" + r.pid + "/run", { method: "POST" })
          .then(() => { toast("已开始生成「薄弱点专项」卷；进度见下方轮询"); showTab("mine"); })
          .catch(e => toast(e.message, false));
      }, false);
  } catch (e) { toast(e.message, false); }
}

/* 学习中心基础数据缓存：subjects/mastery 30s 内复用，避免多次「加载中」闪烁 */
let LEARN_CACHE = { subjects: null, mastery: null, t: 0 };
const LEARN_TTL = 30000;
async function cachedSubjects() {
  if (LEARN_CACHE.subjects && Date.now() - LEARN_CACHE.t < LEARN_TTL) return LEARN_CACHE.subjects;
  const r = await api("/api/library/subjects");
  LEARN_CACHE.subjects = r; LEARN_CACHE.t = Date.now();
  return r;
}
async function cachedMastery() {
  if (LEARN_CACHE.mastery && Date.now() - LEARN_CACHE.t < LEARN_TTL) return LEARN_CACHE.mastery;
  const r = await api("/api/library/mastery");
  LEARN_CACHE.mastery = r; LEARN_CACHE.t = Date.now();
  return r;
}
function invalidateLearnCache() { LEARN_CACHE.subjects = null; LEARN_CACHE.mastery = null; LEARN_CACHE.t = 0; }

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
          <button class="mini-btn primary" style="padding:2px 8px" onclick="learnRecAction('explain','${esc(k.subject || "")}','${esc(k.name)}')">讲解</button>
          <button class="mini-btn" style="padding:2px 8px" onclick="learnRecAction('tutor','${esc(k.subject || "")}','${esc(k.name)}')">提问</button>
        </span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:5px">
        <div class="stack-bar"><i class="ok" style="width:${cs}%"></i><i class="miss" style="width:${ms}%"></i></div>
        <span class="hint" style="font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums">对 ${c}/${tot}</span>
        <button class="mini-btn" style="padding:2px 8px;margin-left:auto" onclick="learnRecAction('queue','${esc(k.subject || "")}','${esc(k.name)}')">铺卡</button>
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
async function loadDashboard(keepSubject) {
  try {
    const subs = (await cachedSubjects()).subjects || [];
    $("dash_subject").innerHTML = '<option value="">全部科目</option>' +
      subs.slice().sort().map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    if (keepSubject && subs.includes(keepSubject)) $("dash_subject").value = keepSubject;
    const subject = $("dash_subject").value;
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
  $("dash_loop").innerHTML = dBanner + '<div class="loop-flow">' + stages.map(([k, v, cls], i) =>
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
}

async function loadLibrary() {
  invalidateLearnCache();   // 学习中心刷新 = 全量刷新（子视图内交互走 30s 缓存）
  try {
    const [mk, my, recm] = await Promise.all([
      api("/api/library/mistakes"),
      cachedMastery(),
      api("/api/library/recommend?limit=6"),
    ]);
    renderLibrary(mk.mistakes, my, recm.recommend);
    loadDashboard();                             // 学习闭环总览
    loadExplainCtx(appliedSubject());          // M3：同步刷新科目 / 知识点 / 讲解产物
    loadTutorCtx();                            // M4：同步刷新提问式学习的科目 / 知识点 / 会话
    loadReviewCtx(appliedSubject());           // M5：同步刷新复习计划（SM-2 间隔重复）
  } catch (e) {
    $("learn_kp").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
function appliedSubject() {
  const el = $("exp_subject");
  return el && el.value ? el.value : "";
}
function renderLibrary(mistakes, my, recm) {
  const stats = my.stats || {};
  $("learn_stats").innerHTML = `共 <b>${stats.total_knowledge || 0}</b> 个知识点 · 薄弱 <b style="color:#f87171">${stats.weak || 0}</b> · 错题 <b>${stats.total_mistakes || 0}</b>`;
  // 薄弱点诊断 + 待学优先级
  const kps = my.knowledge || [];
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
        <button class="mini-btn primary" onclick="learnRecAction('explain','${esc(r.subject || "")}','${esc(r.name)}')">→ 讲解</button>
        <button class="mini-btn" onclick="learnRecAction('tutor','${esc(r.subject || "")}','${esc(r.name)}')">→ 提问</button>
        <button class="mini-btn" onclick="learnRecAction('queue','${esc(r.subject || "")}','${esc(r.name)}')">铺卡</button>
      </span>
    </div>`).join("") : `<div class="hint">暂无薄弱点，先把错题收进来。</div>`;

  // 错题本（增强版：→讲解 / →提问 / 详情展开 / 已掌握 / 删除）
  $("learn_mk_count").textContent = `${mistakes.length} 道`;
  if (!mistakes.length) {
    $("learn_mk").innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
      <div class="sub">错题本还是空的<br>粘贴一道错题入库，或点「拍题(图片 OCR)」「批量导入(JSON)」</div>
    </div>`;
  } else {
    $("learn_mk").innerHTML = mistakes.map(mm => mkRowHTML(mm)).join("");
  }
}
const ERR_LABEL = { concept_gap: "概念缺失", confusion: "易混", calculation: "计算失误", misread: "误读题干", reasoning: "推理断链" };
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
    ${mm.image_ref && mm.source_ref && mm.source_ref.pid ? `<div><b>图</b>：<img src="/api/projects/${esc(mm.source_ref.pid)}/assets/${esc(mm.image_ref)}" style="max-width:320px;max-height:240px;border-radius:8px;border:1px solid var(--line);display:block;margin:6px 0" onerror="this.remove()"></div>` : ""}
    ${(mm.options || []).length ? `<div><b>选项</b>：${mm.options.map((o, i) => `${"ABCDEF"[i] || i + 1}. ${esc(o)}`).join("　")}</div>` : ""}
    ${mm.answer ? `<div class="ans">✓ 答案：${esc(mm.answer)}</div>` : ""}
    ${mm.user_answer ? `<div><b>我的作答</b>：${esc(mm.user_answer)}</div>` : ""}
    ${mm.analysis ? `<div><b>解析</b>：${esc(mm.analysis)}</div>` : ""}`;
  const kp = (mm.know_tags || [])[0] || mm.topic || "";
  return `<div class="mk-row">
    <div class="mk-main">
      <div class="mk-q" onclick="mkDetailTgl('${esc(mm.id)}')" title="点击展开详情">${esc(mm.question || "(无题干)")}</div>
      <div class="mk-meta">${meta.join("")}</div>
      <div class="mk-detail" id="mkd_${esc(mm.id)}">${detail || '<div class="hint">（无更多详情）</div>'}</div>
    </div>
    <div class="mk-actions">
      ${kp ? `<button class="mini-btn primary" onclick="learnRecAction('explain','${esc(mm.subject || "")}','${esc(kp)}')">→ 讲解</button>
      <button class="mini-btn" onclick="learnRecAction('tutor','${esc(mm.subject || "")}','${esc(kp)}')">→ 提问</button>` : ""}
      ${mm.learned ? "" : `<button class="act" style="padding:5px 11px;font-size:12px" onclick="mkLearn('${esc(mm.id)}',true)">已掌握</button>`}
      <button class="act gray" style="padding:5px 11px;font-size:12px;color:#f87171" onclick="mkDel('${esc(mm.id)}')">删除</button>
    </div>
  </div>`;
}
function mkDetailTgl(id) {
  const d = $("mkd_" + id);
  if (d) d.classList.toggle("open");
}
/* 学习中心推荐/错题行动作：讲解 / 提问 / 铺卡（复用既有流程，先定位到对应视图） */
async function learnRecAction(kind, subject, kpName) {
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
      await loadTutorCtx();
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
async function mkLearn(id, learned) {
  try {
    await api(`/api/library/mistakes/${id}/learn`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ learned: learned }) });
    toast("已计入掌握度"); loadLibrary();
  } catch (e) { toast(e.message, false); }
}
async function mkDel(id) {
  confirmModal("删除错题", `<p style="margin:0;color:var(--dim)">确定删除这道错题吗？对应知识点掌握度会随之刷新。</p>`, "删除", async () => {
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
function mkBatchPick() { $("mk_json").click(); }
async function mkBatchFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const name = (f.name || "").toLowerCase();
  const ext = name.includes(".") ? name.split(".").pop() : "txt";
  try {
    if (ext === "json") {
      // JSON 历史兼容：直接数组提交（结构与批量接口一致），失败时回退 import-file 表单
      let rows = JSON.parse(await f.text());
      if (!Array.isArray(rows)) throw new Error("需为 JSON 数组");
      const added = await api("/api/library/mistakes/batch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rows) });
      toast(`已批量入库 ${added.added || 0} 道`);
      loadLibrary();
    } else {
      // csv / md / txt：统一走本地解析导入（多字段/多格式归一化）
      const fd = new FormData();
      fd.append("file", f);
      const r = await api("/api/library/mistakes/import-file", { method: "POST", body: fd });
      toast(`已导入 ${r.added} 道（解析 ${r.total}，跳过 ${r.skipped}）`);
      loadLibrary();
    }
  } catch (e) { toast(`批量导入失败：${e.message}`, false); }
  finally { input.value = ""; }
}
window.mkLearn = mkLearn; window.mkDel = mkDel; window.mkOcrPick = mkOcrPick;
window.mkBatchPick = mkBatchPick;

/* ---- M3：讲解与学习产物（教材切片 + 联网补充 + 产物管理） ---- */
const LEARN_STATE_ORDER = { weak: 0, shaky: 1, solid: 2, mastered: 3 };
function expMd(md) {
  const raw = String(md || "");
  const inline = t => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>");
  let html = "", inList = false, inQuote = false;
  for (const rawLine of raw.split("\n")) {
    const line = rawLine.trim();
    if (!line) { if (inList) { html += "</ul>"; inList = false; } if (inQuote) { html += "</blockquote>"; inQuote = false; } continue; }
    if (line.startsWith("### ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h4>${inline(line.slice(4))}</h4>`; continue; }
    if (line.startsWith("## ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h3>${inline(line.slice(3))}</h3>`; continue; }
    if (line.startsWith("# ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h2>${inline(line.slice(2))}</h2>`; continue; }
    if (/^[-*·] /.test(line)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(line.slice(2))}</li>`; continue; }
    if (line.startsWith("> ")) { if (!inQuote) { html += "<blockquote>"; inQuote = true; } html += `<p>${inline(line.slice(2))}</p>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (inQuote) { html += "</blockquote>"; inQuote = false; }
    html += `<p>${inline(line)}</p>`;
  }
  if (inList) html += "</ul>";
  if (inQuote) html += "</blockquote>";
  return html;
}
async function loadExplainCtx(preserveSubject) {
  try {
    const [subj, my] = await Promise.all([cachedSubjects(), cachedMastery()]);
    const subs = (subj.subjects || []).sort();
    $("exp_subject").innerHTML = '<option value="">全部科目</option>' +
      subs.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    const keep = preserveSubject && subs.includes(preserveSubject) ? preserveSubject : "";
    if (keep) $("exp_subject").value = keep;
    const kps = (my.knowledge || []).slice().sort((a, b) => {
      const d = (LEARN_STATE_ORDER[b.state ?? "mastered"] ?? 4) - (LEARN_STATE_ORDER[a.state ?? "mastered"] ?? 4);
      return d || (a.score || 0) - (b.score || 0);
    });
    fillExpKp(kps, keep || $("exp_subject").value);
    loadExplains();
  } catch (e) { loadExplains(); }
}
function fillExpKp(kps, subject) {
  const list = subject ? kps.filter(k => !k.subject || k.subject === subject || k.subject === "未分类") : kps;
  $("exp_kp").innerHTML = '<option value="">— 选择薄弱知识点 —</option>' +
    list.map(k =>
      `<option value="${esc(k.name)}" data-id="${esc(k.id || "")}" data-subject="${esc(k.subject || "")}">` +
      `${esc(k.name)} · ${learnChip(k.state)}</option>`).join("");
}
async function loadExplains() {
  const subject = $("exp_subject").value;
  try {
    const r = await api("/api/library/explains?subject=" + encodeURIComponent(subject));
    const recs = r.explains || [];
    $("explain_total").textContent = `${subject || "全部"} · ${recs.length} 篇`;
    if (!recs.length) {
      $("explain_list").innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-book"></use></svg>
        <div class="sub">还没有讲解产物<br>上方选中薄弱知识点 →「生成讲解」，内容自动沉淀为个人复习手册</div>
      </div>`;
      return;
    }
    $("explain_list").innerHTML = recs.map((e, i) => `
      <div class="exp-card" id="expc_${esc(e.id)}">
        <div class="exp-fold" onclick="expFold('${esc(e.id)}')" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <b style="flex:1">${esc(e.kp_name || "")}</b>
          <span class="tag">${esc(e.subject || "未分类")}</span>
          ${e.via_web ? `<span class="tag" style="color:var(--good)">含web补充</span>` : `<span class="tag">纯教材</span>`}
          <span class="hint">${esc((e.created_at || "").slice(0, 16).replace("T", " "))}</span>
          <span class="hint">${(e.sources || []).length} 来源</span>
          <span class="mini-btn">展开</span>
        </div>
        <div class="exp-article">${expMd(e.content || "")}</div>
        ${e.sources && e.sources.length ? `<details style="margin-top:8px"><summary style="font-size:11.5px">来源（${e.sources.length}）——点击查看</summary>
          <div class="hint" style="margin-top:6px;font-size:11.5px;line-height:1.9">${e.sources.map(s => (s.kind === "web" ? "🌐" : "📖") + " " + esc(s.title || s.url || "")).join("<br>")}</div></details>` : ""}
        <div class="btns" style="margin-top:10px">
          ${e.kp_name ? `<button class="mini-btn" onclick="learnRecAction('tutor','${esc(e.subject || "")}','${esc(e.kp_name)}')">→ 提问练习</button>` : ""}
          <button class="mini-btn primary" onclick="expRegen('${esc(e.id)}','${esc(e.subject || "")}','${esc(e.kp_name || "")}')">↻ 重新生成</button>
          <button class="mini-btn" onclick="expCopy('${esc(e.id)}',this)">复制</button>
          <button class="mini-btn danger" onclick="expDel('${esc(e.id)}')">删除</button>
        </div>
      </div>`).join("");
  } catch (e) { $("explain_list").innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`; }
}
/* 触发 LLM 前的成本提示（估算；以官网为准）——讲解/提问按次记账，明明白白 */
function estLlmCost(inWan, outWan) {
  const prov = (state.providers || []).find(p => p.id === state.provider);
  const price = prov && prov.price;
  let cny = null;
  if (price) cny = inWan * 1e4 / 1e6 * (price.input || 0) + outWan * 1e4 / 1e6 * (price.output || 0);
  return `预计 ≈ ${(inWan + outWan).toFixed(1)} 万 token`
    + (cny != null ? ` · 约 ¥${cny.toFixed(2)}` : "")
    + `（${prov ? prov.name : "当前服务商"} 参考价，以官网为准）`;
}
async function expGenerate() {
  const opt = $("exp_kp").selectedOptions[0];
  if (!opt || !opt.value) { toast("请先选择待讲解的知识点", false); return; }
  const subject = $("exp_subject").value || opt.dataset.subject || "";
  const btn = $("btn_exp_gen"); const old = btn.textContent;
  btn.textContent = "讲解中…"; btn.disabled = true;
  const useWeb = $("exp_web").checked;
  $("exp_cost").textContent = (useWeb ? "正在检索教材切片（不足时联网补充）并精讲，请稍候… " : "正在结合教材切片精讲，请稍候… ") + "｜ " + estLlmCost(useWeb ? 2.2 : 1.4, 0.35);
  try {
    const r = await api("/api/library/explain", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, kp_name: opt.value, kp_id: opt.dataset.id || "", use_web: useWeb }) });
    toast("讲解已生成并沉淀到复习手册");
    const v = r.explain && r.explain.via_web ? "含联网补充" : "基于教材切片";
    $("exp_cost").textContent = `已生成：《${r.title}》· ${v}`;
    loadExplains();
  } catch (e) { toast(e.message, false); $("exp_cost").textContent = ""; }
  finally { btn.textContent = old; btn.disabled = false; }
}
async function expExport() {
  const subject = $("exp_subject").value;
  try {
    const r = await api("/api/library/explains/export?subject=" + encodeURIComponent(subject), { method: "POST" });
    downloadText((subject || "全部科目") + "-复习手册.md", r.markdown);
    toast("复习手册已导出");
  } catch (e) { toast(e.message, false); }
}
async function expCopy(id, btn) {
  try { const r = await api("/api/library/explains/" + id); copyText(r.explain.content || "", btn || null);
        if (!btn) toast("已复制讲解全文"); }
  catch (e) { toast(e.message, false); }
}
/* 折叠/展开讲解产物全文 */
function expFold(id) {
  const c = $("expc_" + id);
  if (!c) return;
  const open = c.classList.toggle("open");
  const lbl = c.querySelector(".exp-fold .mini-btn");
  if (lbl) lbl.textContent = open ? "收起" : "展开";
}
async function expRegen(id, subject, kpName) {
  try {
    await api("/api/library/explains/" + id, { method: "DELETE" });
    await learnRecAction("explain", subject, kpName);
  } catch (e) { toast(e.message, false); loadExplains(); }
}
window.expFold = expFold; window.expRegen = expRegen;
async function expDel(id) {
  confirmModal("删除讲解产物", `<p style="margin:0;color:var(--dim)">确定删除这篇讲解吗？删除后不可恢复。</p>`, "删除", async () => {
    try { await api("/api/library/explains/" + id, { method: "DELETE" }); toast("已删除"); loadExplains(); }
    catch (e) { toast(e.message, false); }
  });
}
function downloadText(name, content) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 500);
}
window.expGenerate = expGenerate; window.expExport = expExport;
window.expDel = expDel; window.expCopy = expCopy; window.loadExplains = loadExplains;

/* ---- M4：提问式学习（Socratic MedTutor 对话） ---- */
const TUTOR_STATES = [
  { key: "weak",     label: "薄弱" },
  { key: "shaky",    label: "不稳" },
  { key: "solid",    label: "扎实" },
  { key: "mastered", label: "掌握" },
];
const TUTOR_QTYPES = {
  explain: "解释", apply: "应用", contrast: "对比",
  predict: "预测", trace: "追溯",
};
let tutorState = { sessions: [], active: null, busy: false };
function tutorChip(state) { return learnChip(state); }
function tutorStatePath(state) {
  const idx = TUTOR_STATES.findIndex(s => s.key === state);
  const cur = (idx < 0 ? 0 : idx);
  const color = TUTOR_STATES[cur].key === "weak" ? "#f87171"
    : TUTOR_STATES[cur].key === "shaky" ? "#fbbf24"
    : TUTOR_STATES[cur].key === "solid" ? "#34d399" : "var(--good)";
  return {
    segs: TUTOR_STATES.map((s, i) => `<i class="${i <= cur ? "on" : ""}"></i>`).join(""),
    color, state: TUTOR_STATES[cur].label,
  };
}
function tutorRowCore(extra = "") {
  const subj = $("tu_subject").value;
  const rec = tutorState.sessions.find(s => s.id === tutorState.active) || null;
  const lv = TUTOR_QTYPES[rec && rec.current && rec.current.type] || "解释";
  const chip = rec ? tutorChip(rec.state) : "";
  return { subj, rec, lv, chip };
}
async function loadTutorCtx() {
  try {
    const [subj, my] = await Promise.all([cachedSubjects(), cachedMastery()]);
    const subs = (subj.subjects || []).sort();
    $("tu_subject").innerHTML = '<option value="">全部科目</option>' +
      subs.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    await fillTutorKp($("tu_subject").value);
  } catch (e) { /* 局部失败不阻塞 */ }
  loadTutorSessions();
}
function fillTutorKp(subject) {
  cachedMastery().then(my => {
    const kps = (my.knowledge || []).slice().sort((a, b) => {
      const d = (LEARN_STATE_ORDER[b.state ?? "mastered"] ?? 4) - (LEARN_STATE_ORDER[a.state ?? "mastered"] ?? 4);
      return d || (a.score || 0) - (b.score || 0);
    });
    const list = subject ? kps.filter(k => !k.subject || k.subject === subject || k.subject === "未分类") : kps;
    $("tu_kp").innerHTML = '<option value="">— 选择知识点 —</option>' +
      list.map(k =>
        `<option value="${esc(k.name)}" data-subject="${esc(k.subject || "")}">` +
        `${esc(k.name)} · ${learnChip(k.state)}</option>`).join("");
  }).catch(() => { $("tu_kp").innerHTML = '<option value="">— 选择知识点 —</option>'; });
}
async function loadTutorSessions() {
  const subject = $("tu_subject").value;
  try {
    const r = await api("/api/library/tutor/sessions?subject=" + encodeURIComponent(subject));
    tutorState.sessions = r.sessions || [];
    renderTutorSide();
  } catch (e) { renderTutorSide(e.message); }
}
function renderTutorSide(err) {
  const list = tutorState.sessions;
  $("tutor_total").textContent = `${list.length} 场会话`;
  if (tutorState.active) { tutorShowConversation(); return; }
  $("tutor_cost").textContent = "";
  const box = $("tutor_body");
  if (err) { box.innerHTML = `<div class="hint">会话加载失败：${esc(err)}</div>`; return; }
  if (!list.length) {
    box.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-learn"></use></svg>
      <div class="sub">还没有提问会话<br>上方选中薄弱知识点 →「开始提问」，与 MedTutor 展开一场引导式对话</div>
    </div>`;
    return;
  }
  box.innerHTML = `<div class="tu-wrap"><div class="tu-side">${list.map(sessionItem).join("")}</div>
    <div class="hint" style="padding:24px 8px">左侧选一场会话继续，或上方「开始提问」开启新对话。</div></div>`;
  $("btn_tu_exit").style.display = "none";
}
function sessionItem(s) {
  const lv = TUTOR_QTYPES[s.current && s.current.type] || "解释";
  const at = (s.updated_at || "").slice(5, 16).replace("T", " ");
  return `<div class="tu-item" onclick="tutorResume('${esc(s.id)}')">
    <div class="ti-name">${esc(s.kp_name || "未命名知识点")}</div>
    <div class="ti-meta">${esc(s.subject || "未知科目")} · ${s.rounds.length} 轮 · ${tutorChip(s.state)}
      <span style="margin-left:auto">${esc(at)}</span>
      <button class="ti-x" title="删除会话" onclick="event.stopPropagation();tutorDel('${esc(s.id)}')">×</button>
    </div></div>`;
}
function conversationHTML(s) {
  const path = tutorStatePath(s.state);
  const rounds = (s.rounds || []).map(r => `
    <div class="tu-q"><span class="tu-badge">MedTutor · ${TUTOR_QTYPES[r.type] || r.type}提问 · 第${r.round}轮</span>${esc(r.question)}</div>
    <div class="tu-a"><small>你 · 得分 <span class="tu-score" style="color:${r.score >= 2 ? "var(--good)" : "var(--bad)"}">${r.score}</span>/3</small>${esc(r.user_answer)}</div>
    ${r.gap ? `<div class="tu-gap">${esc(r.gap) || ""}</div>` : ""}`).join("");
  let bottom;
  const cur = s.current || { type: "explain", text: "" };
  if (cur.text) {
    bottom = `<div class="tu-q tu-next"><span class="tu-badge">MedTutor · ${TUTOR_QTYPES[cur.type] || cur.type}提问</span>${esc(cur.text)}</div>
      <div class="tu-inputbar">
        <textarea id="tu_answer" placeholder="在文本框里作答…（写不下可先答要点，MedTutor 会追问细节）"></textarea>
        <button class="act" onclick="tutorSubmit()">提交作答</button>
      </div>`;
  } else {
    bottom = `<div class="tu-gap">本轮已达成掌握目标，可以「开始提问」开辟新一轮，或换一个知识点。</div>`;
  }
  return `<div class="tu-wrap">
    <div class="tu-side">${tutorState.sessions.map(sessionItem).join("")}</div>
    <div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">
        <b style="flex:1">${esc(s.kp_name || "")}</b>
        <span class="tag">${esc(s.subject || "未分类")}</span>
        <span class="hint">${esc((s.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
      <div class="tu-state" style="color:${path.color}">${path.segs}</div>
      <div class="tu-legend">
        ${TUTOR_STATES.map(x => `<span class="s-${x.key}">${x.label}</span>`).join("")}
        <b style="margin-left:auto">当前：${path.state}</b>
      </div>
      <div class="hint" style="margin-top:4px">连续答对（得分≥2）两次推动概念状态晋升一档；答偏会同类追问细化。</div>
      <div class="tu-bubbles" id="tu_bubbles" style="margin-top:10px">${rounds}${bottom}</div>
    </div>
  </div>`;
}
function tutorShowConversation() {
  const s = tutorState.sessions.find(x => x.id === tutorState.active);
  if (!s) { renderTutorSide(); return; }
  $("tutor_body").innerHTML = conversationHTML(s);
  $("btn_tu_exit").style.display = "";
  $("btn_tu_start").textContent = "另开一场";
  const bb = $("tu_bubbles"); if (bb) bb.scrollTop = bb.scrollHeight;
}
async function tutorStart() {
  const opt = $("tu_kp").selectedOptions[0];
  if (!opt || !opt.value) { toast("请先选择待学习的知识点", false); return; }
  const subject = $("tu_subject").value || opt.dataset.subject || "";
  const btn = $("btn_tu_start"); const old = btn.textContent;
  btn.textContent = "出题中…"; btn.disabled = true; tutorState.busy = true;
  $("tutor_cost").textContent = "正在结合教材切片生成第一问（按次记账）… ｜ " + estLlmCost(0.8, 0.05);
  try {
    const r = await api("/api/library/tutor/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, kp_name: opt.value }) });
    tutorState.active = r.session.id;
    await loadTutorSessions();
    toast("已开启一场苏格拉底式对话");
    $("tutor_cost").textContent = "第一问已就绪，请作答。";
    tutorShowConversation();
  } catch (e) { toast(e.message, false); $("tutor_cost").textContent = ""; }
  finally { btn.textContent = old; btn.disabled = false; tutorState.busy = false; }
}
async function tutorSubmit() {
  const ta = $("tu_answer"); const text = (ta && ta.value.trim()) || "";
  if (!tutorState.active) return;
  if (!text && !tutorState._confirmed) {
    confirmModal("提交空作答", `<p style="margin:0;color:var(--dim)">当前没有作答内容，是否只提交「不会答」（MedTutor 会据此调整追问）？</p>`,
      "提交", async () => { tutorState._confirmed = true; tutorSubmit(); });
    return;
  }
  tutorState._confirmed = false;
  const btn = document.querySelector("#tutor_body .act");
  const old = btn ? btn.textContent : ""; if (btn) { btn.textContent = "判分中…"; btn.disabled = true; }
  $("tutor_cost").textContent = "MedTutor 正在判分并准备下一问…";
  try {
    const r = await api("/api/library/tutor/answer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: tutorState.active, user_answer: text }) });
    const cur = tutorState.sessions.find(x => x.id === tutorState.active);
    if (cur) { cur.state = r.session.state; cur.streak = r.session.streak;
      cur.rounds = r.session.rounds; cur.current = r.session.current;
      cur.updated_at = r.session.updated_at; }
    $("tutor_cost").textContent = `本轮得分 ${r.score}/3` + (r.gap ? ` —— ${r.gap}` : "");
    tutorShowConversation();
  } catch (e) { toast(e.message, false); $("tutor_cost").textContent = ""; }
  finally { if (btn) { btn.textContent = old; btn.disabled = false; } }
}
async function tutorResume(id) {
  try {
    const r = await api("/api/library/tutor/" + id);
    const s = r.session;
    const i = tutorState.sessions.findIndex(x => x.id === id);
    if (i >= 0) tutorState.sessions[i] = s; else tutorState.sessions.unshift(s);
    tutorState.active = id;
    $("tutor_cost").textContent = "";
    tutorShowConversation();
  } catch (e) { toast(e.message, false); }
}
function tutorExit() { tutorState.active = null; $("btn_tu_start").textContent = "开始提问"; renderTutorSide(); }
function tutorDel(id) {
  confirmModal("删除提问会话", `<p style="margin:0;color:var(--dim)">确定删除这场提问会话？问答记录将被清空，知识点掌握度不受影响。</p>`, "删除", async () => {
    try {
      await api("/api/library/tutor/" + id, { method: "DELETE" });
      tutorState.sessions = tutorState.sessions.filter(s => s.id !== id);
      if (tutorState.active === id) tutorExit();
      else renderTutorSide();
      toast("会话已删除");
    } catch (e) { toast(e.message, false); }
  });
}
window.fillTutorKp = fillTutorKp; window.tutorStart = tutorStart; window.tutorSubmit = tutorSubmit;
window.tutorResume = tutorResume; window.tutorExit = tutorExit; window.tutorDel = tutorDel;

/* ---- M5：复习计划（SM-2 间隔重复）---- */
const RVC_STATES = {
  new: { t: "新卡", c: "var(--info)" },
  learning: { t: "学习中", c: "#fbbf24" },
  review: { t: "复习", c: "#34d399" },
  relearning: { t: "重学", c: "#f87171" },
};
let rvSubject = "";
function rvChip(state) {
  const s = RVC_STATES[state] || { t: state || "未知", c: "var(--dim)" };
  const border = `color-mix(in srgb, ${s.c} 20%, transparent)`;
  const bg = `color-mix(in srgb, ${s.c} 8%, transparent)`;
  return `<span class="learn-chip" style="color:${s.c};border-color:${border};background:${bg}">${esc(s.t)}</span>`;
}
async function loadReviewCtx(subject = "") {
  rvSubject = subject;
  try {
    const [today, subs] = await Promise.all([
      api("/api/library/review/today?subject=" + encodeURIComponent(subject)),
      cachedSubjects(),
    ]);
    fillReviewSubjects(subs);
    renderReview(today);
  } catch (e) { $("rv_body").innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
function fillReviewSubjects(resp) {
  const sel = $("rv_subject");
  if (!sel || sel.dataset.inited) return;
  const subs = resp.subjects || [];
  sel.innerHTML = `<option value="">全部科目</option>` + subs.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  sel.dataset.inited = "1";
  sel.value = rvSubject;
}
function renderReview(today) {
  const st = today.stats || {};
  $("rv_total").textContent = `今日到期 ${st.due || 0} · 总卡 ${st.total || 0}`;
  const strip = [
    ["今日到期", st.due || 0, "#f87171"],
    ["总卡片", st.total || 0, "var(--info)"],
    ["进行中", st.in_progress || 0, "#fbbf24"],
    ["新卡", st.new || 0, "#34d399"],
  ];
  $("rv_stats").innerHTML = strip.map(([k, v, c]) =>
    `<div class="rv-stat"><b style="color:${c}">${v}</b><span>${k}</span></div>`).join("");
  const due = today.cards || [];
  if (!due.length) {
    $("rv_body").innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-refresh"></use></svg>
      <div class="sub">今天没有到期卡片<br>点「铺卡（薄弱点全部入队）」把薄弱知识点铺进队列，复习后按 SM-2 自动排入下次日期</div>
    </div>`;
    return;
  }
  $("rv_body").innerHTML = due.map(rvCard).join("");
}
function rvCard(c) {
  const meta = `间隔 ${c.interval || 0} 天 · 难度 ${(c.ease || 2.5).toFixed(2)} · 背 ${c.reps || 0} 次 · 忘 ${c.lapses || 0} 次`;
  const grades = [0, 1, 2, 3, 4, 5].map(q =>
    `<button class="rv-g${q}" onclick="rvGrade('${esc(c.id)}',${q})" title="质量 ${q}/5 分">${q}</button>`).join("");
  return `<div class="rv-card">
    <div class="rv-top">${rvChip(c.state)}<span class="hint" style="font-size:11px">${esc(c.subject || "未分类")}</span>
      <button class="rv-x" title="移出复习队列" onclick="rvDel('${esc(c.id)}')">×</button></div>
    <div class="rv-q">${esc(c.kp_name || "(未命名知识点)")}</div>
    <div class="rv-meta">${esc(meta)}</div>
    <details class="rv-hint" ontoggle="rvHint(this,'${esc(c.kp_name || "")}','${esc(c.subject || "")}')">
      <summary>📖 查看提示（教材原文 · 不消耗 AI）</summary>
      <div class="rv-hintbody"><span class="hint">展开后自动检索教材切片</span></div>
    </details>
    <div class="rv-grades"><span class="hint" style="font-size:11px">自评：</span>${grades}</div>
    <div class="rv-legend hint">0懵了 · 1很困难 · 2想岔 · 3勉强 · 4想起 · 5秒答</div>
  </div>`;
}
/* 复习卡「查看提示」：懒加载教材原文切片（零 LLM，纯本地检索） */
async function rvHint(det, kpName, subject) {
  if (!det || det.dataset.loaded === "1" || !det.open) return;
  det.dataset.loaded = "1";
  const body = det.querySelector(".rv-hintbody");
  body.innerHTML = '<span class="spin"></span><span class="hint">正在检索教材切片…</span>';
  try {
    const r = await api(`/api/library/explain/slices?subject=${encodeURIComponent(subject || "")}&query=${encodeURIComponent(kpName || "")}&limit=5`);
    const sl = r.slices || [];
    if (!sl.length) {
      body.innerHTML = `<div class="hint">教材中未检索到「${esc(kpName)}」相关内容 —— 可到「讲解与学习产物」用联网补充生成。</div>`;
      return;
    }
    body.innerHTML = sl.map(s =>
      `<div class="rv-slice"><b>${esc(s.title || s.sid || "切片")}</b>${esc((s.text || "").slice(0, 300))}${(s.text || "").length > 300 ? "…" : ""}</div>`).join("");
  } catch (e) {
    body.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
window.rvHint = rvHint;
async function rvGrade(cid, q) {
  try {
    await api("/api/library/review/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cid, quality: q }),
    });
    toast(`已记录 ${q}/5 分，卡片已按 SM-2 排入下次复习`);
    await Promise.all([loadReviewCtx(rvSubject), loadLibrary()]);
  } catch (e) { toast(e.message, false); }
}
async function rvQueueAll() {
  try {
    const r = await api("/api/library/review/queue-all?subject=" + encodeURIComponent(rvSubject));
    toast(r.added ? `已入队 ${r.added} 张薄弱卡片` : "没有新的薄弱知识点需要入队");
    loadReviewCtx(rvSubject);
  } catch (e) { toast(e.message, false); }
}
async function rvDel(cid) {
  try {
    await api("/api/library/review/" + cid, { method: "DELETE" });
    toast("已移出复习队列");
    loadReviewCtx(rvSubject);
  } catch (e) { toast(e.message, false); }
}
window.loadReviewCtx = loadReviewCtx; window.rvQueueAll = rvQueueAll; window.rvGrade = rvGrade; window.rvDel = rvDel;

window.ankiPreview = ankiPreview;
/* Anki 卡样预览：前 3 张卡正反面（直接复用项目题目数据，零后端改动） */
async function ankiPreview(pid) {
  try {
    const r = await api("/api/projects/" + encodeURIComponent(pid) + "/questions");
    const qs = (r.questions || []).slice(0, 3);
    if (!qs.length) { toast("项目中没有题目，先完成生成", false); return; }
    const LETTERS = "ABCDEF";
    const cards = qs.map(q => {
      const stem = q.case_stem ? `【案例】${esc(q.case_stem)}<br>` : "";
      const opts = (q.options || []).map((o, i) => `${LETTERS[i]}. ${esc(o)}`).join("<br>");
      return `<div class="ankiface"><div class="af-front">
        <div class="af-tags"><span class="tag">${esc(q.type)}</span><span class="tag">${esc(q.bloom)}</span>${q.case_id ? `<span class="tag">案例 ${esc(q.case_id)}</span>` : ""}${q.subtopic ? `<span class="tag">${esc(q.subtopic)}</span>` : ""}</div>
        ${stem}${esc(q.question)}${opts ? `<div class="af-opts">${opts}</div>` : ""}</div>
        <div class="af-back"><b>✅ 答案：${esc(q.answer)}</b><br>${esc(q.analysis)}</div></div>`;
    }).join("");
    $("md_title").textContent = "Anki 卡样预览（前 3 张）";
    $("md_body").innerHTML = `
      <div class="hint" style="margin:0 0 10px;line-height:1.8">导出后卡面如上：<b>正面 = 题型/Bloom 标签 + 题干 + 选项</b>，<b>反面 = 答案 + 解析</b>；Anki 标签 = 题型 / Bloom / 章节。共 ${r.questions.length} 题（此处展示前 3 张）。</div>
      <div class="ankifaces">${cards}</div>`;
    $("md_ok").textContent = "知道了";
    $("md_ok").className = "act";
    $("modal_mask").style.display = "flex";
    $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; };
  } catch (e) { toast(e.message, false); }
}
