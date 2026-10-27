/* exported _openModal, anyOverlayOpen, askModal, closeModal, confirmModal, focusableIn,
   modalOnCancel, setBackgroundInert, trapTabKey */
/* ---- 模态与焦点管理（外壳层，2026-10-21 从 app.js 抽出） ----
 *
 * 为什么单独一片：全站三个浮层（通用模态 #modal_mask、命令面板 #palette_mask、
 * 沉浸式作答 #bank_drill_card）都声明了 aria-modal="true"，而这套能力此前一处都没有实现。
 * 把它收成一个可复用组件，三个浮层共用同一份 Tab 陷阱 / 背景 inert / 焦点归还，
 * 也避免再往 app.js 里堆（单文件 ≤800 行的判据见 tests/test_v15_frontend_split.py）。
 *
 * 依赖 app.js 的 $ / esc（本片在其后加载）；下游按**运行时**调用，无加载期前向引用。
 */
/* ---- 模态框共用能力：可聚焦清单 / Tab 陷阱 / 背景 inert / 焦点归还 ----
 *
 * 为什么集中在一处：全站有三个浮层（通用模态 #modal_mask、命令面板 #palette_mask、
 * 沉浸式作答 #bank_drill_card），它们都声明了 aria-modal="true"。而 aria-modal 是一个
 * **承诺**——"模态之外不可交互"。此前三处都没有实现，Tab 会直接跑到背后的页签与表单，
 * 读屏用户无法判断自己在浮层里还是在页面上（WCAG 2.4.3 / 4.1.2，A 级）。
 */
const _FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),'
  + 'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function focusableIn(root) {
  if (!root) return [];
  return Array.prototype.filter.call(root.querySelectorAll(_FOCUSABLE),
    el => !el.hidden && el.getClientRects().length > 0);
}
/* Tab / Shift+Tab 在 root 内回卷；root 外的焦点一律拉回第一个。 */
function trapTabKey(ev, root) {
  if (ev.key !== "Tab" || !root) return false;
  const list = focusableIn(root);
  if (!list.length) { ev.preventDefault(); return true; }
  const first = list[0], last = list[list.length - 1];
  const cur = document.activeElement;
  const inside = root.contains(cur);
  if (ev.shiftKey && (cur === first || !inside)) { ev.preventDefault(); last.focus(); return true; }
  if (!ev.shiftKey && (cur === last || !inside)) { ev.preventDefault(); first.focus(); return true; }
  return false;
}
/* 还有别的浮层开着吗？背景 inert 必须由"是否仍有浮层"决定——
   否则「笔记弹窗盖在沉浸作答层上」时，关掉弹窗会把仍被遮住的背景提前解锁。 */
function anyOverlayOpen() {
  return ["modal_mask", "palette_mask", "bank_drill_card"].some(id => {
    const el = $(id);
    return !!el && el.style.display !== "none";
  });
}
/* 浮层打开时把背景移出无障碍树与 Tab 序（inert 不支持时静默降级为「仅陷阱」）。 */
function setBackgroundInert(on) {
  const bg = [document.querySelector("main#main"), document.querySelector("aside.side")];
  bg.forEach(el => { if (el) { try { el.inert = !!on; } catch (e) { /* 旧浏览器 */ } } });
}
/* A-新14（含 D-22）：ESC 关闭 confirmModal 必须触发 onCancel 回调（无 onCancel 时才直接关闭） */
let modalOnCancel = null;
let _modalReturnFocus = null;

/* 关闭通用模态：隐藏 → 还原背景 → **把焦点还给它打开前的元素**。
   焦点归还是关键：此前关闭后焦点落到 <body>，键盘用户要从页首重新 Tab 一遍。 */
function closeModal() {
  const mask = $("modal_mask");
  if (!mask || mask.style.display !== "flex") return;
  mask.style.display = "none";
  if (!anyOverlayOpen()) setBackgroundInert(false);
  const back = _modalReturnFocus; _modalReturnFocus = null;
  if (back && document.contains(back) && typeof back.focus === "function") {
    try { back.focus(); } catch (e) { /* 元素已不可聚焦 */ }
  }
}
function _openModal() {
  const mask = $("modal_mask");
  if (!mask) return;
  if (mask.style.display !== "flex") _modalReturnFocus = document.activeElement;
  mask.style.display = "flex";
  setBackgroundInert(true);
}
function confirmModal(title, body, okLabel, onOk, danger = true, onCancel = null) {
  $("md_title").textContent = title;
  $("md_body").innerHTML = body;
  $("md_ok").textContent = okLabel || "确认";
  $("md_ok").className = "act " + (danger ? "danger" : "");
  _openModal();
  modalOnCancel = onCancel;
  $("md_ok").onclick = () => { closeModal(); modalOnCancel = null; onOk(); };
  $("md_cancel").onclick = () => {
    closeModal();
    const cb = modalOnCancel; modalOnCancel = null;
    if (typeof cb === "function") cb();   // C13：取消后回调（如提示已创建的待运行项目）
  };
  $("md_ok").focus();
}
/* 带文本输入的对话框（替代原生 prompt，风格统一）
   `value`（可选，默认空）：**预填值**——重命名/导入这类场景应当把当前值摆出来直接改，
   而不是只给一个 placeholder 让用户重打一遍。
   ⚠️ 参数顺序是 (title, label, placeholder, onOk, value)；把回调错传到 placeholder 位会
   让 placeholder 变成函数源码、点保存时 onOk 不是函数（2026-10-21 修 bank-drill 笔记弹窗）。 */
function askModal(title, label, placeholder, onOk, value = "") {
  $("md_title").textContent = title;
  $("md_body").innerHTML = `<label style="margin:0;display:block" for="md_input">${esc(label)}</label>
    <input type="text" id="md_input" maxlength="40" placeholder="${esc(placeholder || "")}"
      value="${esc(value || "")}" style="width:100%;margin-top:var(--space-2)">`;
  $("md_ok").textContent = "保存";
  $("md_ok").className = "act";
  _openModal();
  const input = $("md_input");
  input.focus();
  const submit = () => {
    const v = input.value.trim();
    if (!v) { input.classList.add("err"); input.focus(); return; }
    closeModal();
    onOk(v);
  };
  $("md_ok").onclick = submit;
  $("md_cancel").onclick = closeModal;

  input.onkeydown = e => { if (e.key === "Enter") submit(); };
  modalOnCancel = null;   // A-新14：askModal 不携带取消回调，防止 ESC 触发上一个 confirmModal 的残留 onCancel
}
document.addEventListener("keydown", e => {
  const mask = $("modal_mask");
  const open = mask && mask.style.display === "flex";
  if (!open) return;
  if (e.key === "Escape") {
    closeModal();
    // A-新14（含 D-22）：ESC 关闭必须触发 onCancel 回调（无 onCancel 时才直接关闭）
    const cb = modalOnCancel; modalOnCancel = null;
    if (typeof cb === "function") cb();
    return;
  }
  const dialog = mask.querySelector(".modal");
  if (dialog) trapTabKey(e, dialog);
});
$("md_cancel").onclick = closeModal;
