/* exported BLOOMS, L, TAB_KEYS, a, answerIssue, any, applyReviewFilter, b, b2, bad, bar, body, box, box_append, btn, c, card, checkAns, cid, clean, cnt, cur, d, diffPrompt, dropBtn, dropped, e, ed, edits, el, err, f, filtered, first, gg, gix, goDemo, goOwn, groups, hd, html, id, ids, k, keep, kept, la, list, loadPrompts, lts, mark, maybeShowWizard, missing, n, nowDropped, o, okB, okQ, okT, okY, opList, openPromptEdit, openReview, optSrc, opts, optsSrc, ph, pre, prevDrop, prevEdits, promptCache, promptContainer, promptStatusBadge, provs, q, qid, qs, r, renderPrompt, renderReview, restorePrompt, revFilterText, revHideKey, revSaving, reviewDirtyGuard, reviewState, rr, sa, savePrompt, segResizeT, setDrop, show, sib, t, ta, tags, target, text, tk, updBatch, updateRevCount, v, visChecks, visIds, visible, wzClose, wzDone, wzRender, wzSetDots, wzStep */
/* ---- 迭代4：逐题审核台 */
$("btn_review").onclick = () => openReview();
let reviewState = { questions: [], keep: null, drop: new Set(), edits: {}, dirty: false,
                    select: new Set(), hideDropped: false,
                    filter: { q: "", type: "", bloom: "", year: "" } };
let revSaving = false;   // A-03：保存中标志（跨越 openReview 重渲染窗口，防双击双保存）
/* A-06：「隐藏已剔除」视图偏好——随 pid 持久化（刷新/重开不再回弹），不入 dirty（不影响产物） */
function revHideKey() { return "medkit-rev-hide-" + (currentPid || "__new__"); }
/* 审核台脏状态守卫：切主 tab / 换项目 / 刷新关闭都要确认（防未保存修改静默丢失） */
function reviewDirtyGuard() {
  if (reviewState.dirty && !confirm("审核台有未保存的修改（剔除/编辑/重掷尚未保存），确定离开？修改将丢失。")) return false;
  return true;
}
window.addEventListener("beforeunload", (e) => {
  if (reviewState.dirty) { e.preventDefault(); e.returnValue = ""; }
});
async function openReview(preserve = false, scrollToId = null) {
  if (!currentPid) return;
  $("review_panel").style.display = "block";
  const r = await api("/api/projects/" + currentPid + "/questions");
  const prevEdits = preserve ? reviewState.edits : {};
  const prevDrop = preserve ? reviewState.drop : new Set();
  // 状态保留：只保留仍存在的题目 id（重掷/重渲染后原 id 可能变化）
  const ids = new Set(r.questions.map(x => x.id));
  reviewState.edits = Object.fromEntries(Object.entries(prevEdits).filter(([k]) => ids.has(k)));
  reviewState.drop = new Set([...prevDrop].filter(x => ids.has(x)));
  reviewState.questions = r.questions;
  reviewState.dirty = Object.keys(reviewState.edits).length > 0 || reviewState.drop.size > 0;
  renderReview(scrollToId);
}
function updateRevCount() {
  const el = $("rev_title");
  if (!el) return;
  const qs = reviewState.questions;
  const kept = qs.filter(q => !reviewState.drop.has(q.id)).length;
  el.textContent = `逐题审核（保留 ${kept} / 共 ${qs.length} 题）`;
}
function revFilterText(q) {
  const ed = reviewState.edits[q.id] || {};
  return ((q.id || "") + " " + (q.type || "") + " " + (q.bloom || "") + " " + (q.subtopic || "")
    + " " + (q.source_type || "") + " " + String(q.source_year || "")
    + " " + (ed.question ?? q.question ?? "")).toLowerCase();
}
function applyReviewFilter() {
  const f = reviewState.filter;
  const list = $("rev_list");
  if (!list) return;
  let visible = 0;
  [...list.children].forEach(el => {
    if (el.classList.contains("casehead")) return;
    const qid = el.dataset.qid;
    const q = reviewState.questions.find(x => x.id === qid);
    if (!q) { el.style.display = "none"; return; }
    const okT = !f.type || q.type === f.type;
    const okB = !f.bloom || q.bloom === f.bloom;
    const okQ = !f.q || revFilterText(q).includes(f.q);
    const okY = !f.year || (q.source_year || "") === f.year;
    const show = okT && okB && okQ && okY;
    el.style.display = show ? "" : "none";
    if (show) visible++;
  });
  // 组头：组内无可见题目时隐藏
  [...list.children].forEach(el => {
    if (!el.classList.contains("casehead")) return;
    let any = false, sib = el.nextElementSibling;
    while (sib && !sib.classList.contains("casehead")) {
      if (sib.style.display !== "none") { any = true; break; }
      sib = sib.nextElementSibling;
    }
    el.style.display = any ? "" : "none";
  });
  const cnt = $("rev_filter_cnt");
  if (cnt) cnt.textContent = `筛选后 ${visible} / ${reviewState.questions.length} 题`;
}
/* B10/C-11：答案键校验（与后端 R0 口径一致；先归一化第三口径再判非法键；
   A1/A2/A3/A4/B1 单字母，X 型≥2 字母且不重复；R3-06：无 4 选项地板，按实际选项数） */
function answerIssue(type, ans, optCount) {
  const a = normAnswer(ans);
  const lts = letters(optCount);
  if (!a) return "答案键不能为空";
  if (type === "X") {
    if (a.length < 2) return "X 型答案至少 2 个字母（当前「" + a + "」）";
    if (new Set(a).size !== a.length) return "答案键有重复字母";
  } else if (a.length !== 1) return "单选/案例题答案应为单字母（当前「" + a + "」）";
  if ([...a].some(c => lts.indexOf(c) < 0)) return "含选项字母范围外字符（选项 A~" + (lts.slice(-1) || "") + "）";
  return "";
}
function renderReview(scrollToId = null) {
  const box = $("review_panel");
  const qs = reviewState.questions;
  const kept = qs.filter(q => !reviewState.drop.has(q.id)).length;
  const BLOOMS = ["", "记忆", "理解", "应用", "创造"];
  box.innerHTML = `<div class="card" style="margin-top:14px">
    <div class="cardh"><h2 id="rev_title">逐题审核（保留 ${kept} / 共 ${qs.length} 题）</h2>
      <span class="hint" id="rev_filter_cnt"></span></div>
    <div class="hint">✓ 默认保留 · ✗ 剔除（可反悔）· 行内编辑 · 单题重掷（约 30 秒，消耗一次调用）· 完成后点「保存并重渲染」</div>
    <div class="revtool">
      <input type="search" id="rev_search" placeholder="🔍 搜索题号/类型/考点/题干…" value="${esc(reviewState.filter.q)}">
      <select id="rev_ftype"><option value="">全部题型</option>
        ${["A1", "A2", "B1", "X", "A3", "A4"].map(t => `<option value="${t}" ${reviewState.filter.type === t ? "selected" : ""}>${t}</option>`).join("")}
      </select>
      <select id="rev_fbloom"><option value="">全部层级</option>
        ${BLOOMS.slice(1).map(b => `<option value="${b}" ${reviewState.filter.bloom === b ? "selected" : ""}>${b}</option>`).join("")}
      </select>
      <select id="rev_fyear" title="按真题年份过滤"><option value="">全部年份</option>
        ${[...new Set(qs.map(q => String(q.source_year || "").slice(0, 4)))]
          .filter(y => y)
          .sort().reverse()
          .map(y => `<option value="${esc(y)}" ${reviewState.filter.year === y ? "selected" : ""}>${esc(y)} 年</option>`).join("")}
      </select>
      <span id="rev_batch" style="display:none;align-items:center;gap:6px">
        <span class="hint" id="rev_batch_n" style="margin:0"></span>
        <select id="rev_bb" style="min-width:96px"><option value="">Bloom →</option>
          <option>记忆</option><option>理解</option><option>应用</option><option>创造</option></select>
        <button class="act gray" id="rev_bb_apply" style="padding:8px 12px;font-size:12.5px">应用</button>
        <button class="act gray" id="rev_drop_sel" style="padding:8px 12px;font-size:12.5px;color:#f87171">批量剔除</button>
        <button class="act gray" id="rev_restore_sel" style="padding:8px 12px;font-size:12.5px;color:var(--good)">批量恢复</button>
        <button class="act gray" id="rev_sel_vis" style="padding:8px 12px;font-size:12.5px">全选可见</button>
        <button class="act gray" id="rev_sel_inv" style="padding:8px 12px;font-size:12.5px">反选</button>
        <button class="act gray" id="rev_unsel" style="padding:8px 12px;font-size:12.5px">取消选择</button>
      </span>
      <button class="act gray" id="rev_keepall" style="padding:8px 14px;font-size:12.5px">全部保留</button>
      <button class="act gray" id="rev_dropall" style="padding:8px 14px;font-size:12.5px;color:#f87171">全部剔除</button>
      <button class="act" id="rev_save" style="margin-left:auto">保存并重渲染</button>
      <button class="act gray" id="rev_refresh">刷新</button>
      <button class="act gray" id="rev_hide">隐藏已剔除</button>
    </div>
    <div id="rev_list"></div>
  </div>`;
  const list = $("rev_list");
  // A-06：恢复本项目的「隐藏已剔除」偏好（刷新/重开持久化；默认未隐藏）
  try { reviewState.hideDropped = localStorage.getItem(revHideKey()) === "1"; }
  catch (e) { /* ignore */ }
  if (reviewState.hideDropped) list.classList.add("hide-dropped");
  $("rev_hide").textContent = reviewState.hideDropped ? "显示已剔除" : "隐藏已剔除";
  $("rev_hide").onclick = () => {
    reviewState.hideDropped = list.classList.toggle("hide-dropped");
    $("rev_hide").textContent = reviewState.hideDropped ? "显示已剔除" : "隐藏已剔除";
    try { localStorage.setItem(revHideKey(), reviewState.hideDropped ? "1" : "0"); } catch (e) { /* ignore */ }
  };
  $("rev_refresh").onclick = () => {
    if (reviewState.dirty) {
      confirmModal("刷新将丢弃未保存的修改", `<p style="margin:0;color:var(--dim)">当前有未保存的剔除/编辑，刷新会将其丢弃（产物未变）。继续刷新？</p>`,
        "刷新", () => openReview());
    } else openReview();
  };
  $("rev_search").oninput = e => { reviewState.filter.q = e.target.value.trim().toLowerCase(); applyReviewFilter(); };
  $("rev_ftype").onchange = e => { reviewState.filter.type = e.target.value; applyReviewFilter(); };
  $("rev_fbloom").onchange = e => { reviewState.filter.bloom = e.target.value; applyReviewFilter(); };
  $("rev_fyear").onchange = e => { reviewState.filter.year = e.target.value; applyReviewFilter(); };
  $("rev_keepall").onclick = () => {
    reviewState.drop.clear();
    reviewState.dirty = true;
    document.querySelectorAll(".revq").forEach(d => {
      d.classList.remove("dropped");
      d.querySelector("[data-a=drop]").textContent = "✗ 剔除";
    });
    updateRevCount(); $("rev_save").disabled = false;
    toast("已全部保留");
  };
  $("rev_dropall").onclick = () => {
    const f = reviewState.filter;
    const filtered = !!(f.type || f.bloom || f.q);
    // 有筛选时「全部剔除」只作用于当前可见（筛选结果），防误删被隐藏的题
    const visIds = filtered
      ? [...document.querySelectorAll(".revq")].filter(d => d.style.display !== "none").map(d => d.dataset.qid)
      : null;
    const target = visIds && visIds.length ? visIds : qs.map(q => q.id);
    confirmModal(filtered ? "剔除筛选结果？" : "全部剔除？",
      `<p style="margin:0;color:var(--dim)">${filtered
        ? `将把当前筛选后可见的 <b>${target.length} 道题</b>标记为剔除（共 ${qs.length} 题；可逐题恢复后保存）。确定继续？`
        : `将把<b>全部 ${qs.length} 道题</b>标记为剔除（可逐题恢复后保存）。确定继续？`}</p>`,
      "剔除", () => {
        target.forEach(id => reviewState.drop.add(id));
        reviewState.dirty = true;
        document.querySelectorAll(".revq").forEach(d => {
          if (target.includes(d.dataset.qid)) {
            d.classList.add("dropped");
            d.querySelector("[data-a=drop]").textContent = "↩ 恢复";
          }
        });
        updateRevCount(); $("rev_save").disabled = false;
      });
  };
  /* S3：审核台组维度折叠（案例/选项组），子题仍可单独剔除/编辑/重掷 */
  const groups = [];
  const gix = new Map();
  qs.forEach(q => {
    const cid = q.case_id || "";
    if (cid && gix.has(cid)) { groups[gix.get(cid)].items.push(q); return; }
    if (cid) { gix.set(cid, groups.length); groups.push({ cid, stem: q.case_stem || "", items: [] }); }
    if (cid) { groups[gix.get(cid)].items.push(q); }
    else groups.push({ cid: null, items: [q] });
  });
  groups.forEach(g => {
    if (g.cid) {
      const hd = document.createElement("div");
      hd.className = "casehead";
      hd.dataset.cid = g.cid;
      hd.innerHTML = `案例 <b>${esc(g.cid)}</b> · ${g.items.length} 题 · <span class="hint">${esc((g.stem || "").slice(0, 50))}</span>`;
      hd.style.cursor = "pointer";
      hd.onclick = () => {
        let sib = hd.nextElementSibling;
        while (sib && !sib.classList.contains("casehead")) {
          sib.style.display = sib.style.display === "none" ? "" : "none";
          sib = sib.nextElementSibling;
        }
      };
      list.appendChild(hd);
    }
    g.items.forEach(q => {
    const dropped = reviewState.drop.has(q.id);
    const ed = reviewState.edits[q.id] || {};
    // B1 组题共享选项在 group.options（自身 options 为空）——与渲染层 _effective_options 同口径
    const optSrc = (qq, ee) => {
      const o = (ee && ee.options) || qq.options || [];
      if (o.length || qq.group_kind !== "option_group") return o;
      const gg = qq.group || {};
      return Array.isArray(gg.options) ? gg.options : [];
    };
    const opList = optSrc(q, ed);
    const d = document.createElement("div");
    d.className = "revq" + (dropped ? " dropped" : "");
    d.dataset.qid = q.id;
    d.innerHTML = `
      <div class="qhead">
        <input type="checkbox" class="revck" title="勾选以批量操作">
        <b>${esc(q.id)}</b><span class="tag">${esc(q.type)}</span><span class="tag">${esc(q.bloom)}</span>
        ${q.source_type === "真题" ? `<span class="tag" style="background:rgba(245,158,11,.15);color:var(--warn)">${esc((q.source_year ? String(q.source_year).slice(0, 4) + " " : "") + "真题")}</span>` : ""}
        <span class="hint">${esc(q.subtopic || "")}</span>
        <button class="inlineBtn revact" data-a="drop">${dropped ? "↩ 恢复" : "✗ 剔除"}</button>
        <button class="inlineBtn blue revact" data-a="edit">编辑</button>
        <button class="inlineBtn blue revact" data-a="regen">重掷</button>
        <button class="inlineBtn blue revact" data-a="copy" title="复制题面文本到剪贴板">复制</button>
      </div>
      <div class="qbody" data-f="body">${(q.image_ref || q.data_table)
        ? `<div class="hint" style="margin:0 0 6px">${q.image_ref ? `🖼 含图：${esc(q.image_ref)}（如图所示）` : ""}${q.data_table ? `<span class="tag">📋 含表格数据</span>` : ""}</div>` : ""}${esc(ed.question ?? q.question)}
        <ul>${opList.map((o, i) => `${letters(opList.length)[i]}. ${esc(o)}`).join("</li><li>")}</ul>
        <div class="hint good">✓ 答案：${esc(ed.answer ?? q.answer)} · ${esc((ed.analysis ?? q.analysis) || "")}</div>
      </div>
      <div class="optsrow" data-f="editrow" style="${ed._editOpen ? "" : "display:none"}">
        <select class="eb" data-e="type" style="max-width:96px" title="题型">
          <option value="">题型</option>
          ${["A1","A2","A3","A4","B1","X"].map(t => `<option value="${t}" ${(ed.type ?? q.type) === t ? "selected" : ""}>${t}</option>`).join("")}
        </select>
        <input class="eb" data-e="subtopic" placeholder="章节/知识点" value="${esc(ed.subtopic ?? q.subtopic ?? "")}" style="max-width:170px">
        <input class="eb" data-e="question" placeholder="题干" value="${esc(ed.question ?? q.question)}">
        <input class="eb" data-e="analysis" placeholder="解析" value="${esc((ed.analysis ?? q.analysis) || "")}">
        <input class="eb" data-e="answer" placeholder="答案键（如 B / BDE）" value="${esc(ed.answer ?? q.answer)}" style="max-width:120px">
        <input class="eb" data-e="bloom" placeholder="Bloom（记忆/理解/应用/创造）" value="${esc(ed.bloom ?? q.bloom)}" style="max-width:180px">
      </div>
      <div class="hint bad" data-f="anschk" style="display:none;margin:4px 0"></div>
      <div class="optsrow" data-f="editopts" style="${ed._editOpen ? "" : "display:none"}">
        ${q.group_kind === "option_group"
          ? `<div class="hint" style="margin:0 0 4px">共享选项（本组所有子题共用）——修改会同步整组</div>
             <input class="eb" data-e="groupoptions" placeholder="每行一个共享选项（所有子题共用）" value="${esc((ed.groupOptions || (q.group && q.group.options) || []).join("\n"))}">`
          : opList.map((o, i) =>
              `<input class="eb" data-e="opt${i}" placeholder="选项${letters(opList.length)[i]}" value="${esc(o)}">`).join("")}
      </div>`;
    d.querySelector("[data-a=drop]").onclick = () => {
      const dropBtn = d.querySelector("[data-a=drop]");
      if (reviewState.drop.has(q.id)) reviewState.drop.delete(q.id);
      else reviewState.drop.add(q.id);
      reviewState.dirty = true;
      const nowDropped = reviewState.drop.has(q.id);
      d.classList.toggle("dropped", nowDropped);
      dropBtn.textContent = nowDropped ? "↩ 恢复" : "✗ 剔除";
      updateRevCount();
      $("rev_save").disabled = false;
    };
    d.querySelector("[data-a=edit]").onclick = () => {
      const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
      e._editOpen = !e._editOpen;
      d.querySelector('[data-f="editrow"]').style.display = e._editOpen ? "" : "none";
      d.querySelector('[data-f="editopts"]').style.display = e._editOpen ? "" : "none";
    };
    d.querySelector("[data-a=copy]").onclick = () => {
      const e = reviewState.edits[q.id] || {};
      const optsSrc = (e.options ?? optSrc(q, null));
      const L = letters(optsSrc.length);   // R3-16：复制题面同样按实际选项数
      const opts = optsSrc.map((o, i) => `${L[i]}. ${o}`).join("\n");
      const text = `[${q.id}] ${q.type} · ${(e.bloom ?? q.bloom) || ""} · ${(e.subtopic ?? q.subtopic) || ""}\n`
        + `${(e.question ?? q.question) || ""}\n${opts}\n答案：${(e.answer ?? q.answer) || ""}\n解析：${(e.analysis ?? q.analysis) || ""}`;
      copyText(text, d.querySelector("[data-a=copy]"));
    };
    d.querySelector("[data-a=regen]").onclick = async () => {
      // B12：案例/选项组子题、图/表题重掷会破坏组结构或 image_ref——前端先拦截
      if (q.group_kind === "case" || q.group_kind === "option_group" || q.case_id || q.image_ref || q.data_table) {
        toast("该题属于案例/选项组或含图/表：重掷会破坏组结构或图题引用，请用「编辑」修改", false);
        return;
      }
      const btn = d.querySelector("[data-a=regen]");
      btn.disabled = true; btn.textContent = "重掷中…";
      try {
        const rr = await api("/api/projects/" + currentPid + "/regen", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: q.id }) });
        toast(rr.warning ? `已重掷 ${q.id} · ${rr.warning}` : `已重掷 ${q.id}`);   // C-16：新题含案例字段 → 提示
        // 保留其它题目的编辑/剔除状态；该题重掷后作废旧编辑
        delete reviewState.edits[q.id];
        reviewState.drop.delete(q.id);
        await openReview(true, q.id);
      } catch (e) { toast(e.message, false); btn.disabled = false; btn.textContent = "重掷"; }
    };
    /* B10：答案键按题型即时校验（A 型单字母 / X 型≥2 字母且均在选项范围内） */
    const checkAns = () => {
      const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
      const t = e.type || q.type || "";
      const a = e.answer !== undefined ? e.answer : q.answer;
      const n = e.options ? e.options.length : opList.length;
      const err = t ? answerIssue(t, a, n) : "";
      e._answerInvalid = err;
      const c = d.querySelector('[data-f="anschk"]');
      if (c) { c.style.display = err ? "" : "none"; c.textContent = err ? "✗ " + err : ""; }
    };
    d.querySelectorAll("[data-e]").forEach(inp => {
      inp.oninput = () => {
        const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
        const k = inp.dataset.e;
        if (k === "groupoptions") {
          // R3-11：共享选项入口 → 每行一项，写 group.options（保存时后端同步整组）
          e.groupOptions = inp.value.split("\n").map(x => x.trim()).filter(Boolean);
          e.options = e.groupOptions;
        } else if (k.startsWith("opt")) { e.options = (e.options || optSrc(q, null).slice()); e.options[+k.slice(3)] = inp.value; }
        else e[k] = inp.value;
        checkAns();
        reviewState.dirty = true;
        $("rev_save").disabled = false;
      };
      if (inp.tagName === "SELECT") inp.onchange = inp.oninput;
    });
    checkAns();
    list.appendChild(d);
    });
  });
  /* ---- 批量操作（勾选 → 改 Bloom / 批量剔除 / 批量恢复 / 取消选择）---- */
  reviewState.select = reviewState.select || new Set();
  const updBatch = () => {
    const bar = $("rev_batch");
    const n = reviewState.select.size;
    bar.style.display = n ? "flex" : "none";
    $("rev_batch_n").textContent = n ? `已选 ${n} 题 · ` : "";
  };
  const setDrop = (id, now) => {
    const card = document.querySelector(`.revq[data-qid="${CSS.escape(id)}"]`);
    if (!card) return;
    card.classList.toggle("dropped", now);
    const b = card.querySelector("[data-a=drop]");
    if (b) b.textContent = now ? "↩ 恢复" : "✗ 剔除";
  };
  document.querySelectorAll(".revq .revck").forEach(ck => {
    const qid = ck.closest(".revq").dataset.qid;
    ck.onchange = () => { if (ck.checked) reviewState.select.add(qid); else reviewState.select.delete(qid); updBatch(); };
  });
  $("rev_bb_apply").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    const v = $("rev_bb").value;
    if (!v) return toast("请先选择要应用的 Bloom 层级", false);
    reviewState.select.forEach(id => {
      reviewState.edits[id] = reviewState.edits[id] || {};
      reviewState.edits[id].bloom = v;
      const tags = document.querySelectorAll(`.revq[data-qid="${CSS.escape(id)}"] .qhead .tag`);
      if (tags[1]) tags[1].textContent = v;
    });
    reviewState.dirty = true;
    $("rev_save").disabled = false;
    toast(`已将 ${reviewState.select.size} 题的 Bloom 改为「${v}」`);
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_drop_sel").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    reviewState.select.forEach(id => { reviewState.drop.add(id); setDrop(id, true); });
    reviewState.dirty = true; $("rev_save").disabled = false;
    toast(`已剔除 ${reviewState.select.size} 题（保存后生效）`);
    updateRevCount();
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_restore_sel").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    reviewState.select.forEach(id => { reviewState.drop.delete(id); setDrop(id, false); });
    reviewState.dirty = true; $("rev_save").disabled = false;
    toast(`已恢复 ${reviewState.select.size} 题`);
    updateRevCount();
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_unsel").onclick = () => {
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  /* B13：全选当前筛选可见项 / 反选（均只作用于可见项，避免误选被筛选隐藏的题） */
  const visChecks = () => [...document.querySelectorAll(".revq")]
    .filter(d => d.style.display !== "none").map(d => d.querySelector(".revck")).filter(Boolean);
  $("rev_sel_vis").onclick = () => {
    visChecks().forEach(c => { c.checked = true; reviewState.select.add(c.closest(".revq").dataset.qid); });
    updBatch();
  };
  $("rev_sel_inv").onclick = () => {
    visChecks().forEach(c => {
      c.checked = !c.checked;
      const id = c.closest(".revq").dataset.qid;
      if (c.checked) reviewState.select.add(id); else reviewState.select.delete(id);
    });
    updBatch();
  };
  updBatch();
  // A-13：按钮禁用态统一以「运行时状态」为准（模板不再内联 disabled——模板与运行时可能不同步）
  $("rev_save").disabled = !reviewState.dirty;
  $("rev_save").onclick = async () => {
    // A-03：保存中标志——双保存窗口不只看按钮 disabled（重渲染会重建按钮），
    // 模块级 saving 从点击一直守到 showProject 完成后才能再次点击
    if (revSaving) return;
    revSaving = true;
    try {
      // B10：答案键校验未通过的编辑不允许保存（防单选/多选键错乱污染产物与判分）
      const bad = Object.entries(reviewState.edits)
        .filter(([k, v]) => v._answerInvalid && !reviewState.drop.has(k));
      if (bad.length) {
        const first = bad[0][0];
        toast(`答案键校验未通过（如 ${first}），请修正后再保存`, false);
        const el = document.querySelector(`.revq[data-qid="${CSS.escape(first)}"]`);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
      const keep = qs.map(x => x.id).filter(id => !reviewState.drop.has(id));
      // C-10/R3-14：保留 0 题 → 弹确认说明并中止（后端拒绝保存空题库；剔除意图不再静默蒸发）
      if (qs.length && !keep.length) {
        confirmModal("无法保存空题库",
          "<p>你将剔除全部题目（后端拒绝保存空题库）。<br>请至少保留一题；整卷作废请到「我的项目」删除项目。</p>",
          "知道了", null, false);
        return;
      }
      const edits = Object.entries(reviewState.edits).filter(([k, v]) => k !== "drop" && v && Object.keys(v).some(x => !x.startsWith("_")) && !reviewState.drop.has(k))
        .map(([id, v]) => {
          const clean = { id };
          ["question", "options", "answer", "analysis", "bloom", "type", "subtopic"].forEach(f => {
            if (v[f] !== undefined) clean[f] = (f === "answer" ? normAnswer(v[f]) : v[f]);   // C-11：存紧凑形式 BD
          });
          return clean;
        });
      const btn = $("rev_save");
      btn.disabled = true; btn.textContent = "保存中…";
      // B15：明示重渲染范围（题库/押题卷/手册/Anki/.apkg），避免大批量保存时误以为卡死
      $("rev_filter_cnt").textContent = "正在重渲染 5 项产物（题库 MD/HTML · 押题卷 · 复习手册 · Anki）…";
      await api("/api/projects/" + currentPid + "/questions/review", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep: keep, drop: [], edits: edits }) });
      toast("已保存并重渲染全部产物");
      await openReview();       // 重建审核台（新按钮按 A-13 以 !dirty 状态还原）
      await showProject(currentPid);
      // A-03：重渲染全部完成后才允许再次保存
      const b2 = $("rev_save");
      if (b2) { b2.disabled = !reviewState.dirty; }
    } catch (e) {
      toast(e.message, false);
      const btn = $("rev_save");
      if (btn) { btn.disabled = false; btn.textContent = "保存并重渲染"; }
      applyReviewFilter();   // B33：失败后还原「正在重渲染…」状态文案（恢复筛选计数）
    } finally {
      revSaving = false;
    }
  };
  applyReviewFilter();
  if (scrollToId) {
    const el = document.querySelector(`.revq[data-qid="${CSS.escape(scrollToId)}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/* ---- ④ 提示词 */
let promptCache = null;
async function loadPrompts() {
  const box = $("prompt_list");
  if (!box) return;
  box.innerHTML = '<div class="hint"><span class="spin"></span>加载中…</div>';
  try {
    const r = await api("/api/prompts");
    promptCache = r.prompts;
    box.innerHTML = "";
    if (!r.prompts.length) {
      box.innerHTML = '<div class="empty"><div class="sub">未找到内置提示词（安装可能不完整）</div></div>';
      return;
    }
    r.prompts.forEach(p => renderPrompt(p));
  } catch (e) {
    box.innerHTML = `<div class="hint bad">加载失败：${esc(e.message)}</div>`;
  }
}
function promptStatusBadge(p) {
  if (p.using === "custom" && p.drifted) return `<span class="pstatus drift">官方已更新 · 使用旧自定义</span>`;
  if (p.using === "custom") return `<span class="pstatus custom">已自定义（影子副本生效中）</span>`;
  return `<span class="pstatus">使用内置</span>`;
}
function renderPrompt(p) {
  const d = document.createElement("details");
  d.className = "card";
  d.style.marginTop = "12px";
  const ph = (p.placeholders || []).map(x => `<span class="chip" data-ph="${esc(x)}">${esc(x)}</span>`).join("");
  d.innerHTML = `
    <summary style="font-size:15px">
      <span class="ppm" style="display:inline-flex;margin:0">
        <span class="b">${esc(p.name)}</span><span>${esc(p.role)}</span>${promptStatusBadge(p)}
      </span>
    </summary>
    <div class="hint">占位符（运行时替换，勿删）：<span class="phchips">${ph}</span></div>
    <div class="btns">
      <button class="act gray" onclick="openPromptEdit('${esc(p.name)}')">编辑</button>
      ${p.using === "custom" ? `<button class="act gray" onclick="restorePrompt('${esc(p.name)}')">恢复默认</button>
      <button class="act gray" onclick="diffPrompt('${esc(p.name)}')">与官方版对比</button>` : ""}
    </div>
    <pre class="pview" id="pv_${esc(p.name).replace(".", "_")}">${esc(p.content)}</pre>
    <div id="peditor_${esc(p.name).replace(".", "_")}" style="display:none"></div>
    <div id="pdiff_${esc(p.name).replace(".", "_")}" style="display:none"></div>`;
  // 占位符高亮
  const pre = d.querySelector("pre.pview");
  let html = esc(p.content);
  (p.placeholders || []).forEach(x => { html = html.split(esc(x)).join(`<mark>${esc(x)}</mark>`); });
  pre.innerHTML = html;
  box_append(promptContainer(), d);
}
function promptContainer() { return $("prompt_list"); }
function box_append(el, d) { el.appendChild(d); }
async function openPromptEdit(name) {
  const r = promptCache.find(x => x.name === name);
  if (!r) return;
  const cur = r.content;
  const ed = $("peditor_" + name.replace(".", "_"));
  ed.style.display = "block";
  ed.innerHTML = `
    <textarea class="pedit" id="pedit_${esc(name).replace(".", "_")}" style="width:100%">${esc(cur)}</textarea>
    <div class="hint" id="phcheck_${esc(name).replace(".", "_")}">校验：编辑时请保留全部占位符</div>
    <div class="btns">
      <button class="act" onclick="savePrompt('${esc(name)}')">保存（写入影子副本）</button>
      <button class="act gray" onclick="document.getElementById('peditor_${esc(name).replace(".", "_")}').style.display='none'">取消</button>
    </div>`;
  const ta = ed.querySelector("textarea");
  ta.oninput = () => {
    const missing = (r.placeholders || []).filter(x => !ta.value.includes(x));
    const el = $("phcheck_" + name.replace(".", "_"));
    el.textContent = missing.length ? `缺少：${missing.join("、")}` : "占位符齐全 ✓";
    el.className = "hint " + (missing.length ? "bad" : "good");
    ed.querySelectorAll(".phchips .chip").forEach(c => {
      const t = c.dataset.ph;
      c.classList.toggle("missing", missing.includes(t));
    });
  };
  ed.querySelector("textarea").focus();
}
async function savePrompt(name) {
  const ta = document.querySelector("#peditor_" + name.replace(".", "_") + " textarea");
  try {
    await api("/api/prompts/" + encodeURIComponent(name), { method: "PUT",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: ta.value }) });
    toast("已保存影子副本（生效中）");
    loadPrompts();
    confirmModal("先试出一题验证效果？", "提示词已改——建议在「新建课题」点「试出一题」验证效果，防止改坏。",
      "去试出题", () => { location.hash = "bank"; showTab("bank"); }, false);
  } catch (e) { toast(e.message, false); }
}
async function restorePrompt(name) {
  confirmModal("恢复默认？", `将删除影子副本，恢复内置提示词（对「${esc(name)}」）。`,
    "恢复默认", async () => {
      try { await api("/api/prompts/" + encodeURIComponent(name), { method: "DELETE" }); toast("已恢复内置"); loadPrompts(); }
      catch (e) { toast(e.message, false); }
    });
}
async function diffPrompt(name) {
  const r = promptCache.find(x => x.name === name);
  if (!r || !r.custom) return;
  const el = $("pdiff_" + name.replace(".", "_"));
  el.style.display = "block";
  const a = r.builtin.split("\n"), b = r.custom.split("\n");
  const n = Math.max(a.length, b.length);
  let la = "", lb = "";
  for (let i = 0; i < n; i++) {
    const sa = a[i] || "", sb = b[i] || "";
    const mark = sa !== sb;
    la += (mark ? "<b>" : "") + esc(sa || " ") + (mark ? "</b>" : "") + "\n";
    lb += (mark ? "<b>" : "") + esc(sb || " ") + (mark ? "</b>" : "") + "\n";
  }
  el.innerHTML = `<div class="diffwrap"><pre>【内置】\n${la}</pre><pre>【自定义】\n${lb}</pre></div>`;
}

/* ---- 首启欢迎向导（医学生视角，3 步）----
   触发：未配置 Key 且从未完成引导；完成或「跳过」写 localStorage，关闭/ESC 不写（下次再提示） */
let wzStep = 0;
function wzDone() {
  try { localStorage.setItem("medkit-onboarded", "1"); } catch (e) { /* ignore */ }
  $("wizard_mask").style.display = "none";
}
/* 关闭/ESC 不写「已完成」（下次仍会提示），但记本会话内不再重复弹（防反复打扰） */
function wzClose() {
  try { sessionStorage.setItem("medkit-wz-seen", "1"); } catch (e) { /* ignore */ }
  $("wizard_mask").style.display = "none";
}
function wzSetDots() {
  document.querySelectorAll(".wsteps i").forEach((d, i) => d.classList.toggle("on", i === wzStep));
}
function wzRender() {
  wzSetDots();
  const body = $("wz_body"), btns = $("wz_btns");
  if (wzStep === 0) {
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">把你的<b>教材 + 老师划的重点</b>交给 AI，本地生成一套全新的复习资料。
      所有素材只保存在你自己的电脑上，AI 调用使用你自己的 API Key（约 ¥1~5/套，费用透明可见）。</div>
      <div class="wgrid">
        <div class="wcard"><svg class="ic"><use href="#i-bank"></use></svg><b>全新题库</b><span>按你的教材章节出题<br>A1/A2/X 型 + 图表题</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-paper"></use></svg><b>交互押题卷</b><span>计时答题 · 自动判分<br>错题重练 · 可打印</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-learn"></use></svg><b>复习手册</b><span>考点速记 / 易混淆<br>临床路径 / 数值速查</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-target"></use></svg><b>学习中心</b><span>错题沉淀 · 掌握度诊断<br>教材讲解 · 提问式复习</span></div>
      </div>
      <div class="hint">还可以导出 <b>Anki 卡片包</b>，直接导入 Anki 背题。</div>`;
    btns.innerHTML = `<button class="act" id="wz_next">下一步：连接 AI（2 分钟）</button>`;
    $("wz_next").onclick = () => { wzStep = 1; wzRender(); };
  } else if (wzStep === 1) {
    const provs = (state.providers || []).filter(p => p.register_url);
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">出题需要一个大模型「API Key」——相当于你和 AI 服务商之间的<b>充值卡</b>。
      推荐注册 <b>DeepSeek</b>（便宜，充值 ¥10 可出多套题）：点官网注册 → 充值 → 复制 Key，回到本软件「我的 → 连接服务商」粘贴保存即可。</div>
      ${provs.map(p => `<div class="wprov"><svg class="ic" style="width:20px;height:20px;color:var(--accent);flex:none"><use href="#i-key"></use></svg>
        <b>${esc(p.name)}</b><span>${esc(p.note || "")}</span>
        <a class="provlink" href="${esc(p.register_url)}" target="_blank" rel="noopener">官网注册 ↗</a></div>`).join("")}
      <div class="hint" style="margin-top:10px">不知道选哪个？先用 DeepSeek 就够了。稍后再配置也不影响了解软件。</div>`;
    btns.innerHTML = `<button class="act gray" id="wz_back">上一步</button>
      <button class="act" id="wz_next">我已拿到 Key（去配置）</button>
      <button class="act gray" id="wz_later">稍后配置，先看看</button>`;
    $("wz_back").onclick = () => { wzStep = 0; wzRender(); };
    $("wz_next").onclick = () => { wzDone(); showTab("mine"); $("api_key").focus(); };
    $("wz_later").onclick = () => { wzStep = 2; wzRender(); };
  } else {
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">准备好了？两种开始方式任选：</div>
      <div class="wentry" id="wz_demo" role="button" tabindex="0">
        <svg class="ic"><use href="#i-paper"></use></svg>
        <div><b>载入示例，立即体验</b><span>不用上传任何文件，30 秒看懂出题效果（点「试出一题」）</span></div>
      </div>
      <div class="wentry" id="wz_own" role="button" tabindex="0">
        <svg class="ic"><use href="#i-proj"></use></svg>
        <div><b>用我自己的教材开始</b><span>上传 PDF/Word 教材 + 老师重点 → 创建课题 → 生成</span></div>
      </div>`;
    btns.innerHTML = `<button class="act gray" id="wz_back">上一步</button>
      <button class="act" id="wz_fin">完成，开始使用</button>`;
    $("wz_back").onclick = () => { wzStep = 1; wzRender(); };
    $("wz_fin").onclick = () => wzDone();
    const goDemo = () => {
      // ME-1/A1：无 Key 时「载入示例」= 流程必然失败——先定向到连接页；
      // R3-05/A-新1：先关遮罩再跳（否则盖板压在连接页上，表单点不到）
      if (!(state.cfg && state.cfg.api_key_masked)) {
        toast("试出一题需要用 API Key——请先完成「我的 → 连接服务商」配置（充值 ¥10 可出多套题）", false);
        wzClose();
        showTab("mine"); $("api_key").focus();
        return;
      }
      wzDone(); showTab("bank"); $("btn_sample").click();
    };
    const goOwn = () => { wzDone(); showTab("bank"); };
    $("wz_demo").onclick = goDemo;
    $("wz_demo").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goDemo(); } };
    $("wz_own").onclick = goOwn;
    $("wz_own").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goOwn(); } };
  }
}
function maybeShowWizard() {
  try {
    if (localStorage.getItem("medkit-onboarded")) return;
    if (sessionStorage.getItem("medkit-wz-seen")) return;   // 本会话已看过（关闭/ESC 过）→ 不重复弹
  } catch (e) { return; }
  const c = state.cfg;
  if (!c || c.api_key_masked) return;   // 已有 Key → 老用户，不打扰
  wzStep = 0; wzRender();
  $("wizard_mask").style.display = "flex";
}
$("wz_skip").onclick = wzDone;
$("wizard_mask").addEventListener("click", e => { if (e.target.id === "wizard_mask") wzClose(); });

/* ---- init */
ratioSum(); bloomSum();
/* Ctrl/⌘+1..5 快速切换页签（v0.8.1：开始/刷题/题库/学习中心/我的） */
const TAB_KEYS = ["", "start", "study", "bank", "learn", "mine"];
window.addEventListener("keydown", e => {
  // A6：焦点在输入框/编辑器时不触发页签快捷键（防打字时被切走/吞键）
  const t = e.target;
  // A-新15：焦点守卫纳入 SELECT（下拉聚焦时不触发页签快捷键，防误切/吞键）
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (!(e.ctrlKey || e.metaKey) || !(e.key >= "1" && e.key <= "5")) return;
  const tk = TAB_KEYS[+e.key];
  if (!tk) return;
  e.preventDefault();
  location.hash = tk;
  showTab(tk);
});
/* 窄屏适配：窗口尺寸变化（如侧栏折叠）→ 防抖重绘配比条，重新做标签像素级适配 */
let segResizeT = 0;
window.addEventListener("resize", () => {
  clearTimeout(segResizeT);
  segResizeT = setTimeout(() => {
    if ($("tab-bank").classList.contains("show")) { ratioSum(); bloomSum(); }
  }, 120);
});
$("req_count").textContent = "0/500";
api("/api/health").then(h => {
  state.version = h.version || "";
  $("side_ver").textContent = "v" + (h.version || "");
}).catch(() => {});
/* v0.6:版本号可点 → 手动检查更新;启动 4s 后静默检查一次 */
$("side_ver").onclick = () => checkUpdate(false);
$("side_ver").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); checkUpdate(false); } };
setTimeout(() => checkUpdate(true), 4000);
loadConfig().then(maybeShowWizard).catch(e => toast(e.message, false));
probeSampleAvailability();
