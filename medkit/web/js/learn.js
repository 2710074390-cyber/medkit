/* exported HL_KEYWORDS, LEARN_ALT_KEYS, LEARN_CACHE, LEARN_STATE, LEARN_TTL, REX_DRAFTS, REX_STATS, SYL_DRAFTS, SYL_LOADED, SYL_STD, anchor, b, batch, body, box, btns, buf, c, cachedMastery, cachedSubjects, consumeSSE, cur, d, dec, est, event, f, fd, first, fold, gapPaper, hint, hlKw, i, inp, invalidateLearnCache, items, kps, label, learnChip, loop, lv, meta, nav, nb, ok, on, pct, pill, pv, qs, r, raw, reader, refreshOverviewIfAny, renderRexDrafts, rexAnalyze, rexConfirmAll, rexDraftClear, rexFile, rexFilePick, rexFileRender, rexHeat, rexItemDel, rexReport, rexSubject, s, sb, sel, setLearnNavBadge, setNavTabBadge, showLearnView, sr, sseAbort, sseAbortAll, sseAborts, sseStopUI, st, stats, stdName, subject, subs, sylDraftClear, sylDraftDel, sylEnsure, sylFail, sylItemDel, sylLoad, sylParse, sylParseConfirm, sylPaste, sylRender, sylReport, sylSeedImport, sylSeedPick, sylSetStd, sylStructurize, sylTeacherImport, sylTeacherPick, t, text, total, txt, updateLearnBadges, v */
/* U-17：跨文件 / 内联 HTML 处理器引用的顶层声明（经典脚本共享全局作用域）*/
  /* U-17：跨文件/内联 HTML 引用的顶层声明（经典脚本共享全局作用域）*/
/* ---- ④ 学习中心（v0.7 M1/M2：错题本 + 掌握度诊断） ---- */
/* R4-02 流式取消：在途 AbortController 按入口（anchorId）登记——A-16：讲解/提问可并发生成时
   互不误杀（旧实现单 _sseAbort + 全局单按钮，sseStopUI 先清上一处=先杀并发流；且声明前置
   （showLearnView 在脚本求值期即可能被 initLearnView 调用，置于声明行前会触发 let TDZ
   ReferenceError，中断整个学习中心脚本）——Map 为 const，无 TDZ 问题 */
const sseAborts = new Map();   // anchorId → AbortController（在途流式）
const LEARN_STATE = { weak: "待加强", shaky: "需复习", solid: "较熟练", mastered: "已掌握" };
function learnChip(state) {
  const txt = LEARN_STATE[state] || state || "未知";
  return `<span class="learn-chip ${esc(state || "")}">${txt}</span>`;
}
/* PRD 6.4.1：解析关键词高亮——医学解析高频关键词加粗标红（先 esc 再替换，安全无注入） */
const HL_KEYWORDS = ["首选药", "首选", "金标准", "确诊", "禁忌证", "禁忌症", "禁用", "一线", "特效药", "不良反应", "并发症", "鉴别诊断"];
function hlKw(text) {
  let s = esc(text);
  for (const k of HL_KEYWORDS) s = s.split(k).join(`<b class="kw">${k}</b>`);
  return s;
}
/* ---- 学习中心子导航：一屏一任务（概览/错题本/讲解产物/提问学习/复习计划） ---- */
function showLearnView(name) {
  sseAbortAll();   // R4-02：切换子视图/离开学习中心 → 中止在途流式，避免隐藏容器里白烧 token
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
/* IMP-12①：学习中心子导航 Alt+1..5 直达（v0.8.1：复习计划迁入「刷题」，5 视图） */
const LEARN_ALT_KEYS = ["overview", "mistakes", "explain", "tutor", "syllabus"];
window.addEventListener("keydown", e => {
  // A6：焦点在输入框/编辑器时不触发子视图快捷键（防打字时被切走/吞键）
  const t = e.target;
  // A-新15：焦点守卫纳入 SELECT（下拉聚焦时不触发子视图快捷键，防误切/吞键）
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (!e.altKey || e.ctrlKey || e.metaKey || !(e.key >= "1" && e.key <= "5")) return;
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
  if (v === "review") v = "overview";   // v0.8.1：复习计划已迁入「刷题」tab，旧记忆重定向概览
  const ok = ["overview", "mistakes", "explain", "tutor", "syllabus"].includes(v);
  if (ok) showLearnView(v);
})();
/* 侧栏待办徽章（v0.8.1 拆分）：刷题 tab = 今日到期复习；学习中心 tab = 进行中提问 */
function setNavTabBadge(tab, n, title) {
  const b = document.querySelector('button[data-tab="' + tab + '"]');
  if (!b) return;
  let d = b.querySelector(".navbadge");
  if (n > 0) {
    if (!d) { d = document.createElement("span"); d.className = "navbadge"; b.appendChild(d); }
    d.textContent = n > 99 ? "99+" : String(n);
    d.title = title || (`待办 ${n} 项 → 去「${tab === "study" ? "刷题" : "学习中心"}」`);
    d.setAttribute("aria-label", d.title);
    d.dataset.source = tab;
    d.style.cursor = "pointer";
  } else if (d) d.remove();
}
function setLearnNavBadge(n, detail) {
  /* 参数 n 保留兼容（旧调用点）；实际徽章按 detail 拆分 */
  setNavTabBadge("study", detail.due || 0, `今日到期复习 ${detail.due || 0} 张 → 去刷题`);
  setNavTabBadge("learn", detail.tutor || 0, `进行中提问 ${detail.tutor || 0} 场 → 去学习中心`);
}
/* 子导航计数徽章（闭环数据回填） */
function updateLearnBadges(d) {
  const loop = (d && d.loop) || {};
  const nb = (id, v, hot) => { const el = $(id); if (!el) return;
    el.textContent = v > 0 ? v : "";
    el.classList.toggle("hot", !!hot && v > 0);
    const label = { nb_overview: "概览", nb_mistakes: "错题本", nb_explain: "讲解产物",
                    nb_tutor: "提问学习", nb_review: "复习计划" }[id] || id;
    el.title = v > 0 ? `${label}：${v} 项（点击进入对应视图）` : "";
    el.setAttribute("aria-label", el.title || label);
  };
  nb("nb_overview", 0);
  nb("nb_mistakes", loop.mistakes || 0);                              // 资料库规模，非待办 → 不标红
  nb("nb_explain", loop.explains || 0);
  nb("nb_tutor", loop.tutor || 0);
  nb("nb_review", loop.review || 0, (d && d.review && d.review.due) > 0);
  const r = (d && d.review) || {};
  const t = (d && d.tutor) || {};
  // 真实待办（可立即执行）：今日到期复习 + 进行中提问会话；无待办 → 无红点
  setLearnNavBadge((r.due || 0) + (t.in_progress || 0),
                   { due: r.due || 0, tutor: t.in_progress || 0 });
}
/* ---- ⑥ 大纲管理（WP-01/WP-10 考试锚定 · 教师重点为主 · 官方306补充） ---- */
let SYL_DRAFTS = [];
let SYL_LOADED = false;
let SYL_STD = "teacher";   // 大纲标准二选一：teacher=教师重点(主要依据) / seed=官方306(补充，内置种子或上传导入)
/* C16：标准选择恢复（sylSetStd 写入 sessionStorage，此处首读；sylRender/首渲会用 SYL_STD） */
(function () {
  try {
    const s = sessionStorage.getItem("medkit-syl-std");
    if (s === "teacher" || s === "seed") SYL_STD = s;
  } catch (e) { /* ignore */ }
  // D-07：恢复后同步 pill 高亮（否则显示「教师重点」但实际按官方大纲口径计算覆盖）
  if (SYL_STD !== "teacher") {
    document.querySelectorAll("#syl_std .css-pill").forEach(b => {
      const on = b.dataset.std === SYL_STD;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
  }
})();
/* 大纲标准二选一：syl_std pill 仅 teacher / seed 两档（移除历史 all 档，见 AGENT_HANDOFF） */
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
    // R3-26：去掉 options.length<=1 守卫——每次与后端科目集对比增量并入新科目（保留原选中值）
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">全部科目</option>' + subs
        .map(s => `<option value="${esc(s.subject)}">${esc(s.subject)}（${s.items} 条目）</option>`).join("");
      if (cur && subs.some(s => s.subject === cur)) sel.value = cur;
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
  /* C16：标准选择持久化（刷新/重开保持） */
  try { sessionStorage.setItem("medkit-syl-std", std); } catch (e) { /* ignore */ }
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
      const stdName = { teacher: "教师重点", seed: "官方 306（补充）" }[SYL_STD] || SYL_STD;
      body.innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
        <div class="sub">暂无${stdName}条目 — ${SYL_STD === "teacher"
          ? '在「新建课题」上传教师重点后自动同步；或下方「上传教师重点文件」/粘贴教师重点考点清单。'
          : '点「导入官方306种子」加载教材/真题种子；或下方「上传官方大纲(md/txt)」一键导入官方条目。'}</div>
        <button class="act gray" onclick="sylPaste()">粘贴/导入大纲 →</button>
      </div>`;
      return;
    }
    body.innerHTML = d.chapters.map(ch => {
      const items = (ch.items || []).map(it => {
        const s = it.status === "mastered" ? ["已掌握", "mastered"] :
                  it.status === "covered" ? ["已覆盖", "solid"] : ["未覆盖", "pending"];
        return `<div class="syl-item"><span class="learn-chip ${s[1]}">${s[0]}</span>
          <span class="grow"><b>${esc(it.item)}</b>${it.matched ? `<div class="hint">已匹配：${esc(it.matched)}</div>` : ""}</span>
          ${it.id ? `<button class="rv-x" title="删除该条目（可重新导入）" data-id="${esc(it.id)}" onclick="sylItemDel(this)">×</button>` : ""}</div>`;
      }).join("");
      return `<div class="syl-chap">${esc(ch.chapter)} <span class="hint">（覆盖 ${ch.covered + ch.mastered}/${ch.total} · 未覆盖 ${ch.pending}）</span></div>` +
        (items || '<div class="hint" style="margin-left:10px">（无条目，待粘贴）</div>');
    }).join("");
  } catch (e) { sylFail(e.message); }
}
async function sylEnsure() {
  const r = await api("/api/syllabus/ensure", { method: "POST", body: JSON.stringify({ force: false }) });
  if (r.note && r.note.includes("未内置大纲")) {
    toast(r.note, false);
  } else if (!r.imported) {
    toast("内置大纲已导入过（幂等，无新增）");
  } else {
    toast(`大纲种子导入：新增 ${r.imported} 条（幂等）`);
  }
  sylLoad();
}
function sylPaste() {
  document.getElementById("syl_paste_card").style.display = "";
}
async function sylStructurize() {
  const text = document.getElementById("syl_paste_text").value;
  if (!text.trim()) { toast("请先粘贴大纲原文", false); return; }
  const subject = document.getElementById("syl_subject").value || "";
  try {
    const r = await api("/api/syllabus/outline/structurize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, subject }) });
    const d = r.diff || {};
    document.getElementById("syl_paste_preview").innerHTML =
      `<div class="hint">${esc(r.note || "")}</div>` +
      `<div class="syl-chips"><div class="syl-stat"><b>${r.stats?.subjects?.length || 0}</b><div class="hint">科目</div></div>` +
      `<div class="syl-stat"><b>${r.stats?.chapters || 0}</b><div class="hint">章</div></div>` +
      `<div class="syl-stat"><b>${d.structured_items || 0}</b><div class="hint">结构化条目</div></div>` +
      `<div class="syl-stat"><b>${d.missing || 0}</b><div class="hint">缺失</div></div></div>` +
      (r.original_path ? `<div class="hint" style="margin-top:6px">原文已保存：${esc(r.original_path)}</div>` : "");
  } catch (e) { toast(e.message, false); }
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
    SYL_DRAFTS.slice(0, 30).map((d, i) => `<div class="syl-item">
      <span class="learn-chip pending">${esc(d.subject || "?")}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span>
      <button class="rv-x" title="移除该草稿" onclick="sylDraftDel(${i})">×</button></div>`).join("") +
    (SYL_DRAFTS.length > 30 ? `<div class="hint">…共 ${SYL_DRAFTS.length} 条（全部入库）</div>` : "") +
    `<div class="btns" style="margin-top:8px"><button class="mini-btn" onclick="sylDraftClear()">取消草稿</button></div>`;
}
async function sylParseConfirm() {
  if (!SYL_DRAFTS || !SYL_DRAFTS.length) { toast("先解析再确认"); return; }
  const items = SYL_DRAFTS.map(d => ({ subject: d.subject || "未分类", chapter: d.chapter || "", item: d.item }));
  const r = await api("/api/syllabus/confirm", { method: "POST", body: JSON.stringify({ items }) });
  toast(`已入库 ${r.added} 条大纲条目`);
  SYL_DRAFTS = [];
  document.getElementById("syl_paste_preview").innerHTML = '<div class="hint">已入库。可继续粘贴或点顶部「刷新」。</div>';
  await sylLoad();   // 等待确认后的刷新完成，避免迟到渲染覆盖后续标准切换
}
function sylDraftDel(i) {
  SYL_DRAFTS.splice(i, 1);
  document.getElementById("syl_paste_preview").innerHTML = SYL_DRAFTS.length
    ? `<div class="hint">预览 ${SYL_DRAFTS.length} 条：</div>` + SYL_DRAFTS.slice(0, 30)
        .map((d, j) => `<div class="syl-item"><span class="learn-chip pending">${esc(d.subject || "?")}</span>
          <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span>
          <button class="rv-x" title="移除该草稿" onclick="sylDraftDel(${j})">×</button></div>`).join("")
    : '<div class="hint">草稿已清空。</div>';
}
function sylDraftClear() {
  SYL_DRAFTS = [];
  document.getElementById("syl_paste_preview").innerHTML = '<div class="hint">草稿已取消。</div>';
}
window.sylDraftDel = sylDraftDel; window.sylDraftClear = sylDraftClear;
async function sylItemDel(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  confirmModal("删除大纲条目", `<p style="margin:0;color:var(--dim)">确定删除该条目？删除后覆盖率将即时更新；误删可重新导入（种子/教师重点源）。</p>`, "删除", async () => {
    try {
      await api("/api/syllabus/items/" + encodeURIComponent(id), { method: "DELETE" });
      toast("已删除条目");
      await sylLoad();
    } catch (e) { toast(e.message, false); }
  });
}
window.sylItemDel = sylItemDel;
function sylSeedPick() {
  document.getElementById("syl_seed_file").click();
}
function sylTeacherPick() {
  document.getElementById("syl_teacher_file").click();
}
async function sylTeacherImport() {
  // 教师重点文件（PDF文本层/DOCX/MD/TXT）→ 草稿→确认两段式（R3-25）与粘贴同门槛：
  // preview=1 只解析不落库，渲染草稿列表（计数 N 条未入库），用户点「确认入库」才批量入库
  const inp = document.getElementById("syl_teacher_file");
  const f = inp.files && inp.files[0];
  inp.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  fd.append("preview", "1");
  const subject = document.getElementById("syl_subject")?.value || "";
  if (subject) fd.append("subject", subject);
  try {
    const r = await api("/api/syllabus/teacher/import-file", { method: "POST", body: fd });
    if (r.mode === "error") { toast(r.note || "文件解析失败", false); return; }
    if (!r.drafts || !r.drafts.length) { toast(r.note || "未识别到条目", false); return; }
    SYL_DRAFTS = r.drafts;
    // R3-25：展开草稿确认区（与粘贴导入同一预览面板）
    document.getElementById("syl_paste_card").style.display = "";
    const kps = (r.knowledge || []).slice(0, 10).map(k => esc(k.name)).join("、");
    document.getElementById("syl_paste_preview").innerHTML =
      `<div class="hint">教师重点草稿 ${SYL_DRAFTS.length} 条（未入库${r.subject ? ` · 科目：${esc(r.subject)}` : ""}）——核实后确认：</div>` +
      SYL_DRAFTS.slice(0, 30).map(d => `<div class="syl-item">
        <span class="learn-chip pending">${esc(d.subject || "?")}</span>
        <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span></div>`).join("") +
      (SYL_DRAFTS.length > 30 ? `<div class="hint">…共 ${SYL_DRAFTS.length} 条（确认全部入库）</div>` : "") +
      (kps ? `<div class="hint">知识点提取（前 10 条）：${kps}…（共 ${(r.knowledge || []).length} 条）</div>` : "") +
      `<div class="btns" style="margin-top:8px"><button class="act" onclick="sylParseConfirm()">确认全部入库</button>
       <button class="mini-btn" onclick="sylDraftClear()">取消草稿</button></div>`;
  } catch (e) {
    toast(e.message || "导入失败", false);
  }
  sylLoad();
}async function sylSeedImport() {
  // 官方 306 大纲文件（md/txt）→ LLM 契约抽取 → source='seed' 幂等入库（一键导入）
  const inp = document.getElementById("syl_seed_file");
  const f = inp.files && inp.files[0];
  inp.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/syllabus/seed/import-file", { method: "POST", body: fd });
    if (!r.drafts || !r.drafts.length) { toast(r.note || "未识别到条目", false); return; }
    toast(`${r.note || "官方 306 导入"} · 入库新增 ${r.added ?? "?"} 条（共 ${r.total ?? r.drafts.length} 条，幂等）`);
    SYL_DRAFTS = r.drafts;
    document.getElementById("syl_paste_preview").innerHTML =
      `<div class="hint">官方 306 草稿 ${SYL_DRAFTS.length} 条（已入库 source=seed）：</div>` +
      SYL_DRAFTS.slice(0, 30).map(d => `<div class="syl-item">
        <span class="learn-chip pending">${esc(d.subject || "?")}</span>
        <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span></div>`).join("");
  } catch (e) {
    toast(e.message || "导入失败", false);
  }
  sylLoad();
}
async function sylReport() {
  const subject = document.getElementById("syl_subject").value || "";
  const qs = new URLSearchParams();
  if (subject) qs.set("subject", subject);
  qs.set("source", SYL_STD);
  try {
    const r = await api("/api/syllabus/report?" + qs.toString());
    downloadText("大纲管理-未覆盖清单_" + (subject || "全部") + ".md", r.markdown);
  } catch (e) { toast(e.message || "导出失败", false); }
}

/* ---- ⑦ 真题考频（WP-02） ---- */
let REX_DRAFTS = [];
let REX_STATS = {};   // U-17：最近一次真题分析的统计（sentences/unmatched），供批量确认后重渲染复用
function rexSubject() { return document.getElementById("syl_subject")?.value || ""; }
async function rexAnalyze() {
  const text = document.getElementById("rex_text").value;
  if (!text.trim()) { toast("先粘贴真题文本", false); return; }
  let r;
  try {
    r = await api("/api/library/realexams/analyze",
      { method: "POST", body: JSON.stringify({ text, subject: rexSubject() }) });
  } catch (e) { toast(e.message || "分析失败", false); return; }
  REX_DRAFTS = r.drafts || [];
  REX_STATS = r.stats || {};
  if (!REX_DRAFTS.length) {
    const box = document.getElementById("rex_drafts");
    box.innerHTML = `<div class="hint">未识别到考点命中（${r.stats?.unmatched ?? 0} 句未命中词典）——可先导入大纲种子或粘贴大纲，词典越全频次越准。</div>`;
    toast(`分析完成：命中 0 条（${r.stats?.unmatched ?? 0} 句未匹配）`, false);
    return;
  }
  renderRexDrafts(r.stats || {});
}
function renderRexDrafts(stats) {
  // R3-04：草稿列表统一走全量重渲染——跳过删除后重算 data-skip 索引与「草稿确认（N 条）」计数
  const box = document.getElementById("rex_drafts");
  if (!REX_DRAFTS.length) {
    box.innerHTML = '<div class="hint">已清空选择。</div>';
    return;
  }
  // IMP-12③：草稿确认区折叠（默认收起，避免长表格把热力表挤出首屏）
  box.innerHTML = `<details class="rex-fold" open><summary>草稿确认（${REX_DRAFTS.length} 条）</summary>
    <div class="hint">来源句子 ${stats.sentences ?? 0} · 未命中 ${stats.unmatched ?? 0} ——核实后确认：</div>` +
    REX_DRAFTS.slice(0, 30).map((d, i) => `<div class="syl-item">
      <span class="learn-chip pending">×${d.freq}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")} · ${esc(d.subject || "?")}</div></span>
      <button class="act gray mini" data-skip="${i}">跳过</button></div>`).join("") +
    (REX_DRAFTS.length > 30 ? `<div class="hint">…共 ${REX_DRAFTS.length} 条（确认全部入库）</div>` : "") +
    `<div class="btns" style="margin-top:8px"><button class="act" onclick="rexConfirmAll()">确认全部入库</button>
     <button class="mini-btn" onclick="rexDraftClear()">取消草稿</button>
     <span class="hint" style="align-self:center">未确认不进入任何推荐权重（红线）</span></div></details>`;
  box.querySelectorAll("[data-skip]").forEach(b => b.onclick = () => {
    REX_DRAFTS.splice(+b.dataset.skip, 1);
    renderRexDrafts(stats);   // 全量重渲染：索引与计数同步更新
  });
}
async function rexConfirmAll() {
  if (!REX_DRAFTS.length) { toast("先分析"); return; }
  const total = REX_DRAFTS.length;
  const batch = REX_DRAFTS.slice(0, 200);
  let r;
  try {
    r = await api("/api/library/realexams/confirm",
      { method: "POST", body: JSON.stringify({ items: batch }) });
  } catch (e) { toast(e.message || "确认失败", false); return; }
  if (total > 200) {
    REX_DRAFTS = REX_DRAFTS.slice(200);
    toast(`已确认 ${r.added} 条（单次上限 200 条；剩余 ${REX_DRAFTS.length} 条待确认，请再次点击「确认全部入库」）`);
    renderRexDrafts(REX_STATS);   // U-17：原为 rexAnalyzeRender()——该函数全仓不存在（运行时 TypeError）
    return;
  }
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
  let r;
  try {
    r = await api("/api/library/realexams/freq" + (rexSubject() ? "?subject=" + encodeURIComponent(rexSubject()) : ""));
  } catch (e) { toast(e.message || "加载频次失败", false); return; }
  const box = document.getElementById("rex_heat");
  document.getElementById("rex_meta").textContent = `累计命中 ${r.total} 次`;
  if (!r.chapters.length) { box.innerHTML = '<div class="hint">暂无已确认频次。</div>'; return; }
  box.innerHTML = `<table class="rex-tab"><thead><tr><th>章节</th><th>频次</th><th>高频条目（前5）</th></tr></thead><tbody>` +
    r.chapters.slice(0, 15).map(ch => `<tr><td>${esc(ch.chapter)}</td><td><b>${ch.freq}</b></td><td>` +
      ch.items.slice(0, 5).map(i => `<span class="rex-item">${esc(i.item)}×${i.freq}` +
        (i.id ? ` <button class="rv-x" title="删除该频次记录" data-id="${esc(i.id)}" onclick="rexItemDel(this)">×</button>` : "") +
        `</span>`).join(" · ") + `</td></tr>`).join("") +
    `</tbody></table>`;
}
function rexFilePick() { document.getElementById("rex_file").click(); }
async function rexFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  let r;
  try {
    r = await api("/api/library/realexams/analyze-file", { method: "POST", body: fd });
  } catch (e) { toast(e.message || "文件解析失败", false); input.value = ""; return; }
  REX_DRAFTS = r.drafts || [];
  document.getElementById("rex_text").value = `（已解析文件 ${f.name}：${r.stats?.sentences ?? 0} 句 · 命中 ${REX_DRAFTS.length} 条）`;
  rexFileRender();
  input.value = "";
}
/* C21：文件草稿渲染（支持逐条跳过，与粘贴分析一致） */
function rexFileRender() {
  const box = document.getElementById("rex_drafts");
  if (!REX_DRAFTS.length) { box.innerHTML = '<div class="hint">文件解析命中 0 条。</div>'; return; }
  box.innerHTML = `<details class="rex-fold" open><summary>草稿确认（${REX_DRAFTS.length} 条）</summary>
    <div class="hint">文件草稿——核实后确认：</div>
    ${REX_DRAFTS.slice(0, 30).map((d, i) => `<div class="syl-item"><span class="learn-chip pending">×${d.freq}</span>
      <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter)}</div></span>
      <button class="act gray mini" data-skip="${i}">跳过</button></div>`).join("")}
    ${REX_DRAFTS.length > 30 ? `<div class="hint">…共 ${REX_DRAFTS.length} 条（确认全部入库）</div>` : ""}
    <div class="btns" style="margin-top:8px"><button class="act" onclick="rexConfirmAll()">确认全部入库</button>
      <button class="mini-btn" onclick="rexDraftClear()">取消草稿</button></div></details>`;
  box.querySelectorAll("[data-skip]").forEach(b => b.onclick = () => {
    REX_DRAFTS.splice(+b.dataset.skip, 1);
    rexFileRender();
  });
}
window.rexFileRender = rexFileRender;
function rexDraftClear() {
  REX_DRAFTS = [];
  const box = document.getElementById("rex_drafts");
  if (box) box.innerHTML = '<div class="hint">草稿已取消。</div>';
}
async function rexItemDel(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  confirmModal("删除频次记录", `<p style="margin:0;color:var(--dim)">确定删除这条已确认的频次记录？仅删除统计数据，不影响真题原文；删除后热力表即时更新。</p>`, "删除", async () => {
    try {
      await api("/api/library/realexams/" + encodeURIComponent(id), { method: "DELETE" });
      toast("已删除频次记录");
      rexHeat();
    } catch (e) { toast(e.message, false); }
  });
}
window.rexDraftClear = rexDraftClear; window.rexItemDel = rexItemDel;
async function rexReport() {
  const qs = rexSubject() ? "?subject=" + encodeURIComponent(rexSubject()) : "";
  try {
    const r = await api("/api/library/realexams/report" + qs);
    downloadText("真题高频考点_" + (rexSubject() || "全部") + ".md", r.markdown);
  } catch (e) { toast(e.message || "导出失败", false); }
}

/* ---- ⑧ 一键刷薄弱组卷（WP-03） ---- */
async function gapPaper() {
  if (!FEATURES.gap) { toast("该功能已在服务端禁用", false); return; }
  const sel = document.getElementById("dash_subject");
  let subject = sel ? sel.value : "";
  if (!subject) {
    // 「全部科目」时自动取第一个可选科目（薄弱组卷必须绑定科目）——不再空报「科目范围」错
    const first = sel && Array.from(sel.options).find(o => o.value);
    if (first) {
      subject = first.value;
      if (sel) sel.value = subject;
      /* C15：隐式选科要明示 */
      toast(`薄弱组卷需绑定科目——已自动选用「${subject}」`, false);
    } else {
      toast("暂无可选科目：请先在「错题本」导入错题或确认知识点科目", false);
      return;
    }
  }
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
      }, false, () => {
        // C13：取消后项目已创建但未运行——明确告知去向，避免留下「空转项目」疑惑
        toast("已创建「薄弱组卷」项目（未开始生成）——可到「我的项目」查看并开始或删除", false);
      });
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
/* WP-8：手动解析 SSE 事件流（fetch ReadableStream）；并发事件逐帧回调 */
async function consumeSSE(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i); buf = buf.slice(i + 2);
      let event = "message", data = "";
      raw.split("\n").forEach(l => {
        if (l.startsWith("event:")) event = l.slice(6).trim();
        else if (l.startsWith("data:")) data += l.slice(5).trim();
      });
      if (data) { try { onEvent(event, JSON.parse(data)); } catch (e) { /* 单帧解析失败跳过 */ } }
    }
  }
}
/* R4-02：流式 SSE（讲解/提问）的取消支持——AbortController +「停止生成」按钮 + 切视图/切 tab 即中断。
   A-16：按 anchorId 登记在途 AbortController——多个入口可并发生成：sseAbort(anchorId) 只杀本入口，
   sseAbortAll()（切视图/切 tab）杀全部并清全部按钮；abort() 使 fetch/reader 抛 AbortError，
   由消费方 catch 处理为「已停止生成（未保存）」。 */
function sseAbortAll() {
  for (const [, c] of sseAborts) { try { c.abort(); } catch (e) { /* ignore */ } }
  sseAborts.clear();
  document.querySelectorAll(".sse_stop_btn").forEach(b => b.remove());
}
function sseAbort(anchorId) {
  const c = sseAborts.get(anchorId);
  if (c) { try { c.abort(); } catch (e) { /* ignore */ } }
  sseAborts.delete(anchorId);
  const sb = document.getElementById("sse_stop_btn_" + anchorId);
  if (sb) sb.remove();
}
function sseStopUI(anchorId) {
  sseAbort(anchorId);   // 只清同入口残留（并发入口互不误杀：A-16）
  const anchor = document.getElementById(anchorId);
  if (!anchor || !anchor.parentElement) return;
  const sb = document.createElement("button");
  sb.id = "sse_stop_btn_" + anchorId; sb.className = "mini-btn danger sse_stop_btn";
  sb.textContent = "■ 停止生成";
  sb.title = "中止本次 AI 生成（未完成的内容不保存）";
  sb.onclick = (e) => { e.preventDefault(); e.stopPropagation(); sseAbort(anchorId); };
  anchor.parentElement.insertBefore(sb, anchor.nextSibling);
}
/* C12：讲解答题等操作后刷新概览（保持当前科目口径；失败静默） */
function refreshOverviewIfAny() {
  if (typeof loadOverview !== "function") return;
  const sel = document.getElementById("dash_subject");
  loadOverview(sel ? sel.value : "").catch(() => {});
}
