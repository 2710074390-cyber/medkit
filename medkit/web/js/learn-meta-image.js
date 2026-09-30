/* EP-01 图像录入（错题图片 → 识别 → 归因 → 入库）
 *
 * 加载顺序契约（V-15）：本片属 `learn` 族，接在 learn-meta.js 之后（最后一篇）。
 * ⚠️ **加载期只定义、不执行**——除了文件末尾那次 `showLearnView` 包装
 *    （它引用的 `showLearnView` 定义在更早的 learn.js 里，不构成前向引用）。
 *    所有 DOM 访问都发生在 `miLoadCapability()` / `miStart()` 被调用时。
 *
 * ## 交互设计（借鉴成熟客户端的做法）
 *
 * 1. **能力先声明**：进视图先拉 `/api/errors/image/capability`，横幅直说
 *    "当前模型支持读图（推荐）" 或 "未确认支持 → 将用 OCR 兜底"，
 *    并给出 `prefer` 覆盖开关。**不猜**，也不让用户自己试。
 * 2. **等待可感知**：识别阶段把模型原始输出**边到边显**（`delta`），
 *    而不是只转一个圈——一张医学题图要读十几秒，静默等待最容易让人以为卡死。
 * 3. **闸门前置**：`confidence` / `my_reasoning` 未填时「开始识别」是禁用的，
 *    并说明原因（图片里通常印着答案，事后补填等于给校准数据编故事）。
 * 4. **失败要说清**：识别两条通道各自的结果都在 `attempts` 里，原样展示，
 *    不折叠成"识别失败"四个字。
 */
/* exported miLoadCapability, miCapabilityRender, miPickFile, miStart, miReset, miGateCheck,
   miRenderFields, miFocus, miDirectToggle, miCommit */

// 当前识别能力（`/api/errors/image/capability` 的缓存；进视图时刷新）
let miCap = null;
// 待识别的图片文件（拖拽/粘贴/选择三处入口都写到这里）
let miFile = null;
// 最近一次识别结果（**原样**保存，含出处标记等不可见字段）；用户编辑后与它合并再回传
let miFields = null;
// 在途 AbortController 的 anchorId（识别与入库各一个）
const MI_ANCHOR_GO = "mi_go";
const MI_ANCHOR_COMMIT = "mi_commit";

/** 进视图时调用：拉能力信息 + 重置闸门提示。 */
async function miLoadCapability() {
  const hint = $("mi_cap_hint");
  if (!hint) return;                     // 老版本 HTML 没有这块 → 静默跳过
  hint.innerHTML = `<span class="spin"></span>正在检测当前模型是否支持图像输入…`;
  try {
    miCap = await api("/api/errors/image/capability");
  } catch (e) {
    miCap = null;
    hint.innerHTML = `<span class="mt-warn">能力检测失败：${esc(e.message || e)}</span>`;
    return;
  }
  miCapabilityRender();
  miGateCheck();
}

/** 渲染能力横幅。`prefer` 被显式改成 vision/ocr 时，横幅说明"你覆盖了自动选择"。 */
function miCapabilityRender() {
  const hint = $("mi_cap_hint");
  const badge = $("mi_cap");
  if (!hint) return;
  const sel = $("mi_prefer");
  const prefer = sel ? sel.value : "auto";
  if (!miCap) { hint.innerHTML = `<span class="hint">能力信息不可用（不影响使用，将按自动选择）</span>`; return; }

  const isVision = miCap.vision;
  const eff = prefer === "auto" ? miCap.preferred : prefer;
  if (badge) badge.textContent = eff === "vision" ? "原生视觉" : "OCR";

  const overridden = prefer !== "auto" && prefer !== miCap.preferred;
  let html;
  if (eff === "vision") {
    html = `<span class="mt-ok">✓ 用<b>原生视觉模型</b>直接读图</span>
      <span class="hint">（${esc(miCap.model || "未配置模型")}）—— 一次调用同时认字与分字段，还能看出图上标了哪个选项。</span>`;
  } else {
    html = `<span class="mt-warn">⚠ 当前模型未确认支持图像输入 → 用 <b>OCR</b> 识别</span>
      <span class="hint">（${esc(miCap.reason || "")}）</span><br>
      <span class="hint">OCR 走 ${esc(miCap.ocr_label || "MinerU")}，识别后由本地规则拆字段（不再调用模型）。</span>`;
  }
  if (overridden) {
    html += `<br><span class="hint">你已手动指定「${prefer === "vision" ? "强制原生视觉" : "强制 OCR"}」，
      覆盖了自动选择${prefer === "vision" && !isVision ? "——模型名未带视觉标记，若实际不支持会失败并自动降级 OCR" : ""}。</span>`;
  }
  if (miCap.ocr_needs_network && eff === "ocr") {
    html += `<br><span class="hint">注意：OCR 需要联网（MinerU 服务）。</span>`;
  }
  hint.innerHTML = html;
}

/** 闸门自检：两项都填了才允许发起识别。 */
function miGateCheck() {
  const btn = $("mi_go");
  const gate = $("mi_gate_hint");
  if (!btn) return;
  const conf = $("mi_conf") ? $("mi_conf").value : "";
  const reason = $("mi_reason") ? $("mi_reason").value.trim() : "";
  const ready = !!conf && !!reason;
  btn.disabled = !ready;
  if (gate) {
    if (ready) {
      gate.innerHTML = `<span class="mt-ok">✓ 已填：把握程度 ${esc(conf)}/5 —— 可以开始识别了</span>`;
    } else {
      const miss = [];
      if (!conf) miss.push("把握程度");
      if (!reason) miss.push("当时的想法");
      gate.innerHTML = `还需填写：<b>${miss.join(" · ")}</b>。
        这两项必须<b>先填</b>：识别会把图里的答案一起读出来，事后补自评就等于给校准数据编故事。`;
    }
  }
}

/** 「识别后直接入库」勾选态：切换主按钮文案，让"这一下会发生什么"一目了然。 */
function miDirectToggle() {
  const btn = $("mi_go");
  if (!btn) return;
  btn.textContent = ($("mi_direct") && $("mi_direct").checked)
    ? "识别并入库" : "① 识别图片";
}

/* ---------------------------------------------------------------- 三处图片入口 */

/** 「新增错题」卡片里的「拍题录入」按钮跳到这里。
 *
 * 为什么不保留第二套拍题实现：旧入口只做 MinerU OCR 并回填文本框——不做能力判定
 * （模型支持原生视觉也不会用）、不过闸门、不跑归因，与本卡片口径分裂；
 * 同一视图放两个「拍题」按钮，用户无从知道该点哪个。故这里只做**引导**：
 * 滚到卡片，并聚焦"下一个该填/该点的东西"（闸门字段优先，填齐才轮到投放区）。
 *
 * 顺带刷新能力横幅：用户可能刚在设置页换过模型，缓存里的 `miCap` 会过期。
 */
function miFocus() {
  const drop = $("mi_drop");
  if (!drop) return;                       // 老版本 HTML → 静默跳过
  const conf = $("mi_conf");
  const reason = $("mi_reason");
  const target = (!conf || !conf.value) ? conf
    : ((!reason || !reason.value.trim()) ? reason : drop);
  if (target && target.scrollIntoView) target.scrollIntoView({ behavior: "smooth", block: "center" });
  if (target && target.focus) target.focus({ preventScroll: true });
  miLoadCapability();
}

function miPickFile(input) {
  const f = input.files && input.files[0];
  input.value = "";               // 允许连续选同一张
  if (f) miAccept(f);
}

/** 统一收图：体积前置校验（超限不落盘、不发请求），然后更新投放区。 */
function miAccept(file) {
  if (!file) return;
  if (!/^image\//.test(file.type || "") && !/\.(png|jpe?g|webp|bmp)$/i.test(file.name || "")) {
    toast("只支持 PNG / JPEG / WebP / BMP 图片", false);
    return;
  }
  const MAX = (miCap && miCap.max_vision_bytes) ? 20 * 1024 * 1024 : 20 * 1024 * 1024;
  if (file.size > MAX) {
    toast(`图片 ${(file.size / 1048576).toFixed(1)} MB 超过 20 MB，请压缩或裁剪后重试`, false);
    return;
  }
  miFile = file;
  const drop = $("mi_drop");
  const sub = $("mi_drop_sub");
  if (drop) drop.classList.add("has-file");
  if (sub) {
    sub.innerHTML = `已选择：<b>${esc(file.name || "截图")}</b>
      <span class="hint">（${fmtBytes(file.size)}）</span> · 点此可换一张`;
  }
  miGateCheck();
}

/** 粘贴截图（只在学习中心错题本视图可见时接管，避免全局抢事件）。 */
document.addEventListener("paste", e => {
  const view = document.getElementById("lv-mistakes");
  if (!view || !view.classList.contains("show")) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const it of items) {
    if (it.type && it.type.indexOf("image") === 0) {
      const f = it.getAsFile();
      if (f) { e.preventDefault(); miAccept(f); toast("已从剪贴板读取截图"); return; }
    }
  }
});

/* 拖拽：dragover 必须 preventDefault，否则浏览器会直接打开图片（整页跳走） */
(function miInitDrop() {
  const drop = document.getElementById("mi_drop");
  if (!drop) return;                    // 老版本 HTML → 不挂载
  drop.addEventListener("click", () => { const f = $("mi_file"); if (f) f.click(); });
  drop.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const f = $("mi_file"); if (f) f.click(); }
  });
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", e => {
    const dt = e.dataTransfer;
    const f = dt && dt.files && dt.files[0];
    if (f) miAccept(f);
  });
})();

/* ---------------------------------------------------------------- 主流程 */
function miReset() {
  miFile = null;
  miFields = null;
  const drop = $("mi_drop");
  if (drop) drop.classList.remove("has-file", "over");
  const sub = $("mi_drop_sub");
  if (sub) sub.textContent = "支持 PNG / JPEG / WebP / BMP，单张 ≤ 20 MB";
  ["mi_progress", "mi_raw", "mi_result"].forEach(id => {
    const el = $(id);
    if (el) { el.innerHTML = ""; el.style.display = "none"; }
  });
  miDirectToggle();   // 主按钮文案跟着勾选态复位
  miGateCheck();
}

function miStageAdd(text, cls) {
  const box = $("mi_progress");
  if (!box) return;
  box.style.display = "block";
  box.insertAdjacentHTML("beforeend",
    `<div class="mi-step ${cls || ""}">${cls === "run" ? '<span class="spin"></span>' : ""}${esc(text)}</div>`);
  box.scrollTop = box.scrollHeight;
}

/** 把最后一条"进行中"的步骤定态（避免残留转圈）。 */
function miStageSettle() {
  const box = $("mi_progress");
  if (!box) return;
  box.querySelectorAll(".mi-step.run").forEach(el => {
    el.classList.remove("run");
    const sp = el.querySelector(".spin");
    if (sp) sp.remove();
  });
}

function miRawAppend(text) {
  const box = $("mi_raw");
  if (!box) return;
  box.style.display = "block";
  box.textContent += text;
  box.scrollTop = box.scrollHeight;
}

/** 渲染识别结果（供用户核对；后续归因/入库由后端完成，这里只展示事实）。 */
/** 渲染识别结果——**可编辑**。
 *
 * 为什么这一步值得多一次交互：OCR 认错字是常态（纸质讲义照片尤甚），
 * 而 `confidence` / `my_reasoning` 是**不可补填**的——错字连同闸门数据一起落库后，
 * 事后要改只能删了重录，等于让用户重新回忆当时的确信程度（那份回忆已经不可靠了）。
 * 成熟客户端（Cherry Studio / LobeChat 一类）的做法都是「识别 → 校对 → 提交」。
 *
 * 科目/章节/主题的初值 = 闸门卡里填的（若有）优先，否则用识别到的；
 * 用户在**这里**的编辑才是最终值（见 `miCommit` 传空 subject/chapter/topic 的理由）。
 */
function miRenderFields(via, fields, warnings, opts) {
  const el = $("mi_result");
  if (!el) return;
  el.style.display = "block";   // ⚠️ 必须显式显示：miStart 会把它设成 none，
                                // 不恢复的话整块核对表单**看不见**（浏览器用例实测踩到）
  const f = fields || {};
  // 只读态：一步到位模式（`intake/image`）**已经落库**，再给一个「确认入库」按钮
  // 只会诱导重复提交（同一道题建两张卡）。故只展示"我读到了什么"，不给提交入口。
  const ro = !!(opts && opts.readonly);
  const R = ro ? " readonly" : "";
  const viaLabel = via === "vision" ? "原生视觉模型"
    : (via === "ocr" ? "OCR（MinerU）" : "手工填写");
  const uncertain = new Set(f.uncertain || []);
  const mark = k => uncertain.has(k) ? ' <span class="mt-warn" title="模型自认没把握">待核对</span>' : "";
  const warn = (warnings || []).map(w => `<div class="mt-warn">· ${esc(w)}</div>`).join("");
  const gateVal = id => ($(id) ? $(id).value.trim() : "");
  const pick = (gateId, fallback) => gateVal(gateId) || String(fallback || "");

  el.innerHTML = `
    <div class="mi-card">
      <div class="hint" style="margin-bottom:6px">识别方式：<b>${esc(viaLabel)}</b>
        ${f.legible === false ? ' · <span class="mt-warn">图片可能不清晰</span>' : ""}
        · ${ro ? "<b>已直接入库</b>（这是我从图里读到的内容）"
               : "<b>下面是识别结果，可直接改；确认无误再入库</b>"}</div>
      <div class="mi-field"><b>题干</b>${mark("question")}
        <textarea id="mi_f_question" rows="3"${R}>${esc(f.question || "")}</textarea></div>
      <div class="mi-field"><b>选项（一行一个）</b>${mark("options")}
        <textarea id="mi_f_options" rows="4" placeholder="A. …"${R}>${esc((f.options || []).join("\n"))}</textarea></div>
      <div class="mi-field"><b>答案</b>${mark("answer")}
        <input type="text" id="mi_f_answer" value="${esc(f.answer || "")}"${R}
               placeholder="图中没印就留空——不要猜">
        <span class="hint">${f.answer_from_image
          ? "（图中标注，原样转录）" : "（图中未见答案；此处若填，视为你自己提供的答案）"}</span></div>
      <div class="mi-field"><b>你的作答</b>
        <input type="text" id="mi_f_user_answer" value="${esc(f.user_answer || "")}"${R}></div>
      <div class="mi-field"><b>解析</b>
        <textarea id="mi_f_analysis" rows="2"${R}>${esc(f.analysis || "")}</textarea></div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <div style="flex:1;min-width:100px"><label>科目</label>
          <input type="text" id="mi_f_subject" value="${esc(pick("mi_subject", f.subject))}"${R}></div>
        <div style="flex:1;min-width:100px"><label>章节</label>
          <input type="text" id="mi_f_chapter" value="${esc(pick("mi_chapter", f.chapter))}"${R}></div>
        <div style="flex:1;min-width:100px"><label>主题</label>
          <input type="text" id="mi_f_topic" value="${esc(f.topic || "")}"${R}></div>
      </div>
      ${f.notes ? `<div class="hint" style="margin-top:6px">识别备注：${esc(f.notes)}</div>` : ""}
      ${warn ? `<div class="mi-field"><b>提示</b>${warn}</div>` : ""}
      <div class="btns" style="margin-top:10px">
        ${ro
          ? '<span class="hint">已入库。若识别有误，请到下方错题本里编辑该条（或删掉重录）。</span>'
          : '<button class="act" id="mi_commit" onclick="miCommit()">② 确认入库</button>'
            + '<button class="act gray" onclick="miReset()">放弃</button>'
            + '<span class="hint" id="mi_commit_hint"></span>'}
      </div>
    </div>`;
}

/** 把编辑框里的内容读回并**合并进原识别结果**（保留 `answer_from_image` 等不可见字段）。
 *
 * 出处标记的处理：用户在答案框里**手填**了答案时，把 `answer_from_image` 置真——
 * 语义上它变成"用户提供的答案"，而《总纲》§3.2 的红线正是「正确答案必须由考生提供」，
 * 故这比"图里读到的"**更强**，不是绕过。反之用户把答案**清空**了，标记也一起清掉。
 */
function miCollectFields() {
  const out = Object.assign({}, miFields || {});
  const val = id => ($(id) ? $(id).value : "");
  out.question = val("mi_f_question").trim();
  out.options = val("mi_f_options").split("\n").map(s => s.trim()).filter(Boolean);
  out.answer = val("mi_f_answer").trim();
  out.user_answer = val("mi_f_user_answer").trim();
  out.analysis = val("mi_f_analysis").trim();
  out.subject = val("mi_f_subject").trim();
  out.chapter = val("mi_f_chapter").trim();
  out.topic = val("mi_f_topic").trim();
  if (out.answer) out.answer_from_image = true;
  else out.answer_from_image = false;
  if (!out.user_answer) out.user_answer_from_image = false;
  // 用户已核对过：原「待核对」标记不该再传下去（否则入库后仍被标为存疑）
  out.uncertain = [];
  return out;
}

/** 发一次 SSE POST 并把帧交给 `onEvent`；返回是否收到终态（`done` / `error`）。
 *
 * 抽出来是因为有 3 条路径（识别 / 确认入库 / 一步到位）要发同一个形态的请求；
 * 三份手写的 fetch+consumeSSE 必然漂移（本项目已有一处「两份编排各自漂移」的教训）。
 *
 * ⚠️ 清理用 `sseAbort`（它会**移除**停止按钮），**不能**用 `sseStopUI`
 * ——后者会再插一个新的"■ 停止生成"按钮，跑完留在页面上（本文件初版就踩了）。
 */
async function miPostSSE(url, body, anchorId, onEvent) {
  const abort = new AbortController();
  sseStopUI(anchorId);              // 先清同入口残留，再挂新的
  sseAborts.set(anchorId, abort);
  let finished = false;
  try {
    const opts = { method: "POST", body, signal: abort.signal };
    if (typeof body === "string") opts.headers = { "Content-Type": "application/json" };
    const res = await fetch(url, opts);
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text/event-stream")) {
      // 非流式响应（中间件报错 / 代理剥掉 SSE）→ 把后端说明捞出来
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || j.msg || `服务返回 ${res.status}`);
    }
    await consumeSSE(res, (ev, data) => {
      if (ev === "done" || ev === "error") finished = true;
      onEvent(ev, data);
    });
    if (!finished) miStageAdd("流式连接提前结束（未收到终态）", "warn");
  } catch (e) {
    miStageSettle();
    if (e.name === "AbortError") miStageAdd("已取消", "warn");
    else { miStageAdd("请求失败：" + (e.message || e), "bad"); toast(e.message || "请求失败", false); }
  } finally {
    sseAbort(anchorId);             // 移除 controller + 停止按钮
  }
  return finished;
}

/** 通用的 stage / delta / error 帧处理（三条路径共用）。 */
function miOnFrame(ev, data) {
  if (ev === "stage") {
    miStageSettle();
    miStageAdd(data.label || data.name || "处理中…", "run");
  } else if (ev === "delta") {
    miRawAppend(data.text || "");
  } else if (ev === "error") {
    miStageSettle();
    const att = (data.attempts || []).map(a => `${a.via}：${a.error}`).join("；");
    miStageAdd("失败：" + (data.msg || "未知错误"), "bad");
    if (att) miStageAdd("各通道结果：" + att, "bad");
    toast(data.msg || "失败", false);
  }
}

/** 入库成功的收尾（三条路径共用）。 */
function miOnDone(data) {
  miStageSettle();
  const att = data.attributed ? "已完成 AI 归因" : "未跑 AI 归因";
  miStageAdd(`已入库（${att}）`, "ok");
  (data.warnings || []).forEach(w => miStageAdd("提示：" + w, "warn"));
  toast(data.attributed ? "已入库并完成归因" : "已入库");
  const cb = $("mi_commit");
  if (cb) { cb.disabled = true; cb.textContent = "已入库"; }
  const hint = $("mi_commit_hint");
  if (hint) hint.textContent = "本条已保存，可继续录下一条";
  if (typeof loadLibrary === "function") loadLibrary();
  if (typeof invalidateLearnCache === "function") invalidateLearnCache();
  miFile = null;
  miFields = null;
}

/** 主入口：按「识别后直接入库」勾选态分流。
 *
 * - 未勾（默认）→ 两步：`image/extract/stream` 识别 → 用户核对 → `intake/stream` 入库；
 * - 已勾 → 一步：`intake/image`（识别完直接落库，适合整页清晰的截图）。
 */
async function miStart() {
  if (!miFile) { toast("请先拖入 / 选择 / 粘贴一张错题图片", false); return; }
  const conf = $("mi_conf") ? $("mi_conf").value : "";
  const reason = $("mi_reason") ? $("mi_reason").value.trim() : "";
  if (!conf || !reason) { miGateCheck(); toast("请先填「把握程度」和「当时的想法」", false); return; }
  const direct = !!($("mi_direct") && $("mi_direct").checked);
  ["mi_progress", "mi_raw", "mi_result"].forEach(id => {
    const el = $(id);
    if (el) { el.innerHTML = ""; el.style.display = "none"; }
  });
  miFields = null;
  if (direct) await miOneShot();
  else await miRecognize();
}

/** 第一步：只识别，不落库；渲染**可编辑**结果并等用户确认。 */
async function miRecognize() {
  const btn = $("mi_go");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
  const fd = new FormData();
  fd.append("file", miFile, miFile.name || "mistake.png");
  fd.append("prefer", $("mi_prefer") ? $("mi_prefer").value : "auto");
  try {
    await miPostSSE("/api/errors/image/extract/stream", fd, MI_ANCHOR_GO, (ev, data) => {
      if (ev === "fields") {
        miStageSettle();
        miStageAdd(`识别完成（${data.via === "vision" ? "原生视觉" : "OCR"}）——请核对后入库`, "ok");
        miFields = data.fields || {};
        miRenderFields(data.via, miFields, data.warnings || []);
      } else if (ev === "result") {
        // 非流式语义的终态帧：失败时展示各通道原因
        if (!data.ok) miOnFrame("error", { msg: data.error || "识别失败",
                                           attempts: data.attempts || [] });
        else {
          miStageSettle();
          miStageAdd(`识别完成（${data.via === "vision" ? "原生视觉" : "OCR"}）——请核对后入库`, "ok");
          miFields = data.fields || {};
          miRenderFields(data.via, miFields, data.warnings || []);
        }
      } else {
        miOnFrame(ev, data);
      }
    });
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
    miGateCheck();
  }
}

/** 第二步：把**用户核对过的字段**入库（`intake/stream`）。
 *
 * `subject` / `chapter` / `topic` 刻意传空串：它们已经在编辑框里被用户改过了
 * （`miCollectFields` 读回来的就是最终值），再传一次会让"闸门卡里的旧值"覆盖用户的新编辑。
 */
async function miCommit() {
  if (!miFields) { toast("请先识别", false); return; }
  const conf = $("mi_conf") ? $("mi_conf").value : "";
  const reason = $("mi_reason") ? $("mi_reason").value.trim() : "";
  if (!conf || !reason) { miGateCheck(); toast("请先填「把握程度」和「当时的想法」", false); return; }
  const fields = miCollectFields();
  if (!fields.question) { toast("题干不能为空——请补全后再入库", false); return; }

  const btn = $("mi_commit");
  if (btn) { btn.disabled = true; btn.textContent = "入库中…"; }
  try {
    await miPostSSE("/api/errors/intake/stream", JSON.stringify({
      fields,
      confidence: conf,
      my_reasoning: reason,
      subject: "", chapter: "", topic: "",
      attribute: !!($("mi_attr") && $("mi_attr").checked),
    }), MI_ANCHOR_COMMIT, (ev, data) => {
      if (ev === "done") miOnDone(data);
      else miOnFrame(ev, data);
    });
  } finally {
    const cb = $("mi_commit");
    if (cb && !cb.disabled) { cb.disabled = false; cb.textContent = "② 确认入库"; }
    miGateCheck();
  }
}

/** 一步到位：`intake/image`（识别完直接落库）。 */
async function miOneShot() {
  const btn = $("mi_go");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
  const fd = new FormData();
  fd.append("file", miFile, miFile.name || "mistake.png");
  fd.append("prefer", $("mi_prefer") ? $("mi_prefer").value : "auto");
  fd.append("confidence", $("mi_conf") ? $("mi_conf").value : "");
  fd.append("my_reasoning", $("mi_reason") ? $("mi_reason").value.trim() : "");
  fd.append("subject", $("mi_subject") ? $("mi_subject").value.trim() : "");
  fd.append("chapter", $("mi_chapter") ? $("mi_chapter").value.trim() : "");
  fd.append("attribute", ($("mi_attr") && $("mi_attr").checked) ? "1" : "0");
  try {
    await miPostSSE("/api/errors/intake/image", fd, MI_ANCHOR_GO, (ev, data) => {
      if (ev === "fields") {
        miStageSettle();
        miStageAdd(`识别完成（${data.via === "vision" ? "原生视觉" : "OCR"}）`, "ok");
        miRenderFields(data.via, data.fields, data.warnings || [], { readonly: true });
      } else if (ev === "done") {
        miOnDone(data);
      } else {
        miOnFrame(ev, data);
      }
    });
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
    miGateCheck();
  }
}

/* 进入「错题本」子视图时自动刷新能力横幅。
 *
 * 用包装而不是改 learn.js：本片是 learn 族的**最后一篇**，learn.js 已定义
 * `showLearnView`，故此处的引用不构成前向引用（V-15 闸门会校验这一点）。
 * `typeof` 守卫保证即使本片被单独加载（老 HTML）也不会报错。
 */
(function miHookLearnView() {
  if (typeof showLearnView !== "function") return;
  const prev = showLearnView;
  showLearnView = function (name) {
    prev(name);
    if (name === "mistakes") miLoadCapability();
  };
})();

/* 加载期补一次：`learn.js` 的 `initLearnView()` 是在**它自己加载时**就调
 * `showLearnView(记住的视图)` 的——那一刻本片还没加载、包装还没装上。
 * 所以「上次停在错题本 → 刷新页面」这条路径不会经过包装，
 * 能力横幅会永远停在"正在检测当前模型是否支持图像输入…"。
 * 这里按「视图是否已 show」补一次（子视图的 .show 与 tab 无关，故判它即可）。
 */
(function miInitIfViewAlreadyShown() {
  const run = () => {
    const view = document.getElementById("lv-mistakes");
    if (view && view.classList.contains("show")) miLoadCapability();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();

