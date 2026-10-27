/* exported vaultRebuild, vaultSearch, vaultSearchClear, vaultAsk, vaultAskGlobal,
   vaultClose, vaultEdit, vaultOpen, vaultFilter, vaultFilterClear, vaultNewToggle,
   vaultPickPhoto, vaultPhotoPick, vaultCreate, vaultNewClear, vaultGrade,
   vaultReviewLoad, vaultLoad */

/* ---- 本地错题（vault）视图：读文件索引 + 薄弱项 + Agent 问答 ----
 *
 * 设计要点（见 docs/错题本地文件化与Agent助手_设计方案_2026-10-13.md）：
 * - 文件是真身，本节所有内容都从 /api/vault/* 读；写回只有「保存我的理解/疑问」
 *   与「把 Agent 答复存进这个错题」两个入口，且都要用户点确认。
 * - Agent 不在线时全部功能照常（本节没有一处依赖 ask 的结果）。
 */

let _vaultItems = [];
let _vaultCur = "";      // 当前打开的错题（索引里的相对路径）
let _vaultListSeq = 0;   // 列表请求序号：晚到的过期响应不许覆盖更新的结果（防闪烁/丢筛选）
let _vaultFilter = { subject: "", chapter: "", topic: "" };   // 薄弱项表点行 → 按组筛错题列表

function _esc(s) { const d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; }

async function vaultStatus() {
  try {
    const st = await api("/api/vault/status");
    const box = $("vault_status");
    if (box) {
      box.innerHTML = st.is_vault
        ? `文件夹：<code>${_esc(st.root)}</code> ｜ 已索引 <b>${st.count}</b> 条`
        : `还没有创建错题文件夹（默认 <code>${_esc(st.default_root)}</code>）`;
    }
    // 复习手册的科目选择器：选项随 vault 里的科目集刷新（保留已选）
    const hb = $("vault_hb_subject");
    if (hb) {
      const cur = hb.value, subs = st.subjects || [];
      hb.innerHTML = '<option value="">全部科目</option>'
        + subs.map(s => `<option value="${_esc(s)}">${_esc(s)}</option>`).join("");
      if (cur && subs.includes(cur)) hb.value = cur;
    }
    return st;
  } catch (e) { toast(e.message, false); return null; }
}

async function vaultRebuild() {
  try {
    const r = await api("/api/vault/rebuild", { method: "POST" });
    toast(`索引已重建：扫描 ${r.scanned} · 新增 ${r.added} · 重解析 ${r.reparsed} · 移除 ${r.removed}`
      + (r.warnings ? ` · ${r.warnings} 条告警` : ""));
    await vaultLoad();
  } catch (e) { toast("重建失败：" + e.message, false); }
}

function _stateLabel(s) {
  return ({ unlearned: "未掌握", learned: "已掌握", archived: "已归档" })[s] || s || "未掌握";
}

async function vaultLoad() {
  await vaultStatus();
  try {
    const st = await api("/api/vault/stats");
    const box = $("vault_stats");
    if (!box) return;
    const g = (st.groups || []).filter(x => x.total > 0);
    if (!st.total) {
      // 空态统一为「图标 + 为什么空 + 一个动作」：不停在"还没有错题"这句话上，直接给下一步
      box.innerHTML = `<div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
        <div class="sub">还没有错题<br>刷题判错的题会自动流进来，也可以手动记一条</div>
        <button class="act gray" onclick="vaultNewToggle()">＋ 新建错题</button>
      </div>`;
    } else if (!g.length) {
      box.innerHTML = `<div class="hint">共 ${st.total} 条，暂无需要优先处理的薄弱项。</div>`;
    } else {
      box.innerHTML = `<table class="bank-preview-table"><thead><tr>
          <th>科目</th><th>章节</th><th>主题</th><th>错题</th><th>未掌握</th><th>状态</th><th>优先级</th>
        </tr></thead><tbody>` + g.slice(0, 12).map(x => `<tr class="vault-statrow"
          title="点一行看这组错题"
          data-sub="${_esc(x.subject)}" data-ch="${_esc(x.chapter)}" data-tp="${_esc(x.topic)}">
          <td>${_esc(x.subject)}</td><td>${_esc(x.chapter)}</td><td>${_esc(x.topic)}</td>
          <td>${x.total}</td><td>${x.unlearned}</td><td>${_stateLabel(x.state)}</td>
          <td>${(x.priority * 100).toFixed(0)}</td></tr>`).join("") + `</tbody></table>`;
      // 用 dataset + 委托，而不是把值拼进 onclick 字符串——主题里带引号时不会逃逸出 JS。
      box.querySelectorAll(".vault-statrow").forEach(tr => {
        tr.onclick = () => vaultFilter(tr.dataset.sub, tr.dataset.ch, tr.dataset.tp);
      });
    }
    const meta = $("vault_list_meta");
    if (meta) meta.textContent = `共 ${st.total} 条 · 未掌握 ${st.unlearned} 条`;
    await vaultRefreshList();
  } catch (e) { toast("读取失败：" + e.message, false); }
}

async function vaultRefreshList() {
  const q = ($("vault_q") && $("vault_q").value.trim()) || "";
  const f = _vaultFilter;
  const qs = new URLSearchParams({ q });
  if (f.subject) qs.set("subject", f.subject);
  if (f.chapter) qs.set("chapter", f.chapter);
  if (f.topic) qs.set("topic", f.topic);
  const seq = ++_vaultListSeq;
  try {
    const r = await api("/api/vault/mistakes?" + qs.toString());
    if (seq !== _vaultListSeq) return;   // 期间又发起了更新的请求 → 丢弃本次过期结果
    _vaultItems = r.items || [];
    const box = $("vault_list");
    if (box) {
      // 2026-10-16 修复（L-7）：路径改走 data-* + 事件绑定，不再拼进 onclick 字符串。
      // 旧写法 `_esc(r.path).replace(/'/g,"\\'")` 无效——_esc 已把 ' 转成 &#39;，
      // HTML 属性实体解码后 JS 字符串即断裂（路径含引号时语法错误/注入面）。
      box.innerHTML = _vaultItems.length ? _vaultItems.map(r => `
        <div class="vault-row" role="button" tabindex="0" data-path="${_esc(r.path)}">
          <span class="vault-q">${_esc(r.question) || "（无题干）"}</span>
          <span class="vault-meta">${_esc([r.subject, r.chapter, r.topic].filter(Boolean).join(" · "))}
            ${r.understanding ? " · 有我的理解" : ""}${r.doubts ? " · 有疑问" : ""}</span>
        </div>`).join("")
        : (q || f.subject || f.chapter || f.topic)
        ? `<div class="empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-target"></use></svg>
            <div class="sub">没有命中的错题<br>换个关键词，或清掉搜索与筛选看全部</div>
            <button class="act gray" onclick="vaultSearchClear()">清除搜索与筛选</button>
          </div>`
        : `<div class="empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-paper"></use></svg>
            <div class="sub">还没有错题<br>刷题判错的题会自动流进来，也可以手动记一条</div>
            <button class="act gray" onclick="vaultNewToggle()">＋ 新建错题</button>
          </div>`;
      box.querySelectorAll(".vault-row").forEach(el => {
        const open = () => vaultOpen(el.dataset.path || "");
        el.onclick = open;
        el.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
      });
    }
    const cl = $("btn_vault_q_clear");
    if (cl) cl.style.display = q ? "" : "none";
    const parts = [f.subject, f.chapter, f.topic].filter(Boolean);
    const fb = $("vault_filter_bar"), fr = $("vault_filter_row");
    if (fb) fb.textContent = parts.length ? `筛选：${parts.join(" / ")}` : "";
    if (fr) fr.style.display = parts.length ? "" : "none";
  } catch (e) { toast("列表读取失败：" + e.message, false); }
}

function vaultSearch() { vaultRefreshList(); }
function vaultSearchClear() { $("vault_q").value = ""; vaultRefreshList(); }

/* 薄弱项表点一行：把该组（科目/章节/主题）的错题筛出来——让「先学什么」直接落到「看哪些题」 */
function vaultFilter(subject, chapter, topic) {
  _vaultFilter = { subject: subject || "", chapter: chapter || "", topic: topic || "" };
  vaultRefreshList();
}
function vaultFilterClear() {
  _vaultFilter = { subject: "", chapter: "", topic: "" };
  vaultRefreshList();
}

async function vaultOpen(rel) {
  _vaultCur = rel;
  const it = _vaultItems.find(x => x.path === rel) || {};
  const box = $("vault_detail");
  if (!box) return;
  box.style.display = "";
  box.innerHTML = `
    <div class="cardh"><h2>${_esc(it.question) || "（无题干）"}</h2>
      <span class="hint">${_esc(rel)}</span></div>
    <div class="hint" style="margin-bottom:var(--space-2)">${_esc(it.analysis || "（无解析）")}</div>
    <details style="margin:6px 0"><summary class="hint">答案（默认收起，防剧透）</summary>
      <div class="hint">${_esc(it.answer || "（无答案）")}</div></details>
    <label for="vlt_und">我的理解（你自己写，可空）</label>
    <textarea id="vlt_und" rows="3" placeholder="当时为什么选错？现在怎么理解？">${_esc(it.understanding || "")}</textarea>
    <label for="vlt_doubts" style="margin-top:var(--space-2)">我的疑问（Agent 会逐条回应）</label>
    <textarea id="vlt_doubts" rows="3" placeholder="哪里还不确定？">${_esc(it.doubts || "")}</textarea>
    <div class="btns" style="margin-top:var(--space-2)">
      <button class="act" onclick="vaultEdit()">保存理解与疑问</button>
      <button class="act gray" onclick="vaultClose()">收起</button>
    </div>
    <label for="vlt_q" style="margin-top:var(--space-3)">问错题助手（只依据你这个文件夹作答，答不出就说不知道）</label>
    <div class="row" style="gap:var(--space-2)">
      <input type="text" id="vlt_q" style="flex:2" placeholder="例如：稳定期会用抗菌药吗？"
             onkeydown="if(event.key==='Enter'){vaultAsk();}">
      <button class="act" id="btn_vault_ask" onclick="vaultAsk()">提问</button>
    </div>
    <div class="hint" style="margin-top:6px">回答会出现在下面的<b>对话区</b>里，可以接着追问（它记得上文）。</div>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function vaultClose() {
  const box = $("vault_detail");
  if (box) box.style.display = "none";
  _vaultCur = "";
}

async function vaultEdit() {
  if (!_vaultCur) return;
  try {
    const und = $("vlt_und").value.trim(), db = $("vlt_doubts").value.trim();
    await api(`/api/vault/mistakes/${encodeURI(_vaultCur)}/section`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "understanding", text: und }) });
    await api(`/api/vault/mistakes/${encodeURI(_vaultCur)}/section`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "doubts", text: db }) });
    toast("已保存到文件");
    await vaultRefreshList();
  } catch (e) { toast("保存失败：" + e.message, false); }
}

/* ---- 新建错题：粘贴 / 拍题 → 直接成 md ---- */
function vaultNewToggle() {
  const f = $("vault_new_form");
  if (f) f.style.display = f.style.display === "none" ? "" : "none";
}

function vaultPickPhoto() { const el = $("nt_photo"); if (el) el.click(); }

async function vaultPhotoPick(inp) {
  const f = inp && inp.files && inp.files[0];
  if (!f) return;
  const btn = $("btn_nt_photo");
  if (btn) btn.disabled = true;
  try {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/library/mistakes/import-image", { method: "POST", body: fd });
    const q = $("nt_q");
    if (q) q.value = String(r.text || "").slice(0, 4000);
    toast("已识别，请核对题干/选项后再创建");
  } catch (e) { toast("识别失败：" + e.message, false); }
  finally { if (btn) btn.disabled = false; inp.value = ""; }
}

function vaultNewClear() {
  ["nt_sub", "nt_ch", "nt_tp", "nt_q", "nt_opts", "nt_ans", "nt_ana", "nt_und", "nt_doub"]
    .forEach(id => { const el = $(id); if (el) el.value = ""; });
}

async function vaultCreate() {
  const q = ($("nt_q") && $("nt_q").value.trim()) || "";
  if (!q) { toast("先写题干（唯一必填）", false); return; }
  const val = id => ($(id) && $(id).value) || "";
  const opts = val("nt_opts").split("\n").map(s => s.trim()).filter(Boolean);
  try {
    await api("/api/vault/mistakes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q, options: opts, answer: val("nt_ans").trim(), analysis: val("nt_ana"),
        my_reasoning: val("nt_und"), doubts: val("nt_doub"),
        subject: val("nt_sub").trim(), chapter: val("nt_ch").trim(), topic: val("nt_tp").trim() }) });
    toast("已写入错题文件夹（新建即进入今日复习）");
    vaultNewClear();
    await vaultLoad();
    await vaultReviewLoad();
  } catch (e) { toast("创建失败：" + e.message, false); }
}


/* ---- 今日复习：调度器排程（FSRS 默认 / SM-2 legacy）+ 评分回写 ---- */
async function vaultReviewLoad() {
  const box = $("vault_due");
  if (!box) return;
  try {
    const r = await api("/api/vault/review/due");
    const meta = $("vault_due_meta");
    // 排期口径必须可见——换了算法，复习节奏就变了，用户有权知道。
    const alg = r.scheduler === "sm2" ? "SM-2" : "FSRS";
    if (meta) meta.textContent = `到期 ${r.stats.due} / 共 ${r.stats.total} · ${alg}`;
    const items = r.items || [];
    box.innerHTML = items.length ? items.slice(0, 20).map(it => `
      <div class="vault-duerow">
        <span class="vault-q">${_esc(it.question) || "（无题干）"}</span>
        <span class="vault-meta">${_esc([it.subject, it.chapter, it.topic].filter(Boolean).join(" · "))}
          · 到期 ${_esc(it.due || "—")}</span>
        <span class="btns" style="margin-top:6px">
          <button class="act" onclick="vaultGrade('${_esc(it.path).replace(/'/g, "\\'")}',4)">记得</button>
          <button class="act gray" onclick="vaultGrade('${_esc(it.path).replace(/'/g, "\\'")}',2)">模糊</button>
          <button class="act gray" onclick="vaultGrade('${_esc(it.path).replace(/'/g, "\\'")}',0)">忘了</button>
        </span>
      </div>`).join("") : `<div class="hint">今天没有到期的错题。</div>`;
  } catch (e) { box.innerHTML = `<div class="hint">读取失败：${_esc(e.message)}</div>`; }
}

async function vaultGrade(rel, quality) {
  try {
    await api(`/api/vault/review/${encodeURI(rel)}/grade`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quality }) });
    await vaultReviewLoad();
    await vaultLoad();          // 掌握度/薄弱项会因复习而变
  } catch (e) { toast("评分失败：" + e.message, false); }
}

