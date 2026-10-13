/* exported _bankState, bankCreateOpen, bankDelete, bankExportPaper, bankGoalLoad, bankGoalRefresh,
   bankGoalSave, bankImportPick, bankItemCancel, bankItemDel, bankItemEdit, bankItemNew, bankItemSave,
   bankItemStar, bankManageClose, bankManageOpen, bankManageRender, bankPasteClose, bankPasteOpen,
   bankPreviewPaste, bankRename, bankReparse, bankRetryStart, bankRowHTML, bankStartDrill, bankStats,
   loadBankHome */
/* ---- 题库域（bank）：我的题库列表 / 新建 / 重命名 / 删除 / 进入练习 ----
 *
 * 定位：**管理与练习已有题**（零 LLM）。与「题库」页签（出题工坊，烧 LLM token 生成新题）是两件事，
 * 故两者在侧栏分属不同模式（本域属「错题」模式）。
 *
 * 数据来源：`/api/bank/*`（`routers/bank.py` → `core/bank.py`）。题目**不内置、不分发**——
 * 用户导入自备 md/json/csv/txt，数据权利归各自来源方。
 *
 * 分片约定：本片只放「题库列表 + 元操作」；导入向导在 `bank-import.js`、作答引擎在 `bank-drill.js`。
 * 三片同族、按此顺序加载（经典脚本共享全局作用域，顺序即契约）。
 */
let _bankState = { banks: [], current: "", stats: null };

async function loadBankHome(keepDrill) {
  const box = $("bank_list");
  if (!box) return;
  // 「题目管理」卡在切回列表时收起（keepDrill=true 保留）。
  // ⚠️ 练习层（#bank_drill_card）的显隐**不在这里管**——它由 bank-drill.js 专门管理：
  // 切页签走 `bankDrillHide`、进入练习走 `bankDrillStart*`。此处若也设 `display:none`，
  // 会与本函数内 `await api(...)` 的异步返回形成竞态：`bankStartDrill()` 里
  // `loadBankHome()` 与 `bankDrillStart()` 同时起飞，列表先回来时会把刚打开的作答层又隐藏掉。
  if (!keepDrill) {
    const mc = $("bank_manage_card"); if (mc) mc.style.display = "none";
  }
  try {
    const r = await api("/api/bank/banks");
    _bankState.banks = r.banks || [];
    const total = _bankState.banks.reduce((n, b) => n + (b.items || 0), 0);
    const meta = $("bank_meta");
    if (meta) meta.textContent = `${_bankState.banks.length} 个题库 · ${total} 道题`;
    box.innerHTML = _bankState.banks.length
      ? _bankState.banks.map(bankRowHTML).join("")
      : `<div class="empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-doc"></use></svg>
          <div class="sub">还没有题库<br>点上方「导入题库文件」或「粘贴文本导入」，把你自备的 md 题库收进来</div>
        </div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint">题库加载失败：${esc(e.message)}</div>`;
  }
}

function _pct(v) { return Math.round((Number(v) || 0) * 100); }

function bankRowHTML(b) {
  const on = _bankState.current === String(b.id);
  return `<div class="bank-item${on ? " on" : ""}">
    <div class="bank-item-main">
      <div class="bank-item-name">${esc(b.name || "未命名题库")}
        <span class="tag">${esc(b.subject || "未分类")}</span>
        ${b.source ? `<span class="tag">${esc(b.source)}</span>` : ""}
      </div>
      <div class="bank-item-meta">
        <span>题目 <b>${b.items || 0}</b></span>
        <span>已练 <b>${b.attempted || 0}</b></span>
        <span>掌握率 <b>${_pct(b.mastery)}%</b></span>
        <span>正确率 <b>${_pct(b.accuracy)}%</b></span>
        <span>${esc((b.updated_at || b.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
    </div>
    <div class="bank-item-actions">
      <button class="mini-btn primary" data-id="${esc(b.id)}" onclick="bankStartDrill(this)">开始练习</button>
      <button class="mini-btn" data-id="${esc(b.id)}" onclick="bankManageOpen(this)">管理题目</button>
      <button class="mini-btn" data-id="${esc(b.id)}" onclick="bankStats(this)">统计</button>
      <button class="mini-btn" data-id="${esc(b.id)}" onclick="bankExportPaper(this)">导出押题卷</button>
      ${b.has_source ? `<button class="mini-btn" data-id="${esc(b.id)}" onclick="bankReparse(this)" title="用保存的导入原文重新解析（解析器升级后可修旧数据）">重新解析${b.parser_stale ? " ⟳" : ""}</button>` : ""}
      <button class="mini-btn" data-id="${esc(b.id)}" onclick="bankRename(this)">重命名</button>
      <button class="mini-btn danger" data-id="${esc(b.id)}" onclick="bankDelete(this)">删除</button>
    </div>
  </div>`;
}

/* ---- 新建 / 重命名 / 删除 ---- */
function bankCreateOpen() {
  askModal("新建空题库", "题库名称", "如：内科学·呼吸系统", async (name) => {
    try {
      await api("/api/bank/banks", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, subject: "", source: "manual" }),
      });
      toast(`已创建题库「${name}」——可继续导入题目`);
      loadBankHome();
    } catch (e) { toast(e.message, false); }
  });
}

async function bankRename(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  const cur = (_bankState.banks.find(b => String(b.id) === String(bid)) || {});
  askModal("重命名题库", "新名称", cur.name || "", async (name) => {
    try {
      await api(`/api/bank/banks/${encodeURIComponent(bid)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, subject: cur.subject || "", source: cur.source || "" }),
      });
      toast("已重命名");
      loadBankHome();
    } catch (e) { toast(e.message, false); }
  }, cur.name || "");
}

/* ---- 重新解析（解析器改进后修旧数据；需要导入时保存了原文） ---- */
function bankReparse(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  const b = _bankState.banks.find(x => String(x.id) === String(bid)) || {};
  confirmModal("重新解析题库？",
    `<p style="margin:0;color:var(--dim)">用保存的<b>导入原文</b>重新解析题库 <b>${esc(b.name || bid)}</b>，
     替换其中全部 <b>${b.items || 0}</b> 道题。</p>
     <p style="margin:6px 0 0;color:var(--dim)"><b>该题库的作答进度会被清空</b>（题目会重新编号）；
     已回流到错题本的错题<b>不受影响</b>。</p>`,
    "重新解析", async () => {
      try {
        const r = await api(`/api/bank/banks/${encodeURIComponent(bid)}/reparse`, { method: "POST" });
        toast(`已重新解析：${r.items || 0} 题` + (r.attempts_cleared ? `（清空作答 ${r.attempts_cleared} 条）` : ""));
        if (String(_bankState.current) === String(bid)) _bankState.current = "";
        await loadBankHome();
      } catch (e) { toast(e.message, false); }
    });
}

function bankDelete(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  const b = _bankState.banks.find(x => String(x.id) === String(bid)) || {};
  confirmModal("删除题库？",
    `<p style="margin:0;color:var(--dim)">将删除题库 <b>${esc(b.name || bid)}</b> 及其 <b>${b.items || 0}</b> 道题、
     全部作答记录。<br><span class="hint">删除前会自动导出完整备份 JSON 到本地 exports 目录（可追溯，不静默不可恢复）；
     已回流到错题本的错题<b>不受影响</b>。</span></p>`,
    "导出并删除", async () => {
      try {
        const r = await api(`/api/bank/banks/${encodeURIComponent(bid)}`, { method: "DELETE" });
        toast(`已删除（题目 ${r.items || 0} · 作答 ${r.attempts || 0}）` + (r.backup ? "，已备份" : ""));
        if (String(_bankState.current) === String(bid)) {
          _bankState.current = "";
          const dc = $("bank_drill_card"); if (dc) dc.style.display = "none";
        }
        // 被删的正是「题目管理」当前打开的那个 → 一并收起（否则卡片留在屏幕上显示已不存在的题）
        const mc = $("bank_manage_card");
        if (mc && mc.style.display !== "none" && String((_bankState.manage || {}).bid) === String(bid)) {
          mc.style.display = "none";
          _bankState.manage = null;
        }
        loadBankHome();
      } catch (e) { toast(e.message, false); }
    });
}

/* ---- 统计弹窗 ---- */
async function bankStats(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  try {
    const r = await api(`/api/bank/banks/${encodeURIComponent(bid)}/stats`);
    const s = r.stats || {};
    const weak = (s.weak_topics || []).filter(t => t.attempts > 0).slice(0, 8);
    const body = `<div class="dash-metrics">
        <div class="dash-metric"><b>${s.items || 0}</b><span>题目</span></div>
        <div class="dash-metric"><b>${s.attempted || 0}</b><span>已练</span></div>
        <div class="dash-metric"><b>${_pct(s.mastery)}%</b><span>掌握率</span></div>
        <div class="dash-metric"><b>${_pct(s.accuracy)}%</b><span>正确率</span></div>
      </div>
      <p class="hint" style="margin:10px 0 6px">掌握率 = 最后一次作答为「对」的题 / 全部题；正确率 = 做对次数 / 作答次数。</p>
      <h3 style="font-size:13px;margin:12px 0 6px">薄弱考点（按正确率升序）</h3>
      ${weak.length ? weak.map(t => `<div style="display:flex;gap:10px;align-items:center;padding:5px 0;border-bottom:1px dashed var(--line)">
          <span style="flex:1;font-size:12.5px">${esc(t.topic || "未分类")}</span>
          <span class="hint" style="font-size:11.5px">对 ${t.correct}/${t.attempts}</span>
          <b style="font-size:12.5px;color:${t.accuracy < 0.6 ? "var(--bad)" : "var(--text)"}">${_pct(t.accuracy)}%</b>
        </div>`).join("") : `<div class="hint">还没有作答记录——先「开始练习」。</div>`}`;
    confirmModal("题库统计", body, "关闭", () => {}, false);
  } catch (e) { toast(e.message, false); }
}

/* ---- 题目管理（F2 编辑 / F9 收藏·笔记）----
   导入的题库常有解析偏差（答案错位、选项切分不对）。不给编辑通道，用户只能删库重导——
   而重导要重新解析整份 md，代价远大于改一个字段。故与导入**共用同一条归一通道**
   （`bank_iq.normalize_item`），保证「手改出来的题」与「导入进来的题」形状一致。 */
function bankManageOpen(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  if (!bid) return;
  _bankState.current = String(bid);
  const card = $("bank_manage_card");
  const list = $("bank_item_list");
  if (card) card.style.display = "";
  if (list) list.innerHTML = `<div class="hint"><span class="spin"></span>加载题目…</div>`;
  const dc = $("bank_drill_card"); if (dc) dc.style.display = "none";
  api(`/api/bank/banks/${encodeURIComponent(bid)}`).then(r => {
    _bankState.manage = { bid: String(bid), items: r.items || [], editing: "" };
    bankItemCancel();
    bankManageRender();
  }).catch(e => { if (list) list.innerHTML = `<div class="hint">加载失败：${esc(e.message)}</div>`; });
}

function bankManageClose() {
  const card = $("bank_manage_card"); if (card) card.style.display = "none";
  bankItemCancel();
}

function bankManageRender() {
  const m = _bankState.manage || { items: [] };
  const list = $("bank_item_list");
  const meta = $("bank_manage_meta");
  const bank = (_bankState.banks || []).find(b => String(b.id) === String(m.bid)) || {};
  if (meta) meta.textContent = `${bank.name || ""} · ${m.items.length} 道`;
  if (!list) return;
  if (!m.items.length) {
    list.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-doc"></use></svg>
      <div class="sub">这个题库还没有题目<br>点上方「手动加题」，或回列表用「导入题库文件」</div></div>`;
    return;
  }
  list.innerHTML = `<table class="bank-preview-table">
    <thead><tr><th>★</th><th>题干</th><th>题型</th><th>答案</th><th>笔记</th><th></th></tr></thead>
    <tbody>${m.items.map(it => `<tr>
      <td><button class="mini-btn" data-id="${esc(it.id)}" onclick="bankItemStar(this)"
            title="收藏（练习时可用「只练收藏」）">${it.starred ? "★" : "☆"}</button></td>
      <td>${esc((it.question || "").slice(0, 70))}</td>
      <td>${esc(it.type || "")}</td>
      <td>${esc(it.answer || "—")}</td>
      <td>${it.note ? esc(it.note.slice(0, 20)) : '<span class="hint">—</span>'}</td>
      <td style="white-space:nowrap">
        <button class="mini-btn" data-id="${esc(it.id)}" onclick="bankItemEdit(this)">编辑</button>
        <button class="mini-btn danger" data-id="${esc(it.id)}" onclick="bankItemDel(this)">删除</button>
      </td>
    </tr>`).join("")}</tbody></table>`;
}

function _bankItemFormHTML(it) {
  it = it || {};
  const types = ["A1", "A2", "A3", "A4", "B1", "X"];
  return `<div class="card" style="background:var(--card2);margin-bottom:12px">
    <label>题干</label>
    <textarea id="bi_q" rows="3">${esc(it.question || "")}</textarea>
    <label style="margin-top:8px">选项（每行一个，<b>不用写字母</b>——字母由系统补）</label>
    <textarea id="bi_opts" rows="4">${esc((it.options || []).join("\n"))}</textarea>
    <div class="row" style="margin-top:8px">
      <div><label>答案（如 A / AC）</label><input type="text" id="bi_ans" value="${esc(it.answer || "")}"></div>
      <div><label>题型</label><select id="bi_type">${types.map(t =>
        `<option value="${t}"${(it.type || "A1") === t ? " selected" : ""}>${t}</option>`).join("")}</select></div>
      <div><label>考点（可选）</label><input type="text" id="bi_sub" value="${esc(it.subtopic || "")}"></div>
    </div>
    <label style="margin-top:8px">解析（可选）</label>
    <textarea id="bi_ana" rows="3">${esc(it.analysis || "")}</textarea>
    <label style="margin-top:8px">我的笔记（可选，判分后才显示）</label>
    <textarea id="bi_note" rows="2">${esc(it.note || "")}</textarea>
    <label class="chk" style="margin-top:8px"><input type="checkbox" id="bi_star"${it.starred ? " checked" : ""}> 收藏（★）</label>
    <div class="btns" style="margin-top:10px">
      <button class="act" onclick="bankItemSave()">保存</button>
      <button class="act gray" onclick="bankItemCancel()">取消</button>
    </div>
  </div>`;
}

function bankItemNew() {
  _bankState.manage = _bankState.manage || { bid: "", items: [], editing: "" };
  _bankState.manage.editing = "";
  const box = $("bank_item_form");
  if (box) box.innerHTML = _bankItemFormHTML({ type: "A1" });
}

function bankItemEdit(btn) {
  const id = btn && btn.dataset ? btn.dataset.id : btn;
  const m = _bankState.manage || {};
  const it = (m.items || []).find(x => String(x.id) === String(id));
  if (!it) return;
  m.editing = String(id);
  const box = $("bank_item_form");
  if (box) box.innerHTML = _bankItemFormHTML(it);
}

function bankItemCancel() {
  const box = $("bank_item_form");
  if (box) box.innerHTML = "";
  if (_bankState.manage) _bankState.manage.editing = "";
}

async function bankItemSave() {
  const m = _bankState.manage || {};
  if (!m.bid) return;
  const body = {
    question: ($("bi_q") || {}).value || "",
    options: (($("bi_opts") || {}).value || "").split("\n").map(s => s.trim()).filter(Boolean),
    answer: ($("bi_ans") || {}).value || "",
    type: ($("bi_type") || {}).value || "A1",
    subtopic: ($("bi_sub") || {}).value || "",
    analysis: ($("bi_ana") || {}).value || "",
    note: ($("bi_note") || {}).value || "",
    starred: !!($("bi_star") || {}).checked,
  };
  if (!body.question.trim()) { toast("题干不能为空", false); return; }
  try {
    if (m.editing) {
      await api(`/api/bank/items/${encodeURIComponent(m.editing)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      toast("已保存");
    } else {
      const r = await api(`/api/bank/banks/${encodeURIComponent(m.bid)}/items`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      toast(`已加入 ${r.added || 1} 道题`);
    }
    bankItemCancel();
    await bankManageOpen(m.bid);
    loadBankHome(true);
  } catch (e) { toast(e.message, false); }
}

async function bankItemDel(btn) {
  const id = btn && btn.dataset ? btn.dataset.id : btn;
  const m = _bankState.manage || {};
  confirmModal("删除这道题？",
    `<p style="margin:0;color:var(--dim)">删除后该题的作答记录一并移除，不可恢复。</p>`,
    "删除", async () => {
      try {
        await api(`/api/bank/items/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("已删除");
        await bankManageOpen(m.bid);
        loadBankHome(true);
      } catch (e) { toast(e.message, false); }
    });
}

async function bankItemStar(btn) {
  const id = btn && btn.dataset ? btn.dataset.id : btn;
  const m = _bankState.manage || {};
  const it = (m.items || []).find(x => String(x.id) === String(id));
  if (!it) return;
  try {
    const r = await api(`/api/bank/items/${encodeURIComponent(id)}/star`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred: !it.starred }) });
    it.starred = !!r.item.starred;
    bankManageRender();
  } catch (e) { toast(e.message, false); }
}

/* ---- 每日目标（B7）----
   目标存 localStorage（纯个人偏好，不进库）；进度取自 `bank_attempts` 的**今日**计数
   （`GET /api/bank/today`）——统计口径与题库统计同源，不在前端另记一套。 */
const BANK_GOAL_KEY = "medkit-bank-goal";

function bankGoalLoad() {
  let v = 20;
  try {
    const raw = localStorage.getItem(BANK_GOAL_KEY);
    if (raw !== null && raw !== "" && !isNaN(Number(raw))) v = Math.max(0, Math.min(Number(raw), 500));
  } catch (e) { /* 隐私模式：用默认值 */ }
  const inp = $("bank_goal");
  if (inp) inp.value = String(v);
  return v;
}

function bankGoalSave() {
  const inp = $("bank_goal");
  const v = Math.max(0, Math.min(parseInt((inp && inp.value) || "0", 10) || 0, 500));
  if (inp) inp.value = String(v);
  try { localStorage.setItem(BANK_GOAL_KEY, String(v)); } catch (e) { /* ignore */ }
  bankGoalRefresh();
}

async function bankGoalRefresh() {
  const el = $("bank_goal_prog");
  if (!el) return;
  const goal = bankGoalLoad();
  try {
    const r = await api("/api/bank/today");
    const done = r.attempts || 0;
    const okRate = done ? Math.round((r.correct || 0) / done * 100) : 0;
    const okLabel = done ? `${okRate}%` : "—";
    if (!goal) { el.textContent = `今日已练 ${done} 题 · 正确率 ${okLabel}`; return; }
    const pct = Math.min(100, Math.round(done / goal * 100));
    el.innerHTML = `今日已练 <b>${done}</b>/${goal} 题（${pct}%）· 正确率 ${okLabel}`
      + (done >= goal ? ' <b style="color:var(--good)">已达标 ✓</b>' : "");
  } catch (e) { el.textContent = "今日进度加载失败"; }
}

/* ---- 错题重练（F7 增强；作答引擎在 bank-drill.js） ---- */
function bankRetryStart() {
  if (typeof bankDrillStartMistakes === "function") bankDrillStartMistakes("all");
}

/* ---- 导出回流：题库 → 交互押题卷 HTML（复用成熟渲染器，零 LLM） ----
   走**新开标签页**（`/api/bank/.../export/paper` 缺省 inline）——与项目产物页同口径，
   打开即可作答判分（离线可用，单文件无外部依赖）。 */
function bankExportPaper(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  if (!bid) return;
  window.open(`/api/bank/banks/${encodeURIComponent(bid)}/export/paper`, "_blank", "noopener");
}

/* ---- 进入练习（作答引擎在 bank-drill.js） ---- */
function bankStartDrill(btn) {
  const bid = btn && btn.dataset ? btn.dataset.id : btn;
  _bankState.current = String(bid || "");
  loadBankHome();
  if (typeof bankDrillStart === "function") bankDrillStart(_bankState.current, "all");
}
window.loadBankHome = loadBankHome;
window.bankCreateOpen = bankCreateOpen;
window.bankRename = bankRename;
window.bankDelete = bankDelete;
window.bankReparse = bankReparse;
window.bankStats = bankStats;
window.bankStartDrill = bankStartDrill;
window.bankExportPaper = bankExportPaper;
window.bankManageOpen = bankManageOpen;
window.bankManageClose = bankManageClose;
window.bankManageRender = bankManageRender;
window.bankItemNew = bankItemNew;
window.bankItemEdit = bankItemEdit;
window.bankItemSave = bankItemSave;
window.bankItemCancel = bankItemCancel;
window.bankItemDel = bankItemDel;
window.bankItemStar = bankItemStar;
window.bankGoalLoad = bankGoalLoad;
window.bankGoalSave = bankGoalSave;
window.bankGoalRefresh = bankGoalRefresh;
window.bankRetryStart = bankRetryStart;

/* ================================================================ 「刷题」页（tab-study）入口
 *
 * v0.13.0：把侧栏「刷题」做成**医考帮式选择题刷题主入口**——两个子视图：
 *   - `do`（做题，默认）：选题库/错题 + 模式 → 进入沉浸式作答（引擎在 bank-drill.js）；
 *   - `review`（复习）：原 SM-2 闪卡复习（数据由 EP-01 的 `loadStudy()` 加载，职责不变）。
 *
 * 依赖方向：本片（BANK）→ 外壳 `setStudyKeys` / EP-01 `loadStudy`，一律 `typeof` 守卫
 * （缺失即不挂快捷键/不预加载，不炸）——与既有 `openDrillFor` 同范式。
 */
let studyViewCur = "do";

function studyView(name) {
  name = name === "review" ? "review" : (name === "do" ? "do" : studyViewCur || "do");
  studyViewCur = name;
  const nav = $("studynav");
  if (nav) [...nav.querySelectorAll(".css-pill")].forEach(b => {
    const on = b.dataset.sv === name;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  const dv = $("sv-do"), rv = $("sv-review");
  if (dv) dv.style.display = name === "do" ? "" : "none";
  if (rv) rv.style.display = name === "review" ? "" : "none";
  // 复习卡的 1/2/3 自评快捷键只在复习视图挂（否则与做题的选项快捷键抢键）
  if (typeof setStudyKeys === "function") setStudyKeys(name === "review");
  if (name === "review") { if (typeof loadStudy === "function") loadStudy(); }
  else loadDoEntry();
}

/* 做题入口数据：题库下拉 + 快捷开始 + 今日进度（每次进「刷题」页刷新） */
async function loadDoEntry() {
  const sel = $("do_bank");
  try {
    const r = await api("/api/bank/banks");
    _bankState.banks = r.banks || [];
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">— 选择题库 —</option>'
        + _bankState.banks.map(b =>
            `<option value="${esc(b.id)}">${esc(b.name)}（${b.items || 0} 题）</option>`).join("");
      if (cur && _bankState.banks.some(b => String(b.id) === String(cur))) sel.value = cur;
      _bankState.current = sel.value;
    }
  } catch (e) { /* 列表加载失败不阻断入口（下拉留空，用户仍可去「练习」页导入） */ }
  _doQuickRender();
  _doTodayRender();
  bankGoalRefresh();
}

function _doQuickRender() {
  const box = $("do_quick");
  if (!box) return;
  const banks = (_bankState.banks || []).filter(b => (b.items || 0) > 0);
  if (!banks.length) {
    box.innerHTML = `<div class="hint">还没有题库——去「练习」页「导入题库文件」把你自备的
      md / txt / json / csv 题库收进来，或先「新建空题库」手动加题。</div>`;
    return;
  }
  const top = banks.slice(0, 6);
  box.innerHTML = `<div class="hint" style="margin-bottom:6px">快捷开始</div>
    <div class="do-quick-grid">` + top.map(b => `
      <button class="do-quick-card" data-id="${esc(b.id)}" onclick="doStartFrom(this)"
              title="开始刷「${esc(b.name)}」">
        <b>${esc(b.name)}</b>
        <span class="hint">${b.items || 0} 题 · 已练 ${b.attempted || 0} · 掌握 ${_pct(b.mastery)}%</span>
      </button>`).join("") + `</div>`;
}

/* 卡片头右侧：题库总量（今日进度在下方 `#bank_goal_prog`，避免两处重复同一数字） */
function _doTodayRender() {
  const el = $("do_today");
  if (!el) return;
  const banks = _bankState.banks || [];
  const total = banks.reduce((n, b) => n + (b.items || 0), 0);
  el.textContent = banks.length ? `${banks.length} 个题库 · ${total} 道题` : "";
}

function doBankChange() {
  const sel = $("do_bank");
  _bankState.current = sel ? sel.value : "";
}

function _doOpts() {
  return {
    mode: (($("do_mode") || {}).value) || "all",
    runmode: (($("do_runmode") || {}).value) || "practice",
  };
}

function doStart() {
  const sel = $("do_bank");
  const bid = sel ? sel.value : "";
  if (!bid) { toast("请先选择题库", false); return; }
  const o = _doOpts();
  _bankState.current = String(bid);
  if (typeof bankDrillStart === "function") bankDrillStart(bid, o.mode, { runmode: o.runmode });
}

function doStartFrom(btn) {
  const bid = btn && btn.dataset ? (btn.dataset.id || "") : "";
  if (!bid) return;
  const sel = $("do_bank"); if (sel) sel.value = String(bid);
  _bankState.current = String(bid);
  const o = _doOpts();
  if (typeof bankDrillStart === "function") bankDrillStart(bid, o.mode, { runmode: o.runmode });
}

function doStartMistakes() {
  const o = _doOpts();
  if (typeof bankDrillStartMistakes === "function") bankDrillStartMistakes("all", "", { runmode: o.runmode });
}

window.studyView = studyView;
window.loadDoEntry = loadDoEntry;
window.doBankChange = doBankChange;
window.doStart = doStart;
window.doStartFrom = doStartFrom;
window.doStartMistakes = doStartMistakes;
