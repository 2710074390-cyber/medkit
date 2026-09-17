/* exported ART_LABEL, L, STEPS, SUBSTEP_ICON, SUBSTEP_LABEL, ankiOk, artifactLinks, arts, b, bloom, body, box, canResume, cls, cur, d, dl, doclickTrialAgain, examText, extra, extraText, f, fd, first, fmtClock, fmtLogLine, go, hit, href, i, isTerminal, issues, label, list, loadLog, loadProjects, logEl, markErr, meta, pct, pdAssetDel, pdAssetPick, pdAssetUp, pdAssets, pid, q, quota, r, ratios, renderLog, renderStepper, renderSubsteps, rerenderArtifact, resume, s, showProject, slices, ss, st, stageEl, startPoll, stepIdx, stopPoll, subStr, teacherText, updateRunBtn, usage */
/* ---- 试出一题（迭代1B） */
$("btn_trial").onclick = async () => {
  if (!(state.cfg && state.cfg.api_key_masked)) {
    toast("试出一题需要用 API Key——请先在「我的 → 连接服务商」保存", false);
    showTab("mine"); $("api_key").focus();
    return;
  }
  const slices = fullSlices(pres.textbook);
  if (!slices.length) return toast("请先解析教材（或点「载入示例」）再试出题", false);
  if (!filesCount(pres.teacher)) return toast("请先解析教师重点（必填）", false);
  const s = slices[Math.floor(Math.random() * slices.length)];
  const box = $("trial_box");
  box.innerHTML = `<div class="hint"><span class="spin"></span>试生成中（首次约 30~60 秒），来自切片 ${esc(s.sid)} · ${esc(s.title || "")}…</div>`;
  $("btn_trial").disabled = true;
  try {
    const teacherText = fullSlices(pres.teacher).map(x => x.text).join("\n");
    const examText = fullSlices(pres.exam).map(x => x.text).join("\n");      // v0.5.2：真题参与试出（风格校准）
    const extraText = fullSlices(pres.extra).map(x => x.text).join("\n");    // v0.5.2：资料参与试出
    const r = await api("/api/trial", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject: $("subject").value.trim() || "未命名科目",
        exam: $("exam").value,
        requirements: $("requirements").value.trim(),
        knobs: collectKnobs(),
        ratios: ratioSum(),
        bloom: bloomSum(),
        slice_sid: s.sid, slice_title: s.title || "", slice_text: s.text,
        teacher_text: teacherText,
        exam_text: examText, extra_text: extraText,
      }) });
    const q = r.question;
    const L = letters((q.options || []).length);   // R3-16：试出题同样支持 10 选项
    const issues = (r.issues || []).map(i =>
      `<div class="${i.severity === "fail" ? "failline" : "warnline"}">[${esc(i.code)}] ${esc(i.reason)}</div>`).join("");
    box.innerHTML = `
      <div class="trialq">
        <div style="margin:0 0 8px;padding:6px 10px;border:1px dashed var(--warn);border-radius:8px;font-size:12px;color:var(--warn)">⚠️ ${esc(r.note || "试出题不含网络检索/大纲锚定/图片素材，正式生成风格可能不同")}</div>
        <div class="src">试出题 · ${esc(r.from_slice || "")} · <span class="tag">${esc(q.type || "")}</span><span class="tag">${esc(q.bloom || "")}</span></div>
        <div class="qtext">${esc(q.question)}</div>
        <div class="opts">${(q.options || []).map((o, i) => L[i] + ". " + esc(o)).join("<br>")}</div>
        <details><summary>显示答案</summary>
          <div class="ans">✓ 答案：<b>${esc(q.answer)}</b><br>${esc(q.analysis)}</div>
        </details>
        ${issues ? `<div style="margin-top:8px">${issues}</div>` : `<div class="hint good">门禁即检：未发现问题 ✓</div>`}
        <div class="btns" style="margin-top:8px">
          <button class="act" onclick="$('btn_create').click()">满意，创建课题 →</button>
          <button class="act gray" onclick="doclickTrialAgain()">不满意，再试一题</button>
        </div>
        <div class="hint">每次随机换切片；创建后将带着这套参数正式生成全部题（创建前会展示成本预估）</div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint bad">试出题失败：${esc(e.message)}</div>`;
  }
  $("btn_trial").disabled = false;
};
function doclickTrialAgain() { $("btn_trial").click(); }

/* 校验失败：标红 + 滚动定位 + toast（长表单上方可见） */
function markErr(el, msg) {
  (Array.isArray(el) ? el : [el]).forEach(x => {
    x.classList.add("err");
    x.addEventListener("input", () => x.classList.remove("err"), { once: true });
  });
  const first = Array.isArray(el) ? el[0] : el;
  first.scrollIntoView({ behavior: "smooth", block: "center" });
  first.focus({ preventScroll: true });
  toast(msg, false);
}
$("btn_create").onclick = async () => {
  if (!$("subject").value.trim()) return markErr($("subject"), "请填写科目");
  if (!filesCount(pres.textbook)) {
    toast("请先解析教材（必填）——或点「载入示例」体验", false);
    return $("dz_textbook").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (!filesCount(pres.teacher)) {
    toast("请先解析教师重点（必填）", false);
    return $("dz_teacher").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if ($("requirements").value.trim().length > 500) return markErr($("requirements"), "附加要求超过 500 字");
  const ratios = ratioSum();
  const bloom = bloomSum();
  if (Object.values(ratios).reduce((a, b) => a + b, 0) !== 100)
    return markErr([$("r_a1"), $("r_a2"), $("r_b1"), $("r_x")], "题型配比合计应为 100%（当前 " + Object.values(ratios).reduce((a, b) => a + b, 0) + "%）");
  if (Object.values(bloom).reduce((a, b) => a + b, 0) !== 100)
    return markErr([$("b_mem"), $("b_und"), $("b_app"), $("b_cre")], "Bloom 配比合计应为 100%（当前 " + Object.values(bloom).reduce((a, b) => a + b, 0) + "%）");
  try {
    $("btn_create").disabled = true; $("btn_create").textContent = "创建中…";
    // R3-08：创建意图令牌——双击/双标签重复提交后端幂等去重（只建一个项目、只扣一次配额）
    if (!createToken) createToken = "ct-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
    const body = {
      client_token: createToken,
      subject: $("subject").value.trim(),
      exam: $("exam").value,
      target: parseInt($("target").value || "100"),
      ratios, bloom,
      knobs: collectKnobs(),
      requirements: $("requirements").value.trim(),
      toggles: { qbank: $("t_qbank").checked, paper: $("t_paper").checked, review: $("t_review").checked },
      textbook_slices: fullSlices(pres.textbook), teacher_slices: fullSlices(pres.teacher),
      exam_slices: fullSlices(pres.exam), extra_slices: fullSlices(pres.extra),
      teacher_text: fullSlices(pres.teacher).map(s => s.text).join("\n"),
      web_search: $("t_web").checked,
      web_backend: $("ws_backend").value,
      web_ref_quota: parseInt($("web_quota").value || "0"),
      web_manual_text: $("ws_manual").value,
      official_quota: parseInt($("official_quota") ? $("official_quota").value : "0") || 0,
    };
    const r = await api("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    createToken = "";   // 成功即重置：下一次创建是新的意图
    toast("课题已创建：" + r.pid + "（已打开项目详情）");
    // A-新13：创建成功后清空表单（subject 等）、已选文件列表与解析结果，避免第二门课带错素材
    $("subject").value = "";
    ["textbook", "teacher", "exam", "extra"].forEach(role => { state.files[role] = []; renderDz(role); });
    pres = { textbook: null, teacher: null, exam: null, extra: null, sample: false };
    state.pres = pres;
    $("parse_results").innerHTML = "";
    $("ocr_progress").innerHTML = "";
    $("trial_box").innerHTML = "";
    location.hash = "bank";
    showTab("bank");
    showProject(r.pid);
  } catch (e) { toast(e.message, false); }
  $("btn_create").disabled = false; $("btn_create").textContent = "创建课题 →";
};

/* ---- ③ 项目 */
/* v0.5：currentPid/pollTimer/pollFails 已提前声明于脚本顶部（showTab 需在初始化时安全调用 stopPoll） */

async function loadProjects() {
  const r = await api("/api/projects");
  const box = $("proj_list");
  if (!r.projects.length) {
    box.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-mine"></use></svg>
      <div class="sub">还没有项目 · 上传教材与教师重点，生成你的第一套题库</div>
      <button class="act" onclick="document.getElementById('tab-bank').scrollIntoView()">去上方「新建课题」↑</button>
    </div>`;
    return;
  }
  box.innerHTML = "";
  r.projects.forEach(p => {
    const d = document.createElement("div");
    d.className = "proj" + (p.meta_missing ? " orphan" : "");
    d.innerHTML = `<b>${esc(p.subject)}</b>
      <span class="stage${p.running ? " running" : ""}">${p.running ? "● 运行中" : esc(p.stage_label || "……")}</span>
      <div class="meta">${p.meta_missing ? "元数据缺失 · 可删除" : ((p.exam || "") + " · 目标 " + (p.target || 0) + " 题 · " + (p.created || "").slice(0, 16).replace("T", " "))}</div>`;
    d.onclick = () => {
      if (p.meta_missing) {
        // R3-20：孤儿项目（meta 缺失）不可进详情 → 直接提供删除入口
        confirmModal("删除孤儿项目？",
          "项目目录 <b>" + esc(p.pid) + "</b> 缺少元数据（可能因中断产生），将直接删除整个目录，不可恢复。",
          "直接删除", async () => {
            try {
              await api("/api/projects/" + encodeURIComponent(p.pid), { method: "DELETE" });
              toast("孤儿项目已删除");
              loadProjects();
            } catch (e) { toast(e.message, false); }
          });
      } else showProject(p.pid);
    };
    box.appendChild(d);
  });
}
const ART_LABEL = [
  [/^qbank\.md$/i, "📄", "题库 MD"],
  [/^qbank\.html$/i, "🌐", "题库 · 在线"],
  [/押题卷.*\.html$/i, "✍️", "押题卷 · 交互"],
  [/复习手册.*\.md$/i, "📘", "手册 MD"],
  [/复习手册.*\.html$/i, "📗", "手册 · 在线"],
  [/anki_export\.txt$/i, "🧠", "Anki 文本"],
  [/\.apkg$/i, "🃏", "Anki 卡包"],
];
function artifactLinks(pid, names) {
  return `<div class="artgrid">` + (names || []).map(n => {
    const hit = ART_LABEL.find(([re]) => re.test(n));
    const [ico, label] = hit ? [hit[1], hit[2]] : ["📃", n];   // C-06：三元组 [正则,图标,标题] 取下标 1/2
    const href = n.endsWith(".apkg")
      ? `/api/projects/${encodeURIComponent(pid)}/export/apkg`
      : `/api/projects/${encodeURIComponent(pid)}/files/${encodeURIComponent(n)}`;
    const dl = n.endsWith(".apkg") ? " download" : "";
    return `<a class="artchip" href="${esc(href)}"${dl} target="${n.endsWith(".apkg") ? "" : "_blank"}" rel="noopener">
      <span class="ai">${ico}</span><span><b>${esc(label)}</b><small>${esc(n)}</small></span></a>`;
  }).join("") + `</div>`;
}
/* B17：仅重渲染单个产物（后端复用审核渲染层；题库内容不变、无 token 消耗）
   RV1：双签名——内联传 this（data-pid/data-what），程序化调用传 (pid, what) */
async function rerenderArtifact(pidOrEl, what) {
  let pid = pidOrEl;
  if (pidOrEl && typeof pidOrEl === "object" && pidOrEl.dataset) {
    pid = pidOrEl.dataset.pid || "";
    what = pidOrEl.dataset.what || "";
  }
  const label = { qbank: "题库", paper: "押题卷", review: "复习手册", anki: "Anki" }[what] || what;
  confirmModal(`仅重渲染「${label}」？`, `<p style="margin:0;color:var(--dim)">不会改动题库内容，只重新生成对应产物文件（无 token 消耗）。</p>`,
    "重渲染", async () => {
      try {
        const r = await api("/api/projects/" + encodeURIComponent(pid) + "/rerender", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ what }) });
        toast("重渲染完成：" + (r.rendered || []).join("、"));
        showProject(pid);
      } catch (e) { toast(e.message, false); }
    }, false);
}
const STEPS = [["websearch", "网络检索"], ["generating", "出题"], ["gate1", "门禁①"], ["qc", "质检"], ["fixing", "修复"],
               ["finalizing", "汇总"], ["reviewing", "复习"], ["rendering", "产物"]];
function stepIdx(stage) {
  if (stage === "done") return STEPS.length;
  // 终态/未进入阶段不给步骤高亮（stage_label 负责显示真实状态，stepper 保持全灰避免误导）
  if (["cancelled", "error", "quota", "parsing"].includes(stage)) return -1;
  const i = STEPS.findIndex(s => s[0] === stage);
  return i === -1 ? 0 : i;
}
function fmtClock(iso) {
  try { const d = new Date(iso); return d.toLocaleTimeString("zh-CN", { hour12: false }); }
  catch (e) { return ""; }
}
function renderStepper(stage, progress) {
  const cur = stepIdx(stage);
  // A-02：pct 钳制到 0~100（NaN/负数/超 100 的异常进度值不再画出超宽/负宽进度条）
  const pct = progress ? Math.min(100, Math.max(0, Number(progress.pct) || 0)) : 0;
  let subStr = "", desc = "";
  if (progress) {
    subStr = progress.sub_total
      ? ` · ${esc(progress.sub || "子任务")} ${progress.sub_done || 0}/${progress.sub_total}`
      : (progress.sub ? ` · ${esc(progress.sub)}` : "");
    desc = progress.detail ? esc(progress.detail)
      : (stage === "done" ? "已完成" : (pct > 0 ? "进行中…" : "准备中…"));
  }
  return STEPS.map((s, i) =>
    `<span class="stp ${i === cur ? "cur" : i < cur ? "done" : ""}">${s[1]}</span>`).join("")
    + `<span class="stp ${cur >= STEPS.length ? "done" : ""}">完成</span>`
    + (progress ? `<div style="flex:1;min-width:180px">
        <div class="pvbar"><i style="width:${pct}%"></i></div>
        <div id="pvtext">${desc} · ${pct}%${subStr}`
        + (progress.updated ? ` · 更新 ${fmtClock(progress.updated)}` : "") + `</div>
      </div>` : "");
}
const SUBSTEP_LABEL = {pending:"排队中", running:"进行中", done:"完成", failed:"失败", retry:"重试", cancelled:"已取消"};
const SUBSTEP_ICON = {pending:"•", running:"⏳", done:"✓", failed:"✗", retry:"↻", cancelled:"⏹"};
function renderSubsteps(rows, stage) {
  // 终态（done/error/cancelled）展示全部最近事件，运行中按当前阶段过滤
  const isTerminal = ["done", "error", "cancelled"].includes(stage);
  const list = (rows || []).filter(s => isTerminal || !stage || s.stage === stage);
  if (!list.length) return `<div class="hint">暂无子步骤记录${stage ? "（当前阶段：" + esc(stage) + "）" : ""}</div>`;
  return `<details class="substeps-card" open>
    <summary>子步骤 · ${list.length} 条${stage ? " · " + (isTerminal ? "全部阶段" : "阶段 " + esc(stage)) : ""}</summary>
    <div class="substeps">` + list.map(s => {
      const st = s.status || "pending";
      const cls = st === "running" ? " running" : st === "done" ? " done"
        : st === "failed" ? " failed" : st === "retry" ? " retry"
        : st === "cancelled" ? " cancelled" : "";
      return `<details class="substep${cls}"${st === "running" ? " open" : ""}>
        <summary><span class="ss-ico">${SUBSTEP_ICON[st] || "•"}</span> ${esc(s.label || s.step)}
          <small class="hint">${esc(SUBSTEP_LABEL[st] || st)}${s.detail ? " · " + esc(s.detail) : ""}</small></summary>
        <div class="substep-detail">${esc(s.step)}${s.detail ? " · " + esc(s.detail) : ""}${s.ts ? " · " + fmtClock(s.ts) : ""}</div>
      </details>`;
    }).join("") + `</div></details>`;
}
async function showProject(pid) {
  if (currentPid && pid !== currentPid && typeof reviewDirtyGuard === "function" && !reviewDirtyGuard()) return;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  const meta = await api("/api/projects/" + pid);
  $("proj_detail").style.display = "block";
  $("pd_title").textContent = `项目详情 · ${meta.subject}`;
  const quota = (meta.quota || []).map(q =>
    `<span>${esc(q.title ? (q.title.length > 14 ? q.title.slice(0, 14) + "…" : q.title) : q.sid)}：${q.count}题</span>`).join("");
  // R3S-01：meta.usage 仅在管线成功跑完才写入——先判空再计算，新建/取消/error 项目详情不再整页崩溃
  const usage = meta.usage ? `<div class="hint" style="margin-top:6px">本次消耗：输入 ${((meta.usage.prompt_tokens || 0) / 10000).toFixed(2)} + 输出 ${((meta.usage.completion_tokens || 0) / 10000).toFixed(2)} 万 token`
    + (meta.usage.est_cost_cny != null ? ` ≈ ¥${meta.usage.est_cost_cny}` : "") + `（以官网为准）</div>` : "";
  // ME-9：Anki 导出按「产物文件是否存在」判断（后端已放开 stage 门禁）——error/取消后已产出的文件同样可下载
  const ankiOk = (meta.artifacts || []).some(n => /\.apkg$/i.test(n) || /^anki_export\.txt$/i.test(n));
  const extra = [];
  if (meta.requirements) extra.push(`附加要求：${esc(meta.requirements).slice(0, 60)}`);
  if (meta.bloom && Object.keys(meta.bloom).length) extra.push(`Bloom：${["记忆","理解","应用","创造"].map(k => (meta.bloom[k] ?? 0) + "%").join("/")}`);
  if (meta.web_search) extra.push(`网络检索${meta.web_ref_quota ? "（引用 " + meta.web_ref_quota + "%）" : ""}`);
  if (meta.image_warning) extra.push(`⚠️ 本轮未产出图题（已有图片素材可重试/加大题量）`);
  if (meta.exam_chars) extra.push(`自备真题 ${meta.exam_chars.toLocaleString()} 字（考点/风格校准，不照抄）`);
  if (meta.extra_chars) extra.push(`补充资料 ${meta.extra_chars.toLocaleString()} 字`);
  if ((meta.artifacts || []).some(n => /人工复核清单/.test(n))) {
    extra.push("📋 人工复核清单：被门禁/质检/网络冲突拦截、需人工确认的题与原因（见下方产物卡片）");
  }
  $("pd_body").innerHTML = `
    <div class="meta hint">${esc(meta.exam)} · 目标 ${meta.target} 题 · 阶段：<span id="pd_stage">${esc(meta.stage_label || meta.stage || "……")}</span><br>
    产物开关：${meta.toggles.qbank ? "题库✓" : "题库✗"} ${meta.toggles.paper ? "押题卷✓" : "押题卷✗"} ${meta.toggles.review ? "复习手册✓" : "复习手册✗"}
    ${ankiOk ? `<a class="btnart" href="/api/projects/${encodeURIComponent(pid)}/export/anki">导出 Anki（.txt）</a>
      <a class="btnart" href="/api/projects/${encodeURIComponent(pid)}/export/apkg" download>S3 导出 Anki（.apkg）</a>
      <a class="btnart" href="javascript:void(0)" data-pid="${esc(pid)}" onclick="ankiPreview(this)" title="导出前先看卡面样式">预览 Anki 卡样</a>
      <a class="btnart" href="javascript:void(0)" onclick="ankiHelp()" title="如何把导出文件导入 Anki">Anki 导入指引</a>` : ""}</div>
    ${extra.length ? `<div class="hint" style="margin-top:6px">${extra.join(" · ")}</div>` : ""}
    <div id="pd_stepper" class="stepper">${renderStepper(meta.stage, meta.progress)}</div>
    <div id="pd_substeps" class="substeps-card">${renderSubsteps(meta.substeps, meta.stage)}</div>
    <div class="hint" style="margin-top:6px">各章节配额（教师重点词频加权）：</div>
    <div class="quota">${quota}</div>
    <div id="pd_arts" class="hint" style="margin-top:8px">${artifactLinks(pid, meta.artifacts)}</div>
    ${(meta.artifacts || []).some(n => /^qbank\.html$/i.test(n))
      ? `<div class="hint" style="margin-top:6px">仅重渲染（不重跑管线 · 无 token 消耗）：` +
        Object.entries([["qbank", "题库"], ["paper", "押题卷"], ["review", "复习手册"], ["anki", "Anki"]])
          .map(([w, l]) => `<button class="mini-btn" style="padding:2px 9px" data-pid="${esc(pid)}" data-what="${w}" onclick="rerenderArtifact(this)">${l}</button>`).join(" ")
        + ` <span class="hint" style="font-size:11px">— 用于只改产物不改题</span></div>`
      : ""}
    ${usage}
    <div id="pd_assets_box" style="margin-top:12px">
      <div class="hint"><b>图片素材（图/表题）</b>：上传教材插图 / 心电图 / 影像 / 辅检表截图，生成时提示出图题（image_ref 门禁校验，错题可随图查看）；无素材项目零影响。</div>
      <div id="pd_assets" class="hint">加载中…</div>
      <div class="row" style="margin-top:6px;flex-wrap:wrap">
        <input type="text" id="pd_asset_cap" placeholder="图注（如：心电图 · 急性心梗）" style="flex:1;min-width:150px">
        <button class="act gray" onclick="pdAssetPick()">上传图片</button>
        <input type="file" id="pd_asset_file" accept="image/*,.png,.jpg,.jpeg,.webp,.gif" style="display:none" onchange="pdAssetUp(this)">
      </div>
    </div>
    <pre id="pd_log"></pre>`;
  currentPid = pid;
  pdAssets();
  stopPoll();
  updateRunBtn(meta.running, meta.stage);
  $("btn_review").style.display = meta.stage === "done" ? "inline-block" : "none";
  $("review_panel").style.display = "none";
  $("proj_detail").scrollIntoView({ behavior: "smooth", block: "start" });
  if (meta.running) startPoll(pid);
  else if (["done", "error", "cancelled"].includes(meta.stage)) loadLog(pid);
}
async function pdAssets() {
  if (!FEATURES.image_q) return;   // IMP-02：flag 关闭时整卡已隐藏，跳过加载
  try {
    const r = await api("/api/projects/" + currentPid + "/assets");
    const box = $("pd_assets");
    if (!r.assets || !r.assets.length) { box.innerHTML = "暂无图片素材——上传后生成时可出图/表题。"; return; }
    box.innerHTML = r.assets.map(a => `<div class="mk-row" style="margin:6px 0">
      <img src="/api/projects/${esc(currentPid)}/assets/${esc(a.sid)}" alt="${esc(a.caption)}"
        style="max-width:160px;max-height:110px;border-radius:8px;border:1px solid var(--line);margin-right:10px;vertical-align:middle"
        onerror="this.style.display='none'">
      <b>${esc(a.sid)}</b> · ${esc(a.caption)}<span class="hint"> · ${((a.bytes || 0) / 1024).toFixed(0)}KB</span>
      <button class="act gray" style="padding:3px 9px;font-size:11px;margin-left:8px" data-sid="${esc(a.sid)}" onclick="pdAssetDel(this)">删除</button>
    </div>`).join("");
  } catch (e) { const box = $("pd_assets"); if (box) box.innerHTML = "素材加载失败：" + esc(e.message); }
}
function pdAssetPick() { $("pd_asset_file").click(); }
async function pdAssetUp(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  fd.append("caption", $("pd_asset_cap").value || "");
  try {
    const r = await api("/api/projects/" + currentPid + "/assets", { method: "POST", body: fd });
    toast(`已上传素材 ${r.sid}（生成时出图/表题并做 image_ref 门禁）`);
    pdAssets();
  } catch (e) { toast(e.message, false); }
  finally { input.value = ""; }
}
async function pdAssetDel(sidOrEl) {
  // RV1：双签名——内联传 this（data-sid），程序化调用传 sid
  const sid = sidOrEl && sidOrEl.dataset ? (sidOrEl.dataset.sid || "") : sidOrEl;
  await api("/api/projects/" + currentPid + "/assets/" + sid, { method: "DELETE" });
  toast("已删除素材 " + sid);
  pdAssets();
}

async function loadLog(pid) {
  try {
    const s = await api("/api/projects/" + pid + "/status");
    renderLog($("pd_log"), s.log || []);
    const ss = $("pd_substeps");
    if (ss) ss.innerHTML = renderSubsteps(s.substeps, s.stage);
  } catch (e) { /* ignore */ }
}
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } pollFails = 0; }
function updateRunBtn(running, stage, cancelling) {
  const b = $("btn_run");
  if (!b) return;
  if (cancelling) {   // R3-09：取消请求已发出、各阶段检查点陆续生效——禁用停止按钮防重复请求
    b.textContent = "正在取消中…";
    b.disabled = true;
    b.dataset.running = "1";
    b.dataset.resume = "";
    return;
  }
  b.disabled = false;
  if (running) {
    b.textContent = "⏹ 停止生成（保留断点）";
    b.className = "act danger";
    b.dataset.running = "1";
    b.dataset.resume = "";
  } else {
    // B9：error/cancelled 后按钮统一进入「重试生成（从断点）」态（离开再回来也保持）
    const canResume = stage === "error" || stage === "cancelled";
    b.textContent = canResume ? "重试生成（从断点）" : "开始生成";
    b.className = "act";
    b.dataset.running = "";
    b.dataset.resume = canResume ? "1" : "";
  }
}
function fmtLogLine(l) {
  const cls = (l.includes("❌") || l.includes("失败") || l.includes("错误")
    || l.includes("Exception") || l.includes("Traceback")) ? " lg-err"
    : (l.includes("⚠️")) ? " lg-warn" : "";
  return `<div class="lg${cls}">${esc(l)}</div>`;
}
function renderLog(el, lines) {
  if (!el) return;
  el.innerHTML = lines.map(fmtLogLine).join("");
  el.scrollTop = el.scrollHeight;
}
function startPoll(pid) {
  stopPoll();
  pollTimer = setInterval(async () => {
    try {
      const s = await api("/api/projects/" + pid + "/status");
      pollFails = 0;
      const stageEl = $("pd_stage");
      if (stageEl) {
        if (s.cancelling) {
          stageEl.innerHTML = "正在取消中… <span class='spin'></span>";
          stageEl.style.color = "var(--warn)";
        } else {
          stageEl.innerHTML = esc(s.stage_label) + (s.running ? " <span class='spin'></span>" : "");
          stageEl.style.color = "";
        }
      }
      const st = $("pd_stepper");
      if (st) st.innerHTML = renderStepper(s.stage, s.progress);
      const ss = $("pd_substeps");
      if (ss) ss.innerHTML = renderSubsteps(s.substeps, s.stage);
      const logEl = $("pd_log");
      if (logEl) renderLog(logEl, s.log || []);
      const arts = $("pd_arts");
      if (arts && s.artifacts) arts.innerHTML = "已生成：" + artifactLinks(pid, s.artifacts);
      updateRunBtn(s.running, s.stage, s.cancelling);
      if (!s.running && ["done", "error", "cancelled"].includes(s.stage)) {
        stopPoll();
        if (s.stage === "done") { toast("全部产物生成完成 "); $("btn_review").style.display = "inline-block"; }
        if (s.stage === "cancelled") toast("已取消：题目与断点已保留，可再次「开始生成」续跑", false);
        if (s.stage === "error") {
          toast("生成出错：详见下方日志；可点「重试生成（从断点）」续跑（质检及之后阶段会重跑）", false);
        }
        loadLog(pid);
      }
    } catch (e) {
      pollFails++;
      if (pollFails >= 3) {
        stopPoll();
        toast("进度刷新失败：" + e.message, false);
      }
    }
  }, 2500);
}
$("btn_run").onclick = async () => {
  const b = $("btn_run");
  if (b.dataset.busy === "1") return;   // B19：请求在途防双击（重复 POST → 409/双解析）
  try {
    const pid = currentPid;
    if (b.dataset.running === "1") {
      confirmModal("停止生成？",
        "<p>已生成的部分题目与断点会保留；再次「开始生成」将从断点续跑（<b>质检/修复/复习/渲染阶段会重跑</b>）。</p>",
        "停止", async () => {
          b.dataset.busy = "1";
          try {
            await api("/api/projects/" + pid + "/run", { method: "DELETE" });
            toast("正在停止…（已生成部分保留）");
          } catch (e) { toast(e.message, false); }
          finally { b.dataset.busy = ""; }
        });
    } else {
      const resume = b.dataset.resume === "1";
      const go = async () => {
        b.dataset.busy = "1";
        try {
          await api("/api/projects/" + pid + "/run", { method: "POST" });
          toast("管线已启动：⓪网络检索(可选) → MedGen 出题 → 门禁① → MedQC 质检 → MedFix 修复 → 汇总 → MedReview 复习手册 → 渲染产物");
          startPoll(pid);
        } catch (e) { toast(e.message, false); }
        finally { b.dataset.busy = ""; }
      };
      if (resume) {
        // B9：重试前说明重跑范围与费用（用户不知情下重跑可能再次消耗 token）
        confirmModal("从断点重试？",
          "<p>已完成的切片<b>不会</b>重跑；<b>质检/修复/复习/渲染阶段会重跑</b>并产生相应 token 消耗（以实际用量为准）。</p>",
          "从断点重试", go, false);
      } else go();
    }
  } catch (e) { toast(e.message, false); }
};
$("btn_delete").onclick = () => {
  if (!currentPid) return;
  const pid = currentPid;
  confirmModal("确认删除该项目？",
    `项目 <b>${esc(pid)}</b> 及其<b>全部产物</b>将被删除，此操作不可恢复。`,
    "确认删除", async () => {
      try {
        await api("/api/projects/" + pid, { method: "DELETE" });
        toast("项目已删除");
        $("proj_detail").style.display = "none";
        loadProjects();
      } catch (e) { toast(e.message, false); }
    });
};
