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
  { label: "题库（出题工坊）", hint: "用教材出新题", keys: "bank 生成 工坊 出题",
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
  { label: "按薄弱点组一套卷", hint: "按薄弱点自动配题", keys: "gap 薄弱 组卷",
    on: () => genEnabled(), run: () => { showTab("start"); gapPaper(); } },
  { label: "切换亮 / 暗主题", hint: "立即切换并记住", keys: "theme dark light 主题 亮 暗",
    run: () => toggleTheme() },
  { label: "主题跟随系统", hint: "不再固定亮或暗", keys: "theme system 跟随 系统",
    run: () => themeFollowSystem() },
  { label: "备份数据", hint: "打包错题与项目到备份目录", keys: "backup 备份",
    run: () => { showTab("mine"); dmBackup(); } },
  { label: "检查更新", hint: "查最新版本", keys: "update 更新 版本",
    run: () => checkUpdate() },
];

let _paletteShown = [];   // 当前可见项（索引与 DOM 的 data-i 一一对应）：{kind:"cmd",cmd} / {kind:"item",item}
let _paletteIdx = 0;      // 键盘高亮项

/* ---- 内容检索（Ctrl/⌘+K 的第二段结果，v0.22.1 / 2026-10-28 扩到三段）
 *
 * 面板此前只能搜「动作」，而用户真正想搜的常常是**内容本身**：一道题、一条错题、
 * 一个考点。现在命令在前（常用动作，路径短）、内容在后（按域分三段，命中了才往下看）。
 *
 * ⚠️ 三条约束：
 *   1. **输入即搜索会打后端** ⇒ 140ms 防抖 + 序号丢弃过期响应。连打「慢阻肺」四个字
 *      会发四次请求，只有最后一次的结果能落地（否则慢响应后到会覆盖新结果）。
 *   2. **搜索失败不能让面板变哑**：命令照常可用，只是少了内容段。
 *   3. **空输入不发请求**：没输入时用户要看的是命令，不是千题。
 *
 * 三段由**一个**请求取回（`/api/palette/search`），不在前端连发三个：截断口径
 * （每段各取前 N 条）与"一段坏了不拖垮其余"都放在服务端，前端只管展示。
 */
let _paletteGroups = [];  // 内容段：[{kind:"bank"|"mistake"|"kc", label, total, items}]
let _paletteSeq = 0;      // 请求序号（丢弃过期响应）
let _paletteTimer = 0;    // 防抖计时器

function _paletteMerge() {
  const items = [];
  _paletteGroups.forEach(g => (g.items || []).forEach(it => items.push({ kind: "item", group: g.kind, item: it })));
  _paletteShown = _paletteShown.filter(e => e.kind === "cmd").concat(items);
}

function _paletteSearch(k) {
  clearTimeout(_paletteTimer);
  const seq = ++_paletteSeq;
  if (!k) { _paletteGroups = []; return; }
  _paletteTimer = setTimeout(() => {
    api(`/api/palette/search?q=${encodeURIComponent(k)}&limit=6`).then(r => {
      if (seq !== _paletteSeq) return;   // 过期响应：用户已经又敲了字
      _paletteGroups = r.groups || [];
      _paletteMerge();
      paletteRender();
    }).catch(() => { /* 内容检索失败不该让命令面板变成空的 */ });
  }, 140);
}

/* 题目行的文案：题干压成一行（长题干在面板里换行会把列表撑高，反而更难扫） */
function _paletteClip(s, n) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

/* 点中一条内容：题/错题弹只读预览；考点则**留在面板里**用它再搜一次。
   为什么不直接跳「题目管理」：搜题多半是"确认一下这题怎么写的"，
   跳页会丢掉搜索现场，用户还得再搜一次。
   考点则不同——用户点它是想看"这个考点有哪些题"，所以把查询换成考点名再搜一轮
   （面板不关，看的还是同一处）。 */
function _paletteItemOpen(it) {
  const L = "ABCDEFGHIJ";
  const opts = (it.options || []).length
    ? `<div class="opts">${it.options.map((o, i) => `${L[i] || i + 1}. ${esc(o)}`).join("<br>")}</div>`
    : "";
  const ans = it.answer ? `<div class="ans">答案：${esc(it.answer)}</div>` : "";
  const block = (label, text) => (text
    ? `<div class="hint" style="margin-top:var(--space-2)"><b>${label}</b><br>${esc(text)}</div>` : "");
  if (it.kind === "mistake") {
    infoModal(it.subtopic || "错题",
      `<div class="trialq"><div class="src">${esc(it.sub || "错题本")}</div>
        <div class="qtext">${esc(it.title)}</div>${opts}${ans}</div>`
      + block("解析", it.analysis) + block("我的理解", it.understanding)
      + block("我的疑问", it.doubts)
      + `<div class="hint" style="margin-top:var(--space-2)">去「错题」页可以复习它、补写理解和疑问。</div>`);
    return;
  }
  infoModal(it.subtopic || "题目详情",
    `<div class="trialq"><div class="src">${esc(it.sub)}</div>
      <div class="qtext">${esc(it.title)}</div>${opts}${ans}</div>${block("解析", it.analysis)}`);
}

/* 考点 → 用考点名再搜一轮题目（面板保持打开：用户的意图是"看这个考点的题"） */
function _paletteKcDrill(it) {
  const q = $("palette_q");
  if (!q) return;
  q.value = it.title;
  paletteFilter(it.title);
  q.focus();
}

function paletteCommands() {
  // `on` 是"这条命令此刻是否可用"的判据（如题库相关命令只在出题工坊开启时出现）
  return PALETTE_CMDS.filter(c => !c.on || c.on());
}

function _paletteItemRow(it, i) {
  return `<button class="palette-item${i === _paletteIdx ? " on" : ""}"
      id="palette_opt_${i}" role="option" aria-selected="${i === _paletteIdx}"
      data-kind="item" data-i="${i}" onclick="paletteRun(this)"><span>${esc(_paletteClip(it.title, 44))}</span>
      <span class="hint">${esc(it.sub || "")}</span><span class="pk">${esc(it.hit || "题目")}</span></button>`;
}

function _paletteCmdRow(c, i) {
  return `<button class="palette-item${i === _paletteIdx ? " on" : ""}"
      id="palette_opt_${i}" role="option" aria-selected="${i === _paletteIdx}"
      data-kind="cmd" data-i="${i}" onclick="paletteRun(this)"><span>${esc(c.label)}</span>
      <span class="hint">${esc(c.hint)}</span><span class="pk">Enter</span></button>`;
}

function paletteRender() {
  const box = $("palette_list");
  if (!box) return;
  if (!_paletteShown.length) {
    box.innerHTML = `<div class="palette-empty">没有匹配的命令或内容——换个词试试。</div>`;
  } else {
    let html = "";
    let lastGroup = "";
    _paletteShown.forEach((e, i) => {
      // 每段标题只出现一次：命令与内容混排会分不清"Enter 是执行动作还是看题"
      if (e.kind === "item" && e.group !== lastGroup) {
        const g = _paletteGroups.find(x => x.kind === e.group) || { label: "结果", total: 0 };
        html += `<div class="palette-sec" role="presentation" data-group="${esc(e.group)}">`
          + `${esc(g.label)} · ${g.total} 条</div>`;
        lastGroup = e.group;
      }
      html += e.kind === "item" ? _paletteItemRow(e.item, i) : _paletteCmdRow(e.cmd, i);
    });
    box.innerHTML = html;
  }
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
  _paletteShown = paletteCommands()
    .filter(c => !k || (c.label + " " + c.hint + " " + c.keys).toLowerCase().includes(k))
    .map(c => ({ kind: "cmd", cmd: c }));
  _paletteGroups = [];   // 输入变了：上一轮内容结果立刻作废（否则会和新查询混在一起）
  _paletteIdx = 0;
  paletteRender();
  _paletteSearch(k);
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
  _paletteSeq++;   // 关闭后到达的搜索结果一律丢弃（否则下次打开会闪出上一次的题目）
  if (!anyOverlayOpen()) setBackgroundInert(false);
  const q = $("palette_q");
  if (q) q.setAttribute("aria-expanded", "false");
  const back = _paletteReturnFocus; _paletteReturnFocus = null;
  if (back && document.contains(back) && typeof back.focus === "function") {
    try { back.focus(); } catch (e) { /* ignore */ }
  }
}

/* 执行第 i 项（`el` 为被点中的按钮；键盘 Enter 时省略，用当前高亮项）。
   列表里现在有两类项：命令（`cmd.run()`）与内容（预览 / 考点下钻）——分支放在这一处，
   键盘与点击走的是同一条路。 */
function paletteRun(el) {
  const i = el && el.dataset ? Number(el.dataset.i) : _paletteIdx;
  const entry = _paletteShown[i];
  if (!entry) return;
  // 考点下钻**不收面板**：用户的意图是"看这个考点的题"，收掉反而要重新唤起
  if (entry.kind === "item" && entry.item.kind === "kc") { _paletteKcDrill(entry.item); return; }
  paletteClose();   // 其余情况先收面板：命令自己会切页，面板留着会挡住首屏
  try {
    if (entry.kind === "item") _paletteItemOpen(entry.item);
    else entry.cmd.run();
  } catch (e) { toast("命令执行失败：" + ((e && e.message) || e), false); }
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
