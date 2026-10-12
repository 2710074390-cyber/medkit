/* exported _bankDrill, bankDrillFinish, bankDrillMode, bankDrillNext, bankDrillPick, bankDrillRender,
   bankDrillStar, bankDrillStart, bankDrillStartMistakes, bankDrillSubmit, bankDrillRetryWrong */
/* ---- 题库作答引擎（bank-drill）：题干 + 选项 + 提交 + 即时判分 + 解析 ----
 *
 * 与「导出的押题卷」同源同判：判分口径是**集合比较**（`core/bank.answers_equal` ↔
 * `render/qbank_html.py` 内联 JS 的 `answersEqual`）——`"B D"` 等价 `"BD"`。
 * 与押题卷的差别只有一处：这里的作答**记到后端**（`bank_attempts`），判错**自动回流错题本**，
 * 而不是困在浏览器 localStorage 里。
 *
 * 零 LLM：判分、解析展示、错题回流全是本地数据搬运。
 */
let _bankDrill = {
  bid: "", items: [], idx: 0, sel: new Set(), graded: false,
  mode: "all", ok: 0, wrong: [], last: null, busy: false,
  source: "bank",   // bank = 题库练习 / mistakes = 错题重练（判分端点不同，展示文案不同）
};

const _BANK_LETTERS = "ABCDEFGHIJ";

/* A-1 出处标签：`真题 · 2024`（缺项自动省略；两者都缺返回空串 ⇒ 不渲染该行） */
function _bankSrcLabel(it) {
  return [it && it.source_type, it && it.source_year].filter(Boolean).join(" · ");
}

async function bankDrillStart(bid, mode) {
  const card = $("bank_drill_card");
  if (!card) return;
  card.style.display = "";
  _bankDrill = { bid: String(bid || ""), items: [], idx: 0, sel: new Set(), graded: false,
                 mode: mode || "all", ok: 0, wrong: [], last: null, busy: false, source: "bank" };
  const title = $("bank_drill_title");
  if (title) title.textContent = "练习";
  const body = $("bank_drill_body");
  if (body) body.innerHTML = `<div class="hint"><span class="spin"></span>加载题目…</div>`;
  try {
    const r = await api(`/api/bank/banks/${encodeURIComponent(_bankDrill.bid)}/drill`
      + `?mode=${encodeURIComponent(_bankDrill.mode)}`);
    _bankDrill.items = r.items || [];
  } catch (e) {
    if (body) body.innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (!_bankDrill.items.length) {
    if (body) body.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-doc"></use></svg>
      <div class="sub">${_bankDrill.mode === "all"
        ? "这个题库还没有题目<br>先「导入题库文件」把题目收进来"
        : "当前筛选下没有题目（换「全部」试试）"}</div>
    </div>`;
    return;
  }
  bankDrillRender();
}

function bankDrillMode(mode) {
  if (_bankDrill.busy) return;
  if (_bankDrill.source === "mistakes") { bankDrillStartMistakes(mode); return; }
  bankDrillStart(_bankDrill.bid, mode);
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
async function bankDrillStartMistakes(mode, focusId) {
  const card = $("bank_drill_card");
  if (!card) return;
  card.style.display = "";
  const mc = $("bank_manage_card"); if (mc) mc.style.display = "none";
  _bankDrill = { bid: "", items: [], idx: 0, sel: new Set(), graded: false,
                 mode: mode || "all", ok: 0, wrong: [], last: null, busy: false,
                 source: "mistakes" };
  const title = $("bank_drill_title");
  if (title) title.textContent = "错题重练";
  const body = $("bank_drill_body");
  if (body) body.innerHTML = `<div class="hint"><span class="spin"></span>加载错题…</div>`;
  try {
    const r = await api("/api/library/mistakes");
    const all = r.mistakes || [];
    const drillable = all.filter(m => (m.options || []).length);
    _bankDrill.items = _bankDrill.mode === "unlearned" ? drillable.filter(m => !m.learned) : drillable;
  } catch (e) {
    if (body) body.innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`;
    return;
  }
  if (focusId) {
    const i = _bankDrill.items.findIndex(m => String(m.id) === String(focusId));
    if (i >= 0) _bankDrill.idx = i;
    else toast("该错题没有选项（或已被归档隐藏），无法直接重练——从第一题开始", false);
  }
  if (!_bankDrill.items.length) {
    if (body) body.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
      <div class="sub">没有可重练的错题<br>
        ${_bankDrill.mode === "unlearned" ? "当前没有「未掌握」的错题（换「全部错题」试试）"
          : "错题本还是空的——先在「学习中心 → 错题本」录入错题，或在练习里刷题库（判错会自动进错题本）"}</div>
    </div>`;
    return;
  }
  bankDrillRender();
}

function _bankDrillBar() {
  const d = _bankDrill;
  const total = d.items.length;
  const done = Math.min(d.idx, total);
  const pct = total ? Math.round(done / total * 100) : 0;
  const modeBtn = (m, label) =>
    `<button class="mini-btn${d.mode === m ? " primary" : ""}" onclick="bankDrillMode('${m}')">${label}</button>`;
  const modes = d.source === "mistakes"
    ? [["all", "全部错题"], ["unlearned", "只看未掌握"]]
    : [["all", "全部"], ["wrong", "只练错题"], ["undone", "只练未做"], ["starred", "只练收藏"]];
  return `<div class="bank-bar">
      ${modes.map(([m, l]) => modeBtn(m, l)).join("")}
      <span class="hint" id="bank_prog" style="margin-left:auto">进度 ${done}/${total} · 答对 ${d.ok} · ${pct}%</span>
      <span class="bar" style="width:90px;display:inline-block"><i id="bank_prog_i" style="width:${pct}%"></i></span>
    </div>`;
}

/* 就地更新进度（不重渲染整块 —— 重渲染会把刚展开的判分结果清掉） */
function _bankDrillProgress() {
  const d = _bankDrill;
  const total = d.items.length;
  const done = Math.min(d.idx, total);
  const pct = total ? Math.round(done / total * 100) : 0;
  const el = $("bank_prog");
  if (el) el.textContent = `进度 ${done}/${total} · 答对 ${d.ok} · ${pct}%`;
  const bar = $("bank_prog_i");
  if (bar) bar.style.width = pct + "%";
}

function bankDrillRender() {
  const body = $("bank_drill_body");
  if (!body) return;
  const d = _bankDrill;
  if (d.idx >= d.items.length) { bankDrillFinish(); return; }
  const it = d.items[d.idx];
  const opts = it.options || [];
  const multi = String(it.type || "") === "X";
  const isMistake = d.source === "mistakes";
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `${(it.subtopic || it.topic || "未分类")} · ${it.type || ""}`;
  body.innerHTML = `${_bankDrillBar()}
    <div class="hint" style="margin-bottom:8px">
      ${multi ? "多选题：选出<b>全部</b>正确选项（漏选 / 错选都判错）" : "单选题：选一个"}
      ${isMistake ? '<span class="tag" style="margin-left:6px">我的错题</span>' : ""}
      ${!isMistake && it._last_correct === false ? '<span class="tag" style="color:var(--bad);border-color:var(--bad);margin-left:6px">上次做错</span>' : ""}
    </div>
    <div class="bank-q">${esc(it.question || "")}</div>
    <div id="bank_opts">${opts.map((o, i) => `
      <div class="bank-opt" id="bopt_${i}" onclick="bankDrillPick(${i})">
        <span class="bank-opt-k">${_BANK_LETTERS[i] || (i + 1)}</span>
        <span>${esc(o)}</span>
      </div>`).join("")}</div>
    <div class="btns" style="margin-top:12px">
      <button class="act" id="btn_bank_submit" onclick="bankDrillSubmit()">提交作答</button>
      <button class="act gray" onclick="bankDrillNext()">跳过</button>
      ${isMistake ? "" : `<button class="mini-btn" id="btn_bank_star" onclick="bankDrillStar()">${it.starred ? "★ 已收藏" : "☆ 收藏"}</button>`}
    </div>
    <div id="bank_result"></div>`;
}

/* 练习中收藏（F9）：直接落库，按钮就地反馈（不重渲染整块，避免丢掉当前选择）。 */
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
    toast(it.starred ? "已收藏（可用「只练收藏」单独刷）" : "已取消收藏");
  } catch (e) { toast(e.message, false); }
}

function bankDrillPick(i) {
  const d = _bankDrill;
  if (d.graded || d.busy) return;
  const it = d.items[d.idx];
  const multi = String(it.type || "") === "X";
  if (!multi) d.sel.clear();
  if (d.sel.has(i)) d.sel.delete(i); else d.sel.add(i);
  const box = $("bank_opts");
  if (box) [...box.children].forEach((el, k) => el.classList.toggle("sel", d.sel.has(k)));
}

function _bankSelAnswer() {
  return [..._bankDrill.sel].sort((a, b) => a - b)
    .map(i => _BANK_LETTERS[i] || "").join("");
}

async function bankDrillSubmit() {
  const d = _bankDrill;
  if (d.graded || d.busy) return;
  const ans = _bankSelAnswer();
  if (!ans) { toast("请先选择选项", false); return; }
  const it = d.items[d.idx];
  const btn = $("btn_bank_submit");
  d.busy = true;
  if (btn) { btn.disabled = true; btn.textContent = "判分中…"; }
  try {
    const r = d.source === "mistakes"
      ? await api(`/api/library/mistakes/${encodeURIComponent(it.id)}/retry`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_answer: ans }) })
      : await api("/api/bank/grade", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bank_id: d.bid, item_id: it.id, user_answer: ans, mode: "practice" }) });
    d.graded = true;
    d.last = r;
    if (r.correct) d.ok += 1; else d.wrong.push(it);
    _bankShowResult(r, ans);
    _bankDrillProgress();
    if (typeof bankGoalRefresh === "function") bankGoalRefresh();   // 每日目标进度（仅题库作答计入）
  } catch (e) { toast(e.message, false); }
  finally {
    d.busy = false;
    const b2 = $("btn_bank_submit");
    if (b2) { b2.disabled = false; b2.textContent = "提交作答"; }
  }
}

function _bankShowResult(r, userAns) {
  const d = _bankDrill;
  const it = d.items[d.idx];
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
    res.innerHTML = `<div class="bank-ans ${r.correct ? "ok" : "no"}">
        <div style="font-weight:600;color:${r.correct ? "var(--good)" : "var(--bad)"}">
          ${r.correct ? "✓ 答对了" : "✗ 答错了"}
        </div>
        <div style="margin-top:6px">你的作答：<b>${esc(userAns || "—")}</b> · 正确答案：<b>${esc(r.answer || "—")}</b></div>
        ${r.analysis ? `<div style="margin-top:8px"><b>解析</b>：${window.mdRender ? mdRender(r.analysis) : esc(r.analysis)}</div>` : ""}
        ${_bankSrcLabel(r) ? `<div style="margin-top:6px"><b>出处</b>：${esc(_bankSrcLabel(r))}</div>` : ""}
        ${r.note ? `<div style="margin-top:8px"><b>我的笔记</b>：${esc(r.note)}</div>` : ""}
        ${!r.correct && r.synced_mistakes ? `<div class="hint" style="margin-top:8px">已自动加入错题本（可在「学习中心 → 错题本」查看与讲解）</div>` : ""}
        ${d.source === "mistakes" && r.correct ? `<div class="hint" style="margin-top:8px">已归档为「已掌握」（错题本里默认被「只看未掌握」隐藏）</div>` : ""}
        ${d.source === "mistakes" && !r.correct ? `<div class="hint" style="margin-top:8px">仍是未掌握——可去「学习中心」用「讲解 / 提问 / 铺卡」继续攻克</div>` : ""}
      </div>
      <div class="btns" style="margin-top:10px">
        <button class="act" onclick="bankDrillNext()">下一题 →</button>
      </div>`;
  }
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `${(it.subtopic || it.topic || "未分类")} · ${it.type || ""} · ${r.correct ? "对" : "错"}`;
}

function bankDrillNext() {
  const d = _bankDrill;
  if (d.busy) return;
  d.idx += 1;
  d.sel = new Set();
  d.graded = false;
  d.last = null;
  bankDrillRender();
}

function bankDrillRetryWrong() {
  const wrong = _bankDrill.wrong;
  if (!wrong.length) return;
  _bankDrill.items = wrong.slice();
  _bankDrill.idx = 0; _bankDrill.sel = new Set(); _bankDrill.graded = false;
  _bankDrill.ok = 0; _bankDrill.wrong = []; _bankDrill.mode = "all";
  bankDrillRender();
}

function bankDrillFinish() {
  const d = _bankDrill;
  const total = d.items.length;
  const pct = total ? Math.round(d.ok / total * 100) : 0;
  const body = $("bank_drill_body");
  if (!body) return;
  body.innerHTML = `${_bankDrillBar()}
    <div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
      <div class="sub">本组练完：答对 <b>${d.ok}</b> / ${total}（${pct}%）
        ${d.wrong.length
          ? (d.source === "mistakes"
            ? `<br>还有 ${d.wrong.length} 道没答对——继续练或去「学习中心」讲解 / 提问`
            : `<br>做错 ${d.wrong.length} 道——已回流到「学习中心 → 错题本」`)
          : "<br>全部答对，漂亮"}</div>
    </div>
    <div class="btns" style="justify-content:center;margin-top:12px">
      ${d.wrong.length ? `<button class="act" onclick="bankDrillRetryWrong()">只练本次错题（${d.wrong.length}）</button>` : ""}
      <button class="act gray" onclick="bankDrillMode('all')">再练一遍</button>
      <button class="act gray" onclick="loadBankHome()">返回题库列表</button>
    </div>`;
  const meta = $("bank_drill_meta");
  if (meta) meta.textContent = `完成 ${total} 题 · 答对 ${d.ok}`;
  if (typeof loadBankHome === "function") loadBankHome(true);   // 只刷新列表数据，保留总结页
}

window.bankDrillStart = bankDrillStart;
window.bankDrillRender = bankDrillRender;
window.bankDrillPick = bankDrillPick;
window.bankDrillSubmit = bankDrillSubmit;
window.bankDrillNext = bankDrillNext;
window.bankDrillMode = bankDrillMode;
window.bankDrillFinish = bankDrillFinish;
window.bankDrillRetryWrong = bankDrillRetryWrong;
window.bankDrillStar = bankDrillStar;
window.bankDrillStartMistakes = bankDrillStartMistakes;
