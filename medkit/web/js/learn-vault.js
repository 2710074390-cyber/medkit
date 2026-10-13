/* exported vaultInit, vaultRebuild, vaultMigrate, vaultSaveDir, vaultSearch,
   vaultSearchClear, vaultAsk, vaultAskGlobal, vaultPersist, vaultClose, vaultEdit, vaultOpen,
   vaultLoad, vaultView */
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

function _esc(s) { const d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; }

async function vaultStatus() {
  try {
    const st = await api("/api/vault/status");
    const box = $("vault_status");
    if (box) {
      box.innerHTML = st.is_vault
        ? `文件夹：<code>${_esc(st.root)}</code> ｜ 已索引 <b>${st.count}</b> 条`
        : `还没有创建错题文件夹 —— 点上方「选择 / 创建文件夹」（默认 <code>${_esc(st.default_root)}</code>）`;
    }
    const rh = $("vault_root_hint");
    if (rh) rh.textContent = st.is_vault ? `${st.count} 条` : "未初始化";
    const di = $("vault_dir");
    if (di && !di.value) di.placeholder = `（空 = 默认 ${st.default_root}）`;
    const ds = $("vault_dir_status");
    if (ds) ds.textContent = st.is_vault ? `当前生效：${st.root}` : "尚未初始化";
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

async function vaultInit() {
  const cur = await api("/api/vault/status");
  const p = (prompt("错题文件夹路径（留空用默认）：", cur.default_root) || "").trim();
  try {
    const r = await api("/api/vault/init", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p }) });
    toast(r.created ? `已在 ${r.root} 创建错题文件夹` : `已指向已有文件夹 ${r.root}`);
    await vaultLoad();
  } catch (e) { toast("初始化失败：" + e.message, false); }
}

async function vaultSaveDir() {
  const p = $("vault_dir").value.trim();
  try {
    const r = await api("/api/vault/init", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p }) });
    toast(`vault 目录已设为 ${r.root}`);
    await vaultLoad();
  } catch (e) { toast("保存失败：" + e.message, false); }
}

async function vaultMigrate() {
  if (!confirm("把旧错题本导出为 vault 的 markdown 文件？\n\n"
    + "· 只新增文件，不删除、不修改旧错题本\n"
    + "· 已迁移过的会按同 id 覆盖同名文件（幂等）")) return;
  try {
    const r = await api("/api/vault/import-from-library", { method: "POST" });
    toast(`迁移完成：写入 ${r.written} 条，跳过 ${r.skipped} 条（无题干）`);
    await vaultLoad();
  } catch (e) { toast("迁移失败：" + e.message, false); }
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
      box.innerHTML = `<div class="hint">还没有错题。点上方「从旧错题本一键迁移」，或直接在文件夹里新建 md 文件。</div>`;
    } else if (!g.length) {
      box.innerHTML = `<div class="hint">共 ${st.total} 条，暂无需要优先处理的薄弱项。</div>`;
    } else {
      box.innerHTML = `<table class="bank-preview-table"><thead><tr>
          <th>科目</th><th>章节</th><th>主题</th><th>错题</th><th>未掌握</th><th>状态</th><th>优先级</th>
        </tr></thead><tbody>` + g.slice(0, 12).map(x => `<tr>
          <td>${_esc(x.subject)}</td><td>${_esc(x.chapter)}</td><td>${_esc(x.topic)}</td>
          <td>${x.total}</td><td>${x.unlearned}</td><td>${_stateLabel(x.state)}</td>
          <td>${(x.priority * 100).toFixed(0)}</td></tr>`).join("") + `</tbody></table>`;
    }
    const meta = $("vault_list_meta");
    if (meta) meta.textContent = `共 ${st.total} 条 · 未掌握 ${st.unlearned} 条`;
    await vaultRefreshList();
  } catch (e) { toast("读取失败：" + e.message, false); }
}

async function vaultRefreshList() {
  const q = ($("vault_q") && $("vault_q").value.trim()) || "";
  const seq = ++_vaultListSeq;
  try {
    const r = await api("/api/vault/mistakes?q=" + encodeURIComponent(q));
    if (seq !== _vaultListSeq) return;   // 期间又发起了更新的请求 → 丢弃本次过期结果
    _vaultItems = r.items || [];
    const box = $("vault_list");
    if (box) {
      box.innerHTML = _vaultItems.length ? _vaultItems.map(r => `
        <button class="vault-row" onclick="vaultOpen('${_esc(r.path).replace(/'/g, "\\'")}')">
          <span class="vault-q">${_esc(r.question) || "（无题干）"}</span>
          <span class="vault-meta">${_esc([r.subject, r.chapter, r.topic].filter(Boolean).join(" · "))}
            ${r.understanding ? " · 有我的理解" : ""}${r.doubts ? " · 有疑问" : ""}</span>
        </button>`).join("")
      : `<div class="hint">${q ? "没有命中的错题。" : "还没有错题文件。"}</div>`;
    }
    const cl = $("btn_vault_q_clear");
    if (cl) cl.style.display = q ? "" : "none";
  } catch (e) { toast("列表读取失败：" + e.message, false); }
}

function vaultSearch() { vaultRefreshList(); }
function vaultSearchClear() { $("vault_q").value = ""; vaultRefreshList(); }

async function vaultOpen(rel) {
  _vaultCur = rel;
  const it = _vaultItems.find(x => x.path === rel) || {};
  const box = $("vault_detail");
  if (!box) return;
  box.style.display = "";
  box.innerHTML = `
    <div class="cardh"><h2>${_esc(it.question) || "（无题干）"}</h2>
      <span class="hint">${_esc(rel)}</span></div>
    <div class="hint" style="margin-bottom:8px">${_esc(it.analysis || "（无解析）")}</div>
    <details style="margin:6px 0"><summary class="hint">答案（默认收起，防剧透）</summary>
      <div class="hint">${_esc(it.answer || "（无答案）")}</div></details>
    <label>我的理解（你自己写，可空）</label>
    <textarea id="vlt_und" rows="3" placeholder="当时为什么选错？现在怎么理解？">${_esc(it.understanding || "")}</textarea>
    <label style="margin-top:8px">我的疑问（Agent 会逐条回应）</label>
    <textarea id="vlt_doubts" rows="3" placeholder="哪里还不确定？">${_esc(it.doubts || "")}</textarea>
    <div class="btns" style="margin-top:8px">
      <button class="act" onclick="vaultEdit()">保存理解与疑问</button>
      <button class="act gray" onclick="vaultClose()">收起</button>
    </div>
    <hr style="border:none;border-top:1px solid var(--line);margin:14px 0">
    <label>问 Agent（只依据你这个文件夹作答，答不出就说不知道）</label>
    <div class="row" style="gap:8px">
      <input type="text" id="vlt_q" style="flex:2" placeholder="例如：稳定期会用抗菌药吗？"
             onkeydown="if(event.key==='Enter'){vaultAsk();}">
      <button class="act" id="btn_vault_ask" onclick="vaultAsk()">问</button>
    </div>
    <div id="vlt_answer" class="hint" style="margin-top:8px;white-space:pre-wrap"></div>
    <div class="btns" style="margin-top:8px">
      <button class="act gray" id="btn_vault_persist" style="display:none"
        onclick="vaultPersist()">把答复存进这个错题</button>
    </div>`;
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

let _vaultAnswer = "";

/* 单一流式读取：详情内「问」与文件夹全域「问」两处入口共用，避免两份解析逻辑漂移。 */
async function _vaultAskStream(question, path, out) {
  const res = await fetch("/api/vault/ask", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, path }) });
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "", answer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n"); buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ") || line.includes("[DONE]")) continue;
      let ev; try { ev = JSON.parse(line.slice(6)); } catch (e) { continue; }
      if (ev.type === "stage" && out) {
        out.textContent = ev.tool ? `取证中：${ev.tool}…` : (ev.hint || "思考中…");
      } else if (ev.type === "error") {
        if (out) out.textContent = `出错了：${ev.message}`;
      } else if (ev.type === "result") {
        answer = ev.answer || "";
        const cites = (ev.citations || []).map(c => `[来源: ${c.path}${c.section ? "#" + c.section : ""}]`).join("\n");
        if (out) out.textContent = answer + (cites ? "\n\n" + cites : "")
          + (ev.grounded === false ? "\n\n（本回答因缺少可验证依据被拦下——它只依据你的错题库作答）" : "");
      }
    }
  }
  return answer;
}

async function vaultAsk() {
  const q = ($("vlt_q") && $("vlt_q").value.trim()) || "";
  if (!q) { toast("先写你的问题", false); return; }
  const out = $("vlt_answer"), btn = $("btn_vault_ask"), ps = $("btn_vault_persist");
  if (out) out.textContent = "正在取证…";
  if (btn) btn.disabled = true;
  _vaultAnswer = "";
  try {
    _vaultAnswer = await _vaultAskStream(q, _vaultCur || "", out);
    if (ps && _vaultAnswer) ps.style.display = "";
  } catch (e) { if (out) out.textContent = "请求失败：" + e.message; }
  finally { if (btn) btn.disabled = false; }
}

/* 文件夹全域问：不绑定某一条错题（path 为空 ⇒ Agent 自行 search/stats），
   用来回答「我哪章最弱」「这些错有什么共性」这类跨条目的问题。 */
async function vaultAskGlobal() {
  const q = ($("vault_ask_q") && $("vault_ask_q").value.trim()) || "";
  if (!q) { toast("先写你的问题", false); return; }
  const out = $("vault_ask_out"), btn = $("btn_vault_ask_global");
  if (out) out.textContent = "正在取证…";
  if (btn) btn.disabled = true;
  try { await _vaultAskStream(q, "", out); }
  catch (e) { if (out) out.textContent = "请求失败：" + e.message; }
  finally { if (btn) btn.disabled = false; }
}

async function vaultPersist() {
  if (!_vaultCur || !_vaultAnswer) return;
  try {
    await api(`/api/vault/mistakes/${encodeURI(_vaultCur)}/section`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "agent_reply", text: _vaultAnswer }) });
    toast("答复已写进这个错题文件的「## Agent 的答复」");
    await vaultRefreshList();
  } catch (e) { toast("写入失败：" + e.message, false); }
}

/* view 钩子：learn.js 的 showLearnView 会在切到本视图时调它 */
window.vaultView = function () { vaultLoad(); };

