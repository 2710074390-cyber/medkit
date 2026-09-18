/* S3-3（R8+W）：文件名清洗——只保留 basename，剥掉路径分隔与控制字符 */
function _baseName(name) {
  const s = String(name || "").replace(/[\\/]+/g, "/").split("/").pop() || "";
  return s.replace(/[\u0000-\u001f\u007f]/g, "").trim() || "MedKit记忆卡.apkg";
}
/* exported GRADE3_MAP, LETTERS, MEM_GRADE3, RVC_STATES, a, ankiHelp, ankiPreview, bg, blob, body, border, brief, card, cardEl, cards, cd, cur, d, done, due, el, expCardsHint, expHint, fillReviewSubjects, full, gradeBusy, grades, loadReviewCtx, m, memCard, memDel, memExportApkg, memExportTxt, memGrade, memGrade3, meta, msg, old, opts, pct, q, qcardFlip, qs, queueBusy, r, renderMemoryCards, renderSmReview, renderStudyProgress, resp, rvCard, rvChip, rvDel, rvGrade, rvGrade3, rvHint, rvHintGen, rvQueueAll, rvSliceExpand, rvSliceHTML, rvStudyKeys, rvSubject, s, sec, sel, sl, st, stem, strip, studyDueBase, studyDueDate, studyDueResetIfStale, subs, t, todayStr, total, url */
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
  // RV1（2026-09-17 安全审查）：内联事件里的动态参数统一改 data-* + this 传参——
  // esc() 只做 HTML 转义，属性值内实体会被浏览器先解码再交 JS 解析，单引号复活即注入；
  // 程序化调用（loadReviewCtx(rvSubject) 等）仍传字符串，双签名兼容。
  if (subject && typeof subject === "object" && subject.dataset) subject = subject.dataset.subject || "";
  // 切换科目 或 跨天（D-11）→ 重置今日进度基数（杜绝「今日进度 70/100」凭空显示）
  if (rvSubject !== subject) studyDueBase = 0;
  studyDueResetIfStale();
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
  // D-28：每次进入复习视图都刷新科目选项（新增科目可过滤），刷新后保留原选中值
  const sel = $("rv_subject");
  if (!sel) return;
  const cur = sel.value;
  const subs = resp.subjects || [];
  sel.innerHTML = `<option value="">全部科目</option>` + subs.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  if (cur && subs.includes(cur)) sel.value = cur;
  else if (rvSubject && subs.includes(rvSubject)) sel.value = rvSubject;
  else sel.value = "";
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
let studyDueDate = "";   // D-11：跨天重置「今日进度」基数（杜绝隔天凭空显示 70/100）
function studyDueResetIfStale() {
  const todayStr = new Date().toISOString().slice(0, 10);
  if (studyDueDate !== todayStr) { studyDueBase = 0; studyDueDate = todayStr; }
}
function renderStudyProgress(today) {
  const el = $("study_progress");
  if (!el) return;
  studyDueResetIfStale();   // D-11：日期变化也重置基数
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
    `<button class="rv-g${q}" data-id="${esc(c.id)}" data-q="${q}" onclick="rvGrade(this)" title="质量 ${q}/5 分">${q}</button>`).join("");
  return `<div class="qcard" data-card="${esc(c.id)}" onclick="qcardFlip(this, event)">
    <div class="qcard-inner">
      <div class="qcard-face qfront">
        <div class="rv-top">${rvChip(c.state)}<span class="hint" style="font-size:11px">${esc(c.subject || "未分类")}</span>
          <button class="rv-x" title="移出复习队列" data-id="${esc(c.id)}" onclick="rvDel(this)">×</button></div>
        <div class="rv-q">${esc(c.kp_name || "(未命名知识点)")}</div>
        <div class="rv-meta">${esc(meta)}</div>
        <div class="qcard-tip">💡 先在脑中回忆这个知识点，再点卡片翻面看提示</div>
      </div>
      <div class="qcard-face qback">
        <details class="rv-hint" data-kp="${esc(c.kp_name || "")}" data-subject="${esc(c.subject || "")}" ontoggle="rvHint(this)">
          <summary>📖 展开提示（教材原文 · 不消耗 AI）</summary>
          <div class="rv-hintbody"><span class="hint">展开后自动检索教材切片</span></div>
        </details>
        <div class="grades3">
          <button class="g3 forget" data-id="${esc(c.id)}" onclick="rvGrade3(this,'forget')" title="忘了——按 0/5 排期（快捷键 1）">忘了</button>
          <button class="g3 fuzzy" data-id="${esc(c.id)}" onclick="rvGrade3(this,'fuzzy')" title="模糊——按 2/5 排期（快捷键 2）">模糊</button>
          <button class="g3 got" data-id="${esc(c.id)}" onclick="rvGrade3(this,'got')" title="记住——按 4/5 排期（快捷键 3）">记住</button>
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
/* 三按钮评级：按映射质量走原 rvGrade 管线；
   A-12：不再「先删卡再调 API」——API 成功后由 loadReviewCtx 重渲自然移除；
   失败时卡片保留（按钮恢复可重试），R4-19 的「失败重渲恢复」仅作兜底不再常态触发 */
async function rvGrade3(cidOrBtn, key) {
  // RV1：双签名——内联按钮传 this（data-id），键盘/程序化调用传 (cid, key)
  const cid = cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset
    ? (cidOrBtn.dataset.id || "") : cidOrBtn;
  const q = GRADE3_MAP[key];
  await rvGrade(cid, q, { forget: "忘了", fuzzy: "模糊", got: "记住" }[key]);
}
window.rvGrade3 = rvGrade3;
/* 键盘 1/2/3：刷题 tab 下对当前卡（已翻面优先）执行 忘了/模糊/记住。
   D-10：未翻面卡仅翻面不评分——避免误触给首卡打 0 分（污染排期+掌握度）。
   A-15：命名函数 + showTab 配对绑定/移除（进入刷题页挂、离开卸），
   不再常驻 window（避免在其他视图/子视图残留监听）。 */
function rvStudyKeys(e) {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey || !(e.key >= "1" && e.key <= "3")) return;
  if (!$("tab-study") || !$("tab-study").classList.contains("show")) return;
  const card = document.querySelector("#rv_body .qcard.flipped") || document.querySelector("#rv_body .qcard")
            || document.querySelector("#mem_area .qcard.flipped") || document.querySelector("#mem_area .qcard");
  if (!card) return;
  e.preventDefault();
  if (!card.classList.contains("flipped")) {
    card.classList.add("flipped");   // 未翻面：只翻面，不评分
    return;
  }
  // RV1 附带修正：#mem_area 的记忆卡此前误走 rvGrade3（SM-2 复习端点）→ 必报错；
  // 按卡片所属容器分流——记忆卡走 memGrade3（FSRS 端点），复习卡走 rvGrade3。
  const inMem = card.closest("#mem_area");
  (inMem ? memGrade3 : rvGrade3)(card.dataset.card, ["forget", "fuzzy", "got"][+e.key - 1]);
}
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
  // RV1：kp/subject 改经 data-kp/data-subject 属性传入（同 expHint 范式，不做内联 JS 拼参）
  if (det && typeof det === "object" && det.dataset) {
    kpName = det.dataset.kp || "";
    subject = det.dataset.subject || "";
  }
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
        <button class="mini-btn primary" data-kp="${esc(kpName)}" data-subject="${esc(subject)}" onclick="rvHintGen(this)">结合网络与模型知识生成提示</button>
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
  // RV1：双签名——内联按钮传 this（data-kp/data-subject），程序化调用传 (btn, kp, subject)
  if (btn && typeof btn === "object" && btn.dataset) {
    kpName = btn.dataset.kp || "";
    subject = btn.dataset.subject || "";
  }
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
/* R3-03：自评/铺卡防重入——双击不再对同一张卡连发两次 grade（SM-2/FSRS 双计、排期错乱） */
const gradeBusy = new Set();
let queueBusy = false;
async function rvGrade(cidOrBtn, q, label = null) {
  // RV1：双签名——内联按钮传 this（data-id/data-q），程序化调用（rvGrade3/快捷键）传 (cid, q)
  let cid = cidOrBtn;
  if (cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset) {
    cid = cidOrBtn.dataset.id || "";
    q = Number(cidOrBtn.dataset.q);
  }
  if (gradeBusy.has(cid)) return;   // 该卡评分在途 → 忽略重复点击
  gradeBusy.add(cid);
  const cardEl = document.querySelector('.qcard[data-card="' + CSS.escape(cid) + '"]');
  if (cardEl) cardEl.querySelectorAll("button").forEach(b => { b.disabled = true; });
  try {
    await api("/api/library/review/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cid, quality: q }),
    });
    toast(label ? `已记录「${label}」（${q}/5），卡片已按 SM-2 排入下次复习`
                : `已记录 ${q}/5 分，卡片已按 SM-2 排入下次复习`);
    await Promise.all([loadReviewCtx(rvSubject), loadLibrary()]);
  } catch (e) {
    toast(e.message, false);
    if (cardEl && document.body.contains(cardEl)) {
      cardEl.querySelectorAll("button").forEach(b => { b.disabled = false; });
    } else {
      // R4-19：卡片已被「出卡动效」移除 → 失败时重渲恢复（否则本次会话卡片消失且从未判分）
      loadReviewCtx(rvSubject).catch(() => {});
    }
  } finally { gradeBusy.delete(cid); }
}
async function rvQueueAll() {
  if (queueBusy) return;   // R3-03：铺卡防重入（连点不再重复入队）
  queueBusy = true;
  try {
    const r = await api("/api/library/review/queue-all?subject=" + encodeURIComponent(rvSubject), { method: "POST" });   // U-02：后端为 POST 路由，缺 method 会 405
    toast(r.added ? `已入队 ${r.added} 张薄弱卡片` : "没有新的薄弱知识点需要入队");
    loadReviewCtx(rvSubject);
  } catch (e) { toast(e.message, false); }
  finally { queueBusy = false; }
}
async function rvDel(cidOrBtn) {
  // RV1：双签名——内联按钮传 this（data-id），程序化调用传 cid
  const cid = cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset
    ? (cidOrBtn.dataset.id || "") : cidOrBtn;
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
  if (!(typeof FEATURES !== "undefined" && FEATURES.cards)) { sec.innerHTML = ""; return; }
  try {
    const r = await api("/api/library/cards?subject=" + encodeURIComponent(rvSubject) + "&due=1");
    const cards = r.cards || [];
    const st = r.stats || {};
    sec.innerHTML = `<div class="cardh" style="margin-top:6px"><h3 style="margin:0">🧠 医学记忆卡</h3>
      <span class="hint">讲解产物自动沉淀 · 总 ${st.total || 0} / 今日到期 ${st.due || 0}</span>
      <span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <button class="act gray mini" style="padding:4px 10px" onclick="expCardsHint()">+ 生成记忆卡</button>
        <button class="act gray mini" style="padding:4px 10px" onclick="memExportApkg(this)">导出 Anki（.apkg）</button>
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
          <button class="rv-x" title="删除记忆卡" data-id="${esc(c.id)}" onclick="memDel(this)">×</button></div>
        <div class="rv-q">${esc(c.front)}</div>
        <div class="rv-meta">${esc((c.kp_name || "") + (c.sched ? " · " + c.sched.toUpperCase() : ""))}
          · 下次 ${esc(String(c.due || "").slice(0, 10))} · 背 ${c.reps || 0} 次 · 忘 ${c.lapses || 0} 次</div>
        <div class="qcard-tip">💡 先在脑中回忆，再点卡片翻面看答案</div>
      </div>
      <div class="qcard-face qback">
        <div class="rv-slice" style="margin:6px 0">${hlKw(c.back)}</div>
        <div class="grades3">
          <button class="g3 forget" data-id="${esc(c.id)}" onclick="memGrade3(this,'forget')" title="忘了——重来（快捷键 1）">忘了</button>
          <button class="g3 fuzzy" data-id="${esc(c.id)}" onclick="memGrade3(this,'fuzzy')" title="模糊——困难（快捷键 2）">模糊</button>
          <button class="g3 got" data-id="${esc(c.id)}" onclick="memGrade3(this,'got')" title="记住——良好（快捷键 3）">记住</button>
        </div>
        <details class="rv-grades-detail"><summary class="hint">精确自评（FSRS 4 档）</summary>
          <div class="rv-grades">${grades.map(([t, q]) =>
            `<button class="rv-g${q}" data-id="${esc(c.id)}" data-q="${q}" onclick="memGrade(this)">${t}</button>`).join("")}</div>
          <div class="rv-legend hint">重来=遗忘 · 困难=回想吃力 · 良好=正常 · 简单=秒答（三按钮：忘≈重来0 · 糊≈困难2 · 记≈良好3）</div>
        </details>
      </div>
    </div>
  </div>`;
}
async function memGrade3(cidOrBtn, key) {
  // A-12：先成功后移除（API 成功 → loadReviewCtx 重渲移除；失败卡片保留可重试）
  // RV1：双签名——内联按钮传 this（data-id），键盘/程序化调用传 (cid, key)
  const cid = cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset
    ? (cidOrBtn.dataset.id || "") : cidOrBtn;
  await memGrade(cid, MEM_GRADE3[key], { forget: "忘了", fuzzy: "模糊", got: "记住" }[key]);
}
window.memGrade3 = memGrade3;
async function memGrade(cidOrBtn, q, label = null) {
  // RV1：双签名——内联按钮传 this（data-id/data-q），程序化调用传 (cid, q)
  let cid = cidOrBtn;
  if (cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset) {
    cid = cidOrBtn.dataset.id || "";
    q = Number(cidOrBtn.dataset.q);
  }
  if (gradeBusy.has(cid)) return;   // R3-03：记忆卡评分防重入
  gradeBusy.add(cid);
  const cardEl = document.querySelector('.memq[data-card="' + CSS.escape(cid) + '"]');
  if (cardEl) cardEl.querySelectorAll("button").forEach(b => { b.disabled = true; });
  try {
    await api("/api/library/cards/" + encodeURIComponent(cid) + "/grade", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality: q }) });
    toast(label ? `已记录「${label}」（${q}/5），记忆卡已排入下次复习`
                : `已记录自评 ${q}/5，记忆卡已排入下次复习`);
    loadReviewCtx(rvSubject);
  } catch (e) {
    toast(e.message, false);
    if (cardEl && document.body.contains(cardEl)) {
      cardEl.querySelectorAll("button").forEach(b => { b.disabled = false; });
    } else {
      // R4-19：记忆卡同款——被出卡动效移除后失败 → 重渲恢复
      loadReviewCtx(rvSubject).catch(() => {});
    }
  } finally { gradeBusy.delete(cid); }
}
async function memDel(cidOrBtn) {
  // RV1：双签名——内联按钮传 this（data-id），程序化调用传 cid
  const cid = cidOrBtn && typeof cidOrBtn === "object" && cidOrBtn.dataset
    ? (cidOrBtn.dataset.id || "") : cidOrBtn;
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
/* D15：记忆卡导出（.apkg 真包 / .txt 文本；空库时给可读提示）
   R3-24：导出前查 st.total（0 张直接提示）；请求期按钮禁用；fetch+blob 捕获错误；成功后提示张数 */
async function memExportApkg(btn) {
  try {
    if (btn) { btn.disabled = true; btn.textContent = "导出中…"; }
    const qs = rvSubject ? "?subject=" + encodeURIComponent(rvSubject) : "";
    const st = await api("/api/library/cards" + qs);
    const total = (st.stats && st.stats.total) || (st.cards || []).length;
    if (!total) { toast("暂无记忆卡可导出（先在「讲解与学习产物」生成记忆卡）", false); return; }
    const resp = await fetch("/api/library/cards/export/apkg" + qs);
    if (!resp.ok) {
      let msg = "导出失败（HTTP " + resp.status + "）";
      try { const j = await resp.json(); if (j.detail) msg = String(j.detail); } catch (err) { /* ignore */ }
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const cd = resp.headers.get("content-disposition") || "";
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    // S3-3（R8+W）：服务端给的 content-disposition 只取 basename——
    // 原样赋给 a.download 时，含路径分隔符/控制字符的名字会被浏览器按原样使用（越界写/怪名）。
    a.download = m ? _baseName(decodeURIComponent(m[1])) : "MedKit记忆卡.apkg";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 500);
    toast(`已导出 ${total} 张记忆卡 .apkg（Anki 双击即可导入）`);
  } catch (e) { toast(e.message, false); }
  finally { if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = "导出 Anki（.apkg）"; } }
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
async function ankiPreview(pidOrBtn) {
  // RV1：双签名——内联按钮传 this（data-pid），程序化调用传 pid
  const pid = pidOrBtn && typeof pidOrBtn === "object" && pidOrBtn.dataset
    ? (pidOrBtn.dataset.pid || "") : pidOrBtn;
  try {
    const r = await api("/api/projects/" + encodeURIComponent(pid) + "/questions");
    const qs = (r.questions || []).slice(0, 3);
    if (!qs.length) { toast("项目中没有题目，先完成生成", false); return; }
    const cards = qs.map(q => {
      const stem = q.case_stem ? `【案例】${esc(q.case_stem)}<br>` : "";
      // V-15 附带修复：原为**局部硬编码的 6 位字母表**（"ABCDEF"）——最多只显示 6 个选项，
      // 而题库支持 A~J（10 个）。R3-16 立过「不应再存在 6 位硬编码字母表」的规矩，
      // 但该规矩的守卫只扫了 `review-desk.js`（本函数当时在 `learn.js`）→ 长期漏检。
      // 现改走全局 `letters()`（review-desk.js 定义，10 档）。
      const L = letters((q.options || []).length);
      const opts = (q.options || []).map((o, i) => `${L[i]}. ${esc(o)}`).join("<br>");
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
    modalOnCancel = null;   // A-新14：信息弹窗不携带取消回调（防 ESC 触发残留 onCancel）
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
  modalOnCancel = null;   // A-新14：信息弹窗不携带取消回调（防 ESC 触发残留 onCancel）
  $("modal_mask").style.display = "flex";
  $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; };
}
window.ankiHelp = ankiHelp;
