/* ---- ④ 学习中心（v0.7 M1/M2：错题本 + 掌握度诊断） ---- */
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
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
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
    el.classList.toggle("hot", !!hot && v > 0); };
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
/* ---- ⑥ 大纲覆盖（WP-01 考试锚定 · 以教师重点为纲） ---- */
let SYL_DRAFTS = [];
let SYL_LOADED = false;
let SYL_STD = "teacher";   // 大纲标准二选一：teacher=教师重点(默认，即用户自供内容) / seed=官方大纲（内置种子或上传导入）
/* C16：标准选择恢复（sylSetStd 写入 sessionStorage，此处首读；sylRender/首渲会用 SYL_STD） */
(function () {
  try {
    const s = sessionStorage.getItem("medkit-syl-std");
    if (s === "teacher" || s === "seed") SYL_STD = s;
  } catch (e) { /* ignore */ }
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
      const stdName = { teacher: "教师重点", seed: "官方大纲" }[SYL_STD] || SYL_STD;
      body.innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
        <div class="sub">暂无${stdName}条目 — ${SYL_STD === "teacher"
          ? '在「新建课题」上传教师重点后自动同步；或下方「上传教师重点文件」/粘贴教师重点考点清单。'
          : '点「导入内置大纲」加载教材/真题种子；或下方「上传官方大纲(md/txt)」一键导入官方条目。'}</div>
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
          ${it.id ? `<button class="rv-x" title="删除该条目（可重新导入）" onclick="sylItemDel('${esc(it.id)}')">×</button>` : ""}</div>`;
      }).join("");
      return `<div class="syl-chap">${esc(ch.chapter)} <span class="hint">（覆盖 ${ch.covered + ch.mastered}/${ch.total} · 未覆盖 ${ch.pending}）</span></div>` +
        (items || '<div class="hint" style="margin-left:10px">（无条目，待粘贴）</div>');
    }).join("");
  } catch (e) { sylFail(e.message); }
}
async function sylEnsure() {
  const r = await api("/api/syllabus/ensure", { method: "POST", body: JSON.stringify({ force: false }) });
  if (r.note === "seed missing") {
    toast("内置大纲文件缺失：请上传官方大纲(md/txt) 或改用「教师重点」标准", false);
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
  toast(`大纲条目入库：新增 ${r.added} 条`);
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
async function sylItemDel(id) {
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
  // 教师重点文件（PDF文本层/DOCX/MD/TXT）→ 自动处理：解析 → 结构化 → 知识点提取 → 幂等入库（source='teacher'）
  const inp = document.getElementById("syl_teacher_file");
  const f = inp.files && inp.files[0];
  inp.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const subject = document.getElementById("syl_subject")?.value || "";
  if (subject) fd.append("subject", subject);
  try {
    const r = await api("/api/syllabus/teacher/import-file", { method: "POST", body: fd });
    if (r.mode === "error") { toast(r.note || "文件解析失败", false); return; }
    if (!r.drafts || !r.drafts.length) { toast(r.note || "未识别到条目", false); return; }
    const kps = (r.knowledge || []).slice(0, 10).map(k => esc(k.name)).join("、");
    toast(`${r.note || "教师重点导入"} · 入库新增 ${r.added ?? "?"} 条（共 ${r.total ?? r.drafts.length} 条，幂等）`);
    SYL_DRAFTS = r.drafts;
    document.getElementById("syl_paste_preview").innerHTML =
      `<div class="hint">教师重点草稿 ${SYL_DRAFTS.length} 条（已入库 source=teacher${r.subject ? ` · 科目：${esc(r.subject)}` : ""}）：</div>` +
      SYL_DRAFTS.slice(0, 30).map(d => `<div class="syl-item">
        <span class="learn-chip pending">${esc(d.subject || "?")}</span>
        <span class="grow"><b>${esc(d.item)}</b><div class="hint">章：${esc(d.chapter || "（未分章）")}</div></span></div>`).join("") +
      (kps ? `<div class="hint">知识点提取（前 10 条）：${kps}…（共 ${(r.knowledge || []).length} 条）</div>` : "");
  } catch (e) {
    toast(e.message || "导入失败", false);
  }
  sylLoad();
}async function sylSeedImport() {
  // 官方大纲文件（md/txt）→ LLM 契约抽取 → source='seed' 幂等入库（一键导入）
  const inp = document.getElementById("syl_seed_file");
  const f = inp.files && inp.files[0];
  inp.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/syllabus/seed/import-file", { method: "POST", body: fd });
    if (!r.drafts || !r.drafts.length) { toast(r.note || "未识别到条目", false); return; }
    toast(`${r.note || "官方大纲导入"} · 入库新增 ${r.added ?? "?"} 条（共 ${r.total ?? r.drafts.length} 条，幂等）`);
    SYL_DRAFTS = r.drafts;
    document.getElementById("syl_paste_preview").innerHTML =
      `<div class="hint">官方大纲草稿 ${SYL_DRAFTS.length} 条（已入库 source=seed）：</div>` +
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
    downloadText("大纲覆盖报告_" + (subject || "全部") + ".md", r.markdown);
  } catch (e) { toast(e.message || "导出失败", false); }
}

/* ---- ⑦ 真题考频（WP-02） ---- */
let REX_DRAFTS = [];
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
     <button class="mini-btn" onclick="rexDraftClear()">取消草稿</button>
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
    rexAnalyzeRender();
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
        (i.id ? ` <button class="rv-x" title="删除该频次记录" onclick="rexItemDel('${esc(i.id)}')">×</button>` : "") +
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
async function rexItemDel(id) {
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
/* C12：讲解答题等操作后刷新概览（保持当前科目口径；失败静默） */
function refreshOverviewIfAny() {
  if (typeof loadOverview !== "function") return;
  const sel = document.getElementById("dash_subject");
  loadOverview(sel ? sel.value : "").catch(() => {});
}

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
    const [d, mk, my, recm] = await Promise.all([
      api("/api/library/dashboard?subject=" + encodeURIComponent(subject)),
      api("/api/library/mistakes"),
      api("/api/library/mastery?subject=" + encodeURIComponent(subject)),
      api("/api/library/recommend?limit=6&subject=" + encodeURIComponent(subject)),
    ]);
    renderDashboard(d);
    renderLibrary((mk.mistakes || []).filter(m => !subject || m.subject === subject), my, recm.recommend || []);
  } catch (e) {
    $("dash_scope").textContent = "汇总失败";
    $("dash_loop").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
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
      <button class="subj-card${rvSubject === s ? " on" : ""}" onclick="loadReviewCtx('${esc(s)}')" title="只看「${esc(s)}」的到期复习">
        <div class="subj-name">${esc(s)}</div>
        <div class="subj-row"><span>今日到期 <b>${st.review_due || 0}</b></span><span>总卡 <b>${st.review_total || 0}</b></span></div>
        <div class="subj-row"><span>错题 <b>${st.mistakes || 0}</b></span><span>掌握率 <b>${st.mastered_rate || 0}%</b></span></div>
      </button>`;
    const allCard = `
      <button class="subj-card${rvSubject === "" ? " on" : ""}" onclick="loadReviewCtx('')" title="查看全部科目的到期复习">
        <div class="subj-name">全部科目</div>
        <div class="subj-row"><span>共 <b>${subs.length}</b> 个科目</span><span>点卡片按科过滤</span></div>
      </button>`;
    box.innerHTML = subs.length
      ? `<div class="subj-grid">` + allCard + subs.map(s => card(s, byName[s] || {})).join("") + `</div>`
      : `<div class="hint">暂无科目——先在错题本导入错题，或去「题库」生成题目</div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
function appliedSubject() {
  const el = $("exp_subject");
  return el && el.value ? el.value : "";
}
/* C17：错题本「只看未掌握」筛选——缓存最近一次数据，勾选即重渲染（已掌握=归档标记，可隐藏） */
let _libCache = { mistakes: [], my: null, recm: [] };
function renderLibrary(mistakes, my, recm) {
  _libCache = { mistakes: mistakes || [], my: my || null, recm: recm || [] };
  renderLibraryCurrent();
}
function renderLibraryCurrent() {
  const { mistakes, my, recm } = _libCache;
  const chk = document.getElementById("mk_filter_unlearned");
  const onlyUn = !!(chk && chk.checked);
  const list = onlyUn ? mistakes.filter(m => !m.learned) : mistakes;
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
  $("learn_mk_count").textContent = `${list.length} 道`
    + (onlyUn && list.length !== mistakes.length ? `（未掌握 ${list.length} / ${mistakes.length}）` : "");
  if (!list.length) {
    $("learn_mk").innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
      <div class="sub">${mistakes.length
        ? `当前筛选下没有错题（已掌握 ${mistakes.length} 道被隐藏）<br>取消勾选「只看未掌握」可查看全部`
        : `错题本还是空的<br>粘贴一道错题入库，或点「拍题(图片 OCR)」「批量导入(JSON)」`}</div>
    </div>`;
  } else {
    $("learn_mk").innerHTML = list.map(mm => mkRowHTML(mm)).join("");
  }
}
window.renderLibraryCurrent = renderLibraryCurrent;
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
    ${mm.case_stem ? `<div><b>案例</b>：${esc(mm.case_stem)}</div>` : ""}
    ${mm.image_ref && mm.source_ref && mm.source_ref.pid ? `<div><b>图</b>：<img src="/api/projects/${esc(mm.source_ref.pid)}/assets/${esc(mm.image_ref)}" style="max-width:320px;max-height:240px;border-radius:8px;border:1px solid var(--line);display:block;margin:6px 0" onerror="this.remove()"></div>` : ""}
    ${(mm.options || []).length ? `<div><b>选项</b>：${mm.options.map((o, i) => `${"ABCDEF"[i] || i + 1}. ${esc(o)}`).join("　")}</div>` : ""}
    ${mm.answer ? `<div class="ans">✓ 答案：${esc(mm.answer)}</div>` : ""}
    ${mm.user_answer ? `<div><b>我的作答</b>：${esc(mm.user_answer)}</div>` : ""}
    ${mm.analysis ? `<div><b>解析</b>：${hlKw(mm.analysis)}</div>` : ""}`;
  const kp = (mm.know_tags || [])[0] || mm.topic || "";
  return `<div class="mk-row">
    <div class="mk-main">
      <div class="mk-q" onclick="mkDetailTgl('${esc(mm.id)}')" title="点击展开详情">${esc(mm.question || "(无题干)")}</div>
      <div class="mk-meta">${meta.join("")}</div>
      <div class="mk-detail" id="mkd_${esc(mm.id)}">${detail || '<div class="hint">（无更多详情）</div>'}</div>
    </div>
    <div class="mk-actions">
      ${kp ? `<button class="mini-btn primary" onclick="learnRecAction(this)" data-kind="explain" data-subject="${esc(mm.subject || "")}" data-name="${esc(kp)}">→ 讲解</button>
      <button class="mini-btn" onclick="learnRecAction(this)" data-kind="tutor" data-subject="${esc(mm.subject || "")}" data-name="${esc(kp)}">→ 提问</button>` : ""}
      ${mm.learned ? "" : `<button class="act" style="padding:5px 11px;font-size:12px" onclick="mkLearn('${esc(mm.id)}',true)">已掌握</button>`}
      <button class="act gray" style="padding:5px 11px;font-size:12px;color:#f87171" onclick="mkDel('${esc(mm.id)}')">删除</button>
    </div>
  </div>`;
}
function mkDetailTgl(id) {
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
    toast(learned ? "已标记为已掌握（仅归档标记；掌握度仍由真实作答驱动）" : "已取消已掌握标记"); loadLibrary();
  } catch (e) { toast(e.message, false); }
}
async function mkDel(id) {
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
  finally { input.value = ""; btn.disabled = false; btn.textContent = old; }
}
window.mkLearn = mkLearn; window.mkDel = mkDel; window.mkOcrPick = mkOcrPick;
window.mkBatchPick = mkBatchPick;

/* ---- M3：讲解与学习产物（教材切片 + 联网补充 + 产物管理） ---- */
const LEARN_STATE_ORDER = { weak: 0, shaky: 1, solid: 2, mastered: 3 };
/* C2：讲解 Markdown 渲染——在标题/列表/引用基础上补 GFM 表格与围栏代码块（医学对比表/数值表可读） */
function expMd(md) {
  const raw = String(md || "");
  const inline = t => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>");
  const cells = t => String(t).split("|").slice(1, -1).map(c => inline(c.trim()));
  const isTableRow = l => /^\|.*\|$/.test((l || "").trim());
  const isSepRow = l => /^\|[\s:|-]+\|$/.test((l || "").trim());
  let html = "", inList = false, inQuote = false, inCode = false;
  const lines = raw.split("\n");
  for (let li = 0; li < lines.length; li++) {
    const line = (lines[li] || "").trim();
    if (inCode) {
      if (line.startsWith("```")) { html += "</code></pre>"; inCode = false; }
      else html += esc(line) + "\n";
      continue;
    }
    if (line.startsWith("```")) {
      if (inList) { html += "</ul>"; inList = false; }
      if (inQuote) { html += "</blockquote>"; inQuote = false; }
      html += "<pre><code>"; inCode = true; continue;
    }
    if (!line) { if (inList) { html += "</ul>"; inList = false; } if (inQuote) { html += "</blockquote>"; inQuote = false; } continue; }
    // GFM 表格：表头行 + 紧跟分隔行 → 收集表格块
    if (isTableRow(line) && li + 1 < lines.length && isSepRow(lines[li + 1])) {
      if (inList) { html += "</ul>"; inList = false; }
      if (inQuote) { html += "</blockquote>"; inQuote = false; }
      html += "<table><thead><tr>" + cells(line).map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
      li++;                                   // 跳过分隔行
      while (li + 1 < lines.length && isTableRow(lines[li + 1])) {
        li++;
        html += "<tr>" + cells(lines[li]).map(c => `<td>${c}</td>`).join("") + "</tr>";
      }
      html += "</tbody></table>";
      continue;
    }
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
  if (inCode) html += "</code></pre>";
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
          ${e.grounded === false
            ? `<span class="tag" style="color:var(--warn)">无教材原文 · 网络+模型知识</span>`
            : (e.via_web ? `<span class="tag" style="color:var(--good)">含web补充</span>` : `<span class="tag">纯教材</span>`)}
          <span class="hint">${esc((e.created_at || "").slice(0, 16).replace("T", " "))}</span>
          <span class="hint">${(e.sources || []).length} 来源</span>
          <span class="mini-btn">展开</span>
        </div>
        <div class="exp-article">${expMd(e.content || "")}</div>
        ${e.sources && e.sources.length ? `<details style="margin-top:8px"><summary style="font-size:11.5px">来源（${e.sources.length}）——点击查看</summary>
          <div class="hint" style="margin-top:6px;font-size:11.5px;line-height:1.9">${e.sources.map(s => (s.kind === "web" ? "🌐" : "📖") + " " + esc(s.title || s.url || "")).join("<br>")}</div></details>` : ""}
        ${e.kp_name ? `<details style="margin-top:8px" ontoggle="expHint(this)" data-subject="${esc(e.subject || "")}" data-kp="${esc(e.kp_name)}">
          <summary style="font-size:11.5px;cursor:pointer">📄 查看教材切片原文（不消耗 AI）</summary>
          <div class="rv-hintbody exp-slices" id="exps_${esc(e.id)}"><span class="hint">展开后自动检索教材切片…</span></div></details>` : ""}
        <div class="btns" style="margin-top:10px">
          ${e.kp_name ? `<button class="mini-btn" onclick="learnRecAction(this)" data-kind="tutor" data-subject="${esc(e.subject || "")}" data-name="${esc(e.kp_name)}">→ 提问练习</button>` : ""}
          ${(window.FEATURES && FEATURES.cards) ? `<button class="mini-btn" onclick="expCards('${esc(e.id)}','${esc(e.subject || "")}')">🧠 生成记忆卡</button>` : ""}
          <button class="mini-btn primary" onclick="expRegen(this)" data-id="${esc(e.id)}" data-subject="${esc(e.subject || "")}" data-kp="${esc(e.kp_name || "")}">↻ 重新生成</button>
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
    const v = r.explain && r.explain.grounded === false
      ? "未命中教材原文 · 网络+模型知识生成"
      : (r.explain && r.explain.via_web ? "含联网补充" : "基于教材切片");
    $("exp_cost").textContent = `已生成：《${r.title}》· ${v}`;
    loadExplains();
    // C12：生成讲解 → 概览诊断/学习闭环同步刷新（受掌握的讲解产物计数变化）
    invalidateLearnCache();
    refreshOverviewIfAny();
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
async function expRegen(btnOrId, subject, kpName) {
  if (btnOrId && typeof btnOrId === "object" && btnOrId.dataset) {
    const d = btnOrId.dataset;
    subject = d.subject || ""; kpName = d.kp || ""; btnOrId = d.id || "";
  }
  const id = btnOrId;
  confirmModal("重新生成讲解", "<p>将<b>删除当前讲解</b>并以同名重新生成（AI 失败时旧讲解不会自动恢复）。继续？</p>",
    "重新生成", async () => {
      try {
        await api("/api/library/explains/" + id, { method: "DELETE" });
        await learnRecAction("explain", subject, kpName);
      } catch (e) { toast(e.message, false); loadExplains(); }
    }, false);
}
window.expFold = expFold; window.expRegen = expRegen;
/* WP-05/NX-04：讲解产物 → 医学记忆卡（flag = cards 前端同步隐藏按钮） */
async function expCards(eid, subject) {
  try {
    const r = await api("/api/library/cards/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ explain_id: eid }) });
    toast(r.added ? `已生成 ${r.added} 张医学记忆卡（复习计划「🧠 医学记忆卡」可见）`
                  : "记忆卡已存在（幂等，未新增）");
    // C19：用「生成卡时的讲解科目」刷新（复习视图过滤可能不含新卡 → 切到对应科目可见）
    const target = subject || rvSubject;
    if (typeof loadReviewCtx === "function") loadReviewCtx(target);
    if (rvSubject && subject && rvSubject !== subject) {
      toast(`记忆卡科目「${subject}」——复习过滤已切到该科目查看`, false);
    }
  } catch (e) { toast(e.message, false); }
}
window.expCards = expCards;
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
  box.innerHTML = `<div class="hint" style="margin-bottom:6px">
      <button class="mini-btn" onclick="tutorCleanup()" title="删除 30 天无活动的会话（不可恢复）">清理 30 天无活动会话</button>
      <span style="font-size:11px">会话按最近活动排序；太久没动的会越排越后</span></div>
    <div class="tu-wrap"><div class="tu-side">${list.map(sessionItem).join("")}</div>
    <div class="hint" style="padding:24px 8px">左侧选一场会话继续，或上方「开始提问」开启新对话。</div></div>`;
  $("btn_tu_exit").style.display = "none";
}
/* C18：清理 30 天无活动提问会话（防列表无限增长；不可恢复） */
async function tutorCleanup() {
  confirmModal("清理无活动会话？", `<p style="margin:0;color:var(--dim)">将删除 <b>30 天无活动</b>的提问会话，问答记录不可恢复（知识点掌握度不受影响）。</p>`,
    "清理", async () => {
      try {
        const r = await api("/api/library/tutor/cleanup", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ days: 30 }) });
        toast(r.removed ? `已清理 ${r.removed} 场无活动会话` : "没有 30 天无活动的会话");
        loadTutorSessions();
      } catch (e) { toast(e.message, false); }
    }, false);
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
    $("tutor_cost").textContent = r.grounded === false
      ? "第一问已就绪（⚠️ 未命中教材原文，基于网络素材与模型知识）——请作答。"
      : "第一问已就绪，请作答。";
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
    // C3：LLM 判分失败返回 retry=true（score<0 不计分）——不渲染负数分数，改为弱提示重答
    if (r.retry) {
      $("tutor_cost").textContent = "本轮未完成判分（模型未给分）——请围绕考点再作答一次";
    } else {
      const note = r.grounded === false ? "（未命中教材原文 · 网络+模型知识）" : "";
      $("tutor_cost").textContent = `本轮得分 ${r.score}/3${note}` + (r.gap ? ` —— ${r.gap}` : "");
    }
    tutorShowConversation();
    invalidateLearnCache();   // 判分回写掌握度 → 失效学习中心缓存，概览到手最新值
    // C12：提问判分后概览诊断同步刷新（掌握度/优先级可能已变化）
    refreshOverviewIfAny();
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
window.tutorCleanup = tutorCleanup;

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
  if (rvSubject !== subject) studyDueBase = 0;   // 切换科目 → 重置今日进度基数
  rvSubject = subject;
  try {
    const [today, subs] = await Promise.all([
      api("/api/library/review/today?subject=" + encodeURIComponent(subject)),
      cachedSubjects(),
    ]);
    fillReviewSubjects(subs);
    renderSmReview(today);
    renderMemoryCards();      // WP-05/NX-04：医学记忆卡（FSRS 默认 / SM-2 可切）
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
/* v0.8.1：更名 renderSmReview——原 renderReview 与 review-desk.js 的审核台渲染器
   全局重名（经典脚本共享作用域），后者后加载覆盖前者，导致复习卡列表静默不渲染。 */
function renderSmReview(today) {
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
  renderStudyProgress(today);
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
/* PRD 3.5：今日进度 X/Y（X=本次会话已评，Y=进入刷题时的今日到期数；零后端改动） */
let studyDueBase = 0;
function renderStudyProgress(today) {
  const el = $("study_progress");
  if (!el) return;
  const st = today.stats || {};
  if (studyDueBase === 0 && (st.due || 0) > 0) studyDueBase = st.due;
  const total = studyDueBase || (st.due || 0);
  const done = Math.max(0, total - (st.due || 0));
  const pct = total ? Math.min(100, Math.round(100 * done / total)) : 0;
  el.innerHTML = `<div class="sprog">
    <div class="sprog-label">今日进度 <b>${done}/${total}</b>${total === 0 ? "——今天没有到期卡片，点下方「铺卡」把薄弱点排进来" : ""}</div>
    <div class="sprog-bar"><i style="width:${pct}%"></i></div>
  </div>`;
}
/* PRD 6.4.2：三按钮评级映射（决策 3：保留 0~5 六档，三按钮做前端映射）。
   忘=0（懵了）· 糊=2（想岔）· 记=4（想起）；精确档位折叠在「精确自评」里。 */
const GRADE3_MAP = { forget: 0, fuzzy: 2, got: 4 };
function rvCard(c) {
  const meta = `间隔 ${c.interval || 0} 天 · 难度 ${(c.ease || 2.5).toFixed(2)} · 背 ${c.reps || 0} 次 · 忘 ${c.lapses || 0} 次`;
  const grades = [0, 1, 2, 3, 4, 5].map(q =>
    `<button class="rv-g${q}" onclick="rvGrade('${esc(c.id)}',${q})" title="质量 ${q}/5 分">${q}</button>`).join("");
  return `<div class="qcard" data-card="${esc(c.id)}" onclick="qcardFlip(this, event)">
    <div class="qcard-inner">
      <div class="qcard-face qfront">
        <div class="rv-top">${rvChip(c.state)}<span class="hint" style="font-size:11px">${esc(c.subject || "未分类")}</span>
          <button class="rv-x" title="移出复习队列" onclick="rvDel('${esc(c.id)}')">×</button></div>
        <div class="rv-q">${esc(c.kp_name || "(未命名知识点)")}</div>
        <div class="rv-meta">${esc(meta)}</div>
        <div class="qcard-tip">💡 先在脑中回忆这个知识点，再点卡片翻面看提示</div>
      </div>
      <div class="qcard-face qback">
        <details class="rv-hint" ontoggle="rvHint(this,'${esc(c.kp_name || "")}','${esc(c.subject || "")}')">
          <summary>📖 展开提示（教材原文 · 不消耗 AI）</summary>
          <div class="rv-hintbody"><span class="hint">展开后自动检索教材切片</span></div>
        </details>
        <div class="grades3">
          <button class="g3 forget" onclick="rvGrade3('${esc(c.id)}','forget')" title="忘了——按 0/5 排期（快捷键 1）">忘了</button>
          <button class="g3 fuzzy" onclick="rvGrade3('${esc(c.id)}','fuzzy')" title="模糊——按 2/5 排期（快捷键 2）">模糊</button>
          <button class="g3 got" onclick="rvGrade3('${esc(c.id)}','got')" title="记住——按 4/5 排期（快捷键 3）">记住</button>
        </div>
        <details class="rv-grades-detail"><summary class="hint">精确自评（0~5）</summary>
          <div class="rv-grades">${grades}</div>
          <div class="rv-legend hint">0懵了 · 1很困难 · 2想岔 · 3勉强 · 4想起 · 5秒答</div>
        </details>
      </div>
    </div>
  </div>`;
}
/* 卡片翻转：点击卡面翻面（按钮/折叠控件点击不触发翻面） */
function qcardFlip(cardEl, ev) {
  if (ev && ev.target.closest("button,summary,details,a,input,textarea,select")) return;
  cardEl.classList.toggle("flipped");
}
window.qcardFlip = qcardFlip;
/* 三按钮评级：播放出卡动效后按映射质量走原 rvGrade 管线 */
async function rvGrade3(cid, key) {
  const q = GRADE3_MAP[key];
  const cardEl = document.querySelector('.qcard[data-card="' + cid + '"]');
  if (cardEl) { cardEl.classList.add("graded"); setTimeout(() => cardEl.remove(), 260); }
  await rvGrade(cid, q, { forget: "忘了", fuzzy: "模糊", got: "记住" }[key]);
}
window.rvGrade3 = rvGrade3;
/* 键盘 1/2/3：刷题 tab 下对当前卡（已翻面优先）执行 忘了/模糊/记住；未翻面先自动翻面 */
window.addEventListener("keydown", e => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey || !(e.key >= "1" && e.key <= "3")) return;
  if (!$("tab-study") || !$("tab-study").classList.contains("show")) return;
  const card = document.querySelector("#rv_body .qcard.flipped") || document.querySelector("#rv_body .qcard")
            || document.querySelector("#mem_area .qcard.flipped") || document.querySelector("#mem_area .qcard");
  if (!card) return;
  e.preventDefault();
  card.classList.add("flipped");
  rvGrade3(card.dataset.card, ["forget", "fuzzy", "got"][+e.key - 1]);
});
/* 复习卡「查看提示」：懒加载教材原文切片（零 LLM，纯本地检索） */
/* C20：切片原文「展开全文」——默认截断保护版面，需完整阅读时一键展开 */
function rvSliceExpand(btn) {
  const d = btn.closest(".rv-slice");
  if (!d || !d.dataset.full) return;
  const t = d.querySelector(".rv-text");
  if (t) t.textContent = d.dataset.full;
  btn.remove();
}
window.rvSliceExpand = rvSliceExpand;
function rvSliceHTML(s, briefLen) {
  const t = String(s.text || "");
  const brief = t.slice(0, briefLen);
  const full = esc(t);   // 展开全文走纯文本（data-full），关键词高亮仅作用于摘要视图
  return `<div class="rv-slice rv-full" data-full="${full}"><b>${esc(s.title || s.sid || "切片")}</b>`
    + `<span class="rv-text">${hlKw(brief)}</span>${t.length > brief.length
      ? `<button class="mini" style="margin-left:6px" onclick="rvSliceExpand(this)">展开全文</button>` : ""}</div>`;
}
async function rvHint(det, kpName, subject) {
  if (!det || det.dataset.loaded === "1" || !det.open) return;
  det.dataset.loaded = "1";
  const body = det.querySelector(".rv-hintbody");
  body.innerHTML = '<span class="spin"></span><span class="hint">正在检索教材切片…</span>';
  try {
    const r = await api(`/api/library/explain/slices?subject=${encodeURIComponent(subject || "")}&query=${encodeURIComponent(kpName || "")}&limit=5`);
    const sl = r.slices || [];
    if (!sl.length) {
      // RAG 无原文回退：先说明未检索到，再提供「网络 + 模型知识」一键生成（成本前置）
      body.innerHTML = `<div class="hint" style="line-height:1.9">
        教材中未检索到「${esc(kpName)}」原文。<br>
        <button class="mini-btn primary" onclick="rvHintGen(this,'${esc(kpName)}','${esc(subject)}')">结合网络与模型知识生成提示</button>
        <span style="font-size:11px;color:var(--dim)">${estLlmCost(2.2, 0.35)}</span></div>`;
      return;
    }
    body.innerHTML = sl.map(s => rvSliceHTML(s, 300)).join("");
  } catch (e) {
    body.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
window.rvHint = rvHint;
/* 无原文回退：联网检索 + 模型知识生成提示（复用讲解端点，产物同时沉淀到复习手册） */
async function rvHintGen(btn, kpName, subject) {
  const old = btn.textContent; btn.disabled = true; btn.textContent = "生成提示中…";
  try {
    const r = await api("/api/library/explain", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject: subject || "", kp_name: kpName, use_web: true }) });
    const body = btn.closest(".rv-hintbody");
    if (body) body.innerHTML =
      `<div class="hint" style="margin-bottom:8px">未命中教材原文——以下提示由<b>网络检索与模型知识</b>生成（未经教材核实）：</div>`
      + `<div class="exp-article">${expMd(r.explain.content || "")}</div>`;
  } catch (e) { toast(e.message, false); btn.textContent = old; btn.disabled = false; }
}
window.rvHintGen = rvHintGen;
/* 讲解产物「查看教材切片原文」：懒加载（复用 explain/slices 端点，零 LLM） */
async function expHint(det, subject, kpName) {
  if (det && typeof det === "object" && det.dataset) {
    subject = det.dataset.subject || "";
    kpName = det.dataset.kp || "";
  }
  if (!det || det.dataset.loaded === "1" || !det.open) return;
  det.dataset.loaded = "1";
  const body = det.querySelector(".exp-slices");
  if (!body) return;
  body.innerHTML = '<span class="spin"></span><span class="hint">正在检索教材切片…</span>';
  try {
    const r = await api(`/api/library/explain/slices?subject=${encodeURIComponent(subject || "")}&query=${encodeURIComponent(kpName || "")}&limit=5`);
    const sl = r.slices || [];
    if (!sl.length) {
      body.innerHTML = `<div class="hint" style="line-height:1.9">教材中未检索到「${esc(kpName)}」原文——该讲解内容可能基于<b>网络素材与模型知识</b>生成（未经教材核实，见上方「来源」）。如需教材溯源，请上传对应教材后「↻ 重新生成」讲解。</div>`;
      return;
    }
    body.innerHTML = sl.map(s => rvSliceHTML(s, 400)).join("");
  } catch (e) { body.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
window.expHint = expHint;
async function rvGrade(cid, q, label = null) {
  try {
    await api("/api/library/review/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cid, quality: q }),
    });
    toast(label ? `已记录「${label}」（${q}/5），卡片已按 SM-2 排入下次复习`
                : `已记录 ${q}/5 分，卡片已按 SM-2 排入下次复习`);
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
  confirmModal("移出复习队列？", `<p style="margin:0;color:var(--dim)">该复习卡将从队列移除（知识点可随时「铺卡」重新入队）。</p>`, "移出", async () => {
    try {
      await api("/api/library/review/" + cid, { method: "DELETE" });
      toast("已移出复习队列");
      loadReviewCtx(rvSubject);
    } catch (e) { toast(e.message, false); }
  }, false);
}
window.loadReviewCtx = loadReviewCtx; window.rvQueueAll = rvQueueAll; window.rvGrade = rvGrade; window.rvDel = rvDel;

/* ---- WP-05/NX-04：医学记忆卡（讲解产物 → 记忆卡；FSRS 默认 / SM-2 可切） ---- */
async function renderMemoryCards() {
  const sec = $("mem_area");
  if (!sec) return;
  if (!(window.FEATURES && FEATURES.cards)) { sec.innerHTML = ""; return; }
  try {
    const r = await api("/api/library/cards?subject=" + encodeURIComponent(rvSubject) + "&due=1");
    const cards = r.cards || [];
    const st = r.stats || {};
    sec.innerHTML = `<div class="cardh" style="margin-top:6px"><h3 style="margin:0">🧠 医学记忆卡</h3>
      <span class="hint">讲解产物自动沉淀 · 总 ${st.total || 0} / 今日到期 ${st.due || 0}</span>
      <span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <button class="act gray mini" style="padding:4px 10px" onclick="expCardsHint()">+ 生成记忆卡</button>
        <button class="act gray mini" style="padding:4px 10px" onclick="memExportApkg()">导出 Anki（.apkg）</button>
        <button class="act gray mini" style="padding:4px 10px" onclick="memExportTxt()">导出 .txt</button>
        <button class="act gray mini" style="padding:4px 10px" onclick="ankiHelp()">导入指引</button>
      </span></div>`
      + (cards.length
        ? cards.map(memCard).join("")
        : `<div class="empty" style="padding:16px 0"><div class="sub">今日无到期记忆卡<br>到「讲解与学习产物」选中讲解 →「🧠 生成记忆卡」入队（FSRS 间隔重复算法，自动排出每日复习计划）</div></div>`);
  } catch (e) { sec.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
/* PRD 6.4.2：记忆卡三按钮映射（FSRS 四档）：忘=重来(0) · 糊=困难(2) · 记=良好(3)；
   保守映射「记」到良好而非简单，复习间隔略短更稳妥。 */
const MEM_GRADE3 = { forget: 0, fuzzy: 2, got: 3 };
function memCard(c) {
  const grades = [["重来", 0], ["困难", 2], ["良好", 3], ["简单", 5]];
  return `<div class="qcard memq" data-card="${esc(c.id)}" onclick="qcardFlip(this, event)">
    <div class="qcard-inner">
      <div class="qcard-face qfront">
        <div class="rv-top">${rvChip(c.state)}<span class="tag">${esc(c.kind_label || c.kind || "")}</span>
          <span class="hint" style="font-size:11px">${esc(c.subject || "未分类")}</span>
          <button class="rv-x" title="删除记忆卡" onclick="memDel('${esc(c.id)}')">×</button></div>
        <div class="rv-q">${esc(c.front)}</div>
        <div class="rv-meta">${esc((c.kp_name || "") + (c.sched ? " · " + c.sched.toUpperCase() : ""))}
          · 下次 ${esc(String(c.due || "").slice(0, 10))} · 背 ${c.reps || 0} 次 · 忘 ${c.lapses || 0} 次</div>
        <div class="qcard-tip">💡 先在脑中回忆，再点卡片翻面看答案</div>
      </div>
      <div class="qcard-face qback">
        <div class="rv-slice" style="margin:6px 0">${hlKw(c.back)}</div>
        <div class="grades3">
          <button class="g3 forget" onclick="memGrade3('${esc(c.id)}','forget')" title="忘了——重来（快捷键 1）">忘了</button>
          <button class="g3 fuzzy" onclick="memGrade3('${esc(c.id)}','fuzzy')" title="模糊——困难（快捷键 2）">模糊</button>
          <button class="g3 got" onclick="memGrade3('${esc(c.id)}','got')" title="记住——良好（快捷键 3）">记住</button>
        </div>
        <details class="rv-grades-detail"><summary class="hint">精确自评（FSRS 4 档）</summary>
          <div class="rv-grades">${grades.map(([t, q]) =>
            `<button class="rv-g${q}" onclick="memGrade('${esc(c.id)}',${q})">${t}</button>`).join("")}</div>
          <div class="rv-legend hint">重来=遗忘 · 困难=回想吃力 · 良好=正常 · 简单=秒答（三按钮：忘≈重来0 · 糊≈困难2 · 记≈良好3）</div>
        </details>
      </div>
    </div>
  </div>`;
}
async function memGrade3(cid, key) {
  const cardEl = document.querySelector('.memq[data-card="' + cid + '"]');
  if (cardEl) { cardEl.classList.add("graded"); setTimeout(() => cardEl.remove(), 260); }
  await memGrade(cid, MEM_GRADE3[key], { forget: "忘了", fuzzy: "模糊", got: "记住" }[key]);
}
window.memGrade3 = memGrade3;
async function memGrade(cid, q, label = null) {
  try {
    await api("/api/library/cards/" + encodeURIComponent(cid) + "/grade", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality: q }) });
    toast(label ? `已记录「${label}」（${q}/5），记忆卡已排入下次复习`
                : `已记录自评 ${q}/5，记忆卡已排入下次复习`);
    loadReviewCtx(rvSubject);
  } catch (e) { toast(e.message, false); }
}
async function memDel(cid) {
  confirmModal("删除记忆卡？", `<p style="margin:0;color:var(--dim)">该记忆卡将从队列删除（讲解产物可重新「🧠 生成记忆卡」）。</p>`, "删除", async () => {
    try {
      await api("/api/library/cards/" + encodeURIComponent(cid), { method: "DELETE" });
      toast("已删除记忆卡");
      loadReviewCtx(rvSubject);
    } catch (e) { toast(e.message, false); }
  }, false);
}
window.renderMemoryCards = renderMemoryCards; window.memGrade = memGrade; window.memDel = memDel;
/* C8：记忆卡面板内新增入口——跳转讲解视图（讲解产物是记忆卡的生成源） */
function expCardsHint() {
  showLearnView("explain");
  toast("选中一条讲解产物 → 点「🧠 生成记忆卡」即可入队", false);
}
window.expCardsHint = expCardsHint;
/* D15：记忆卡导出（.apkg 真包 / .txt 文本；空库时给可读提示） */
async function memExportApkg() {
  try {
    const a = document.createElement("a");
    a.href = "/api/library/cards/export/apkg?subject=" + encodeURIComponent(rvSubject);
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
    toast("已开始导出记忆卡 .apkg（Anki 双击即可导入）");
  } catch (e) { toast(e.message, false); }
}
async function memExportTxt() {
  try {
    const r = await api("/api/library/cards/export/txt?subject=" + encodeURIComponent(rvSubject));
    downloadText(r.filename || "MedKit记忆卡.txt", r.content);
    toast("已导出记忆卡 .txt（Anki「文件→导入」选择 Tab 分隔）");
  } catch (e) { toast(e.message, false); }
}
window.memExportApkg = memExportApkg; window.memExportTxt = memExportTxt;

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
window.ankiPreview = ankiPreview;
/* Anki 导入指引（.txt 文本导入 / .apkg 桌面导入，含手机端说明） */
function ankiHelp() {
  $("md_title").textContent = "Anki 导入指引";
  $("md_body").innerHTML = `
    <div class="hint" style="line-height:1.9">
      <b>.</b> <b>.apkg 卡包</b>（推荐，电脑端）：<br>
      ① 下载 .apkg 文件 → ② 双击打开（或 Anki「文件 → 导入」）→ ③ 自动建「MedKit 医学题库」牌组 ✓<br><br>
      <b>.</b> <b>.txt 文本</b>（Anki 桌面版）：<br>
      ① 打开 Anki → ② 「文件 → 导入」→ ③ 选择 .txt → ④ 字段分隔符选「Tab」，前 4 行不要跳过 → 导入 ✓<br><br>
      <b>.</b> <b>手机端（AnkiDroid / AnkiMobile）</b>：<br>
      把 .apkg 文件传到手机（微信/QQ/网盘均可）→ 点开文件选择「用 Anki 打开」即可导入；.txt 需先在电脑版导入。<br><br>
      <b>.</b> 标签（题型 / Bloom / 章节）导入后自动带出；「x 型自评卡」正面为判断题干关键词，反面给出正确答案，适合多选自测。
    </div>`;
  $("md_ok").textContent = "知道了";
  $("md_ok").className = "act";
  $("modal_mask").style.display = "flex";
  $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; };
}
window.ankiHelp = ankiHelp;
