# MedKit 网页版前端特色与交互升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把网页版从「功能齐全的工具页」改成「像本地 MedKit 一样有书桌气质、会看时间、允许停下的学习桌面」；不改变信息架构与数据模型。

**Architecture:** 沿用当前零构建源文件 + `tools/build_bundle.py` 发布产物的结构。视觉与交互改动落在 `index.html`、`css/tokens.css`、`css/app.css`、`js/app.js`、`js/bank-drill.js`、`js/bank-drill-result.js`，每次改完重建 1 CSS + 1 JS 产物。行为守卫用 Node 测试 + Playwright 真浏览器走查。

**Tech Stack:** 原生 HTML/CSS/JS（经典脚本共享全局作用域）、FastAPI 可选账号服务、Node `node:test`、Playwright、Python `pytest`。

**目标目录:** `C:\Users\38063\Desktop\medkit-web-contest`

---

## 0. 参照系：本地 MedKit + 成熟项目

| 参照 | 借什么 | 不借什么 |
|---|---|---|
| 本地 MedKit v0.23「书桌」 | 纸、墨、红笔、荧光笔；题干/解析宋体；界面黑体；数据像病历；深夜不劝学；允许停下 | 满屏拟物、手写体、皮革质感 |
| Duolingo | 先体验后建档；分布式引导；失败时先接住情绪；连胜可断 | 排行榜、惩罚式连胜、角色抢内容 |
| Things 3 | 今天只放今天；完成感来自动效与节奏 | 依赖苹果生态的手势 |
| Readwise | 单一强调色；720px 阅读列；荧光笔是产品动作 | 营销页式巨大标题 |
| Khan Academy / Quizlet | 答错就地展开解析；保留你选的选项 | 跳页丢上下文 |
| Anki | 键盘效率；算法诚实 | 把配置项当界面 |
| UWorld / AMBOSS | 逐选项 rationale；数据对齐；准备度而非预测分 | 医院系统式的冷与密 |
| 医考帮 / 蓝基因 | 科目/题库/考点组织；移动刷题效率 | 社区、广告、打卡塞进主路径 |
| Calm / Headspace / NHS | 低认知负荷；温和语气；隐私可见 | 用疗愈话术掩盖功能 |

**反面清单**：吉祥物/宠物、排行榜、惩罚式连胜、满屏手写体、Web Font、CDN、构建步骤、无意义撒彩带、把「AI」当主界面卖点。

---

## 1. 文件地图（先锁定边界）

| 文件 | 职责 | 本次会动什么 |
|---|---|---|
| `index.html` | 页面骨架、今天页、刷题页、设置页 | 今天页任务单、首启卡、空态容器、文案 |
| `css/tokens.css` | 颜色/字号/圆角/字体令牌 | 同步本地 v0.23 暖纸/墨绿/红笔/宋体令牌 |
| `css/app.css` | 网页版视图增量样式 | 今天页、判分就近、错误态、动效、移动端 |
| `js/app.js` | 路由、今天页、刷题页、设置、考试计划 | 时间感知、今天任务单、首启、错误态、文案 |
| `js/bank-drill.js` | 沉浸式作答引擎 | 默认不自动跳题、批改页三层信息、错误态 |
| `js/bank-drill-result.js` | 判分结果 / 逐选项理由 | 红笔对错印、就近解析、正确项理由 |
| `js/store.js` | 本地数据/会话 | 不改数据模型；只读统计供今天页使用 |
| `tools/build_bundle.py` | 合并 CSS/JS | 每次改完必须重跑，否则 index 仍引用旧 bundle |
| `tests/*.mjs` | 前端守卫 | 新增设计令牌、文案、交互契约 |
| `tests/test_server.py` | 账号服务守卫 | 不受本次影响，但每次全量跑 |

**硬约束**：
- 不引入构建链、CDN、Web Font；源文件仍分开。
- 不改变题库/作答/错题数据模型。
- 所有新文案必须先过 `tests/copy-tone.test.mjs`（Task 6 创建）。
- 每个视觉改动必须留亮/暗两套截图；不靠「看起来对」。

---

## 2. 十条「灵性」验收信号

1. 它知道现在几点：05–10 早上 / 11–13 中午 / 14–18 下午 / 19–22 晚上 / 23–04 夜深。
2. 首屏只有一个主动作；第二个动作必须降权。
3. 答错不评判：先说「你选的是 X，正确答案是 Y」，再给下一步。
4. 解析从你所选项下方展开，不跳页。
5. 允许「今天到这儿」；不自动续下一张。
6. 没有数据时不显示 0/0；空态是邀请，不是判决。
7. 错误三段式：发生了什么 / 什么还安全 / 下一步做什么。
8. 完成有轻微触感（140ms 按下、240ms 对错印），不撒彩带。
9. 23:30 后不推新题，只给「过 5 道错题」。
10. 文案像同专业研友：具体、克制、允许停下；不喊「加油」。

---

## 3. 任务

### Task 1: 视觉地基（纸、墨、红笔、荧光笔）

**Files:**
- Replace: `css/tokens.css`（用 `medkit/web/css/tokens.css` 覆盖）
- Modify: `css/app.css`
- Test: `tests/design-tokens.test.mjs`（新建）

- [ ] **Step 1: 写失败测试**

```js
// tests/design-tokens.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const ROOT = new URL("../", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const TOKENS = readFileSync(`${ROOT}css/tokens.css`, "utf8");
const APP = readFileSync(`${ROOT}css/app.css`, "utf8");

test("本地书桌令牌在场", () => {
  for (const key of ["--font-read", "--lh-read", "--seal", "--seal-soft", "--seal-line"]) {
    assert.match(TOKENS, new RegExp(key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*:"));
  }
});

test("阅读面用宋体，界面不硬编码字面色", () => {
  assert.match(APP, /font-family:\s*var\(--font-read\)/);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/design-tokens.test.mjs`
Expected: FAIL，`--font-read` 不在当前 `css/tokens.css`。

- [ ] **Step 3: 覆盖令牌并加阅读面**

把本地 `medkit/web/css/tokens.css` 整文件复制到 `medkit-web-contest/css/tokens.css`（不要手抄数值）。在 `css/app.css` 末尾加：

```css
/* 题干与解析用宋体，界面仍用黑体；这是「书桌」的第一层 */
.bank-q,.bank-ans,.bank-why,.due-ana,.bank-quote{font-family:var(--font-read);line-height:var(--lh-read)}
/* 红笔只做批改，不做装饰 */
.bank-ans.no{border-color:var(--seal-line)}
.bank-ans.ok .bank-qidx{color:var(--seal)}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/design-tokens.test.mjs`
Expected: PASS（2 tests）。

- [ ] **Step 5: 重建并被截图**

Run: `python tools/build_bundle.py && node --test tests/*.test.mjs`
Expected: bundle 生成；全部 Node 测试通过。
用 Playwright 在 390px 与 1280px 各截亮/暗两套，确认没有因为 token 替换出现对比度不足或硬编码色冲突。

---

### Task 2: 今天页改成一张任务单

**Files:**
- Modify: `index.html`（`#tab-start`）
- Modify: `js/app.js`（`loadStart`）
- Modify: `css/app.css`
- Test: 浏览器走查（截图）

- [ ] **Step 1: 写今天页结构**

把 `#tab-start` 的主区改成：

```html
<div class="today-head">
  <div class="today-date" id="today_date">今天</div>
  <h1 class="today-hello" id="today_hello">今天</h1>
  <p class="today-sub" id="today_sub">先看看这一页。</p>
</div>
<div class="today-grid">
  <div class="today-main"><div id="start_body"></div></div>
  <aside class="today-side">
    <details class="card fold" id="stats_card"><summary>这周的节奏</summary><div class="fold-body" id="week_rhythm"></div></details>
    <details class="card fold" id="weak_card"><summary>昨天错在哪</summary><div class="fold-body" id="yesterday_wrong"></div></details>
    <details class="card fold" id="exam_card"><summary>考试</summary><div class="fold-body" id="exam_box"></div></details>
  </aside>
</div>
```

- [ ] **Step 2: 写时间感知与任务单**

在 `js/app.js` 的 `loadStart()` 开头加：

```js
function _todayGreeting(d = new Date()) {
  const h = d.getHours();
  if (h >= 5 && h < 11) return "早上好";
  if (h >= 11 && h < 14) return "中午好";
  if (h >= 14 && h < 18) return "下午好";
  if (h >= 18 && h < 23) return "晚上好";
  return "夜深了";
}
function _todayIsLate(d = new Date()) {
  const h = d.getHours(), m = d.getMinutes();
  return h >= 23 || h < 5 || (h === 23 && m >= 30);
}
```

`loadStart()` 的任务单文案：

```js
const late = _todayIsLate();
const tasks = late
  ? [{ label: "过 5 道错题", action: "todayReview(5)", primary: true },
     { label: "看看这周练了几天", action: "todayOpenStats()", primary: false }]
  : [
      { label: `做一组 ${goal} 题`, action: `todayDrill(${firstKey}, ${goal})`, primary: true },
      { label: due ? `复习到期 ${due} 题` : "看一道错题", action: due ? "doStartMistakes()" : "showTab('learn')", primary: false },
      { label: "看看这周练了几天", action: "todayOpenStats()", primary: false },
    ];
```

底部加：

```html
<button class="act gray" onclick="todayStop()">今天到这儿</button>
```

补三个今天页动作（避免调用不存在的函数）：

```js
async function todayDrill(bid, n) {
  showTab("study");
  await bankDrillStart(bid, "all", { runmode: "practice" });
  const d = _bankDrill;
  d.items = d.items.slice(0, Math.max(1, Number(n) || 20));
  d.idx = 0;
  bankDrillRender();
}
async function todayReview(n) {
  showTab("learn");
  await bankDrillStartMistakes("all");
  const d = _bankDrill;
  d.items = d.items.slice(0, Math.max(1, Number(n) || 5));
  d.idx = 0;
  bankDrillRender();
}
function todayStop() {
  toast("好，今天就到这儿。明天见。");
}
function todayOpenStats() {
  const el = $("stats_card");
  if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth", block: "center" }); }
}
```

- [ ] **Step 3: 写本周节奏 / 昨天错在哪**

```js
function renderWeekRhythm() {
  const week = Store.last7();
  const days = week.map(d => `<span class="rhythm-dot" data-n="${d.count}" title="${d.count} 题">${d.label}</span>`).join("");
  $("week_rhythm").innerHTML = `<div class="rhythm">${days}</div><p class="hint">这周练了 ${week.filter(d => d.count > 0).length} 天</p>`;
}
function renderYesterdayWrong() {
  const rows = Store.weakPoints(1);
  $("yesterday_wrong").innerHTML = rows.length
    ? `<p class="hint">昨天最该补的是「${esc(rows[0].sub || "未分类")}」：错 ${rows[0].wrong} 道。</p>`
    : `<p class="hint">昨天没有错题。今天做几道，这里会记着。</p>`;
}
```

- [ ] **Step 4: 浏览器验收**

```bash
python tools/build_bundle.py
python -m http.server 8099
```

打开 `http://127.0.0.1:8099/`：
- 今天页只有一个主按钮；
- 右栏折叠卡摘要不显示 `0/0`；
- 用浏览器 DevTools 改系统时间到 23:40，刷新后主按钮变「过 5 道错题」；
- 点「今天到这儿」出现 `好，今天就到这儿。明天见。`。

---

### Task 3: 首启：先给一个动作，不给四个零

**Files:**
- Modify: `js/app.js`（`loadStart` 首启分支）
- Modify: `css/app.css`
- Test: 浏览器走查（清空 localStorage 后截图）

- [ ] **Step 1: 写首启分支**

```js
function _firstRunHTML() {
  return `<div class="firstrun-card">
    <h2>先放一份题库进来。</h2>
    <p class="hint">软件不自带题库；导入你自己的 md / json，或者先做 3 道示例题看看手感。</p>
    <div class="btns">
      <button class="act" onclick="todayImport()">导入文件</button>
      <button class="act gray" onclick="todayPaste()">粘贴一段题干</button>
      <button class="act gray" onclick="doSample3()">先做 3 道示例题</button>
    </div>
  </div>`;
}
```

`loadStart()` 在 `Store.stats().done === 0` 且没有题库时返回 `_firstRunHTML()`，不渲染四个统计。

- [ ] **Step 2: 实现 3 道示例**

```js
async function doSample3() {
  const first = Store.bankList(false)[0];
  if (!first) return toast("题库还没加载好", false);
  showTab("study");
  await bankDrillStart(first.key, "all", { runmode: "practice" });
  const d = _bankDrill;
  d.items = d.items.slice(0, 3);
  d.idx = 0;
  bankDrillRender();
}
```

- [ ] **Step 3: 验收**

清空 `localStorage` 后刷新：
- 首屏不出现 0/0、0%、0 天；
- 只有一张便签 + 三个动作；
- 「先做 3 道示例题」进入答题，3 题后出批改页；
- 截图留档。

---

### Task 4: 一张卷子：默认不自动跳题，批改页三层信息

**Files:**
- Modify: `js/bank-drill.js`
- Modify: `js/app.js`（`bankDrillFinish` 调用处/批改页文案）
- Modify: `css/app.css`
- Test: 浏览器走查 + Node 静态断言

- [ ] **Step 1: 关闭默认自动跳题**

```js
/* 书桌方案：答对后不自动把人推走；留 600ms 给「我居然对了」的那一下。 */
const _BANK_AUTO_SECS = 0;
```

`_bankAutoArm()` 在 `secs === 0` 时直接 return（已有逻辑）。

- [ ] **Step 2: 批改页三层信息**

`bankDrillFinish()` 里把 `body.innerHTML` 改成：

```html
<div class="finish">
  <div class="finish-score">${c.ok} / ${total}</div>
  <p class="finish-line">这张卷子做完了。</p>
  <p class="finish-diag">${_finishDiagnosis(d)}</p>
  <div class="btns">
    ${c.no ? `<button class="act" onclick="bankDrillRetryWrong()">过一遍错的 ${c.no} 道</button>` : ""}
    <button class="act gray" onclick="bankDrillExit()">今天到这儿</button>
  </div>
</div>
```

```js
function _finishDiagnosis(d) {
  const wrong = Object.values(d.marks).filter(m => m.graded && !m.correct);
  if (!wrong.length) return "全部答对。";
  const topics = {};
  for (const m of wrong) {
    const t = m.subtopic || "未分类";
    topics[t] = (topics[t] || 0) + 1;
  }
  const top = Object.entries(topics).sort((a, b) => b[1] - a[1])[0];
  return `错的 ${wrong.length} 道里，${top[1]} 道都在「${top[0]}」。`;
}
```

- [ ] **Step 3: 验收**

- 答对后 3 秒不自动跳题；只有「下一题 →」按钮；
- 做完一张卷子，批改页依次出现分数、诊断、一个主行动；
- 点「今天到这儿」退出，不自动开下一张；
- 截图亮/暗。

---

### Task 5: 错误态三段式（发生了什么 / 什么还安全 / 下一步）

**Files:**
- Modify: `js/app.js`
- Modify: `index.html`（给 `loadStart`/`bank_list`/`vault_list` 增加错误容器）
- Test: Playwright 路由拦截

- [ ] **Step 1: 写统一错误组件**

```js
function showErrorBox(box, title, detail, actionLabel, onAction) {
  if (!box) return;
  box.innerHTML = `<div class="err-box" role="alert">
    <b>${esc(title)}</b>
    <p>${esc(detail)}</p>
    <button class="act gray" type="button">${esc(actionLabel || "再试一次")}</button>
  </div>`;
  const btn = box.querySelector("button");
  if (btn && onAction) btn.addEventListener("click", onAction);
}
```

- [ ] **Step 2: 替换技术错误文案**

`loadStart` 的 catch：

```js
showErrorBox(box, "这页数字没读到", "题库和错题都还在。", "再试一次", loadStart);
```

`renderBankList` 的 catch：

```js
showErrorBox(list, "题库没打开", "你的作答和错题都还在。", "再试一次", renderBankList);
```

`loadMistakes` 的 catch 同理。

- [ ] **Step 3: Playwright 验收**

用 `page.route('**/data/banks.json', r => r.abort())`：
- 页面不白屏；
- 错误卡出现「题库没打开」；
- 点「再试一次」能重新请求；
- 不再出现 `Failed to fetch` / `[object Object]`。

---

### Task 6: 文案系统与禁用词守卫

**Files:**
- Modify: `index.html`、`js/app.js`、`js/bank-drill.js`
- Create: `tests/copy-tone.test.mjs`

- [ ] **Step 1: 写失败测试**

```js
// tests/copy-tone.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
const ROOT = new URL("../", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const BANNED = ["赋能", "闭环", "一键", "极致", "全新", "打造", "助力", "一站式", "完美", "沉浸式"];
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}
test("界面文案不出现营销腔/内部黑话", () => {
  const files = ["index.html", ...readdirSync(`${ROOT}js`).filter(f => f.endsWith(".js")).map(f => `js/${f}`)];
  for (const f of files) {
    const text = stripComments(readFileSync(`${ROOT}${f}`, "utf8"));
    for (const w of BANNED) assert.ok(!text.includes(w), `${f} 出现禁用词「${w}」`);
  }
});
```

- [ ] **Step 2: 运行确认失败并改文案**

Run: `node --test tests/copy-tone.test.mjs`
按失败位置改：把「一键/闭环/沉浸式」等换成具体动作；错误文案改三段式；空态改邀请句。

- [ ] **Step 3: 25 组 before/after 中先落地 8 组**

| 位置 | before | after |
|---|---|---|
| 首页副标题 | 今天该复习什么、薄弱点在哪——一眼清楚，点一下就开始 | 先看看今天做什么。 |
| 刷题说明 | 选好科目和范围，点「开始刷题」就进入答题 | 点题库卡片就开始；想换范围再展开。 |
| 空错题 | 还没有错题。 | 还没有错题。做错的题会自动来这儿，连你当时选的那个选项一起。 |
| 搜索无结果 | 没有匹配 | 没找到「X」。试试只搜关键词，或者去错题里找。 |
| 判错 | ✗ 答错了 | 你选的是 B。正确答案是 C。 |
| 加载失败 | 加载失败：Failed to fetch | 这次没读到题库。你的作答和错题都还在。 |
| 完成 | 本组练完 | 这张卷子做完了。 |
| 深夜 | 开始刷题 | 快 12 点了。今天不开始新题，过 5 道错题就睡。 |

- [ ] **Step 4: 运行测试确认通过**

Run: `node --test tests/copy-tone.test.mjs && node --test tests/*.test.mjs`
Expected: PASS。

---

### Task 7: 错题页、题库页、设置页的人性化

**Files:**
- Modify: `index.html`（`#tab-learn` / `#tab-bank` / `#tab-mine`）
- Modify: `js/app.js`
- Modify: `css/app.css`

- [ ] **Step 1: 错题页一屏一任务**

默认只显示「错题助手 + 今日复习」；「薄弱项」「全部错题」收进 tab；新建错题收进按钮。正文第一句：

```html
<p class="psub">做错的题都在这儿。先看今天该复习的，或者直接问我这道题。</p>
```

- [ ] **Step 2: 题库页的「书架」语气**

题库管理卡片标题改「书架」，每行 meta 用「300 题 · 已练 12 · 正确率 75%」；空态：

```html
<p class="hint">书架是空的。导入一份题库，或者先去「刷题」做几道。</p>
```

- [ ] **Step 3: 设置页第一屏**

顺序改为：账号 → 数据与备份 → 外观 → BYOK → 关于。数据卡第一句：

```html
<p class="hint">所有东西都在这台设备上（登录后同步到你的账号）。换电脑前，先导出一份备份。</p>
```

- [ ] **Step 4: 验收**

- 390px 首屏能看到今日复习与一个主按钮；
- 设置页 3 次点击内找到备份；
- 空书架有「导入 / 去刷题」两个动作；
- 截图留档。

---

### Task 8: 移动端与可访问性收尾

**Files:**
- Modify: `css/app.css`
- Test: Playwright 390 / 844×390 / 1280

- [ ] **Step 1: 44px 触控与安全区**

```css
.bank-opt,.subj,.act,.mini-btn{min-height:44px}
@supports (padding:max(0px)) {
  .side{padding-bottom:max(var(--space-3),env(safe-area-inset-bottom))}
  .drill-top{padding-left:max(var(--space-3),env(safe-area-inset-left))}
}
@media (prefers-reduced-motion:reduce){
  .bank-opt,.bank-ans,.resume-card{transition:none!important;animation:none!important}
}
```

- [ ] **Step 2: 焦点与键盘**

```css
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
```

确认 Esc 退出答题、←/→ 翻题、Enter 下一题、A–E 选选项仍可用。

- [ ] **Step 3: 三档浏览器验收**

- 390×844：无横向溢出；
- 844×390（横屏）：上一题/下一题不遮题；
- 1280×900：阅读列 ≤840px，不把控件拉变形；
- 键盘完成「选题库→判分→错题」；
- 截图留档。

---

### Task 9: 视觉验收与 10 个真实场景

**Files:**
- Create: `docs/ux/frontend-character-shots/`（截图目录）
- Modify: `docs/体验测试_2026-10-29.md`（追加本轮结论）

- [ ] **Step 1: 截图基线**

亮/暗 × 390/1280，至少 8 张：今天、刷题、答题、批改、错题、题库管理、设置、首启。

- [ ] **Step 2: 走 10 个场景**

| 场景 | 期望 |
|---|---|
| 23:40 熄灯 | 不推新题，主按钮「过 5 道错题」 |
| 早八前 10 分钟 | 给 5 道错题的最小任务 |
| 地铁没网 | 题库/刷题/错题可用，只有同步提示联网 |
| 考前 7 天 | 错题 + 高频考点，新题降权 |
| 做完一套想停 | 有「今天到这儿」，不自动续 |
| 做到一半被叫走 | 重开「上次还剩 13 题」 |
| 错太多心态崩 | 不摇屏、不红闪、不排名 |
| 小组共用题库 | 导出保留 id，导入预览重复题 |
| 换电脑 | 设置第一句说数据在哪，导出/导入各一个按钮 |
| 实习间隙手机窄屏 | 44px 点击目标，表格自己滚，不要求横屏 |

- [ ] **Step 3: 全量校验**

```bash
python tools/build_bundle.py --check
python tools/build_questions.py --check
node --test tests/*.test.mjs
python -m pytest tests/test_server.py
```

Expected: 全绿；截图目录入库；体验报告追加「本轮已改 / 未改」。

---

## 4. 分批计划

| 批次 | 任务 | 交付目标 |
|---|---|---|
| **P0 · 书桌第一眼** | Task 1–3 | 暖纸/宋体/红笔；今天页一张任务单；首启只有一个动作 |
| **P1 · 一张卷子** | Task 4–6 | 默认不自动跳题；批改三层；错误三段式；文案禁用词守卫 |
| **P2 · 书桌细节** | Task 7–9 | 错题/书架/设置人性化；移动与 AA；10 场景验收 |

**依赖**：Task 1 必须先做；Task 2/3 依赖 Task 1 的令牌；Task 4/5 依赖现有会话/解析；Task 6 最后扫全站文案；Task 7–9 收尾。

---

## 5. 不做

1. 不做吉祥物、排行榜、惩罚式连胜。
2. 不做满屏手写体/皮革/胶带；纸是底，不是贴纸。
3. 不引入 Web Font、CDN、构建链。
4. 不把「AI」做成主界面卖点。
5. 不自动续下一张卷子。
6. 不用 0/0 冒充「今天的状态」。
7. 不在错误文案里暴露英文堆栈/内部字段。

---

## 6. 与现有清单的关系

- 本计划是 `docs/全面优化改进清单_2026-10-29.md` 中 **U-01～U-12** 的前端特色执行版。
- U-01 刷题页去重、F-05 会话断点、F-07 逐选项解析已落地；本计划接着做「视觉 + 语气 + 场景」。
- 每次改 `css/` 或 `js/` 后必须重跑 `python tools/build_bundle.py`，否则 `index.html` 仍引用旧 bundle。
