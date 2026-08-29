const $ = id => document.getElementById(id);
let state = { provider: "", theme: null, files: { textbook: [], teacher: [], exam: [], extra: [] },
              pres: { textbook: null, teacher: null, exam: null, extra: null, sample: false },
              current: null, cfg: null, providers: [], presets: null };
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* IMP-02：WP 级 feature flag（后端 /api/config `features` 节；缺省全开 = 现状兼容）。
   关闭后：学习中心「大纲覆盖」pill / 「⚡一键刷薄弱」/ 项目详情「图片素材」卡 / 真题考频卡 整体隐藏。 */
const FEATURES = { syllabus: true, realexams: true, gap: true, image_q: true, cards: true };
function applyFeatures(cfg) {
  Object.assign(FEATURES, (cfg && cfg.features) || {});
  // 防御：后端未下发某键（如旧 config 无 features 节）时保持默认开，避免「缺键=关闭」的隐性门禁
  for (const k of ["syllabus", "realexams", "gap", "image_q", "cards"]) {
    if (FEATURES[k] === undefined) FEATURES[k] = true;
  }
  const pill = document.querySelector('#learnnav button[data-lv="syllabus"]');
  if (pill) pill.style.display = FEATURES.syllabus ? "" : "none";
  const rexCard = $("rex_card");
  if (rexCard) rexCard.style.display = FEATURES.realexams ? "" : "none";
  const gapBtn = $("btn_gap_paper");
  if (gapBtn) gapBtn.style.display = FEATURES.gap ? "" : "none";
  const assetBox = $("pd_assets_box");
  if (assetBox) assetBox.style.display = FEATURES.image_q ? "" : "none";
  if (!FEATURES.syllabus) {
    let v = null;
    try { v = sessionStorage.getItem("medkit-learn-view"); } catch (e) { /* ignore */ }
    if (v === "syllabus") {
      try { sessionStorage.setItem("medkit-learn-view", "overview"); } catch (e) { /* ignore */ }
      showLearnView("overview");
    }
  }
}

/* v0.5：轮询/项目状态声明提前（showTab 在初始化时即调用 stopPoll，需先于任何 IIFE 就绪） */
let currentPid = null;
let pollTimer = null;
let pollFails = 0;
let ocrRunToken = 0;   // OCR 轮询取消令牌：离开页面自增 → while 循环终止

/* v0.5：全局错误兜底（fetch 失败/脚本异常不再静默） */
window.onerror = function (msg, src, ln, col, err) {
  try { toast("脚本异常：" + ((err && err.message) || msg), false); } catch (e) { /* ignore */ }
  return false;
};
window.addEventListener("unhandledrejection", ev => {
  try {
    const m = (ev.reason && ev.reason.message) ? ev.reason.message : String(ev.reason || "未处理的异步错误");
    toast("异步错误：" + m, false);
  } catch (e) { /* ignore */ }
  ev.preventDefault();
});

function toast(msg, ok = true) {
  const box = $("toasts");
  const t = document.createElement("div");
  t.className = "toast " + (ok ? "good" : "bad");
  t.textContent = msg;
  t.title = "点击关闭";
  t.onclick = () => t.remove();
  box.appendChild(t);
  while (box.children.length > 4) box.removeChild(box.firstChild);
  setTimeout(() => { t.remove(); }, 3800);
}
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && typeof o.body === "string" && (o.method || "GET") !== "GET") {
    // 字符串体默认 JSON（FormData 原样透传，不设 header）
    o.headers = { ...(o.headers || {}), "Content-Type": "application/json" };
  }
  let r;
  try { r = await fetch(path, o); }
  catch (e) { throw new Error("无法连接本地服务，请确认 MedKit 程序仍在运行"); }
  const j = await r.json().catch(() => ({}));
  // 统一错误消费：detail（HTTPException/4 类异常 handler）|| msg（工具端点 ok:false 信封）|| error_code || statusText
  if (!r.ok) {
    const code = j.error_code ? `(${j.error_code})` : "";
    throw new Error((j.detail || j.msg || r.statusText) + code);
  }
  return j;
}
/* A-新14（含 D-22）：ESC 关闭 confirmModal 必须触发 onCancel 回调（无 onCancel 时才直接关闭） */
let modalOnCancel = null;
function confirmModal(title, body, okLabel, onOk, danger = true, onCancel = null) {
  $("md_title").textContent = title;
  $("md_body").innerHTML = body;
  $("md_ok").textContent = okLabel || "确认";
  $("md_ok").className = "act " + (danger ? "danger" : "");
  $("modal_mask").style.display = "flex";
  modalOnCancel = onCancel;
  $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; modalOnCancel = null; onOk(); };
  $("md_cancel").onclick = () => {
    $("modal_mask").style.display = "none";
    const cb = modalOnCancel; modalOnCancel = null;
    if (typeof cb === "function") cb();   // C13：取消后回调（如提示已创建的待运行项目）
  };
  $("md_ok").focus();
}
/* 带文本输入的对话框（替代原生 prompt，风格统一） */
function askModal(title, label, placeholder, onOk) {
  $("md_title").textContent = title;
  $("md_body").innerHTML = `<label style="margin:0">${esc(label)}</label>
    <input type="text" id="md_input" maxlength="40" placeholder="${esc(placeholder || "")}"
      style="width:100%;margin-top:8px">`;
  $("md_ok").textContent = "保存";
  $("md_ok").className = "act";
  $("modal_mask").style.display = "flex";
  const input = $("md_input");
  input.focus();
  const submit = () => {
    const v = input.value.trim();
    if (!v) { input.classList.add("err"); input.focus(); return; }
    $("modal_mask").style.display = "none";
    onOk(v);
  };
  $("md_ok").onclick = submit;
  input.onkeydown = e => { if (e.key === "Enter") submit(); };
  modalOnCancel = null;   // A-新14：askModal 不携带取消回调，防止 ESC 触发上一个 confirmModal 的残留 onCancel
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    if ($("modal_mask").style.display === "flex") {
      $("modal_mask").style.display = "none";
      // A-新14（含 D-22）：ESC 关闭必须触发 onCancel 回调（无 onCancel 时才直接关闭）
      const cb = modalOnCancel; modalOnCancel = null;
      if (typeof cb === "function") cb();
    } else if ($("wizard_mask").style.display === "flex") wzClose();
  }
});
$("md_cancel").onclick = () => { $("modal_mask").style.display = "none"; };

/* ---- 主题 */
function applyTheme() {
  const t = state.theme || (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = t;
  $("u_theme").setAttribute("href", t === "light" ? "#i-sun" : "#i-moon");
  $("btn_theme").setAttribute("aria-label", t === "light" ? "切换到暗色主题" : "切换到亮色主题");
}
function toggleTheme() {
  state.theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  try { localStorage.setItem("medkit-theme", state.theme); } catch (e) { /* 隐私模式等：仅本次生效 */ }
  applyTheme();
}
(function initTheme() {
  try {
    const saved = localStorage.getItem("medkit-theme");
    if (saved === "dark" || saved === "light") state.theme = saved;
  } catch (e) { /* 隐私模式：跳过偏好读取，不中断脚本 */ }
  applyTheme();
  // A9：未手动选过主题 → 跟随系统亮/暗变化（手动切换后即显式固定，不再跟随）
  try {
    const mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
    if (mq && mq.addEventListener) mq.addEventListener("change", () => { if (!state.theme) applyTheme(); });
  } catch (e) { /* 旧浏览器无 matchMedia → 保持初次结果 */ }
})();

/* ---- v0.6：反馈(邮件) + 更新检查 ---- */
const FEEDBACK_MAIL = "2710074390@qq.com";
const REVIEW_SITE = "https://med-review-site.pages.dev/#reviews";

function copyText(text, btn) {
  const done = () => { if (btn) { btn.textContent = "已复制"; setTimeout(() => { btn.textContent = "复制"; }, 1600); } };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("复制失败，请手动选择邮箱复制", false); }
  ta.remove();
}
/* A-新6：带可点击「复制」按钮的 toast（clipboard 失败 → 降级 mailto）——反馈弹窗已关闭后仍可复制邮箱 */
function toastWithCopy(msg, copyValue) {
  const box = $("toasts");
  const t = document.createElement("div");
  t.className = "toast bad";
  const span = document.createElement("span");
  span.textContent = msg;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "复制";
  btn.style.cssText = "margin-left:8px;padding:2px 10px;border:1px solid currentColor;border-radius:6px;background:none;color:inherit;font:inherit;font-size:12px;cursor:pointer";
  btn.onclick = ev => {
    ev.stopPropagation();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(copyValue)
        .then(() => { btn.textContent = "已复制"; })
        .catch(() => { location.href = "mailto:" + copyValue; toast("未获剪贴板权限，已为你打开写邮件", false); });
    } else {
      location.href = "mailto:" + copyValue;
    }
  };
  t.appendChild(span); t.appendChild(btn);
  t.title = "点击关闭";
  t.onclick = () => t.remove();
  box.appendChild(t);
  while (box.children.length > 4) box.removeChild(box.firstChild);
  setTimeout(() => { t.remove(); }, 6000);
}

function openFeedback() {
  $("md_title").textContent = "反馈与建议";
  const ver = state.version ? "v" + state.version : "";
  $("md_body").innerHTML = `
    <p style="margin:0 0 10px;color:var(--dim);font-size:13px">遇到问题或有功能建议，欢迎邮件反馈：</p>
    <div style="display:flex;align-items:center;gap:9px;background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:10px 12px">
      <svg class="ic" style="color:var(--dim)"><use href="#i-mail"></use></svg>
      <b style="font-size:14px;letter-spacing:.3px">${FEEDBACK_MAIL}</b>
      <button class="act gray" id="fb_copy" style="margin-left:auto;padding:5px 12px;font-size:12px">复制</button>
    </div>
    <p style="margin:10px 0 0;color:var(--dim);font-size:12px">点「写邮件」会自动附带版本与系统信息，方便快速定位问题。</p>`;
  $("md_ok").textContent = "写邮件";
  $("md_ok").className = "act";
  $("modal_mask").style.display = "flex";
  modalOnCancel = null;   // A-新14：普通信息弹窗不携带取消回调（防 ESC 触发残留 onCancel）
  $("fb_copy").onclick = () => copyText(FEEDBACK_MAIL, $("fb_copy"));
  $("md_ok").onclick = () => {
    $("modal_mask").style.display = "none";
    const subject = `MedKit ${ver} 反馈`;
    const body = `版本：${ver || "未知"}\n系统：${navigator.platform || "未知"}\n时间：${new Date().toLocaleString("zh-CN")}\n\n（请在此描述你遇到的问题或功能建议）`;
    location.href = `mailto:${FEEDBACK_MAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    // A11+A-新6：邮件客户端可能未安装/静默无反应——弹窗已关闭，toast 内提供可点击「复制」按钮兜底（clipboard 失败降级 mailto）
    toastWithCopy("如未弹出邮件客户端：请点「复制」把邮箱发到微信/备忘录，再手动发送", FEEDBACK_MAIL);
  };
}

/* 更新检查（GitHub Releases，仅提醒 + 跳转下载页） */
function markUpdateDot(on) {
  let d = $("upd_dot");
  if (on && !d) {
    d = document.createElement("span");
    d.id = "upd_dot"; d.className = "updot";
    d.title = "有新版本";
    $("side_ver").appendChild(d);
  } else if (!on && d) d.remove();
}
function showUpdateModal(r) {
  $("md_title").textContent = "检查更新";
  let html = "";
  if (r.has_update) {
    const pre = r.prerelease ? " <span style='color:var(--warn)'>（预览版）</span>" : "";
    html = `<p style="margin:0 0 8px">当前版本 <b>v${esc(r.current)}</b> → 最新版本 <b style="color:var(--good)">v${esc(r.latest)}</b>${pre}</p>`;
    if (r.notes) html += `<div style="max-height:180px;overflow:auto;background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:12.5px;white-space:pre-wrap;line-height:1.6">${esc(r.notes)}</div>`;
    $("md_ok").textContent = "打开下载页";
  } else if (r.error) {
    html = `<p style="margin:0;color:var(--dim)">网络检查失败（无法访问 GitHub），可稍后再试，或直接打开发布页查看。</p>`;
    $("md_ok").textContent = "打开发布页";
  } else {
    html = `<p style="margin:0;color:var(--dim)">已是最新版本 <b>v${esc(r.current)}</b></p>`;
    $("md_ok").textContent = "知道了";
  }
  $("md_body").innerHTML = html;
  $("md_ok").className = "act";
  $("modal_mask").style.display = "flex";
  modalOnCancel = null;   // A-新14：普通信息弹窗不携带取消回调（防 ESC 触发残留 onCancel）
  const url = r.html_url || "https://github.com/2710074390-cyber/medkit/releases/latest";
  $("md_ok").onclick = () => {
    $("modal_mask").style.display = "none";
    if ((r.has_update || r.error) && openExternal(url) === false) {
      toast("当前处于离线状态，无法打开外链（联网后重试）", false);
    }
  };
}
/* A12：统一外链守卫——离线时拦截并提示（浏览器在线状态为轻量预估，仅供提示）
   返回 true=已打开（或无需拦截），false=离线被拦截 */
function openExternal(url) {
  if (navigator.onLine === false) return false;
  window.open(url, "_blank", "noopener");
  return true;
}
document.addEventListener("click", e => {
  const a = e.target && e.target.closest ? e.target.closest("a[target='_blank'][href^='http']") : null;
  if (!a) return;
  if (navigator.onLine === false) {
    e.preventDefault();
    toast("当前处于离线状态，无法打开外链（联网后重试）", false);
  }
});
async function checkUpdate(silent = false) {
  if (!silent) {
    $("md_title").textContent = "检查更新";
    $("md_body").innerHTML = `<p style="margin:0;color:var(--dim)">正在检查新版本…</p>`;
    $("md_ok").textContent = "知道了";
    $("md_ok").className = "act";
    $("modal_mask").style.display = "flex";
    modalOnCancel = null;   // A-新14：普通信息弹窗不携带取消回调（防 ESC 触发残留 onCancel）
    $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; };
  }
  try {
    const r = await api("/api/update/check");
    if (r.has_update) {
      markUpdateDot(true);
      if (silent) toast(`发现新版本 v${r.latest}${r.prerelease ? "（预览版）" : ""} · 点击左下角版本号查看`);
    } else markUpdateDot(false);
    if (!silent) showUpdateModal(r);
  } catch (e) {
    if (!silent) showUpdateModal({ error: "net", current: state.version || "" });
  }
}

/* ---- 导航 + hash 路由 */
let shownTab = null;   // A-新7：记录当前已展示 tab——hashchange 与 showTab 双入口去重（防双倍请求）
function showTab(name) {
  if (typeof window.reviewDirtyGuard === "function" && !window.reviewDirtyGuard()) {
    // 审核台有未保存修改：回滚 hash 到当前 tab，不切换
    const cur = (location.hash || "").replace("#", "");
    if (cur && cur !== name) history.replaceState(null, "", "#" + cur);
    return;
  }
  /* 只作用于主导航（data-tab）：学习中心子导航 #learnnav 也是 <nav>，
     若用裸 "nav button" 会误重置子导航的 aria-selected/active 状态。 */
  document.querySelectorAll("nav button[data-tab]").forEach(x => {
    const on = x.dataset.tab === name;
    x.classList.toggle("active", on);
    if (on) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
    x.setAttribute("aria-selected", on ? "true" : "false");   // IMP-08：主 tab 语义同步
  });
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("show"));
  $("tab-" + name).classList.add("show");
  shownTab = name;   // A-新7：hashchange 目标与当前一致时跳过（见下方 hashchange 监听）
  if (name !== "bank") {            // v0.5：切走题库（项目详情） → 停止进度轮询与 OCR 轮询
    stopPoll();
    ocrRunToken++;
  }
  /* v0.8.1：5 Tab 分发（开始/刷题/题库/学习中心/我的） */
  if (name === "start") loadStart();
  if (name === "study") loadStudy();
  if (name === "bank") { loadProjects(); ratioSum(); bloomSum(); if (typeof updateReady === "function") updateReady(); }   // R3-10：切回建课页重拉成本预估（服务商/模型可能已变）
  if (name === "learn") loadLibrary();
  if (name === "mine") loadPrompts();
}
document.querySelectorAll("nav button[data-tab]").forEach(b => b.onclick = () => {
  location.hash = b.dataset.tab;
  showTab(b.dataset.tab);
});
/* H-1：hash 直达（如 #learn/#mine）时 showTab 会调用 stopPoll/loadProjects/loadLibrary，
   这些函数定义在 app.js 之后加载的 review-desk.js / learn.js 中。初始化须推迟到全部
   脚本执行完毕（DOMContentLoaded），否则 ReferenceError → tab 面板已切但内容空屏。 */
function initTab() {
  const h = (location.hash || "").replace("#", "");
  if (["start", "study", "bank", "learn", "mine"].includes(h)) showTab(h);
  else loadStart();   // 无 hash → 默认落地「开始」仪表盘（此时各脚本已就绪）
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initTab);
} else {
  initTab();
}
window.addEventListener("hashchange", () => {
  const h = (location.hash || "").replace("#", "");
  // A-新7：hash=…; showTab(…) 双入口会让 hashchange 再触发一次 → 目标与当前已展示 tab 一致时跳过（防双倍请求）
  if (["start", "study", "bank", "learn", "mine"].includes(h) && h !== shownTab) showTab(h);
});

/* ---- ① 开始（仪表盘 · PRD 6.1）：今日任务 + 开始学习 + 考试倒计时 + 最近项目 ---- */
async function loadStart() {
  const box = $("start_body");
  if (!box) return;
  try {
    const [d, projs] = await Promise.all([
      api("/api/library/dashboard"),
      api("/api/projects").catch(() => ({ projects: [] })),
    ]);
    const rv = d.review || {};
    const my = d.mastery || {};
    const recent = (projs.projects || []).slice(0, 3);
    box.innerHTML = `
      <div class="start-stats">
        <div class="start-stat"><b>${rv.due || 0}</b><span>今日待复习</span></div>
        <div class="start-stat"><b>${rv.new || 0}</b><span>新卡待学</span></div>
        <div class="start-stat"><b>${rv.done || 0}</b><span>已完成（总卡 ${rv.total || 0}）</span></div>
        <div class="start-stat"><b>${my.mastered_rate || 0}%</b><span>掌握率（${my.total_knowledge || 0} 知识点）</span></div>
      </div>
      <button class="big-start" onclick="showTab('study')">开始学习 →</button>
      <p class="hint" style="text-align:center;margin:8px 0 4px">先清掉今日到期，再去「题库」生成新题</p>
      <div class="card" style="margin-top:14px">
        <div class="cardh"><h2>考试倒计时</h2><span class="hint">可设期末 / 考研日期</span></div>
        <div id="exam_box"></div>
      </div>
      <div class="card" style="margin-top:14px">
        <div class="cardh"><h2>最近项目</h2><span class="hint">点击进入项目详情</span></div>
        ${recent.length ? recent.map(p => `
          <button class="start-proj" onclick="openRecentProject('${esc(p.pid)}')">
            <span class="proj-stage">${esc(p.stage_label)}</span>
            <span class="proj-name">${esc(p.subject || "未命名课题")} · ${esc(p.exam || "未设考试")}${p.target ? " · " + p.target + " 题" : ""}</span>
            ${p.running ? '<span class="spin"></span>' : ""}
          </button>`).join("")
        : `<div class="hint">还没有课题——去「题库」上传教材，AI 帮你生成题库</div>`}
      </div>`;
    renderExamCountdown();
    /* 侧栏待办徽章：今日到期复习 → 刷题；进行中提问 → 学习中心（learn.js 实现） */
    if (typeof setLearnNavBadge === "function") {
      setLearnNavBadge((rv.due || 0) + ((d.tutor && d.tutor.in_progress) || 0),
                       { due: rv.due || 0, tutor: (d.tutor && d.tutor.in_progress) || 0 });
    }
  } catch (e) {
    box.innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}
function renderExamCountdown() {
  const box = $("exam_box");
  if (!box) return;
  let d = "";
  try { d = localStorage.getItem("medkit-exam-date") || ""; } catch (e) { /* ignore */ }
  const input = `<input type="date" id="exam_date_input" value="${d}" onchange="setExamDate(this.value)" title="设置考试日期">`;
  if (!d) {
    box.innerHTML = `<div class="row" style="align-items:center;flex-wrap:wrap;gap:8px">
      <span class="hint">还没有设置考试日期：</span>${input}</div>`;
    return;
  }
  const target = new Date(d + "T23:59:59");
  const diff = Math.ceil((target - new Date()) / 86400000);
  const txt = diff >= 0 ? `距考试还有 <b>${diff}</b> 天` : `考试已过 <b>${-diff}</b> 天`;
  box.innerHTML = `<div class="row" style="align-items:center;flex-wrap:wrap;gap:8px">
    <span style="font-size:14px">${txt}</span>${input}
    <span class="hint">考前 3 天起提示加大复习强度</span></div>`;
}
function setExamDate(v) {
  try { localStorage.setItem("medkit-exam-date", v || ""); } catch (e) { /* ignore */ }
  renderExamCountdown();
  toast(v ? "已设置考试日期（首页倒计时生效）" : "已清除考试日期");
}
window.setExamDate = setExamDate;
function openRecentProject(pid) { showTab("bank"); showProject(pid); }
window.openRecentProject = openRecentProject;
