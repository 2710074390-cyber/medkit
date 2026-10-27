/* exported bankSyncOpen, bankSyncPick, bankSyncRun, bankSyncRefresh */
/* ---- 题库同步（bank-sync）：MedAgentWork 远端题库 → 本地题库域 ----
 *
 * 入口：刷题页「我的题库」→「⇩ 同步官方题库」。三步：读远端清单 → 勾选 → 同步入库。
 *
 * **与「导入题库文件」的分工**（两条路径都保留，别合并）：
 *   - 导入文件：任意来源的任意题库，用户自己下载、自己选；
 *   - 本片：固定来源（MedAgentWork 仓库），只回答「远端哪些批次更新了」——
 *     并**保留上游字段**（题型 / Bloom / 考点 / 真题年份），见 `core/banksync.adapt_item`。
 *
 * **不发任何后台请求**：只有点开面板才会访问 GitHub；关掉面板即结束。
 * 清单在服务端缓存 10 分钟（`core/banksync.TREE_TTL`），反复开关不会把匿名额度吃光。
 *
 * 状态色只靠文案（`未同步 / 有更新 / 已同步`）+ `tag`，不新增 CSS 类——
 * 本仓有「CSS 孤儿类」守卫，为一次性的浮层加样式不值得。
 */
let _bankSync = { sources: [], meta: {}, busy: false };

const _BANK_SYNC_LABEL = { "new": "未同步", changed: "有更新", synced: "已同步", offline: "离线" };

function _bankSyncRender(html) {
  const body = $("md_body");
  if (body) body.innerHTML = html;
}

function _bankSyncMeta() {
  const m = _bankSync.meta || {};
  const parts = [];
  if (m.remote_total) parts.push(`远端共 ${m.remote_total} 题`);
  if (m.remote_updated_at) parts.push(`题库更新于 ${esc(String(m.remote_updated_at).replace("T", " ").slice(0, 16))}`);
  parts.push(`可同步 ${(_bankSync.sources || []).length} 份`);
  return parts.join(" · ");
}

function _bankSyncListHTML() {
  const m = _bankSync.meta || {};
  const rows = _bankSync.sources || [];
  const banner = (m.ok === false)
    ? `<div class="hint" style="color:var(--warn);margin-bottom:var(--space-2)">
         读不到远端清单：${esc(m.error || "网络不可用")}
         ——下面显示的是<b>上次同步记录</b>，点「重试」再试一次。</div>`
    : "";
  if (!rows.length) {
    return `${banner}<div class="hint">没有可同步的题库。可能远端仓库结构变了，或网络不可用。</div>`;
  }
  const body = rows.map(s => {
    const st = String(s.state || "new");
    const count = s.est_count || s.count || 0;
    return `<tr>
      <td><label><input type="checkbox" data-bank-sync="${esc(s.path)}"
             ${st === "synced" ? "" : "checked"}>
        <span>${esc(s.batch || "押题卷")}</span></label></td>
      <td>${esc(s.subject || "（未标注科目）")}</td>
      <td>${count || "—"}</td>
      <td><span class="tag">${esc(_BANK_SYNC_LABEL[st] || st)}</span></td>
    </tr>`;
  }).join("");
  return `${banner}
    <div class="hint" style="margin-bottom:var(--space-2)">
      来源：<a href="${esc(m.url || "")}" target="_blank" rel="noopener noreferrer">${esc(m.repo || "MedAgentWork")}</a>
      · ${_bankSyncMeta()} · 勾选后同步会<b>合并进本地题库</b>，同题保留你的作答记录与笔记。
    </div>
    <div class="bank-bar">
      <button class="act gray" onclick="bankSyncPick('all')">全选</button>
      <button class="act gray" onclick="bankSyncPick('todo')">只选未同步 / 有更新</button>
      <button class="act gray" onclick="bankSyncPick('none')">全不选</button>
      <button class="act gray" onclick="bankSyncRefresh()">重试 / 刷新清单</button>
    </div>
    <div style="max-height:min(300px, 38vh);overflow-y:auto">
      <table class="bank-preview-table">
        <thead><tr><th>批次</th><th>科目</th><th>题数</th><th>状态</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
    <div class="bank-bar" style="margin-top:var(--space-3)">
      <label for="bank_sync_dedupe">重复题</label>
      <select id="bank_sync_dedupe">
        <option value="update">更新已有（保留作答记录）</option>
        <option value="skip">跳过已有</option>
        <option value="new">都新建</option>
      </select>
      <button class="act" id="btn_bank_sync_run" onclick="bankSyncRun()">开始同步</button>
    </div>
    <div class="hint">首次同步会下载每个批次的完整题目（约 0.1~1 MB/份）。匿名访问 GitHub 接口
      限 60 次/小时，连点几轮会撞限流——届时在配置里填 <code>github_token</code> 可提到 5000 次/小时。</div>`;
}

async function bankSyncOpen(refresh) {
  _bankSync = { sources: [], meta: {}, busy: false };
  confirmModal("从 MedAgentWork 同步题库",
    `<div class="hint"><span class="spin"></span>正在读取远端题库清单…</div>`,
    "关闭", () => {}, false);
  try {
    const r = await api("/api/bank/sync/sources" + (refresh ? "?refresh=1" : ""));
    _bankSync.sources = r.sources || [];
    _bankSync.meta = r;
    _bankSyncRender(_bankSyncListHTML());
  } catch (e) {
    _bankSyncRender(`<div class="hint" style="color:var(--bad)">读取远端清单失败：${esc(e.message)}</div>`);
  }
}

function bankSyncRefresh() { return bankSyncOpen(true); }

function bankSyncPick(mode) {
  const list = _bankSync.sources || [];
  Array.prototype.forEach.call(
    document.querySelectorAll("#md_body input[data-bank-sync]"), el => {
      const path = el.getAttribute("data-bank-sync") || "";
      const src = list.filter(s => s.path === path)[0];
      if (mode === "all") el.checked = true;
      else if (mode === "none") el.checked = false;
      else el.checked = !src || src.state !== "synced";
    });
}

function _bankSyncResultHTML(r) {
  const results = r.results || [];
  const rows = results.map(x => {
    const st = String(x.status || "");
    let detail = "";
    if (st === "synced") {
      detail = `新增 ${x.added || 0} · 更新 ${x.updated || 0} · 跳过 ${x.skipped || 0}`;
    } else if (st === "unchanged") {
      detail = "远端无变化，未下载";
    } else if (st === "missing") {
      detail = "远端已无此文件";
    } else {
      detail = x.error || "失败";
    }
    const tag = st === "synced" ? "已同步" : st === "unchanged" ? "无变化" : "失败";
    return `<tr>
      <td>${esc(x.batch || "押题卷")}</td>
      <td>${esc(x.subject || "—")}</td>
      <td><span class="tag">${esc(tag)}</span></td>
      <td>${esc(detail)}</td>
    </tr>`;
  }).join("");
  const failed = Number(r.failed || 0);
  const head = failed
    ? `<div class="hint" style="color:var(--warn);margin-bottom:var(--space-2)">
         完成，但有 ${failed} 份失败（其余已入库）。失败项可稍后重试，不影响已同步的部分。</div>`
    : `<div class="hint" style="color:var(--good);margin-bottom:var(--space-2)">同步完成。</div>`;
  return `${head}
    <div class="hint" style="margin-bottom:var(--space-2)">
      新增 <b>${r.added || 0}</b> · 更新 <b>${r.updated || 0}</b> ·
      跳过 <b>${r.skipped || 0}</b> · 未变 <b>${r.unchanged || 0}</b> · 失败 <b>${failed}</b>
    </div>
    <div style="max-height:min(300px, 38vh);overflow-y:auto">
      <table class="bank-preview-table">
        <thead><tr><th>批次</th><th>科目</th><th>结果</th><th>明细</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function bankSyncRun() {
  if (_bankSync.busy) return;
  const picked = [];
  Array.prototype.forEach.call(
    document.querySelectorAll("#md_body input[data-bank-sync]"), el => {
      if (el.checked) picked.push(el.getAttribute("data-bank-sync") || "");
    });
  const paths = picked.filter(Boolean);
  if (!paths.length) { toast("请先勾选要同步的题库", false); return; }
  const sel = $("bank_sync_dedupe");
  const dedupe = (sel && sel.value) || "update";
  _bankSync.busy = true;
  _bankSyncRender(`<div class="hint"><span class="spin"></span>正在同步 ${paths.length} 份题库
    （逐份下载并入库，请勿关闭窗口）…</div>`);
  try {
    const r = await api("/api/bank/sync/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: paths, dedupe: dedupe }),
    });
    _bankSyncRender(_bankSyncResultHTML(r));
    toast(`同步完成：新增 ${r.added || 0} · 更新 ${r.updated || 0}` + (r.failed ? ` · 失败 ${r.failed}` : ""),
          !r.failed);
    await refreshBankSurfaces();
  } catch (e) {
    _bankSyncRender(`<div class="hint" style="color:var(--bad)">同步失败：${esc(e.message)}</div>`);
  } finally {
    _bankSync.busy = false;
  }
}

window.bankSyncOpen = bankSyncOpen;
window.bankSyncRefresh = bankSyncRefresh;
window.bankSyncPick = bankSyncPick;
window.bankSyncRun = bankSyncRun;
