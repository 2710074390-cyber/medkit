/* exported paletteClose, paletteFilter, paletteKey, paletteOpen, paletteRun */
/* ---- Ctrl/⌘+K 命令面板（v0.19 S4） ----
 *
 * 为什么要它：页签只有 5 个，但"动作"（导入题库 / 新建错题 / 错题重练 / 切主题…）
 * 散落在各页里——老手靠肌肉记忆，新手得逐页翻。命令面板把它们并成一张可搜的表。
 * 它同时是**可见导航的兜底**：侧栏左下角有一个按钮能打开它（`#btn_palette`），
 * 不必先知道快捷键；首次打开时面板本身就把键位写在底部。
 *
 * ⚠️ 加载顺序契约（经典脚本）：本片在 app.js 之后、最后加载。
 *    - 加载期只做两件事：注册 keydown / oninput 监听、给遮罩绑点击；
 *    - 命令的 `run` 里调用的 showTab / bankImportPick / vaultNewToggle 等**一律点时才解析**，
 *      因此不构成跨片加载期前向引用。
 */

/* 命令表：label（主文案）/ hint（副文案）/ keys（搜索别名词）/ on（可用性，缺省恒可用）/ run */
const PALETTE_CMDS = [
  { label: "今天", hint: "今日待办 · 薄弱点", keys: "start today 概览 仪表盘 首页",
    run: () => showTab("start") },
  { label: "刷题", hint: "导入题库 · 开始练习", keys: "study drill 练习 做题",
    run: () => showTab("study") },
  { label: "错题", hint: "错题助手 · 按到期复习", keys: "learn mistakes 错题本 复习",
    run: () => showTab("learn") },
  { label: "题库（出题工坊）", hint: "用教材生成全新题目", keys: "bank 生成 工坊 出题",
    on: () => genEnabled(), run: () => showTab("bank") },
  { label: "设置", hint: "外观 · 连接 · 数据", keys: "mine settings 设置 配置 我的",
    run: () => showTab("mine") },
  { label: "导入题库文件", hint: "md / txt / json / csv，本地解析", keys: "import 导入 题库",
    run: () => { showTab("study"); if (typeof bankImportPick === "function") bankImportPick(); } },
  { label: "新建错题", hint: "手动记一条错题", keys: "new mistake 新建 错题",
    run: () => { showTab("learn"); vaultNewToggle(); } },
  { label: "错题重练", hint: "把错题重新刷一遍", keys: "retry 重练 重做",
    run: () => { showTab("study"); if (typeof bankRetryStart === "function") bankRetryStart(); } },
  { label: "生成复习手册", hint: "按科目汇总你的错题", keys: "handbook 手册 复习",
    run: () => { showTab("learn"); vaultHandbook(); } },
  { label: "一键刷薄弱组卷", hint: "按薄弱点自动配题", keys: "gap 薄弱 组卷",
    on: () => genEnabled(), run: () => { showTab("start"); gapPaper(); } },
  { label: "切换亮 / 暗主题", hint: "立即切换并记住", keys: "theme dark light 主题 亮 暗",
    run: () => toggleTheme() },
  { label: "主题跟随系统", hint: "不再固定亮或暗", keys: "theme system 跟随 系统",
    run: () => themeFollowSystem() },
  { label: "一键备份数据", hint: "打包错题与项目到备份目录", keys: "backup 备份",
    run: () => { showTab("mine"); dmBackup(); } },
  { label: "检查更新", hint: "查最新版本", keys: "update 更新 版本",
    run: () => checkUpdate() },
];

let _paletteShown = [];   // 当前过滤后可见的命令（索引与 DOM 的 data-i 一一对应）
let _paletteIdx = 0;      // 键盘高亮项

function paletteCommands() {
  // `on` 是"这条命令此刻是否可用"的判据（如题库相关命令只在出题工坊开启时出现）
  return PALETTE_CMDS.filter(c => !c.on || c.on());
}

function paletteRender() {
  const box = $("palette_list");
  if (!box) return;
  box.innerHTML = _paletteShown.length
    ? _paletteShown.map((c, i) => `<button class="palette-item${i === _paletteIdx ? " on" : ""}"
        id="palette_opt_${i}" role="option" aria-selected="${i === _paletteIdx}"
        data-i="${i}" onclick="paletteRun(this)"><span>${esc(c.label)}</span>
        <span class="hint">${esc(c.hint)}</span><span class="pk">Enter</span></button>`).join("")
    : `<div class="palette-empty">没有匹配的命令——换个词试试。</div>`;
  /* 焦点始终在输入框上（面板是"搜索 + 回车"形态），因此高亮项要靠
     aria-activedescendant 告知读屏"当前选中的是哪一条"，否则 ↑↓ 完全静默。 */
  const q = $("palette_q");
  if (q) {
    if (_paletteShown.length) q.setAttribute("aria-activedescendant", "palette_opt_" + _paletteIdx);
    else q.removeAttribute("aria-activedescendant");
  }
  const on = box.querySelector(".palette-item.on");
  if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
}

function paletteFilter(q) {
  const k = String(q || "").trim().toLowerCase();
  _paletteShown = paletteCommands().filter(c =>
    !k || (c.label + " " + c.hint + " " + c.keys).toLowerCase().includes(k));
  _paletteIdx = 0;
  paletteRender();
}

/* 打开前记住触发元素：关闭后焦点要回到侧栏那颗按钮（否则落到 <body>）。 */
let _paletteReturnFocus = null;
function paletteOpen() {
  const mask = $("palette_mask");
  if (!mask) return;
  if (mask.style.display !== "flex") _paletteReturnFocus = document.activeElement;
  mask.style.display = "flex";
  setBackgroundInert(true);
  const q = $("palette_q");
  if (q) { q.value = ""; q.setAttribute("aria-expanded", "true"); q.focus(); }
  paletteFilter("");
}

function paletteClose() {
  const mask = $("palette_mask");
  if (!mask || mask.style.display === "none") return;
  mask.style.display = "none";
  if (!anyOverlayOpen()) setBackgroundInert(false);
  const q = $("palette_q");
  if (q) q.setAttribute("aria-expanded", "false");
  const back = _paletteReturnFocus; _paletteReturnFocus = null;
  if (back && document.contains(back) && typeof back.focus === "function") {
    try { back.focus(); } catch (e) { /* ignore */ }
  }
}

/* 执行第 i 条命令（`el` 为被点中的按钮；键盘 Enter 时省略，用当前高亮项）。 */
function paletteRun(el) {
  const i = el && el.dataset ? Number(el.dataset.i) : _paletteIdx;
  const cmd = _paletteShown[i];
  if (!cmd) return;
  paletteClose();   // 先收面板：命令自己会切页，面板留着会挡住首屏
  try { cmd.run(); } catch (e) { toast("命令执行失败：" + ((e && e.message) || e), false); }
}

/* 面板内的键盘操作：↑↓ 移动、Enter 执行、Esc 关闭、Tab 不逃出面板 */
function paletteKey(ev) {
  const n = _paletteShown.length;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    if (!n) return;
    ev.preventDefault();
    _paletteIdx = (_paletteIdx + (ev.key === "ArrowDown" ? 1 : -1) + n) % n;
    paletteRender();
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    paletteRun(null);
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    paletteClose();
  } else if (ev.key === "Tab") {
    ev.preventDefault();   // 模态面板：Tab 不该把焦点送到背后的页面
  }
}

/* 全局唤起：Ctrl/⌘+K 切换开关（在输入框里也生效——"随时唤起"是这个入口的承诺）；
   Esc 关闭（与 app.js 的模态遮罩各管各的，互不干扰）。 */
window.addEventListener("keydown", e => {
  const mask = $("palette_mask");
  if (!mask) return;
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    if (mask.style.display === "flex") paletteClose(); else paletteOpen();
  } else if (e.key === "Escape" && mask.style.display === "flex") {
    e.preventDefault();
    paletteClose();
  }
});
/* 点遮罩空白处关闭（点面板内部不关） */
(function () {
  const mask = $("palette_mask");
  if (mask) mask.onclick = e => { if (e.target === e.currentTarget) paletteClose(); };
})();
