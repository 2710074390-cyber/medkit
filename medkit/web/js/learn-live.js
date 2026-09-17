/* exported LEARN_STATE_ORDER, TUTOR_QTYPES, TUTOR_STATES, a, abort, at, bb, blob, bottom, box, btn, c, cardN, cells, cny, color, conversationHTML, ct, cur, d, downloadText, eid, estLlmCost, expCards, expCardsBusy, expCopy, expDel, expExport, expFold, expGenerate, expMd, expRegen, fillExpKp, fillTutorKp, html, i, id, idx, inline, isSepRow, isTableRow, keep, kps, lbl, line, lines, list, live, loadExplainCtx, loadExplains, loadTutorCtx, loadTutorSessions, note, old, open, opt, path, payload, prevAnswer, price, prov, question, r, raw, recs, renderTutorSide, res, rounds, s, sessionItem, streamed, subject, subs, ta, target, text, tutorChip, tutorCleanup, tutorDel, tutorExit, tutorResume, tutorShowConversation, tutorStart, tutorState, tutorStatePath, tutorSubmit, url, useWeb, v */
/* ---- M3：讲解与学习产物（教材切片 + 联网补充 + 产物管理） ---- */
const LEARN_STATE_ORDER = { weak: 0, shaky: 1, solid: 2, mastered: 3 };
/* C2：讲解 Markdown 渲染——在标题/列表/引用基础上补 GFM 表格与围栏代码块（医学对比表/数值表可读） */
function expMd(md) {
  if (window.mdRender) return mdRender(md);   // WP-9：统一本地富文本渲染（md.js）
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
        <div class="exp-fold" data-id="${esc(e.id)}" onclick="expFold(this)" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
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
          ${(typeof FEATURES !== "undefined" && FEATURES.cards) ? `<button class="mini-btn" onclick="expCards(this)" data-eid="${esc(e.id)}" data-subject="${esc(e.subject || "")}">🧠 生成记忆卡</button>` : ""}
          <button class="mini-btn primary" onclick="expRegen(this)" data-id="${esc(e.id)}" data-subject="${esc(e.subject || "")}" data-kp="${esc(e.kp_name || "")}">↻ 重新生成</button>
          <button class="mini-btn" data-id="${esc(e.id)}" onclick="expCopy(this)">复制</button>
          <button class="mini-btn danger" data-id="${esc(e.id)}" onclick="expDel(this)">删除</button>
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
  const payload = { subject, kp_name: opt.value, kp_id: opt.dataset.id || "", use_web: useWeb };
  $("exp_cost").textContent = (useWeb ? "正在检索教材切片（不足时联网补充）并流式精讲… " : "正在结合教材切片流式精讲… ") + "｜ " + estLlmCost(useWeb ? 2.2 : 1.4, 0.35);
  const live = $("exp_live");
  if (live) { live.style.display = "block"; live.innerHTML = '<div class="hint"><span class="spin"></span>连接流式接口…</div>'; }
  let streamed = false;
  try {
    // R4-02：AbortController +「停止生成」按钮；停止点触发 abort() → fetch 抛 AbortError
    // 注意顺序：sseStopUI 先清本入口残留（sseAbort）——必须【先清后挂】，
    // 否则新建的 controller 会被自己立即 abort（fetch 未发出即 AbortError）。
    const abort = new AbortController();
    sseStopUI("btn_exp_gen");
    sseAborts.set("btn_exp_gen", abort);   // A-16：按入口登记（多入口并发生成互不误杀）
    const res = await fetch("/api/library/explain/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: abort.signal });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("text/event-stream")) throw new Error("stream-unavailable");
    streamed = true;
    let text = "", done = false;
    // A-08：内层不再 finally 清 UI——按钮/controller 清理统一在外层 finally
    await consumeSSE(res, (ev, data) => {
        if (ev === "delta") {
          text += data.text || "";
          if (live) { live.innerHTML = expMd(text) + '<span class="caret"></span>'; live.scrollTop = live.scrollHeight; }
        } else if (ev === "done") {
          done = true;
          const v = data.explain && data.explain.grounded === false
            ? "未命中教材原文 · 网络+模型知识生成"
            : (data.explain && data.explain.via_web ? "含联网补充" : "基于教材切片");
          $("exp_cost").textContent = `已生成：《${data.title}》· ${v}`;
          toast("讲解已生成并沉淀到复习手册");
          loadExplains();
          invalidateLearnCache();
          refreshOverviewIfAny();
        } else if (ev === "error") {
          done = true;
          $("exp_cost").textContent = "流式生成失败：" + (data.msg || "");
          toast(data.msg || "流式生成失败", false);
        } else if (ev === "canceled") {
          done = true; $("exp_cost").textContent = "已停止生成（未保存）";
        }
      });
    if (!done && !text) { $("exp_cost").textContent = "流式接口未返回内容"; }
  } catch (e) {
    // R4-03：断流/取消/出错一律【不再】回退非流式接口，避免与流式并发导致二次扣费；
    // 仅当流式接口本身不可用（streamed 尚未置位，如旧代理剥掉 SSE）才降级非流式。
    if (e.name === "AbortError") {
      $("exp_cost").textContent = "已停止生成（未保存）";
    } else if (streamed) {
      $("exp_cost").textContent = "流式生成失败：" + (e.message || "未知错误");
    } else {
      try {
        const r = await api("/api/library/explain", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
        toast("讲解已生成并沉淀到复习手册");
        const v = r.explain && r.explain.grounded === false
          ? "未命中教材原文 · 网络+模型知识生成"
          : (r.explain && r.explain.via_web ? "含联网补充" : "基于教材切片");
        $("exp_cost").textContent = `已生成：《${r.title}》· ${v}`;
        loadExplains();
        invalidateLearnCache();
        refreshOverviewIfAny();
      } catch (e2) { toast(e2.message, false); $("exp_cost").textContent = ""; }
    }
  } finally {
    sseAbortAll();   // A-08：统一清理（按钮 + 在途 controller）
    btn.textContent = old; btn.disabled = false; if (live) live.style.display = "none";
  }
}
async function expExport() {
  const subject = $("exp_subject").value;
  try {
    const r = await api("/api/library/explains/export?subject=" + encodeURIComponent(subject), { method: "POST" });
    downloadText((subject || "全部科目") + "-复习手册.md", r.markdown);
    toast("复习手册已导出");
  } catch (e) { toast(e.message, false); }
}
async function expCopy(idOrBtn) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id（此时无按钮反馈，仅 toast）
  const btn = idOrBtn && idOrBtn.dataset ? idOrBtn : null;
  const id = btn ? (btn.dataset.id || "") : idOrBtn;
  try { const r = await api("/api/library/explains/" + id); copyText(r.explain.content || "", btn);
        if (!btn) toast("已复制讲解全文"); }
  catch (e) { toast(e.message, false); }
}
/* 折叠/展开讲解产物全文 */
function expFold(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
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
/* WP-05/NX-04：讲解产物 → 医学记忆卡（flag = cards 前端同步隐藏按钮）
   D-21：busy 禁用防连点（与 gradeBusy 同风格）——生成期间按钮禁用，避免重复调用 */
const expCardsBusy = new Set();
async function expCards(btnOrEid, subject) {
  let eid = btnOrEid, btn = null;
  if (btnOrEid && typeof btnOrEid === "object" && btnOrEid.dataset) {
    btn = btnOrEid; eid = btn.dataset.eid || ""; subject = btn.dataset.subject || "";
  }
  if (!eid || expCardsBusy.has(eid)) return;
  expCardsBusy.add(eid);
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  try {
    const r = await api("/api/library/cards/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ explain_id: eid }) });
    toast(r.added ? `已生成 ${r.added} 张医学记忆卡${r.skipped ? `（跳过重复/无效 ${r.skipped} 张）` : ""}（复习计划「🧠 医学记忆卡」可见）`
                  : `记忆卡已存在（幂等，未新增${r.skipped ? `；跳过重复/无效 ${r.skipped} 张` : ""}）`);
    // C19：用「生成卡时的讲解科目」刷新（复习视图过滤可能不含新卡 → 切到对应科目可见）
    const target = subject || rvSubject;
    if (typeof loadReviewCtx === "function") loadReviewCtx(target);
    if (rvSubject && subject && rvSubject !== subject) {
      toast(`记忆卡科目「${subject}」——复习过滤已切到该科目查看`, false);
    }
  } catch (e) { toast(e.message, false); }
  finally {
    expCardsBusy.delete(eid);
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = "🧠 生成记忆卡"; }
  }
}
window.expCards = expCards;
async function expDel(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
  // D-05：删除讲解会级联删除派生记忆卡——确认文案先列出数量（不静默不可恢复消失）
  let cardN = 0;
  try {
    const c = await api("/api/library/cards");
    cardN = (c.cards || []).filter(x => String(x.source || "") === String(id)).length;
  } catch (e) { /* 查询失败不阻断删除流程 */ }
  confirmModal("删除讲解产物", `<p style="margin:0;color:var(--dim)">确定删除这篇讲解吗？删除后不可恢复。</p>`
    + (cardN ? `<p style="margin:6px 0 0;color:var(--warn)">⚠️ 将<b>同时删除</b>由它生成的 <b>${cardN}</b> 张医学记忆卡（不可恢复）。</p>` : ""),
    "删除", async () => {
      try {
        const r = await api("/api/library/explains/" + id, { method: "DELETE" });
        toast("已删除" + (r.cards_removed ? `（含 ${r.cards_removed} 张派生记忆卡）` : ""));
        loadExplains();
      }
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
/* U-17：删除孤儿函数 tutorRowCore（ESLint no-unused-vars 实测全仓无调用者）*/
async function loadTutorCtx(preserveSubject) {
  try {
    const [subj] = await Promise.all([cachedSubjects(), cachedMastery()]);   // U-17：去掉未使用的 my 解构
    const subs = (subj.subjects || []).sort();
    $("tu_subject").innerHTML = '<option value="">全部科目</option>' +
      subs.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    // R3-23：与 loadExplainCtx 同口径——重建下拉后保留原科目（quick-action「→ 提问」不再重置）
    const keep = preserveSubject && subs.includes(preserveSubject) ? preserveSubject : "";
    if (keep) $("tu_subject").value = keep;
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
  // U-17：原此处计算了 lv（TUTOR_QTYPES[...]）但模板从未使用——已删除；
  // 若产品希望会话条目显示层级，需在下方模板中真正插入该值。
  const at = (s.updated_at || "").slice(5, 16).replace("T", " ");
  return `<div class="tu-item" data-id="${esc(s.id)}" onclick="tutorResume(this)">
    <div class="ti-name">${esc(s.kp_name || "未命名知识点")}</div>
    <div class="ti-meta">${esc(s.subject || "未知科目")} · ${s.rounds.length} 轮 · ${tutorChip(s.state)}
      <span style="margin-left:auto">${esc(at)}</span>
      <button class="ti-x" title="删除会话" data-id="${esc(s.id)}" onclick="event.stopPropagation();tutorDel(this)">×</button>
    </div></div>`;
}
function conversationHTML(s) {
  const path = tutorStatePath(s.state);
  const rounds = (s.rounds || []).map(r => `
    <div class="tu-q"><span class="tu-badge">MedTutor · ${TUTOR_QTYPES[r.type] || r.type}提问 · 第${r.round}轮</span>${window.mdRender ? mdRender(r.question) : esc(r.question)}</div>
    <div class="tu-a"><small>你 · 得分 <span class="tu-score" style="color:${r.score >= 2 ? "var(--good)" : "var(--bad)"}">${r.score}</span>/3</small>${esc(r.user_answer)}</div>
    ${r.gap ? `<div class="tu-gap">${window.mdRender ? mdRender(r.gap) : esc(r.gap)}</div>` : ""}`).join("");
  let bottom;
  const cur = s.current || { type: "explain", text: "" };
  if (cur.text) {
    bottom = `<div class="tu-q tu-next"><span class="tu-badge">MedTutor · ${TUTOR_QTYPES[cur.type] || cur.type}提问</span>${window.mdRender ? mdRender(cur.text) : esc(cur.text)}</div>
      <div class="tu-inputbar">
        <textarea id="tu_answer" placeholder="在文本框里作答…（写不下可先答要点，MedTutor 会追问细节）"></textarea>
        <button class="act" onclick="tutorSubmit()">提交作答</button>
      </div>`;
  } else if ((s.rounds || []).length === 0) {
    // D-04：无问题且无轮次 = 第一问生成失败残留会话（旧数据）——明确提示，不再伪装「已达成掌握目标」
    bottom = `<div class="tu-gap">第一问生成失败，请重试——可删除本场会话后重新「开始提问」。</div>`;
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
  $("tutor_cost").textContent = "正在结合教材切片流式生成第一问（按次记账）… ｜ " + estLlmCost(0.8, 0.05);
  const live = $("tu_live");
  if (live) { live.style.display = "block"; live.innerHTML = '<div class="hint"><span class="spin"></span>连接流式接口…</div>'; }
  const payload = { subject, kp_name: opt.value, kp_id: opt.dataset.id || "" };
  let streamed = false;
  try {
    // R4-02：AbortController +「停止生成」按钮；停止→abort()→fetch 抛 AbortError（会话由后端兜底撤销）
    // 顺序同 expGenerate：sseStopUI 先清本入口残留，再挂新 controller（A-16 按入口登记）。
    const abort = new AbortController();
    sseStopUI("btn_tu_start");
    sseAborts.set("btn_tu_start", abort);
    const res = await fetch("/api/library/tutor/start/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: abort.signal });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("text/event-stream")) throw new Error("stream-unavailable");
    streamed = true;
    let question = "", done = false;
    // A-08：清理统一在外层 finally
    await consumeSSE(res, (ev, data) => {
        if (ev === "delta") { question += data.text || ""; if (live) live.textContent = question; }
        else if (ev === "done") {
          done = true;
          tutorState.active = data.session.id;
          toast("已开启一场苏格拉底式对话");
          $("tutor_cost").textContent = data.grounded === false
            ? "第一问已就绪（⚠️ 未命中教材原文，基于网络素材与模型知识）——请作答。"
            : "第一问已就绪，请作答。";
          loadTutorSessions().then(() => tutorShowConversation());
        } else if (ev === "error") {
          done = true; $("tutor_cost").textContent = ""; toast(data.msg || "第一问生成失败", false);
        } else if (ev === "canceled") {
          done = true; $("tutor_cost").textContent = "已停止出题（会话已撤销）";
        }
      });
    if (!done && !question) { $("tutor_cost").textContent = "流式接口未返回内容"; }
  } catch (e) {
    // R4-03：断流/取消/出错【不再】回退非流式起点，避免二次扣费；仅流式接口本身不可用才回退
    if (e.name === "AbortError") {
      $("tutor_cost").textContent = "已停止出题（会话已撤销）";
    } else if (streamed) {
      $("tutor_cost").textContent = "第一问生成失败：" + (e.message || "未知错误");
    } else {
      try {
        const r = await api("/api/library/tutor/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload) });
        tutorState.active = r.session.id;
        await loadTutorSessions();
        toast("已开启一场苏格拉底式对话");
        $("tutor_cost").textContent = r.grounded === false
          ? "第一问已就绪（⚠️ 未命中教材原文，基于网络素材与模型知识）——请作答。"
          : "第一问已就绪，请作答。";
        tutorShowConversation();
      } catch (e2) { toast(e2.message, false); $("tutor_cost").textContent = ""; }
    }
  } finally {
    sseAbortAll();   // A-08：统一清理（按钮 + 在途 controller）
    btn.textContent = old; btn.disabled = false; tutorState.busy = false; if (live) live.style.display = "none";
  }
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
    // D-26：重渲染前保存用户刚输入的作答，retry 时渲染后回填（允许在原答案上修改）
    const prevAnswer = $("tu_answer") ? $("tu_answer").value : "";
    // C3：LLM 判分失败返回 retry=true（score<0 不计分）——不渲染负数分数，改为弱提示重答
    if (r.done) {
      // D-20：已达轮次上限（后端不调 LLM 直接返回提示）
      $("tutor_cost").textContent = r.note || "已达本轮轮次上限，感谢练习——可另开一场会话继续";
    } else if (r.retry) {
      $("tutor_cost").textContent = "本轮未完成判分（模型未给分）——请围绕考点再作答一次";
    } else {
      const note = r.grounded === false ? "（未命中教材原文 · 网络+模型知识）" : "";
      $("tutor_cost").textContent = `本轮得分 ${r.score}/3${note}` + (r.gap ? ` —— ${r.gap}` : "");
    }
    tutorShowConversation();
    if (r.retry && prevAnswer) { const nt = $("tu_answer"); if (nt) nt.value = prevAnswer; }
    invalidateLearnCache();   // 判分回写掌握度 → 失效学习中心缓存，概览到手最新值
    // C12：提问判分后概览诊断同步刷新（掌握度/优先级可能已变化）
    refreshOverviewIfAny();
  } catch (e) { toast(e.message, false); $("tutor_cost").textContent = ""; }
  finally { if (btn) { btn.textContent = old; btn.disabled = false; } }
}
async function tutorResume(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
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
function tutorDel(idOrEl) {
  // RV1：双签名——内联传 this（data-id），程序化调用传 id
  const id = idOrEl && idOrEl.dataset ? (idOrEl.dataset.id || "") : idOrEl;
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
