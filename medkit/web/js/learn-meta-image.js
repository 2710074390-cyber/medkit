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
   miRenderFields, miFocus */

// 当前识别能力（`/api/errors/image/capability` 的缓存；进视图时刷新）
let miCap = null;
// 待识别的图片文件（拖拽/粘贴/选择三处入口都写到这里）
let miFile = null;

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
  const drop = $("mi_drop");
  if (drop) drop.classList.remove("has-file", "over");
  const sub = $("mi_drop_sub");
  if (sub) sub.textContent = "支持 PNG / JPEG / WebP / BMP，单张 ≤ 20 MB";
  ["mi_progress", "mi_raw", "mi_result"].forEach(id => {
    const el = $(id);
    if (el) { el.innerHTML = ""; el.style.display = "none"; }
  });
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
function miRenderFields(via, fields, warnings) {
  const el = $("mi_result");
  if (!el) return;
  const f = fields || {};
  const viaLabel = via === "vision" ? "原生视觉模型" : "OCR（MinerU）";
  const uncertain = new Set(f.uncertain || []);
  const mark = k => uncertain.has(k) ? ' <span class="mt-warn" title="模型自认没把握">待核对</span>' : "";
  const opts = (f.options || []).map(o => `<div class="mi-opt">${esc(o)}</div>`).join("");
  const warn = (warnings || []).map(w => `<div class="mt-warn">· ${esc(w)}</div>`).join("");

  el.innerHTML = `
    <div class="mi-card">
      <div class="hint" style="margin-bottom:6px">识别方式：<b>${esc(viaLabel)}</b>
        ${f.legible === false ? ' · <span class="mt-warn">图片可能不清晰</span>' : ""}</div>
      <div class="mi-field"><b>题干</b>${mark("question")}<div class="mi-q">${esc(f.question || "（未识别出题干）")}</div></div>
      ${opts ? `<div class="mi-field"><b>选项</b>${mark("options")}<div class="mi-opts">${opts}</div></div>` : ""}
      <div class="mi-field"><b>答案（图中标注）</b>${mark("answer")}
        <div>${f.answer ? esc(f.answer) : '<span class="hint">图中未印答案——已留空，请你自行填写</span>'}</div></div>
      ${f.user_answer ? `<div class="mi-field"><b>你的作答</b><div>${esc(f.user_answer)}</div></div>` : ""}
      ${f.analysis ? `<div class="mi-field"><b>解析（图中原文）</b><div class="hint">${esc(f.analysis)}</div></div>` : ""}
      <div class="mi-field"><b>科目 / 章节</b>
        <div>${esc(f.subject || "—")} / ${esc(f.chapter || "—")}${f.topic ? " / " + esc(f.topic) : ""}</div></div>
      ${warn ? `<div class="mi-field"><b>提示</b>${warn}</div>` : ""}
    </div>`;
}

async function miStart() {
  if (!miFile) { toast("请先拖入 / 选择 / 粘贴一张错题图片", false); return; }
  const conf = $("mi_conf") ? $("mi_conf").value : "";
  const reason = $("mi_reason") ? $("mi_reason").value.trim() : "";
  if (!conf || !reason) { miGateCheck(); toast("请先填「把握程度」和「当时的想法」", false); return; }

  const btn = $("mi_go");
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
  ["mi_progress", "mi_raw", "mi_result"].forEach(id => {
    const el = $(id);
    if (el) { el.innerHTML = ""; el.style.display = "none"; }
  });

  const fd = new FormData();
  fd.append("file", miFile, miFile.name || "mistake.png");
  fd.append("prefer", $("mi_prefer") ? $("mi_prefer").value : "auto");
  fd.append("confidence", conf);
  fd.append("my_reasoning", reason);
  fd.append("subject", $("mi_subject") ? $("mi_subject").value.trim() : "");
  fd.append("chapter", $("mi_chapter") ? $("mi_chapter").value.trim() : "");
  fd.append("attribute", ($("mi_attr") && $("mi_attr").checked) ? "1" : "0");

  // R4-02 同款：AbortController + 切视图即中断（sseAbortAll 会 abort 本入口）
  const abort = new AbortController();
  sseStopUI("mi_go");
  sseAborts.set("mi_go", abort);

  let finished = false;
  try {
    const res = await fetch("/api/errors/intake/image", { method: "POST", body: fd, signal: abort.signal });
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text/event-stream")) {
      // 非流式响应（旧代理剥掉 SSE / 中间件报错）→ 尽量把后端的错误说明捞出来
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || j.msg || `服务返回 ${res.status}`);
    }
    await consumeSSE(res, (ev, data) => {
      if (ev === "stage") {
        miStageSettle();
        miStageAdd(data.label || data.name || "处理中…", "run");
      } else if (ev === "delta") {
        miRawAppend(data.text || "");
      } else if (ev === "fields") {
        miStageSettle();
        miStageAdd(`识别完成（${data.via === "vision" ? "原生视觉" : "OCR"}）`, "ok");
        miRenderFields(data.via, data.fields, data.warnings);
      } else if (ev === "done") {
        finished = true;
        miStageSettle();
        const att = data.attributed ? "已完成 AI 归因" : "未跑 AI 归因";
        miStageAdd(`已入库（${att}）`, "ok");
        (data.warnings || []).forEach(w => miStageAdd("提示：" + w, "warn"));
        toast(data.attributed ? "已识别并完成归因" : "已识别并入库");
        // 刷新错题本与元认知看板（数据变了）
        if (typeof loadLibrary === "function") loadLibrary();
        if (typeof invalidateLearnCache === "function") invalidateLearnCache();
        miFile = null;
      } else if (ev === "error") {
        finished = true;
        miStageSettle();
        const att = (data.attempts || []).map(a => `${a.via}：${a.error}`).join("；");
        miStageAdd("失败：" + (data.msg || "未知错误"), "bad");
        if (att) miStageAdd("各通道结果：" + att, "bad");
        toast(data.msg || "识别失败", false);
      }
    });
    if (!finished) miStageAdd("流式连接提前结束（未收到终态）", "warn");
  } catch (e) {
    miStageSettle();
    if (e.name === "AbortError") miStageAdd("已取消", "warn");
    else { miStageAdd("请求失败：" + (e.message || e), "bad"); toast(e.message || "请求失败", false); }
  } finally {
    sseAborts.delete("mi_go");
    sseStopUI("mi_go");
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

