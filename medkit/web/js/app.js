const $ = id => document.getElementById(id);
let state = { provider: "", theme: null, files: { textbook: [], teacher: [], exam: [], extra: [] },
              pres: { textbook: null, teacher: null, exam: null, extra: null, sample: false },
              current: null, cfg: null, providers: [], presets: null };
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* IMP-02：WP 级 feature flag（后端 /api/config `features` 节；缺省全开 = 现状兼容）。
   关闭后：学习中心「大纲覆盖」pill / 「⚡一键刷薄弱」/ 项目详情「图片素材」卡 / 真题考频卡 整体隐藏。 */
const FEATURES = { syllabus: true, realexams: true, gap: true, image_q: true };
function applyFeatures(cfg) {
  Object.assign(FEATURES, (cfg && cfg.features) || {});
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
function confirmModal(title, body, okLabel, onOk, danger = true) {
  $("md_title").textContent = title;
  $("md_body").innerHTML = body;
  $("md_ok").textContent = okLabel || "确认";
  $("md_ok").className = "act " + (danger ? "danger" : "");
  $("modal_mask").style.display = "flex";
  $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; onOk(); };
  $("md_cancel").onclick = () => { $("modal_mask").style.display = "none"; };
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
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    if ($("modal_mask").style.display === "flex") $("modal_mask").style.display = "none";
    else if ($("wizard_mask").style.display === "flex") wzClose();
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
  $("fb_copy").onclick = () => copyText(FEEDBACK_MAIL, $("fb_copy"));
  $("md_ok").onclick = () => {
    $("modal_mask").style.display = "none";
    const subject = `MedKit ${ver} 反馈`;
    const body = `版本：${ver || "未知"}\n系统：${navigator.platform || "未知"}\n时间：${new Date().toLocaleString("zh-CN")}\n\n（请在此描述你遇到的问题或功能建议）`;
    location.href = `mailto:${FEEDBACK_MAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
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
    html = `<p style="margin:0 0 8px">当前版本 <b>v${esc(r.current)}</b> → 最新版本 <b style="color:var(--good)">v${esc(r.latest)}</b></p>`;
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
  const url = r.html_url || "https://github.com/2710074390-cyber/medkit/releases/latest";
  $("md_ok").onclick = () => {
    $("modal_mask").style.display = "none";
    if (r.has_update || r.error) window.open(url, "_blank", "noopener");
  };
}
async function checkUpdate(silent = false) {
  if (!silent) {
    $("md_title").textContent = "检查更新";
    $("md_body").innerHTML = `<p style="margin:0;color:var(--dim)">正在检查新版本…</p>`;
    $("md_ok").textContent = "知道了";
    $("md_ok").className = "act";
    $("modal_mask").style.display = "flex";
    $("md_ok").onclick = () => { $("modal_mask").style.display = "none"; };
  }
  try {
    const r = await api("/api/update/check");
    if (r.has_update) {
      markUpdateDot(true);
      if (silent) toast(`发现新版本 v${r.latest} · 点击左下角版本号查看`);
    } else markUpdateDot(false);
    if (!silent) showUpdateModal(r);
  } catch (e) {
    if (!silent) showUpdateModal({ error: "net", current: state.version || "" });
  }
}

/* ---- 导航 + hash 路由 */
function showTab(name) {
  document.querySelectorAll("nav button").forEach(x => {
    const on = x.dataset.tab === name;
    x.classList.toggle("active", on);
    if (on) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
    x.setAttribute("aria-selected", on ? "true" : "false");   // IMP-08：主 tab 语义同步
  });
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("show"));
  $("tab-" + name).classList.add("show");
  if (name !== "proj") {           // v0.5：切走项目详情 → 停止进度轮询与 OCR 轮询
    stopPoll();
    ocrRunToken++;
  }
  if (name === "mine") loadProjects();
  if (name === "prompts") loadPrompts();
  if (name === "learn") loadLibrary();
  if (name === "proj") { ratioSum(); bloomSum(); }   // 面板从隐藏变可见 → 重测配比条标签适配
}
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  location.hash = b.dataset.tab;
  showTab(b.dataset.tab);
});
(function initTab() {
  const h = (location.hash || "").replace("#", "");
  if (["conn", "proj", "mine", "learn", "prompts"].includes(h)) showTab(h);
})();
window.addEventListener("hashchange", () => {
  const h = (location.hash || "").replace("#", "");
  if (["conn", "proj", "mine", "learn", "prompts"].includes(h)) showTab(h);
});
