/* exported _bankImport, bankCommitImport, bankImportCancel, bankImportFile, bankImportPick,
   bankPreviewPaste, bankProjectImport, bankProjectOpen, bankRenderPreview, bankTargetSync,
   bankPasteClose, bankPasteOpen */
/* ---- 题库导入向导（bank-import）：文件 / 粘贴 → 解析预览 → 确认入库 ----
 *
 * 三步走（借鉴 Quizlet 导入向导的「先预览再保存」）：
 *   ① 文件或粘贴 → ② 预览表（逐题显示推断题型 / 选项数 / 答案，逐条列告警）→ ③ 选目标题库 + 重复策略 → 入库。
 *
 * **能力边界**（首屏就写清，别让用户猜）：
 *   - 识别靠本地规则（零 LLM）：`### 题号` / `1.` / `第N题` 起题，`- A.` / `A、` 认选项，
 *     `答案/【答案】/✅ 答案` 认答案，`解析/💡` 认解析；
 *   - 题型**由答案字母数推断**（多字母 → X 型），可入库后修改；
 *   - 图 / 表题只保真不保证（md 里没有图）；
 *   - **不内置、不分发任何题库**——数据权利归各自来源方。
 */
let _bankImport = { items: [], warnings: [], source: "", name: "" };

function bankImportPick() { const f = $("bank_file"); if (f) f.click(); }

async function bankImportFile(input) {
  const f = input.files && input.files[0];
  input.value = "";                       // 允许连续选同一个文件
  if (!f) return;
  const btn = $("btn_bank_import");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "解析中…"; }
  try {
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/bank/import/file", { method: "POST", body: fd });
    _bankImport = { items: r.items || [], warnings: r.warnings || [],
                    source: f.name || "", name: (f.name || "").replace(/\.[^.]+$/, "") };
    bankRenderPreview(r);
    const pc = $("bank_paste_card"); if (pc) pc.style.display = "none";
  } catch (e) { toast("导入解析失败：" + e.message, false); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}

function bankPasteOpen() {
  const c = $("bank_paste_card");
  if (c) c.style.display = "";
  const t = $("bank_paste_text");
  if (t) t.focus();
}
function bankPasteClose() { const c = $("bank_paste_card"); if (c) c.style.display = "none"; }

async function bankPreviewPaste() {
  const ta = $("bank_paste_text");
  const text = (ta && ta.value) || "";
  if (!text.trim()) { toast("请先粘贴题库文本", false); return; }
  try {
    const r = await api("/api/bank/import/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, fmt: "md" }),
    });
    _bankImport = { items: r.items || [], warnings: r.warnings || [],
                    source: "粘贴文本", name: "导入题库" };
    bankRenderPreview(r);
    bankPasteClose();
  } catch (e) { toast(e.message, false); }
}

function bankRenderPreview(r) {
  const card = $("bank_import_card");
  const body = $("bank_import_body");
  if (!card || !body) return;
  card.style.display = "";
  const meta = $("bank_import_meta");
  if (meta) meta.textContent = `${_bankImport.source} · 识别 ${r.total || 0} 题（解析块 ${r.parsed || 0}）`;
  const items = _bankImport.items || [];
  const rows = items.slice(0, 30);
  const warn = (_bankImport.warnings || []).slice(0, 8);
  const opts = (_bankState.banks || []).map(b =>
    `<option value="${esc(b.id)}">${esc(b.name)}（${b.items || 0} 题）</option>`).join("");
  body.innerHTML = `
    <div class="hint" style="margin-bottom:8px">
      识别靠<b>本地规则</b>（零 LLM）：起题 = <code>### 题号</code>/<code>1.</code>/<code>第N题</code>；
      选项 = <code>- A.</code>/<code>A、</code>；答案 = <code>答案：</code>/<code>【答案】</code>/<code>✅ 答案：</code>；
      解析 = <code>解析：</code>/<code>💡</code>。<b>题型按答案字母数推断</b>（多字母 → X 型），入库后可改。
      图 / 表题只保真不保证；解析不了的题不会进来。
    </div>
    ${warn.length ? `<div class="hint" style="color:var(--warn);margin-bottom:8px">告警 ${(_bankImport.warnings || []).length} 条：${warn.map(esc).join("；")}</div>` : ""}
    ${items.length ? `<div style="max-height:360px;overflow-y:auto;margin-bottom:8px">
      <table class="bank-preview-table">
        <thead><tr><th>#</th><th>题干</th><th>题型</th><th>选项</th><th>答案</th></tr></thead>
        <tbody>${rows.map((it, i) => `<tr>
          <td>${i + 1}</td>
          <td>${esc((it.question || "").slice(0, 90))}</td>
          <td>${esc(it.type || "")}${it._type_inferred ? '<span class="hint"> 推断</span>' : ""}</td>
          <td>${(it.options || []).length}</td>
          <td>${esc(it.answer || "—")}</td>
        </tr>`).join("")}</tbody>
      </table>${items.length > rows.length ? `<div class="hint" style="margin-top:6px">…还有 ${items.length - rows.length} 道未展示（全部都会入库）</div>` : ""}
    </div>` : `<div class="hint">没有识别到题目——请检查格式（是否每题都带「答案」标记）。</div>`}
    <div class="bank-bar">
      <label>入库到</label>
      <select id="bank_target" onchange="bankTargetSync()">
        <option value="">＋ 新建题库</option>${opts}
      </select>
      <input type="text" id="bank_new_name" value="${esc(_bankImport.name || "导入题库")}" placeholder="新题库名称" style="min-width:150px">
      <input type="text" id="bank_new_subject" value="" placeholder="科目（如：内科学）" style="min-width:120px">
      <label>重复题</label>
      <select id="bank_dedupe">
        <option value="update">更新已有</option>
        <option value="skip">跳过已有</option>
        <option value="new">都新建</option>
      </select>
      <button class="act" id="btn_bank_commit" onclick="bankCommitImport()">确认入库（${items.length} 题）</button>
      <button class="act gray" onclick="bankImportCancel()">取消</button>
    </div>`;
  bankTargetSync();
}

function bankTargetSync() {
  const sel = $("bank_target");
  const nameInp = $("bank_new_name");
  if (!sel || !nameInp) return;
  nameInp.style.display = sel.value ? "none" : "";
}

async function bankCommitImport() {
  const items = _bankImport.items || [];
  if (!items.length) { toast("没有可导入的题目", false); return; }
  const sel = $("bank_target");
  const nameInp = $("bank_new_name");
  const subjInp = $("bank_new_subject");
  const dedupe = ($("bank_dedupe") || {}).value || "update";
  const bid = sel ? sel.value : "";
  const name = nameInp ? (nameInp.value || "").trim() : "";
  const subject = subjInp ? (subjInp.value || "").trim() : "";
  if (!bid && !name) { toast("请填写新题库名称", false); return; }
  const btn = $("btn_bank_commit");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "入库中…"; }
  try {
    const r = await api("/api/bank/import/commit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bank_id: bid, name, subject, source: "md_import", items, dedupe }),
    });
    toast(`入库完成：新增 ${r.added || 0} · 更新 ${r.updated || 0} · 跳过 ${r.skipped || 0}`);
    bankImportCancel();
    _bankState.current = String(r.bank_id || "");
    await loadBankHome();
  } catch (e) { toast(e.message, false); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}

function bankImportCancel() {
  _bankImport = { items: [], warnings: [], source: "", name: "" };
  const card = $("bank_import_card"); if (card) card.style.display = "none";
  const body = $("bank_import_body"); if (body) body.innerHTML = "";
  const pc = $("bank_paste_card"); if (pc) pc.style.display = "none";
}

/* ---- 从出题工坊项目导入（F11）：项目产物 questions_final.json → 题库 ----
 *
 * **复制而非引用**：项目可被删，题库不该跟着消失。导入走服务端（不把题目正文拉到浏览器再传回去），
 * 案例/选项组/图/表/溯源字段原样带过来（不搬运等于导入即降级）。
 */
async function bankProjectOpen() {
  try {
    const r = await api("/api/bank/projects");
    const list = r.projects || [];
    const body = !list.length
      ? `<div class="hint">还没有可导入的项目——先去「题库」（出题工坊）生成一套题，
         生成完成后这里就会出现。</div>`
      : `<div class="hint" style="margin-bottom:8px">下列项目已生成题目产物。导入是<b>复制</b>，
         之后删掉项目也不影响题库。</div>`
        + list.map(p => `<div class="bank-item">
            <div class="bank-item-main">
              <div class="bank-item-name">${esc(p.subject || "（未命名课题）")}</div>
              <div class="bank-item-meta"><span>${esc(p.pid)}</span><span>题目 <b>${p.count || 0}</b></span></div>
            </div>
            <div class="bank-item-actions">
              <button class="mini-btn primary" data-pid="${esc(p.pid)}" data-subject="${esc(p.subject || "")}"
                      onclick="bankProjectImport(this)">导入为题库</button>
            </div>
          </div>`).join("");
    confirmModal("从出题项目导入", body, "关闭", () => {}, false);
  } catch (e) { toast(e.message, false); }
}

function bankProjectImport(btn) {
  const pid = btn && btn.dataset ? (btn.dataset.pid || "") : "";
  const subj = btn && btn.dataset ? (btn.dataset.subject || "") : "";
  if (!pid) return;
  askModal("导入项目题目", "新题库名称", subj || ("项目 " + pid), async (name) => {
    try {
      const r = await api("/api/bank/import/project", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid: pid, name: name }),
      });
      toast(`已导入 ${r.added || 0} 题`
        + (r.updated ? ` · 更新 ${r.updated}` : "") + (r.skipped ? ` · 跳过 ${r.skipped}` : ""));
      _bankState.current = String(r.bank_id || "");
      await loadBankHome();
    } catch (e) { toast(e.message, false); }
  }, subj || ("项目 " + pid));
}

window.bankImportPick = bankImportPick;
window.bankImportFile = bankImportFile;
window.bankPasteOpen = bankPasteOpen;
window.bankPasteClose = bankPasteClose;
window.bankPreviewPaste = bankPreviewPaste;
window.bankRenderPreview = bankRenderPreview;
window.bankTargetSync = bankTargetSync;
window.bankCommitImport = bankCommitImport;
window.bankImportCancel = bankImportCancel;
window.bankProjectOpen = bankProjectOpen;
window.bankProjectImport = bankProjectImport;
