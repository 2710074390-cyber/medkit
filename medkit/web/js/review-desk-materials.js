/* exported ROLE_LABEL, ROW_STATE, UP_OK_EXT, UP_OK_MIME, a, addFiles, b, bar, box, btn, c, cancelN, chars, cls, cntEl, cny, collectKnobs, currentFormPayload, data, delPreset, doRemove, doneN, dz, el, est, estLine, estimateCost, ext, f, failN, fd, files, filesCount, fillPayload, finished, fullSlices, groups, ids, j, jobs, k, list, loadPresets, loadSessionAs, loadSessions, loadSessionsAsTextbook, mime, mk, model, msgEl, myToken, n, nQ, nSlices, names, ocrBadge, okFiles, p, parseGroup, pickFiles, pres, price, probeSampleAvailability, prov, r, removeFile, renderChips, renderDz, renderResults, res, resTotalChars, row, rowsBox, runOcrJobs, runningN, s, saved, skipped, slices, stEl, started, t, tEl, tb, tpl, tplKey, updateOcrUi, updateReady, warns, wrap, x */
/* ---- ② 预设（2C） */
function currentFormPayload() {
  return {
    exam: $("exam").value, target: parseInt($("target").value || "100"),
    ratios: ratioSum(),
    bloom: bloomSum(),
    knobs: collectKnobs(),
    requirements: $("requirements").value.trim(),
    official_quota: parseInt($("official_quota") ? $("official_quota").value : "0") || 0,
  };
}
function collectKnobs() {
  const k = {};
  if ($("k_difficulty").value) k.difficulty = $("k_difficulty").value;
  if ($("k_analysis").value) k.analysis_style = $("k_analysis").value;
  if ($("k_stem").value) k.stem_style = $("k_stem").value;
  return k;
}
function fillPayload(p) {
  if (!p) return;
  if (p.exam && [...$("exam").options].some(o => o.value === p.exam)) $("exam").value = p.exam;
  if (p.target) $("target").value = p.target;
  const r = p.ratios || {};
  $("r_a1").value = r.A1 ?? 40; $("r_a2").value = r.A2 ?? 30;
  $("r_b1").value = r.B1 ?? 20; $("r_x").value = r.X ?? 10;
  const b = p.bloom || {};
  $("b_mem").value = b["记忆"] ?? 30; $("b_und").value = b["理解"] ?? 40;
  $("b_app").value = b["应用"] ?? 25; $("b_cre").value = b["创造"] ?? 5;
  const k = p.knobs || {};
  $("k_difficulty").value = k.difficulty || ""; $("k_analysis").value = k.analysis_style || "";
  $("k_stem").value = k.stem_style || "";
  $("requirements").value = (p.requirements || "").slice(0, 500);
  if ($("official_quota")) $("official_quota").value = p.official_quota ?? 0;
  ratioSum(); bloomSum();
  $("req_count").textContent = $("requirements").value.length + "/500";
}
async function loadPresets() {
  const r = await api("/api/presets");
  state.presets = r;
  renderChips(r);
}
function renderChips(r) {
  const box = $("preset_chips");
  if (!box) return;
  box.innerHTML = "";
  const mk = (p) => {
    const c = document.createElement("button");
    c.className = "chip" + (p.builtin ? " builtin" : "");
    c.title = p.desc || "";
    c.innerHTML = `${p.builtin ? "◆" : "◇"} ${esc(p.name)}`;
    if (!p.builtin) {
      // R4-25：删除入口不再拼接行内 onclick（p.id 含撇号会击穿 JS）——事件绑定 + DOM 挂载
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "✕";
      x.onclick = (ev) => { ev.stopPropagation(); delPreset(p.id); };
      c.appendChild(x);
    }
    c.onclick = () => confirmModal("应用预设？",
      `「${esc(p.name)}」将<b>覆盖当前参数</b>（'科目'不覆盖）。<br><span class="hint">${esc(p.desc || "")}</span>`,
      "应用", () => { fillPayload(p.payload); toast("预设已应用：" + p.name); }, false);
    box.appendChild(c);
  };
  [...(r.builtins || []), ...(r.customs || [])].forEach(mk);
}
async function delPreset(id) {
  try { await api("/api/presets/" + id, { method: "DELETE" }); toast("预设已删除"); loadPresets(); }
  catch (e) { toast(e.message, false); }
}
$("btn_preset_save").onclick = () => {
  askModal("保存预设", "预设名称：", "如：期末冲刺·计算题加强", async name => {
    try {
      const r = await api("/api/presets", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, desc: "", payload: currentFormPayload() }) });
      toast("预设已保存：" + r.name);
      loadPresets();
    } catch (e) { toast(e.message, false); }
  });
};
$("btn_preset_export").onclick = () => {
  const data = JSON.stringify({ kind: "medkit-preset", payload: currentFormPayload() }, null, 2);
  const a = document.createElement("a");
  a.download = "medkit-preset-" + ($("subject").value.trim() || "untitled") + ".json";
  a.href = "data:application/json;charset=utf-8," + encodeURIComponent(data);
  a.click();
  toast("预设文件已导出（另一台机器「导入」即可回填）");
};
$("btn_preset_import").onclick = () => $("f_preset_import").click();
$("f_preset_import").onchange = async () => {
  const f = $("f_preset_import").files[0];
  $("f_preset_import").value = "";
  if (!f) return;
  try {
    const j = JSON.parse(await f.text());
    const p = (j.kind === "medkit-preset" && j.payload) ? j.payload : j.payload || j;
    if (!p || typeof p !== "object") throw new Error("格式不符");
    fillPayload(p);
    toast("预设已导入并回填");
  } catch (e) { toast("预设文件不合法：" + e.message, false); }
};

/* ---- ② 素材拖拽 */
const ROLE_LABEL = { textbook: "教材", teacher: "教师重点", exam: "真题", extra: "资料" };
/* B1：上传类型白名单与后端 TEXT_SUFFIXES 对齐（.bmp 后端支持但 accept 未列 → 补上；.doc 不支持） */
const UP_OK_EXT = ["pdf", "docx", "md", "markdown", "txt", "text", "png", "jpg", "jpeg", "webp", "bmp"];
/* A-11：MIME 白名单（拖拽/选择共用）——扩展名只防「类型不对」，MIME 再挡一层
   「改扩展名的伪文件」（如 exe 改名 .png）；octet-stream/空 MIME 视为未知放行（部分浏览器不给） */
const UP_OK_MIME = new Set(["application/pdf", "text/markdown", "text/plain", "text/x-markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif",
  "application/octet-stream"]);
["textbook", "teacher", "exam", "extra"].forEach(role => {
  const dz = $("dz_" + role), input = $("f_" + role);
  input.onchange = () => { addFiles(role, [...input.files]); input.value = ""; };
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", e => {
    e.preventDefault(); dz.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      addFiles(role, [...e.dataTransfer.files]);
      toast(`已加入${ROLE_LABEL[role] || role}：${e.dataTransfer.files.length} 个文件`);
    }
  });
  $("dzc_" + role).onclick = () => { state.files[role] = []; renderDz(role); };
});
function pickFiles(role) { $("f_" + role).click(); }
function addFiles(role, files) {
  const list = state.files[role];
  for (const f of files) {
    if (!f || !f.size) { toast("空文件已忽略", false); continue; }
    const ext = (f.name || "").split(".").pop().toLowerCase();
    if (ext && !UP_OK_EXT.includes(ext)) {
      toast(`「${f.name}」类型不支持（支持 PDF/DOCX/MD/TXT/图片 png·jpg·webp·bmp），已跳过`, false);
      continue;
    }
    // A-11：MIME 检查（扩展名之外的伪文件防线；未知/缺省 MIME 放行，不误伤）
    const mime = (f.type || "").toLowerCase();
    if (mime && !UP_OK_MIME.has(mime) && !mime.startsWith("text/")) {
      toast(`「${f.name}」文件内容类型可疑（${f.type}），已跳过——请确认是支持的文档/图片后重试`, false);
      continue;
    }
    list.push({ name: f.name, size: f.size, file: f });
  }
  renderDz(role);
}
function renderDz(role) {
  const el = $("dzl_" + role);
  const list = state.files[role];
  el.innerHTML = list.map((f, i) =>
    `<div class="dzfile"><span class="nm">${esc(f.name)}</span><span class="sz">${(f.size / 1048576).toFixed(1)}MB</span>
     <button onclick="removeFile('${role}',${i})">移除</button></div>`).join("");
  $("dzc_" + role).style.display = list.length ? "block" : "none";
}
function removeFile(role, i) {
  const doRemove = () => { state.files[role].splice(i, 1); renderDz(role); };
  if (role === "textbook" || role === "teacher") {
    confirmModal("移除文件", `从清单移除「${esc(state.files[role][i].name)}」？`, "移除", doRemove, false);
  } else doRemove();
}

/* ---- ② 解析 */
let pres = state.pres;

async function parseGroup(role) {
  const files = state.files[role].map(f => f.file);
  if (!files.length) return null;
  // B1：单个超限文件只跳过该文件，不再让整组解析失败
  const okFiles = [];
  const skipped = [];
  for (const f of files) {
    if (f.size > 200 * 1024 * 1024) {
      toast(`「${f.name}」超过 200 MB：已跳过，其余文件继续解析（建议按章节拆分后重传）`, false);
      skipped.push(f);
      continue;
    }
    okFiles.push(f);
  }
  let res = { results: [] };
  if (okFiles.length) {
    const fd = new FormData();
    okFiles.forEach(f => fd.append("files", f));
    fd.append("role", role);
    fd.append("ocr", $("t_ocr").checked ? "1" : "0");
    res = await api("/api/parse", { method: "POST", body: fd });
  }
  // B29：跳过的超大文件在解析结果里可见（error 行）；同时返回过滤后的文件列表，OCR 对位不再错行
  skipped.forEach(f => res.results.push({ name: f.name, error: "超过 200 MB 已跳过（建议按章节拆分后重传）" }));
  res.files = okFiles;
  return res;
}

async function runOcrJobs(group, role) {
  const myToken = ++ocrRunToken;   // v0.5：离开页面（自增 token）→ 轮询循环终止
  // B29：与 parseGroup 共用同一份过滤后文件列表（跳过超大文件的列表），OCR 对位不再错行
  const files = (group.files && group.files.length) ? group.files : state.files[role].map(f => f.file);
  const jobs = [];
  group.results.forEach((r, i) => {
    if (r.ocr_needed && $("t_ocr").checked && files[i]) {
      jobs.push({ file: files[i], idx: i, jobId: null, state: "queued", msg: "排队中", row: null });
    }
  });
  if (!jobs.length) return;
  const box = $("ocr_progress");
  box.innerHTML = `
    <div class="ocrwrap" role="status" aria-live="polite">
      <div class="ocrhead">
        <span class="spin"></span><span class="t" data-role="t">正在识别扫描件 / 图片</span>
        <span class="cnt" data-role="cnt">0/${jobs.length} 完成</span>
        <span class="note">MinerU · 文件上传至云端识别</span>
      </div>
      <div class="ocrbar running" data-role="bar"><i></i></div>
      <div data-role="rows"></div>
    </div>`;
  const rowsBox = box.querySelector('[data-role="rows"]');
  const ROW_STATE = {  // 状态 → 芯片文案/样式（queued 由轮询更新为 run）
    queued: ["queued", "排队中"],
    running: ["run", "识别中"],
    done: ["done", "完成 ✓"],
    failed: ["failed", "失败"],
    cancelled: ["cancel", "已取消"],
  };
  const updateOcrUi = () => {
    const n = jobs.length;
    const doneN = jobs.filter(j => j.state === "done").length;
    const failN = jobs.filter(j => j.state === "failed").length;
    const cancelN = jobs.filter(j => j.state === "cancelled").length;
    const runningN = jobs.filter(j => !["done", "failed", "cancelled"].includes(j.state)).length;
    const finished = doneN + failN + cancelN;
    const tEl = box.querySelector('[data-role="t"]');
    const cntEl = box.querySelector('[data-role="cnt"]');
    if (!runningN && finished === n) {                       // 全部终态
      tEl.textContent = failN ? "识别结束（部分失败）" : cancelN === n ? "识别已取消" : "识别完成 ✓";
      const cls = failN ? "bad" : cancelN === n ? "" : "good";
      tEl.style.color = cls === "bad" ? "var(--bad)" : cls === "good" ? "var(--good)" : "var(--accent2)";
      cntEl.textContent = `完成 ${doneN}/${n}` + (failN ? ` · 失败 ${failN}` : "") + (cancelN ? ` · 取消 ${cancelN}` : "");
    } else {
      tEl.style.color = "";
      cntEl.textContent = runningN ? `识别中 ${runningN}/${n}` : `完成 ${doneN}/${n}`;
    }
    const bar = box.querySelector('[data-role="bar"]');
    bar.classList.toggle("running", runningN > 0);
    bar.classList.toggle("bad", failN > 0);
    bar.querySelector("i").className = failN ? "bad" : "";
    bar.querySelector("i").style.width = (finished / n * 100).toFixed(1) + "%";
    jobs.forEach(j => {
      const row = j.row; if (!row) return;
      const [cls, txt] = ROW_STATE[j.state] || ROW_STATE.queued;
      const stEl = row.querySelector("[data-role=st]");
      stEl.className = "ocrst " + cls;
      stEl.textContent = txt;
      row.classList.toggle("running", j.state === "queued" || j.state === "running");
      const msgEl = row.querySelector("[data-role=msg]");
      msgEl.textContent = j.state === "done" ? "已自动加入输入" : j.state === "queued" ? "" : (j.msg || "");
      msgEl.className = "msg " + (j.state === "done" ? "good" : j.state === "failed" ? "bad" : "");
      row.querySelector("[data-role=cancel]").disabled = ["done", "failed", "cancelled"].includes(j.state);
    });
  };
  jobs.forEach(j => {
    const fd = new FormData();
    fd.append("file", j.file); fd.append("role", "ocr");
    j.promise = api("/api/ocr/start", { method: "POST", body: fd })
      .then(x => { j.jobId = x.job_id; });
    j.promise.catch(() => { j.state = "failed"; j.msg = "任务创建失败"; });
    const row = document.createElement("div");
    row.className = "ocrrow";
    row.innerHTML = `<span class="fname" title="${esc(j.file.name)}">${esc(j.file.name)}</span>
      <span class="ocrst queued" data-role="st">排队中</span>
      <span class="msg" data-role="msg"></span>
      <span class="ocrmini"></span>
      <button data-role="cancel" class="cancel">取消</button>`;
    row.querySelector("[data-role=cancel]").onclick = async () => {
      if (j.jobId) { await api("/api/ocr/jobs/" + j.jobId, { method: "DELETE" }).catch(() => {}); }
      j.state = "cancelled"; j.msg = "已取消";
      updateOcrUi();
    };
    rowsBox.appendChild(row);
    j.row = row;
  });
  updateOcrUi();
  await Promise.all(jobs.map(j => j.promise));

  const started = jobs.filter(j => j.jobId);
  while (myToken === ocrRunToken && started.some(j => !["done", "failed", "cancelled"].includes(j.state))) {
    await new Promise(r => setTimeout(r, 2000));
    await Promise.all(started.map(async j => {
      if (["done", "failed", "cancelled"].includes(j.state)) return;
      const s = await api("/api/ocr/jobs/" + j.jobId).catch(() => null);
      if (!s) return;
      j.state = s.state; j.msg = s.msg || s.state;
      if (s.state === "done" && s.result) {
        group.results[j.idx] = s.result;
      } else if (s.state === "failed") {
        group.results[j.idx] = { name: j.file.name, error: (s.msg || "识别失败") };
      }
    }));
    updateOcrUi();
  }
  updateOcrUi();
  started.forEach(j => {
    if (j.state === "cancelled") group.results[j.idx] = { name: j.file.name, error: "已取消识别" };
  });
}

function filesCount(res) { return (res && res.results || []).filter(r => r.ok).length; }
function resTotalChars(res) {
  return (res && res.results || []).filter(r => r.ok).reduce((a, r) => a + (r.chars || 0), 0);
}
function renderResults(roleLabel, res) {
  const el = $("parse_results");
  const wrap = document.createElement("div");
  wrap.innerHTML = `<div class="hint good">—— ${roleLabel} ——</div>`;
  (res.results || []).forEach(r => {
    if (r.ok) {
      const ocrBadge = (r.via && r.via.startsWith("mineru"))
        ? `<span class="tag">${r.via === "mineru-v4" ? "MinerU 精准 API" : "MinerU 轻量 API"}</span><span class="hint good">${esc(r.via_note || "已自动加入输入")}</span> `
        : "";
      const warns = (r.warnings || []).map(w => `<div class="warnline">${esc(w)}</div>`).join("");
      wrap.innerHTML += `<div class="res"><span class="name">${esc(r.name)}</span> · ${r.chars} 字 · ${r.slice_count} 切片 · 估算输入 ≈ ${((r.est_tokens || 0) / 10000).toFixed(1)} 万 token
        ${ocrBadge}${warns}
        <details><summary>预览切片（${(r.slices || []).length} 条，全部展开）</summary>${(r.slices || []).map(s => `<div><b>[${esc(s.sid)}] ${esc(s.title || "（全文）")}</b><br>${esc(s.preview)}${(s.text || "").length > (s.preview || "").length ? "…" : ""}</div>`).join("<hr>")}</details></div>`;
    } else {
      wrap.innerHTML += `<div class="res" style="border-color:var(--bad)"><span class="name">${esc(r.name)}</span> <span class="hint bad">${esc(r.error)}</span></div>`;
    }
  });
  el.appendChild(wrap);
}
/* S2：成本公式统一走后端（core/cost.estimate_run，与 Python 同源），旧内嵌公式删除 */
async function estimateCost() {
  // U-17：旧内嵌成本公式删除后遗留的 chars 已移除（成本统一走后端 core/cost）
  const nSlices = (pres.textbook && pres.textbook.results || []).filter(r => r.ok)
    .reduce((a, r) => a + (r.slice_count || 0), 0);
  const nQ = Math.max(parseInt($("target").value || "100"), 1);
  try {
    const r = await api("/api/cost/estimate", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chars_textbook: resTotalChars(pres.textbook) || 0,
                             chars_teacher: resTotalChars(pres.teacher) || 0,
                             n_slices: nSlices, n_questions: nQ }) });
    return { inp: r.input_tokens, out: r.output_tokens, tot: r.total_tokens };
  } catch (e) { return null; }   // A-新8：接口失败 → null，调用方显示「预估不可用（点击重试）」而非 ≈0.0
}
async function updateReady() {
  const tb = pres.textbook, tc = pres.teacher, ex = pres.exam, xt = pres.extra;
  const warns = [];
  [...(tb && tb.results || []), ...(tc && tc.results || [])].forEach(r => warns.push(...(r.warnings || [])));
  let estLine = "解析后此处显示成本预估";
  if (resTotalChars(tb) || resTotalChars(tc)) {
    const est = await estimateCost();
    if (!est) {
      // A-新8：成本预估接口失败 → 明确「预估不可用（点击重试）」，不得显示误导性的 ≈0.0/¥0.00
      estLine = `预估不可用（<a href="javascript:void(0)" onclick="updateReady()" style="text-decoration:underline">点击重试</a>）`;
    } else {
    const prov = (state.providers || []).find(p => p.id === state.provider);
    const price = prov && prov.price;
    const cny = price ? (est.inp / 1e6 * (price.input || 0) + est.out / 1e6 * (price.output || 0)) : null;
    const model = modelValue("model_gen") || (state.cfg && state.cfg.model_gen) || "";
    estLine = `预计注入/输出合计 ≈ ${(est.tot / 10000).toFixed(1)} 万 token`
      + (cny != null ? ` · 约 ¥${cny.toFixed(2)}` : "")
      + `（${prov ? prov.name : "当前服务商"} ${esc(model)} 参考价，以官网为准）`
      + (warns.length ? " · 有 " + warns.length + " 条体检提示" : "")
      + ($("t_web").checked ? " · ＋网络检索 ≈ 3 轮 × 3~5 次查询（费用以所选后端官网为准）" : "");
    }
  }
  $("ready_list").innerHTML = `
    <div class="ready">
      <span>素材就绪检查：</span>
      ${filesCount(tb) ? `<span class="ok">教材 ✓ ${filesCount(tb)} 文件</span>` : `<span class="no">教材 ✗ 未解析（必填）</span>`}
      ${filesCount(tc) ? `<span class="ok">教师重点 ✓ ${filesCount(tc)} 文件</span>` : `<span class="no">教师重点 ✗ 未解析（必填）</span>`}
      ${filesCount(ex) ? `<span class="ok">自备真题 ✓ ${filesCount(ex)} 文件</span>` : `<span class="opt">自备真题 —（可选）</span>`}
      ${filesCount(xt) ? `<span class="ok">补充资料 ✓ ${filesCount(xt)} 文件</span>` : `<span class="opt">补充资料 —（可选）</span>`}
      ${pres.sample ? `<span class="opt">（示例素材）</span>` : ""}
      ${$("t_web").checked ? `<span class="opt">网络检索已开启</span>` : ""}
      <span class="est">${estLine}</span>
    </div>`;
}
$("btn_parse").onclick = async () => {
  const btn = $("btn_parse"); const old = btn.textContent;
  // B19：解析期间禁用按钮（防双击 → 重复解析/409 混淆）
  btn.disabled = true; btn.textContent = "解析中…";
  $("parse_results").innerHTML = '<div class="hint"><span class="spin"></span>解析中…</div>';
  $("ocr_progress").innerHTML = "";
  try {
    const groups = [
      { res: await parseGroup("textbook"), role: "textbook", label: "教材（必填）", key: "textbook", render: true },
      { res: await parseGroup("teacher"), role: "teacher", label: "教师重点（必填）", key: "teacher", render: true },
      { res: await parseGroup("exam"), role: "exam", label: "自备真题（可选）", key: "exam", render: false },
      { res: await parseGroup("extra"), role: "extra", label: "补充资料（可选）", key: "extra", render: false },
    ].filter(g => g.res && g.res.results.length);
    $("parse_results").innerHTML = "";
    for (const g of groups) {
      await runOcrJobs(g.res, g.role);
      pres[g.key] = g.res;
      if (g.render || g.res.results.some(r => r.ok)) renderResults(g.label, g.res);
    }
    pres.sample = false;
    await updateReady();
    toast("解析完成：体检与成本预估已更新");
  } catch (e) { $("parse_results").innerHTML = `<div class="hint bad">${esc(e.message)}</div>`; }
  finally { btn.disabled = false; btn.textContent = old; }
};

$("btn_sample").onclick = async () => {
  try {
    $("btn_sample").disabled = true; $("btn_sample").textContent = "载入中…";
    const s = await api("/api/sample");
    if (!s.sample) throw new Error(s.error || "示例加载失败");
    pres = { textbook: { results: [s.textbook] }, teacher: { results: [s.teacher] }, exam: null, extra: null, sample: true };
    state.pres = pres;
    $("subject").value = s.subject;
    $("parse_results").innerHTML = "";
    $("ocr_progress").innerHTML = "";
    renderResults("教材（示例）", pres.textbook);
    renderResults("教师重点（示例）", pres.teacher);
    await updateReady();
    toast("示例素材已载入：先点「试出一题」看效果，满意后「创建课题 →」");
  } catch (e) { toast(e.message, false); }
  $("btn_sample").disabled = false; $("btn_sample").textContent = "手头还没素材？载入示例体验";
};
/* WP-12：纯净安装包无示例数据 → 探测并降级“载入示例”按钮（开发版不受影响） */
async function probeSampleAvailability() {
  try {
    const s = await api("/api/sample");
    const btn = $("btn_sample");
    if (btn && !s.sample && s.available === false) {
      btn.disabled = true; btn.title = s.error || "";
      btn.textContent = "示例仅开发版可用（纯净版请自备素材/上传官方大纲）";
    }
  } catch (e) { /* 探测失败不阻塞页面 */ }
}

/* ---- S3：素材库（历史解析会话）与项目模板 ---- */
async function loadSessions() {
  try {
    const r = await api("/api/sessions");
    const box = $("sess_box");
    if (!box) return;
    const list = r.sessions || [];
    if (!list.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="card">
      <h3 style="margin-bottom:6px">素材库（历史解析会话 · 跨项目复用 / 多教材合并）</h3>
      <div class="hint">勾选多个会话 → 「合并载入为教材」（quota 跨 session 按章加权）；单个会话可载入为教师重点。删除即失效。</div>
      ${list.map(s => `<div class="sessrow" style="display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)">
        <input type="checkbox" class="sessck" data-id="${esc(s.id)}">
        <b style="width:220px">${esc(s.name)}</b>
        <span class="hint">${esc(s.role)} · ${(s.chars || 0).toLocaleString()} 字 · ${s.slice_count} 章节 · ${esc(s.created)}</span>
        <button class="inlineBtn blue" data-a="loadtg" data-id="${esc(s.id)}">载入为教师重点</button>
        <button class="inlineBtn" data-a="del" data-id="${esc(s.id)}">删除</button>
      </div>`).join("")}
      <div class="btns"><button class="act" id="sess_merge">合并载入为教材</button></div>
    </div>`;
    box.querySelectorAll("[data-a=loadtg]").forEach(b => b.onclick = () => loadSessionAs(b.dataset.id, "teacher"));
    box.querySelectorAll("[data-a=del]").forEach(b => b.onclick = async () => {
      try { await api("/api/sessions/" + b.dataset.id, { method: "DELETE" }); toast("会话已删除"); loadSessions(); }
      catch (e) { toast(e.message, false); }
    });
    $("sess_merge").onclick = () => {
      const ids = [...box.querySelectorAll(".sessck:checked")].map(x => x.dataset.id);
      if (!ids.length) { toast("请先勾选要合并的会话", false); return; }
      loadSessionsAsTextbook(ids);
    };
  } catch (e) { /* 素材库不可用不阻塞主流程 */ }
}
async function loadSessionAs(sid, role) {
  const s = await api("/api/sessions/" + sid);
  const chars = (s.slices || []).reduce((a, x) => a + (x.text || "").length, 0);
  const res = { ok: true, name: s.name, chars: chars, slice_count: s.slice_count,
                slices: s.slices, est_tokens: Math.round(chars * 0.8), warnings: [], via: "session" };
  if (role === "teacher") { pres.teacher = { results: [res], sample: false }; renderResults("教师重点（会话）", pres.teacher); }
  else { pres.textbook = { results: [res], sample: false }; renderResults("教材（会话）", pres.textbook); }
  state.pres = pres;
  await updateReady();
  toast(`已载入「${s.name}」（${s.slice_count} 章节）`);
}
async function loadSessionsAsTextbook(ids) {
  const slices = [];
  const names = [];
  for (const sid of ids) {
    const s = await api("/api/sessions/" + sid);
    slices.push(...(s.slices || []));
    names.push(s.name);
  }
  // F4：多会话合并 → 各会话切片 sid 均从 S001 起始，合并后统一重编号（后端 create_project 亦防御性重编号）
  slices.forEach((s, i) => { s.sid = `S${String(i + 1).padStart(3, "0")}`; });
  const chars = slices.reduce((a, x) => a + (x.text || "").length, 0);
  const res = { ok: true, name: names.join(" + "), chars: chars, slice_count: slices.length,
                slices: slices, est_tokens: Math.round(chars * 0.8), warnings: [], via: "session" };
  pres.textbook = { results: [res], sample: false };
  state.pres = pres;
  renderResults("教材（多会话合并）", pres.textbook);
  await updateReady();
  toast(`已合并载入 ${slices.length} 章节（来自 ${ids.length} 个会话）`);
}
$("btn_sess").onclick = async () => {
  try {
    let saved = 0;
    for (const [role, g, label] of [["textbook", pres.textbook, "教材"],
                                    ["teacher", pres.teacher, "教师重点"]]) {
      const slices = ((g && g.results) || []).flatMap(r => r.slices || []);
      if (!slices.length) continue;
      await api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: label + "会话", role, slices,
                               source_name: ((g.results || [])[0] || {}).name || "" }) });
      saved++;
    }
    if (!saved) { toast("请先「解析并预览」（或用会话载入素材）再保存", false); return; }
    toast("已保存 " + saved + " 个素材会话（见下方素材库，可跨项目复用）");
    loadSessions();
  } catch (e) { toast(e.message, false); }
};
/* 项目配置模板：subject/exam/target/题型配比/Bloom/旋钮/附加要求 一键存/取
   A-01（R5）：键必须含作用域（本仓库「localStorage 键含 pid」约定）——medkit-tpl-<pid>，
   避免 A 项目存的模板被应用到 B 项目（跨项目参数污染）；新建课题表单用 __new__ 作用域 */
function tplKey() { return "medkit-tpl-" + (currentPid || "__new__"); }
$("btn_tpl_save").onclick = () => {
  try {
    const tpl = {
      subject: $("subject").value, exam: $("exam").value, target: $("target").value,
      ratios: { A1: $("r_a1").value, A2: $("r_a2").value, B1: $("r_b1").value, X: $("r_x").value },
      bloom: { 记忆: $("b_mem").value, 理解: $("b_und").value, 应用: $("b_app").value, 创造: $("b_cre").value },
      knobs: { difficulty: $("k_difficulty").value, analysis_style: $("k_analysis").value, stem_style: $("k_stem").value },
      requirements: $("requirements").value,
    };
    localStorage.setItem(tplKey(), JSON.stringify(tpl));
    toast(currentPid ? "已存为项目模板（仅作用于本项目）" : "已存为项目模板（仅作用于新建课题）");
  } catch (e) { toast(e.message, false); }
};
$("btn_tpl_apply").onclick = async () => {
  try {
    const t = JSON.parse(localStorage.getItem(tplKey()) || "null");
    if (!t) { toast(currentPid ? "本项目还没有模板" : "还没有新建课题模板", false); return; }
    if (t.subject) $("subject").value = t.subject;
    if (t.exam) $("exam").value = t.exam;
    if (t.target) { $("target").value = t.target; }
    (["r_a1", "r_a2", "r_b1", "r_x"]).forEach(k => {
      if (t.ratios) { const v = t.ratios[{ "r_a1": "A1", "r_a2": "A2", "r_b1": "B1", "r_x": "X" }[k]]; if (v) $(k).value = v; }
    });
    (["b_mem", "b_und", "b_app", "b_cre"]).forEach(k => { if (t.bloom) { const v = t.bloom[{ "b_mem": "记忆", "b_und": "理解", "b_app": "应用", "b_cre": "创造" }[k]]; if (v) $(k).value = v; } });
    if (t.knobs) {
      if (t.knobs.difficulty) $("k_difficulty").value = t.knobs.difficulty;
      if (t.knobs.analysis_style) $("k_analysis").value = t.knobs.analysis_style;
      if (t.knobs.stem_style) $("k_stem").value = t.knobs.stem_style;
    }
    if (t.requirements) $("requirements").value = t.requirements;
    ratioSum(); bloomSum();                                  // 刷新可视化配比条
    $("req_count").textContent = $("requirements").value.length + "/500";
    toast("模板已应用（服务商/模型为全局配置，在「连接服务商」确认）");
    await updateReady();
  } catch (e) { toast("模板应用失败：" + e.message, false); }
};
loadSessions();

function fullSlices(res) {
  return (res && res.results || []).filter(r => r.ok).flatMap(r => (r.slices || []));
}
