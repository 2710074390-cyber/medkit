/* exported _bankDrill, bankDrillCardTgl, bankDrillExit, bankDrillFinish, bankDrillGoto, bankDrillHide,
   bankDrillMode, bankDrillNext, bankDrillNote, bankDrillPick, bankDrillPrev, bankDrillRender,
   bankDrillRetryWrong, bankDrillStar, bankDrillStart, bankDrillStartMistakes, bankDrillSubmit,
   bankDrillToggleRunmode */
/* ---- 题库作答引擎（bank-drill）：**沉浸式选择题刷题**（医考帮式） ----
 *
 * 与「导出的押题卷」同源同判：判分口径是**集合比较**（`core/bank.answers_equal` ↔
 * `render/qbank_html.py` 内联 JS 的 `answersEqual`）——`"B D"` 等价 `"BD"`。
 * 与押题卷的差别只有一处：这里的作答**记到后端**（`bank_attempts`），判错**自动回流错题本**。
 *
 * ## 交互（v0.13.0 起，对齐医考帮）
 *
 * - **沉浸式**：整层覆盖内容区（`#bank_drill_card.drill-ov`），从「刷题」/「练习」页均可进入；
 * - **点选项即判分**：单选点中立即判分并展开解析（不再需要「提交」那一步）；多选点「确认作答」；
 * - **答题卡**：题号网格（对/错/收藏/未做四态），点击跳转任意题（快捷键 C）；
 * - **背题模式**：直接显示答案与解析（`/items/{id}/reveal`，只读**不记流水**）；可随时与练习模式互切；
 * - **上下题 / 跳题**：已作答的题回看时保留作答与解析；
 * - **键盘**：A–Z / 1–9 选选项，Enter 提交或下一题，← → 上下题，C 答题卡，Esc 退出。
 *
 * 零 LLM：判分、解析展示、错题回流全是本地数据搬运。
 */
let _bankDrill = {
  bid: "", items: [], idx: 0, sel: new Set(), graded: false,
  mode: "all", runmode: "practice", last: null, busy: false,
  source: "bank",           // bank = 题库练习 / mistakes = 错题重练（判分端点不同，展示文案不同）
  marks: {},                // item_id -> {picked, graded, correct, answer, analysis, note, src…}（已作答记录）
  cardOpen: false,          // 答题卡是否展开
  startedAt: 0, itemAt: 0, timerId: null,
};

const _BANK_LETTERS = "ABCDEFGHIJ";
const _BANK_RECITE = "recite";

/* A-1 出处标签：`真题 · 2024`（缺项自动省略；两者都缺返回空串 ⇒ 不渲染该行） */
function _bankSrcLabel(it) {
  return [it && it.source_type, it && it.source_year].filter(Boolean).join(" · ");
}

function _bankIsMulti(it) { return String((it && it.type) || "") === "X"; }

/* ---- 沉浸层开关 ---- */
function _bankDrillEl() { return $("bank_drill_card"); }

function _bankDrillOpen() {
  const el = _bankDrillEl();
  if (el) el.style.display = "";
  document.body.classList.add("drill-open");
}

/* ---- 计时（总时长 + 单题时长，供 grade 记 secs） ---- */
function _bankDrillStartTimer() {
  _bankDrillStopTimer();
  const d = _bankDrill;
  d.startedAt = Date.now();
  const tick = () => {
    const el = $("bank_timer");
    if (!el) return;
    const s = Math.floor((Date.now() - d.startedAt) / 1000);
    el.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  };
  tick();
  d.timerId = setInterval(tick, 1000);
}
function _bankDrillStopTimer() {
  if (_bankDrill.timerId) { clearInterval(_bankDrill.timerId); _bankDrill.timerId = null; }
}
function _bankItemSecs() {
  const d = _bankDrill;
  return d.itemAt ? Math.max(0, Math.round((Date.now() - d.itemAt) / 1000)) : 0;
}

/* ---- 键盘快捷键（仅沉浸层可见时生效；输入框内不拦截） ---- */
let _bankDrillKeyBound = false;
function _bankDrillKey(e) {
  const el = _bankDrillEl();
  if (!el || el.style.display === "none") return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  const d = _bankDrill;
  const it = d.items[d.idx];
  const k = e.key;
  if (k === "Escape") { bankDrillExit(); e.preventDefault(); return; }
  if (k === "c" || k === "C") { bankDrillCardTgl(); e.preventDefault(); return; }
  if (k === "ArrowRight") { bankDrillNext(); e.preventDefault(); return; }
  if (k === "ArrowLeft") { bankDrillPrev(); e.preventDefault(); return; }
  if (k === "Enter") {
    if (d.graded || d.runmode === _BANK_RECITE) bankDrillNext();
    else if (it && _bankIsMulti(it)) bankDrillSubmit();
    e.preventDefault();
    return;
  }
  let i = -1;
  if (/^[a-zA-Z]$/.test(k)) i = k.toUpperCase().charCodeAt(0) - 65;
  else if (/^[1-9]$/.test(k)) i = parseInt(k, 10) - 1;
  if (i >= 0 && it && i < (it.options || []).length) { bankDrillPick(i); e.preventDefault(); }
}
function _bankDrillKeyOn() {
  if (_bankDrillKeyBound) return;
  document.addEventListener("keydown", _bankDrillKey);
  _bankDrillKeyBound = true;
}
function _bankDrillKeyOff() {
  if (!_bankDrillKeyBound) return;
  document.removeEventListener("keydown", _bankDrillKey);
  _bankDrillKeyBound = false;
}

function _bankDrillSetRunmodeBtn() {
  const btn = $("btn_drill_runmode");
  if (btn) btn.textContent = _bankDrill.runmode === _BANK_RECITE ? "练习模式" : "背题模式";
}

/* ---- 开始：题库练习 ---- */
async function bankDrillStart(bid, mode, opts) {
  opts = opts || {};
  const d = _bankDrill;
  const prevRun = d.runmode;
  Object.assign(d, {
    bid: String(bid || ""), items: [], idx: 0, sel: new Set(), graded: false,
    mode: mode || "all", runmode: opts.runmode || prevRun || "practice",
    last: null, busy: false, source: "bank", marks: {}, cardOpen: false,
  });
  _bankDrillOpen();
  _bankDrillSetRunmodeBtn();
  _bankDrillCardReset();
  const title = $("bank_drill_title");
  if (title) title.textContent = d.runmode === _BANK_RECITE ? "背题" : "练习";
  const body = $("bank_drill_body");
  if (body) body.innerHTML = `<div class="hint"><span class="spin"></span>加载题目…</div>`;
  try {
    const r = await api(`/api/bank/banks/${encodeURIComponent(d.bid)}/drill`
      + `?mode=${encodeURIComponent(d.mode)}`);
    d.items = r.items || [];
  } catch (e) {
    if (body) body.innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (!d.items.length) {
    if (body) body.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-doc"></use></svg>
      <div class="sub">${d.mode === "all"
        ? "这个题库还没有题目<br>先「导入题库文件」把题目收进来"
        : "当前筛选下没有题目（换「全部」试试）"}</div>
    </div>`;
    return;
  }
  _bankDrillStartTimer();
  _bankDrillKeyOn();
  bankDrillRender();
}

function bankDrillMode(mode) {
  if (_bankDrill.busy) return;
  if (_bankDrill.source === "mistakes") { bankDrillStartMistakes(mode); return; }
  bankDrillStart(_bankDrill.bid, mode, { runmode: _bankDrill.runmode });
}

/* ---- 错题重练（F7 增强）：把**自己的错题**当作练习卷来刷 ----
 *
 * 与题库练习共用同一套作答/判分 UI，差别只有两处：
 * ① 题源 = `/api/library/mistakes`（不是题库）；
 * ② 判分端点 = `/api/library/mistakes/{id}/retry`（判对即归档「已掌握」）。
 * 判分口径仍是同一份集合比较，不复制规则。
 *
 * 只收**有选项**的错题：没有选项就没有可判的作答（填空白由用户自行判分，不在这里伪造）。
 *
 * `focusId`（可选）：从错题本的「→ 重练」直达时指定要练的那道题（找不到就从头开始，
 * 并说明原因——「点了没反应」比「明确告诉你为什么」更糟）。
 */
async function bankDrillStartMistakes(mode, focusId, opts) {
  opts = opts || {};
  const d = _bankDrill;
  // 切范围（全部错题 / 只看未掌握）时**保持当前题目**（若它仍在筛选结果里）：
  // 用户只是想换个范围，把人丢回第一题等于打断他正在做的那题，也丢掉了已作答记录。
  const keepId = (d.source === "mistakes" && d.items[d.idx]) ? d.items[d.idx].id : "";
  Object.assign(d, {
    bid: "", items: [], idx: 0, sel: new Set(), graded: false,
    mode: mode || "all", runmode: opts.runmode || d.runmode || "practice",
    last: null, busy: false, source: "mistakes",
    marks: {}, cardOpen: false,
  });
  _bankDrillOpen();
  _bankDrillSetRunmodeBtn();
  _bankDrillCardReset();
  const mc = $("bank_manage_card"); if (mc) mc.style.display = "none";
  const title = $("bank_drill_title");
  if (title) title.textContent = d.runmode === _BANK_RECITE ? "背题（错题）" : "错题重练";
  const body = $("bank_drill_body");
  if (body) body.innerHTML = `<div class="hint"><span class="spin"></span>加载错题…</div>`;
  try {
    const r = await api("/api/library/mistakes");
    const all = r.mistakes || [];
    const drillable = all.filter(m => (m.options || []).length);
    d.items = d.mode === "unlearned" ? drillable.filter(m => !m.learned) : drillable;
  } catch (e) {
    if (body) body.innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (focusId) {
    const i = d.items.findIndex(m => String(m.id) === String(focusId));
    if (i >= 0) d.idx = i;
    else toast("该错题没有选项（或已被归档隐藏），无法直接重练——从第一题开始", false);
  } else if (keepId) {
    const i = d.items.findIndex(m => String(m.id) === String(keepId));
    if (i >= 0) d.idx = i;                 // 保留当前题；不在新范围里则回第一题（并交给结果页说明）
  }
  if (!d.items.length) {
    if (body) body.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
      <div class="sub">没有可重练的错题<br>
        ${d.mode === "unlearned" ? "当前没有「未掌握」的错题（换「全部错题」试试）"
          : "错题本还是空的——先在「学习中心 → 错题本」录入错题，或在练习里刷题库（判错会自动进错题本）"}</div>
    </div>`;
    return;
  }
  _bankDrillStartTimer();
  _bankDrillKeyOn();
  bankDrillRender();
}

/* ---- 顶部模式按钮条（范围筛选：全部 / 只练错题 / 只练未做 / 只练收藏） ---- */
function _bankDrillBar() {
  const d = _bankDrill;
  const modeBtn = (m, label) =>
    `<button class="mini-btn${d.mode === m ? " primary" : ""}" onclick="bankDrillMode('${m}')">${label}</button>`;
  const modes = d.source === "mistakes"
    ? [["all", "全部错题"], ["unlearned", "只看未掌握"]]
    : [["all", "全部"], ["wrong", "只练错题"], ["undone", "只练未做"], ["starred", "只练收藏"]];
  return `<div class="bank-bar">${modes.map(([m, l]) => modeBtn(m, l)).join("")}
      <span class="hint drill-tip">${d.runmode === _BANK_RECITE
        ? "背题模式：直接看答案与解析，不用作答"
        : "点选项即判分（多选需点「确认作答」）"}</span>
    </div>`;
}

/* 就地更新进度（不重渲染整块 —— 重渲染会把刚展开的判分结果清掉） */
function _bankCounts() {
  const d = _bankDrill;
  let ok = 0, no = 0;
  for (const it of d.items) {
    const m = d.marks[it.id];
    if (m && m.graded) { if (m.correct) ok += 1; else no += 1; }
  }
  return { ok, no, undone: Math.max(0, d.items.length - ok - no) };
}
function _bankDrillProgress() {
  const d = _bankDrill;
  const total = d.items.length;
  const c = _bankCounts();
  const done = c.ok + c.no;
  const pct = total ? Math.round(done / total * 100) : 0;
  const el = $("bank_prog");
  if (el) el.textContent = `进度 ${Math.min(d.idx + 1, total)}/${total} · 对 ${c.ok} · 错 ${c.no}`;
  const bar = $("bank_prog_i");
  if (bar) bar.style.width = pct + "%";
}

/* ---- 渲染当前题 ---- */
function bankDrillRender() {
  const body = $("bank_drill_body");
  if (!body) return;
  const d = _bankDrill;
  if (d.idx >= d.items.length) { bankDrillFinish(); return; }
  const it = d.items[d.idx];
  const opts = it.options || [];
  const multi = _bankIsMulti(it);
  const isMistake = d.source === "mistakes";
  const recite = d.runmode === _BANK_RECITE;
  const m = d.marks[it.id];
  const graded = !!(m && m.graded);
  d.graded = graded;
  d.itemAt = Date.now();
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `${(it.subtopic || it.topic || "未分类")} · ${it.type || ""}`;
  body.innerHTML = `${_bankDrillBar()}
    <div class="bank-qhead">
      <span class="bank-qidx">第 ${d.idx + 1} / ${d.items.length} 题</span>
      <span class="tag">${multi ? "多选" : "单选"}</span>
      ${isMistake ? '<span class="tag">我的错题</span>' : ""}
      ${recite ? '<span class="tag recite">背题模式</span>' : ""}
      ${!recite && !graded && it._last_correct === false
        ? '<span class="tag" style="color:var(--bad);border-color:var(--bad)">上次做错</span>' : ""}
    </div>
    ${it.case_stem ? `<div class="hint" style="margin-bottom:8px">案例：${esc(it.case_stem)}</div>` : ""}
    <div class="bank-q">${esc(it.question || "")}</div>
    <div id="bank_opts"${recite ? ' class="recite"' : ""}>${opts.map((o, i) => `
      <div class="bank-opt" id="bopt_${i}" onclick="bankDrillPick(${i})">
        <span class="bank-opt-k">${_BANK_LETTERS[i] || (i + 1)}</span>
        <span>${esc(o)}</span>
      </div>`).join("")}</div>
    <div class="drill-actions">
      <button class="mini-btn" onclick="bankDrillPrev()"${d.idx === 0 ? " disabled" : ""}>← 上一题</button>
      ${multi && !graded && !recite ? '<button class="act" id="btn_bank_submit" onclick="bankDrillSubmit()">确认作答</button>' : ""}
      ${graded || recite
        ? '<button class="act" onclick="bankDrillNext()">下一题 →</button>'
        : '<button class="act gray" onclick="bankDrillNext()">跳过</button>'}
      ${isMistake ? "" : `<button class="mini-btn" id="btn_bank_star" onclick="bankDrillStar()">${it.starred ? "★ 已收藏" : "☆ 收藏"}</button>`}
      ${isMistake ? "" : '<button class="mini-btn" onclick="bankDrillNote()">✎ 笔记</button>'}
    </div>
    <div id="bank_result"></div>`;
  if (graded) {
    _bankShowResult({
      correct: m.correct, answer: m.answer, analysis: m.analysis, note: m.note,
      source_type: m.source_type, source_year: m.source_year, synced_mistakes: m.synced,
    }, m.picked);
  } else if (recite) {
    _bankReciteReveal(it);
  } else if (d.sel.size) {
    _bankPaintSel();
  }
  _bankDrillProgress();
  _bankDrillRenderCard();
}

/* ---- 点选项：单选立即判分；多选切换选中 ---- */
function bankDrillPick(i) {
  const d = _bankDrill;
  if (d.busy || d.runmode === _BANK_RECITE) return;
  const it = d.items[d.idx];
  if (!it) return;
  const m = d.marks[it.id];
  if (m && m.graded) return;                 // 已作答：回看状态，不再改
  const opts = it.options || [];
  if (i < 0 || i >= opts.length) return;
  if (!_bankIsMulti(it)) {
    d.sel.clear();
    d.sel.add(i);
    _bankPaintSel();
    bankDrillSubmit();                       // 单选：点中即判分
    return;
  }
  if (d.sel.has(i)) d.sel.delete(i); else d.sel.add(i);
  _bankPaintSel();
}

function _bankPaintSel() {
  const box = $("bank_opts");
  if (!box) return;
  [...box.children].forEach((el, k) => el.classList.toggle("sel", _bankDrill.sel.has(k)));
}

function _bankSelAnswer() {
  return [..._bankDrill.sel].sort((a, b) => a - b)
    .map(i => _BANK_LETTERS[i] || "").join("");
}

/* ---- 提交判分 ---- */
async function bankDrillSubmit() {
  const d = _bankDrill;
  if (d.busy || d.runmode === _BANK_RECITE) return;
  const it = d.items[d.idx];
  if (!it) return;
  const m0 = d.marks[it.id];
  if (m0 && m0.graded) return;
  const ans = _bankSelAnswer();
  if (!ans) { toast("请先选择选项", false); return; }
  d.busy = true;
  try {
    const r = d.source === "mistakes"
      ? await api(`/api/library/mistakes/${encodeURIComponent(it.id)}/retry`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_answer: ans }) })
      : await api("/api/bank/grade", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bank_id: d.bid, item_id: it.id, user_answer: ans,
                                 mode: "practice", secs: _bankItemSecs() }) });
    d.graded = true;
    d.last = r;
    d.marks[it.id] = {
      picked: ans, graded: true, correct: !!r.correct,
      answer: r.answer, analysis: r.analysis, note: r.note,
      source_type: r.source_type, source_year: r.source_year,
      synced: r.synced_mistakes,
    };
    _bankShowResult(r, ans);
    _bankDrillProgress();
    _bankDrillRenderCard();
    if (typeof bankGoalRefresh === "function") bankGoalRefresh();   // 每日目标进度（题库作答计入）
  } catch (e) { toast(e.message, false); }
  finally { d.busy = false; }
}

/* ---- 背题模式：只读取答案（不记流水）并标绿正确项 ---- */
async function _bankReciteReveal(it) {
  const d = _bankDrill;
  const res = $("bank_result");
  if (res) res.innerHTML = `<div class="hint"><span class="spin"></span>加载答案…</div>`;
  try {
    let r;
    if (d.source === "mistakes") {
      r = { answer: it.answer, analysis: it.analysis, note: it.note,
            source_type: it.source_type, source_year: it.source_year };
    } else {
      r = await api(`/api/bank/items/${encodeURIComponent(it.id)}/reveal`);
    }
    _bankShowResult({
      correct: null, answer: r.answer, analysis: r.analysis, note: r.note,
      source_type: r.source_type, source_year: r.source_year,
    }, "");
  } catch (e) {
    if (res) res.innerHTML = `<div class="hint">答案加载失败：${esc(e.message)}</div>`;
  }
}

/* ---- 判分结果 / 背题答案渲染（correct===null 表示背题：不判对错） ---- */
function _bankShowResult(r, userAns) {
  const d = _bankDrill;
  const it = d.items[d.idx];
  const recite = r.correct === null;
  const right = new Set(String(r.answer || "").split(""));
  const mine = new Set(String(userAns || "").split(""));
  const box = $("bank_opts");
  if (box) {
    [...box.children].forEach((el, i) => {
      const L = _BANK_LETTERS[i] || "";
      // ⚠️ 不能写 `classList.add(a ? "right" : (b ? "wrong" : ""))` ——
      // 传空串会抛 `Failed to execute 'add' on 'DOMTokenList'`（实测：判对时
      // 未选中的干扰项正好落进这个分支，整个结果区因此不渲染）。
      el.classList.remove("sel", "right", "wrong");
      if (right.has(L)) el.classList.add("right");
      else if (mine.has(L)) el.classList.add("wrong");
    });
  }
  const res = $("bank_result");
  if (res) {
    const head = recite
      ? '<div style="font-weight:600;color:var(--info)">答案（背题）</div>'
      : `<div style="font-weight:600;color:${r.correct ? "var(--good)" : "var(--bad)"}">${r.correct ? "✓ 答对了" : "✗ 答错了"}</div>`;
    res.innerHTML = `<div class="bank-ans ${recite ? "rec" : (r.correct ? "ok" : "no")}">
        ${head}
        <div style="margin-top:6px">${recite ? "" : `你的作答：<b>${esc(userAns || "—")}</b> · `}正确答案：<b>${esc(r.answer || "—")}</b></div>
        ${r.analysis ? `<div style="margin-top:8px"><b>解析</b>：${window.mdRender ? mdRender(r.analysis) : esc(r.analysis)}</div>` : ""}
        ${_bankSrcLabel(r) ? `<div style="margin-top:6px"><b>出处</b>：${esc(_bankSrcLabel(r))}</div>` : ""}
        ${r.note ? `<div style="margin-top:8px"><b>我的笔记</b>：${esc(r.note)}</div>` : ""}
        ${!recite && !r.correct && r.synced_mistakes ? '<div class="hint" style="margin-top:8px">已自动加入错题本（可在「学习中心 → 错题本」查看与讲解）</div>' : ""}
        ${!recite && d.source === "mistakes" && r.correct ? '<div class="hint" style="margin-top:8px">已归档为「已掌握」（错题本里默认被「只看未掌握」隐藏）</div>' : ""}
        ${!recite && d.source === "mistakes" && !r.correct ? '<div class="hint" style="margin-top:8px">仍是未掌握——可去「学习中心」用「讲解 / 提问 / 铺卡」继续攻克</div>' : ""}
      </div>`;
  }
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `${(it.subtopic || it.topic || "未分类")} · ${it.type || ""}`
    + (recite ? " · 背题" : (r.correct ? " · 对" : " · 错"));
}

/* ---- 上一题 / 下一题 / 跳题 ---- */
function _bankDrillGoto(idx) {
  const d = _bankDrill;
  if (idx < 0 || idx >= d.items.length) return;
  d.idx = idx; d.sel = new Set(); d.graded = false; d.last = null;
  bankDrillRender();
}
function bankDrillNext() {
  const d = _bankDrill;
  if (d.busy) return;
  _bankDrillGoto(Math.min(d.idx + 1, d.items.length));
}
function bankDrillPrev() {
  const d = _bankDrill;
  if (d.busy || d.idx === 0) return;
  _bankDrillGoto(d.idx - 1);
}
function bankDrillGoto(i) { _bankDrillGoto(parseInt(i, 10) || 0); }

/* ---- 答题卡 ---- */
function _bankDrillCardReset() {
  const pane = $("bank_drill_cardpane");
  if (pane) pane.style.display = "none";
}
function bankDrillCardTgl() {
  const pane = $("bank_drill_cardpane");
  if (!pane) return;
  _bankDrill.cardOpen = !_bankDrill.cardOpen;
  pane.style.display = _bankDrill.cardOpen ? "" : "none";
  _bankDrillRenderCard();
}
function _bankDrillRenderCard() {
  const pane = $("bank_drill_cardpane");
  if (!pane || !_bankDrill.cardOpen) return;
  const d = _bankDrill;
  const cells = d.items.map((it, i) => {
    const m = d.marks[it.id];
    const cls = (m && m.graded) ? (m.correct ? "ok" : "no") : (it.starred ? "star" : "");
    const cur = i === d.idx ? " cur" : "";
    return `<button class="dc-cell ${cls}${cur}" onclick="bankDrillGoto(${i})"
      title="第 ${i + 1} 题${m && m.graded ? (m.correct ? "（对）" : "（错）") : ""}">${i + 1}</button>`;
  }).join("");
  const c = _bankCounts();
  pane.innerHTML = `<div class="dc-head">
      <b>答题卡</b>
      <span class="hint">对 ${c.ok} · 错 ${c.no} · 未做 ${c.undone}</span>
      <button class="mini-btn" onclick="bankDrillCardTgl()" title="收起（C）">✕</button>
    </div>
    <div class="dc-grid">${cells}</div>
    <div class="dc-legend">
      <span><i class="lg ok"></i>对</span><span><i class="lg no"></i>错</span>
      <span><i class="lg star"></i>收藏</span><span><i class="lg"></i>未做</span>
    </div>`;
}

/* ---- 练习 / 背题 模式互切 ---- */
function bankDrillToggleRunmode() {
  const d = _bankDrill;
  d.runmode = d.runmode === _BANK_RECITE ? "practice" : _BANK_RECITE;
  _bankDrillSetRunmodeBtn();
  const title = $("bank_drill_title");
  if (title && d.source === "bank") title.textContent = d.runmode === _BANK_RECITE ? "背题" : "练习";
  toast(d.runmode === _BANK_RECITE ? "已切到背题模式：直接看答案与解析" : "已切回练习模式：点选项即判分");
  d.sel = new Set(); d.graded = false; d.last = null;
  bankDrillRender();
}

/* ---- 练习中收藏（F9）：直接落库，按钮就地反馈（不重渲染整块，避免丢掉当前选择）。 ---- */
async function bankDrillStar() {
  const d = _bankDrill;
  const it = d.items[d.idx];
  if (!it) return;
  try {
    const r = await api(`/api/bank/items/${encodeURIComponent(it.id)}/star`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred: !it.starred }) });
    it.starred = !!r.item.starred;
    const btn = $("btn_bank_star");
    if (btn) btn.textContent = it.starred ? "★ 已收藏" : "☆ 收藏";
    _bankDrillRenderCard();
    toast(it.starred ? "已收藏（可用「只练收藏」单独刷）" : "已取消收藏");
  } catch (e) { toast(e.message, false); }
}

/* ---- 题目笔记：判分后才显示（可能写着答案） ---- */
function bankDrillNote() {
  const d = _bankDrill;
  const it = d.items[d.idx];
  if (!it) return;
  askModal("题目笔记", "写点什么（判分后才显示）", async (note) => {
    try {
      await api(`/api/bank/items/${encodeURIComponent(it.id)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }) });
      it.note = note;
      const m = d.marks[it.id]; if (m) m.note = note;
      toast("笔记已保存");
      bankDrillRender();
    } catch (e) { toast(e.message, false); }
  }, it.note || "");
}

function bankDrillRetryWrong() {
  const d = _bankDrill;
  const wrong = d.items.filter(it => { const m = d.marks[it.id]; return m && m.graded && !m.correct; });
  if (!wrong.length) return;
  d.items = wrong.slice();
  d.idx = 0; d.sel = new Set(); d.graded = false; d.last = null;
  d.marks = {}; d.mode = "all";
  _bankDrillStartTimer();
  bankDrillRender();
}

/* ---- 退出沉浸层 ---- */
/* 仅收起沉浸层（不提示、不刷新）——切页签时由外壳 `showTab` 调用；用户主动退出请用 bankDrillExit。 */
function bankDrillHide() {
  const el = _bankDrillEl();
  if (!el || el.style.display === "none") return;
  el.style.display = "none";
  document.body.classList.remove("drill-open");
  _bankDrillStopTimer();
  _bankDrillKeyOff();
}

function bankDrillExit() {
  const d = _bankDrill;
  const answered = Object.keys(d.marks).length;
  bankDrillHide();
  if (answered) toast(`已退出练习（本组答了 ${answered} 题）`);
  // 回到入口页时刷新（列表统计/每日进度随作答变化）
  if (typeof loadStudy === "function") loadStudy();
  if (typeof loadBankHome === "function") loadBankHome(true);
}

function bankDrillFinish() {
  const d = _bankDrill;
  const total = d.items.length;
  const c = _bankCounts();
  const pct = total ? Math.round(c.ok / total * 100) : 0;
  _bankDrillStopTimer();
  const body = $("bank_drill_body");
  if (!body) return;
  body.innerHTML = `${_bankDrillBar()}
    <div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
      <div class="sub">本组练完：答对 <b>${c.ok}</b> / ${total}（${pct}%）
        ${c.no
          ? (d.source === "mistakes"
            ? `<br>还有 ${c.no} 道没答对——继续练或去「学习中心」讲解 / 提问`
            : `<br>做错 ${c.no} 道——已回流到「学习中心 → 错题本」`)
          : "<br>全部答对，漂亮"}</div>
    </div>
    <div class="btns" style="justify-content:center;margin-top:12px">
      ${c.no ? `<button class="act" onclick="bankDrillRetryWrong()">只练本次错题（${c.no}）</button>` : ""}
      <button class="act gray" onclick="bankDrillMode('all')">再练一遍</button>
      <button class="act gray" onclick="bankDrillExit()">退出</button>
    </div>`;
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `完成 ${total} 题 · 答对 ${c.ok}`;
  _bankDrillRenderCard();
  if (typeof loadBankHome === "function") loadBankHome(true);   // 只刷新列表数据，保留总结页
}

window.bankDrillStart = bankDrillStart;
window.bankDrillRender = bankDrillRender;
window.bankDrillPick = bankDrillPick;
window.bankDrillSubmit = bankDrillSubmit;
window.bankDrillNext = bankDrillNext;
window.bankDrillPrev = bankDrillPrev;
window.bankDrillGoto = bankDrillGoto;
window.bankDrillCardTgl = bankDrillCardTgl;
window.bankDrillToggleRunmode = bankDrillToggleRunmode;
window.bankDrillExit = bankDrillExit;
window.bankDrillHide = bankDrillHide;
window.bankDrillNote = bankDrillNote;
window.bankDrillMode = bankDrillMode;
window.bankDrillFinish = bankDrillFinish;
window.bankDrillRetryWrong = bankDrillRetryWrong;
window.bankDrillStar = bankDrillStar;
window.bankDrillStartMistakes = bankDrillStartMistakes;
