/* exported _vaultChat, _vaultSse, vaultAsk, vaultAskGlobal, vaultChatAsk, vaultChatClear,
   vaultChatCopy, vaultChatDownload, vaultChatKey, vaultChatPersist, vaultChatQuickRender,
   vaultChatRegen, vaultChatRender, vaultChatStop, vaultHandbook, vaultView,
   vaultOpenSource, vaultVariantSelect, vaultVariantStart, vaultVariantStartPath,
   vaultVariantSubmit */
/* ---- 错题助手（聊天层，learn 族第 4 片） ----
 *
 * 为什么单独立片：聊天层（流式 / 停止 / 重生成 / 会话持久化）与「vault 文件浏览器」
 * （目录管理 / 列表 / 下载 / 新建 / SM-2 复习）是**两件事**，合并会让单文件越过
 * 项目自定判据「任一 JS ≤ 800 行」（`tests/test_v15_frontend_split.py`）。
 * 加载顺序 = 依赖顺序：本片依赖 `learn-vault.js` 的 `vaultLoad` / `vaultRefreshList` /
 * `_vaultCur` / `_esc`（调用发生在**运行时**，不是加载期）。
 */
/* ---- 错题助手：聊天框式（**真·流式** + 可停止 + 多轮 + 可重生成） ----
 *
 * 形态与交互对齐主流开源 Agent 客户端（LobeChat / Open WebUI / ChatGPT-Next-Web 一类）：
 *   · **消息模型**：`{role, text, stage, cites, grounded, tools, pending, error, stopped, handbook}`；
 *   · **逐字流式**：后端 SSE 的 `delta` 帧直接追加进气泡（`requestAnimationFrame` 节流重绘，
 *     只重画那一个气泡，不重排整列）；
 *   · **可停止**：`AbortController` 断流，服务端同步截断 provider 流（见 `routers/vault.py` 的
 *     `threading.Event`）——「停止」要真的省 token，不是只停 UI；
 *   · **贴底才自动滚动**：用户往上翻历史时不被拽回底部（经典的 agent UI 细节）；
 *   · **会话持久化**：`sessionStorage`（刷新不丢），可「清空对话」；
 *   · 每条答复带「重新生成 / 存入错题 / 复制」，手册带「⇩ 下载 .md」；
 *   · **取证过程可见**：把调过的工具名按顺序列出来（`取证：search_my_mistakes → get_topic_stats`）。
 *
 * 四条硬约束仍由后端说了算（前端只如实显示 `grounded:false`，**不参与判断**）。
 */
const _VAULT_CHAT_KEY = "medkit-vault-chat";
const _VAULT_CHAT_MAX = 40;      // 持久化的消息上限（防止 sessionStorage 无限膨胀）
const _VAULT_CHAT_TEXT_MAX = 8000;

let _vaultChat = [];
let _vaultChatBusy = false;
let _vaultChatCtl = null;        // 在途 AbortController（「停止生成」用）
let _vaultChatPinned = true;     // 是否贴在底部（决定自动滚动）
let _vaultAnswerPath = "";       // 最近一次答复绑定的错题（空 = 文件夹全域问）
let _vaultPaintRaf = 0;
let _vaultPaintIdx = -1;
let _variantBusy = false;        // 变式生成/判分在途（与聊天流式互不阻塞）
window._vaultSummary = window._vaultSummary || { total: 0, weak: 0, due: 0 };
const _VAR_LETTERS = "ABCDE";

function _shortPath(p) {
  const s = String(p || "");
  const tail = s.split("/").pop() || s;
  return tail.replace(/\.md$/i, "");
}
function _vaultContextLabel(path) {
  const it = (typeof _vaultItems !== "undefined" ? _vaultItems : []).find(x => x.path === path);
  return it && it.question ? String(it.question) : "";
}
function _optText(o) {
  return String(o == null ? "" : o).replace(/^[A-Ea-e][\.、\s]+/, "").trim();
}

const _VAULT_QUICK = [
  "我最近哪个章节最弱？",
  "这些错题有什么共性？",
  "帮我按优先级排一个复习顺序",
  "我在「我的理解」里写的，有没有共同的误解？",
];

/* ---- 会话持久化（刷新不丢；只存**已落定**的消息） ---- */
function _vaultChatSave() {
  try {
    const slim = _vaultChat.filter(m => !m.pending && !m.variant).slice(-_VAULT_CHAT_MAX).map(m => ({
      role: m.role, text: String(m.text || "").slice(0, _VAULT_CHAT_TEXT_MAX),
      cites: m.cites || [], grounded: m.grounded, note: m.note || "",
      tools: m.toolNames || [], failed: m.failed || [],
      error: !!m.error, stopped: !!m.stopped,
      handbook: !!m.handbook, filename: m.filename || "",
    }));
    sessionStorage.setItem(_VAULT_CHAT_KEY, JSON.stringify(slim));
  } catch (e) { /* 隐私模式：仅本次会话内存 */ }
}
function _vaultChatRestore() {
  try {
    const raw = sessionStorage.getItem(_VAULT_CHAT_KEY);
    if (!raw) return;
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return;
    _vaultChat = arr.filter(m => m && m.text && (m.role === "user" || m.role === "assistant"));
  } catch (e) { _vaultChat = []; }
}

function _vaultChatSetBusy(on) {
  const b = $("btn_vault_ask_global");
  if (b) { b.disabled = on; b.textContent = on ? "生成中…" : "发送"; }
}

function vaultChatQuickRender() {
  const box = $("vault_chat_quick");
  if (!box) return;
  box.innerHTML = _VAULT_QUICK.map(q =>
    `<button class="chat-chip" data-q="${_esc(q)}" onclick="vaultChatAsk(this)">${_esc(q)}</button>`).join("");
}

function vaultChatAsk(btn) {
  const q = btn && btn.dataset ? (btn.dataset.q || "") : "";
  if (q) _vaultChatSend(q, "");
}

function vaultChatClear() {
  _vaultChat = []; _vaultAnswerPath = "";
  try { sessionStorage.removeItem(_VAULT_CHAT_KEY); } catch (e) { /* ignore */ }
  vaultChatRender();
  const m = $("vault_handbook_meta"); if (m) m.textContent = "";
  toast("已清空对话（错题文件不受影响）");
}

/* ---- 渲染：整列 / 单个气泡 ---- */
function _vaultBodyHTML(m) {
  if (m.text) {
    const html = (typeof mdRender === "function") ? mdRender(m.text) : esc(m.text);
    return m.pending ? html + '<span class="chat-caret"></span>' : html;
  }
  if (m.pending) return `<span class="spin"></span> ${esc(m.stage || "思考中…")}`;
  return '<span class="hint">（没有内容）</span>';
}

function _vaultMetaHTML(m) {
  const bits = [];
  if (m.path) {
    bits.push(`<span class="chat-context">针对：${esc(m.contextLabel || _shortPath(m.path))}</span>`);
  }
  if ((m.toolNames || []).length) bits.push(`取证：${m.toolNames.map(esc).join(" → ")}`);
  if ((m.cites || []).length) {
    bits.push("来源：" + m.cites.map((c, j) =>
      `<button type="button" class="chat-cite" data-path="${esc(c.path)}" data-sec="${esc(c.section || "")}"
        onclick="vaultOpenSource(this)" title="${esc(c.path)}${c.section ? "#" + esc(c.section) : ""}">[${j + 1}] ${esc(_shortPath(c.path))}${c.section ? " · " + esc(c.section) : ""}</button>`
    ).join(" "));
  }
  if (m.grounded === false && !m.error) {
    bits.push("（缺少可验证依据被拦下——它只依据你的错题库作答）");
  }
  if ((m.failed || []).length) bits.push(`工具失败：${m.failed.map(esc).join("、")}`);
  if (m.stopped) bits.push("（已停止生成）");
  if (m.note) bits.push(esc(m.note));
  return bits.length ? `<div class="chat-src">${bits.join("<br>")}</div>` : "";
}

function _vaultActsHTML(m, i) {
  if (m.pending) {
    return `<div class="chat-tools"><button class="mini-btn danger" data-act="stop" data-i="${i}"
      onclick="vaultChatStop()">■ 停止生成</button></div>`;
  }
  if (m.error || !m.text) return "";
  const btns = [];
  if (m.path) {
    btns.push(`<button class="mini-btn" data-path="${esc(m.path)}" onclick="vaultVariantStart(this)">练 3 道变式</button>`);
    btns.push(`<button class="mini-btn" data-path="${esc(m.path)}" onclick="vaultOpenSource(this)">打开原题</button>`);
  }
  if (m.handbook) {
    btns.push(`<button class="mini-btn" data-act="download" data-i="${i}" onclick="vaultChatDownload(this)">⇩ 下载手册(.md)</button>`);
  } else {
    if (m.path) btns.push(`<button class="mini-btn" data-act="persist" data-i="${i}" onclick="vaultChatPersist(this)">存入错题</button>`);
    btns.push(`<button class="mini-btn" data-act="regen" data-i="${i}" onclick="vaultChatRegen(this)">↻ 重新生成</button>`);
  }
  btns.push(`<button class="mini-btn" data-act="copy" data-i="${i}" onclick="vaultChatCopy(this)">复制</button>`);
  return `<div class="chat-tools">${btns.join("")}</div>`;
}

function _variantFeedbackHTML(r) {
  if (!r) return "";
  const rats = r.option_rationales && Object.keys(r.option_rationales).length
    ? `<details class="variant-rats"><summary>逐选项理由</summary><ul>` +
      Object.keys(r.option_rationales).sort().map(k =>
        `<li><b>${esc(k)}</b> ${mdRender(r.option_rationales[k] || "")}</li>`).join("") +
      `</ul></details>` : "";
  const quote = r.source_quote
    ? `<div class="variant-quote"><b>原文</b>：${esc(r.source_quote)}</div>` : "";
  return `<div class="variant-feedback ${r.correct ? "ok" : "bad"}">${r.correct
    ? "✓ 正确" : "✗ 你选 " + esc(r.user_answer || "—") + "，正确答案 " + esc(r.answer || "")}</div>
    <div class="variant-analysis">${mdRender(r.analysis || "")}</div>${rats}${quote}`;
}

function _variantCardHTML(m, i) {
  const v = m.variant || {};
  const head = `<div class="variant-head">错点变式训练 <span class="hint">同考点 · 不同问法</span></div>`;
  if (v.loading) {
    return `<div class="chat-msg bot variant-card" data-i="${i}">${head}
      <div class="hint"><span class="spin"></span> 正在围绕这条错题生成变式题…</div></div>`;
  }
  if (v.error) {
    return `<div class="chat-msg bot variant-card err" data-i="${i}">${head}
      <div class="variant-feedback bad">生成失败：${esc(v.error)}</div>
      <div class="variant-actions"><button class="mini-btn" onclick="vaultChatRender()">知道了</button></div></div>`;
  }
  const items = (v.items || []).map((it, qi) => {
    const chosen = (v.answers || {})[String(qi)] || "";
    const got = v.result && (v.result.items || []).find(x => x.index === qi);
    const opts = (it.options || []).map((o, oi) => {
      const L = _VAR_LETTERS[oi] || String.fromCharCode(65 + oi);
      let cls = chosen === L ? " sel" : "";
      if (got) {
        if (got.correct && L === got.answer) cls = " ok";
        else if (L === got.user_answer) cls = " bad";
        else if (L === got.answer) cls = " ans";
      }
      return `<button type="button" class="variant-opt${cls}" data-q="${qi}" data-opt="${L}"
        onclick="vaultVariantSelect(this)" ${got ? "disabled" : ""}>${L}. ${esc(_optText(o))}</button>`;
    }).join("");
    return `<div class="variant-q" data-q="${qi}">
      <div class="variant-stem">${qi + 1}. ${esc(it.question)}</div>
      <div class="variant-opts">${opts}</div>
      ${got ? _variantFeedbackHTML(got) : ""}
    </div>`;
  }).join("");
  const summary = v.result
    ? `<div class="variant-sum ${v.result.passed ? "ok" : "bad"}">${v.result.passed
      ? "迁移通过" : "本轮未通过"} · ${v.result.correct}/${v.result.total}
      ${v.result.source && v.result.source.ok === false ? " · 原题写回失败：" + esc(v.result.source.reason || "") : ""}</div>`
    : (v.gradeError ? `<div class="variant-sum bad">判分失败：${esc(v.gradeError)}</div>` : "");
  const actions = v.result
    ? `<div class="variant-actions">
        ${v.source_path ? `<button class="act gray" data-path="${esc(v.source_path)}" onclick="vaultVariantStart(this)">再练一组</button>` : ""}
        <button class="mini-btn" onclick="vaultChatRender()">收起结果</button></div>`
    : `<div class="variant-actions"><button class="act" onclick="vaultVariantSubmit(${i})" ${v.pending ? "disabled" : ""}>${v.pending ? "判分中…" : "提交判分"}</button></div>`;
  return `<div class="chat-msg bot variant-card" data-i="${i}">${head}${items}${actions}${summary}</div>`;
}

function _vaultMsgHTML(m, i) {
  if (m.variant) return _variantCardHTML(m, i);
  if (m.role === "user") return `<div class="chat-msg user" data-i="${i}">${esc(m.text)}</div>`;
  return `<div class="chat-msg bot${m.error ? " err" : ""}" data-i="${i}">
      <div class="chat-body">${_vaultBodyHTML(m)}</div>
      ${_vaultMetaHTML(m)}
      ${_vaultActsHTML(m, i)}
    </div>`;
}

function vaultChatRender() {
  const box = $("vault_chat");
  if (!box) return;
  /* innerHTML 重建会销毁所有旧节点 ⇒ 焦点必然落回 <body>。
     记住"焦点在第几条消息的哪个动作按钮上"，重建后按同一个 data-act 放回去。 */
  const act = document.activeElement;
  const keepI = (act && box.contains(act) && act.dataset) ? act.dataset.i : "";
  const keepAct = (act && box.contains(act) && act.dataset) ? (act.dataset.act || "") : "";
  if (!_vaultChat.length) {
    const s = window._vaultSummary || {};
    const bits = [];
    if (s.total) bits.push(`${s.total} 条错题`);
    if (s.weak) bits.push(`${s.weak} 个薄弱点`);
    if (s.due) bits.push(`${s.due} 条今日到期`);
    box.innerHTML = `<div class="chat-empty">${bits.length
      ? "你目前有 " + bits.join(" · ") + "。"
      : "还没有对话。"}点上面的问题，或直接在下面输入——它只依据你的错题文件作答。</div>`;
    return;
  }
  box.innerHTML = _vaultChat.map((m, i) => _vaultMsgHTML(m, i)).join("");
  _vaultChatScroll(true);
  if (keepAct) {
    const again = box.querySelector(`[data-act="${keepAct}"][data-i="${keepI}"]`);
    if (again) { try { again.focus(); } catch (e) { /* 目标已不存在 */ } }
  }
}

/* 流式期间关掉 live 播报（否则读屏会把逐字增量当新内容反复打断），
   落地时再打开 —— 整段回答只播一次。 */
function _vaultChatLive(on) {
  const box = $("vault_chat");
  if (box) box.setAttribute("aria-live", on ? "polite" : "off");
}

/* 流式期间只重画**那一个**气泡（整列重排会打断选中/滚动，也慢） */
function _vaultChatPaint(i) {
  const box = $("vault_chat");
  if (!box) return;
  const el = box.querySelector(`.chat-msg[data-i="${i}"] .chat-body`);
  const m = _vaultChat[i];
  if (!el || !m) return;
  if (m.pending && m.text) {
    /* 只追纯文本：此前每帧都对"累计全文"跑一次 mdRender，是 O(n²)
       （4000 字 × 约 500 帧 ≈ 200 万字符解析），长回答会持续占用主线程。
       落地（result）时再一次性渲染 Markdown —— 观感反而更接近"逐字输出"。 */
    el.textContent = m.text;
    const caret = document.createElement("span");
    caret.className = "chat-caret";
    caret.setAttribute("aria-hidden", "true");
    el.appendChild(caret);
    return;
  }
  el.innerHTML = _vaultBodyHTML(m);
}

function _vaultChatPaintSoon(i) {
  _vaultPaintIdx = i;
  if (_vaultPaintRaf) return;
  _vaultPaintRaf = requestAnimationFrame(() => {
    _vaultPaintRaf = 0;
    const k = _vaultPaintIdx; _vaultPaintIdx = -1;
    if (k >= 0) _vaultChatPaint(k);
  });
}

/* ---- 滚动：只在「用户本来就贴在底部」时自动跟随 ---- */
function _vaultChatAtBottom(box) {
  return box.scrollHeight - box.scrollTop - box.clientHeight < 64;
}
function _vaultChatScroll(force) {
  const box = $("vault_chat");
  if (!box) return;
  if (force || _vaultChatPinned) box.scrollTop = box.scrollHeight;
}
(function initVaultChatScroll() {
  const box = $("vault_chat");
  if (box) box.addEventListener("scroll", () => { _vaultChatPinned = _vaultChatAtBottom(box); });
})();

/* ---- SSE 消费（单一实现：提问与手册共用，避免两份解析漂移） ---- */
async function _vaultSse(url, payload, onEvent, signal) {
  const res = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), signal });
  if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n"); buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const body = line.slice(6);
      if (body.includes("[DONE]")) continue;
      let ev; try { ev = JSON.parse(body); } catch (e) { continue; }
      onEvent(ev);
    }
  }
}

/* 发送一条提问：推入用户气泡 + 一个流式助手气泡，逐帧更新。
   `echo=false`（重新生成）不重复插入用户气泡。 */
async function _vaultChatSend(question, path, echo = true) {
  if (_vaultChatBusy) { toast("上一条还在生成——先点「停止生成」或等它说完", false); return; }
  const q = String(question || "").trim();
  if (!q) { toast("先写你的问题", false); return; }
  const history = _vaultChat.filter(m => !m.pending && m.text)
    .slice(-8).map(m => ({ role: m.role, content: m.text }));
  if (echo) _vaultChat.push({ role: "user", text: q });
  const bubble = { role: "assistant", text: "", pending: true, stage: "正在取证…", toolNames: [],
                   path: path || "", contextLabel: _vaultContextLabel(path || "") };
  _vaultChat.push(bubble);
  const i = _vaultChat.length - 1;
  _vaultChatBusy = true;
  _vaultChatSetBusy(true);
  _vaultChatCtl = new AbortController();
  _vaultChatPinned = true;
  _vaultChatLive(false);   // 流式期间不播报逐字增量
  vaultChatRender();
  try {
    await _vaultSse("/api/vault/ask", { question: q, path: path || "", history }, ev => {
      if (ev.type === "stage") {
        if (ev.tool) { bubble.stage = `取证中：${ev.tool}…`; bubble.toolNames.push(ev.tool); }
        else bubble.stage = ev.hint || "思考中…";
        _vaultChatPaintSoon(i);
      } else if (ev.type === "delta") {
        bubble.text = (bubble.text || "") + (ev.text || "");
        _vaultChatPaintSoon(i);
        _vaultChatScroll(false);
      } else if (ev.type === "error") {
        bubble.pending = false; bubble.error = true;
        bubble.text = "出错了：" + (ev.message || "未知错误");
        _vaultChatLive(true);
        vaultChatRender();
      } else if (ev.type === "result") {
        bubble.pending = false;
        bubble.text = ev.answer || bubble.text || "";     // 收尾以 result 为准（覆盖流出的文本）
        bubble.cites = ev.citations || [];
        bubble.grounded = ev.grounded !== false;
        bubble.note = ev.note || "";
        bubble.failed = (ev.tools || []).filter(t => t && t.ok === false).map(t => t.tool);
        bubble.toolNames = (ev.tools || []).map(t => t.tool);
        _vaultAnswerPath = path || "";
        _vaultChatLive(true);   // 落地：整段回答播报一次
        vaultChatRender();
      }
    }, _vaultChatCtl.signal);
    if (bubble.pending) {
      bubble.pending = false;
      bubble.text = bubble.text || "（没有收到答复，可重试）";
      _vaultChatLive(true);
      vaultChatRender();
    }
  } catch (e) {
    bubble.pending = false;
    if (e && e.name === "AbortError") {
      bubble.stopped = true;
      if (!bubble.text) bubble.text = "（已停止生成）";
    } else {
      bubble.error = true;
      bubble.text = "请求失败：" + ((e && e.message) || e);
    }
    vaultChatRender();
  } finally {
    _vaultChatBusy = false;
    _vaultChatCtl = null;
    _vaultChatSetBusy(false);
    _vaultChatLive(true);   // 无论成功/停止/失败，播报都要恢复
    _vaultChatSave();
  }
}

/* 停止生成：断流 + 服务端同步截断 provider 流（已生成的部分保留） */
function vaultChatStop() {
  if (_vaultChatCtl) { try { _vaultChatCtl.abort(); } catch (e) { /* ignore */ } }
  toast("已停止生成（已生成的部分保留）");
}

/* 重新生成：删掉这条答复，用**同一条提问**重发（上下文不变） */
async function vaultChatRegen(btn) {
  const i = btn && btn.dataset ? parseInt(btn.dataset.i, 10) : -1;
  if (i < 0 || !_vaultChat[i] || _vaultChatBusy) return;
  let u = -1;
  for (let k = i - 1; k >= 0; k--) if (_vaultChat[k].role === "user") { u = k; break; }
  if (u < 0) { toast("找不到对应的提问", false); return; }
  const q = _vaultChat[u].text, path = _vaultAnswerPath;
  _vaultChat.splice(i, 1);
  vaultChatRender();
  await _vaultChatSend(q, path, false);
}

/* 详情内「提问」：绑定当前打开的那条错题（path），回答同样落进对话区。 */
async function vaultAsk() {
  const inp = $("vlt_q");
  const q = (inp && inp.value.trim()) || "";
  if (!q) { toast("先写你的问题", false); return; }
  if (inp) inp.value = "";
  const box = $("vault_chat");
  if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  await _vaultChatSend(q, _vaultCur || "");
}

/* 文件夹全域问：不绑定某一条错题（path 为空 ⇒ Agent 自行 search/stats），
   用来回答「我哪章最弱」「这些错有什么共性」这类跨条目的问题。 */
function vaultAskGlobal() {
  const inp = $("vault_ask_q");
  const q = (inp && inp.value.trim()) || "";
  if (!q) { toast("先写你的问题", false); return; }
  if (inp) inp.value = "";
  _vaultChatSend(q, "");
}

function vaultChatKey(e) {
  if (e.key !== "Enter" || e.shiftKey) return;   // Shift+Enter 换行
  e.preventDefault();
  vaultAskGlobal();
}

/* 把某条答复写回错题文件（**唯一写回入口**，必须用户点）：
   目标 = 这条答复提问时绑定的错题；没有绑定则用当前打开的那条。 */
async function vaultChatPersist(btn) {
  const i = btn && btn.dataset ? parseInt(btn.dataset.i, 10) : -1;
  const m = _vaultChat[i];
  if (!m || !m.text) return;
  const rel = _vaultAnswerPath || _vaultCur;
  if (!rel) { toast("先在下面「错题」里打开一条错题，再点「存入错题」", false); return; }
  try {
    await api(`/api/vault/mistakes/${encodeURI(rel)}/section`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "agent_reply", text: m.text }) });
    toast("答复已写进这个错题文件的「## Agent 的答复」");
    await vaultRefreshList();
  } catch (e) { toast("写入失败：" + e.message, false); }
}

function vaultChatCopy(btn) {
  const i = btn && btn.dataset ? parseInt(btn.dataset.i, 10) : -1;
  const m = _vaultChat[i];
  if (m && m.text) copyText(m.text, btn);
}

function vaultChatDownload(btn) {
  const i = btn && btn.dataset ? parseInt(btn.dataset.i, 10) : -1;
  const m = _vaultChat[i];
  if (m && m.text) downloadText(m.filename || "MedKit复习手册.md", m.text);
}

/* ---- 引用跳转：点来源 chip 打开原错题 ---- */
async function vaultOpenSource(btn) {
  const path = btn && btn.dataset ? (btn.dataset.path || "") : "";
  if (!path) return;
  const sec = btn && btn.dataset ? (btn.dataset.sec || "") : "";
  await vaultOpen(path);
  const box = $("vault_detail");
  if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    if (sec === "我的理解" && $("vlt_und")) $("vlt_und").focus();
    else if (sec === "我的疑问" && $("vlt_doubts")) $("vlt_doubts").focus();
  } catch (e) { /* 元素未渲染 */ }
  toast(sec ? `已打开来源错题（${sec}）` : "已打开来源错题");
}

/* ---- 错点变式训练：生成 → 作答 → 判分 → 回流 ---- */
async function vaultVariantStartPath(path) {
  const rel = String(path || "").trim();
  if (!rel) { toast("先打开一条错题，再练变式", false); return; }
  if (_variantBusy) { toast("上一组变式还在生成或判分，稍等一下", false); return; }
  _variantBusy = true;
  const box = $("vault_chat");
  if (box) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  const msg = { role: "assistant", text: "", variant: { loading: true, source_path: rel } };
  _vaultChat.push(msg);
  _vaultChatPinned = true;
  vaultChatRender();
  try {
    const r = await api("/api/vault/variants/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: rel, n: 3 }) });
    msg.variant = { source_path: r.set.source_path || rel, setId: r.set.set_id,
                    items: r.set.items || [], answers: {}, result: null,
                    rejected: r.rejected || [] };
    if (!msg.variant.items.length) throw new Error("没有可用的变式题（可能全部未过校验）");
    if (msg.variant.rejected.length) {
      toast(`已生成 ${msg.variant.items.length} 道变式；${msg.variant.rejected.length} 道未通过校验`, false);
    }
  } catch (e) {
    msg.variant = { error: (e && e.message) || String(e), source_path: rel };
  } finally {
    _variantBusy = false;
    vaultChatRender();
    _vaultChatSave();
  }
}

function vaultVariantStart(btn) {
  const path = btn && btn.dataset ? (btn.dataset.path || "") : "";
  vaultVariantStartPath(path);
}

function vaultVariantSelect(btn) {
  const card = btn && btn.closest ? btn.closest(".variant-card") : null;
  if (!card) return;
  const i = Number(card.dataset.i);
  const q = btn.dataset.q || "";
  const opt = btn.dataset.opt || "";
  const m = _vaultChat[i];
  if (!m || !m.variant || m.variant.result || m.variant.pending) return;
  m.variant.answers = m.variant.answers || {};
  m.variant.answers[q] = opt;
  vaultChatRender();
  const again = document.querySelector(
    `.variant-card[data-i="${i}"] .variant-opt[data-q="${q}"][data-opt="${opt}"]`);
  if (again) { try { again.focus(); } catch (e) { /* ignore */ } }
}

async function vaultVariantSubmit(i) {
  const m = _vaultChat[i];
  const v = m && m.variant;
  if (!v || !v.setId || v.pending || v.result) return;
  const items = v.items || [];
  const missing = items.map((_, q) => String(q)).filter(q => !(v.answers || {})[q]);
  if (missing.length) {
    toast(`还有 ${missing.length} 道没选——先选完再提交`, false);
    return;
  }
  v.pending = true;
  v.gradeError = "";
  vaultChatRender();
  try {
    const r = await api(`/api/vault/variants/${encodeURIComponent(v.setId)}/grade`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: v.answers }) });
    v.result = r;
    v.pending = false;
    vaultChatRender();
    await vaultLoad();
    await vaultReviewLoad();
    toast(r.passed
      ? `变式通过 ${r.correct}/${r.total}；迁移证据已记录`
      : `变式未通过 ${r.correct}/${r.total}；已按「错了当天再来」排进今日复习`, false);
  } catch (e) {
    v.pending = false;
    v.gradeError = (e && e.message) || String(e);
    vaultChatRender();
    toast("判分失败：" + v.gradeError, false);
  }
}

/* ---- 薄弱点复习手册（按科目，**流式**） ----
 *
 * 与「提问」同一条底线：依据只有你自己的 vault（薄弱项分组 + 错题条目原文）。
 * 错题库为空时后端直接给说明、**不调模型**（不烧 token，也没有幻觉空间）。
 */
async function vaultHandbook() {
  if (_vaultChatBusy) { toast("助手还在生成——先点「停止生成」或等它说完", false); return; }
  const sel = $("vault_hb_subject");
  const subject = sel ? sel.value : "";
  const meta = $("vault_handbook_meta"), btn = $("btn_vault_handbook");
  _vaultChat.push({ role: "user", text: `【生成薄弱点复习手册】${subject || "全部科目"}` });
  const bubble = { role: "assistant", text: "", pending: true, handbook: true,
                   stage: "正在归纳你的薄弱点（约 10~40 秒）…", toolNames: [] };
  _vaultChat.push(bubble);
  const i = _vaultChat.length - 1;
  _vaultChatBusy = true;
  _vaultChatSetBusy(true);
  _vaultChatCtl = new AbortController();
  _vaultChatPinned = true;
  if (btn) btn.disabled = true;
  if (meta) meta.textContent = "生成中…";
  _vaultChatLive(false);
  vaultChatRender();
  try {
    await _vaultSse("/api/vault/handbook", { subject }, ev => {
      if (ev.type === "stage") {
        bubble.stage = ev.hint || bubble.stage;
        _vaultChatPaintSoon(i);
      } else if (ev.type === "delta") {
        bubble.text = (bubble.text || "") + (ev.text || "");
        _vaultChatPaintSoon(i);
        _vaultChatScroll(false);
      } else if (ev.type === "error") {
        bubble.pending = false; bubble.error = true;
        bubble.text = "手册生成失败：" + (ev.message || "未知错误");
        if (meta) meta.textContent = "";
        _vaultChatLive(true);
        vaultChatRender();
      } else if (ev.type === "result") {
        bubble.pending = false;
        bubble.text = ev.markdown || bubble.text || "（模型返回了空手册）";
        bubble.grounded = ev.grounded !== false;
        bubble.note = ev.note || "";
        bubble.filename = `MedKit复习手册_${subject || "全部科目"}_`
          + new Date().toISOString().slice(0, 10) + ".md";
        if (meta) {
          meta.textContent = ev.grounded === false
            ? (ev.note || "数据不足，未生成")
            : `已生成：${ev.items || 0} 条错题 · ${ev.groups || 0} 个薄弱组`;
        }
        _vaultChatLive(true);
        vaultChatRender();
      }
    }, _vaultChatCtl.signal);
    if (bubble.pending) { bubble.pending = false; vaultChatRender(); }
  } catch (e) {
    bubble.pending = false;
    if (e && e.name === "AbortError") {
      bubble.stopped = true;
      if (!bubble.text) bubble.text = "（已停止生成）";
    } else {
      bubble.error = true;
      bubble.text = "手册生成失败：" + ((e && e.message) || e);
    }
    if (meta) meta.textContent = "";
    vaultChatRender();
  } finally {
    _vaultChatBusy = false;
    _vaultChatCtl = null;
    _vaultChatSetBusy(false);
    _vaultChatLive(true);
    if (btn) btn.disabled = false;
    _vaultChatSave();
  }
}

/* view 钩子：learn.js 的 enterMistakes 会在切到本页时调它 */
window.vaultView = function () {
  vaultLoad();
  vaultReviewLoad();
  _vaultChatRestore();
  vaultChatQuickRender();
  vaultChatRender();
};

/* 兜底：**脚本加载期**就落在错题页时，`window.vaultView` 还没定义（learn.js 先加载、
   它的 `enterMistakes()` 在脚本求值期即可能被调用，那时本片尚未求值），
   于是错题助手会停在占位文案——必须刷新浏览器才出数据。
   本 IIFE 在所有脚本就绪后补一次加载：**幂等**（`vaultLoad` 可重复调，列表侧还有请求序号防抖），
   且只在「错题页已展示 + 错题本视图已展示」时触发。 */
(function initVaultPane() {
  const ensure = () => {
    const tab = document.getElementById("tab-learn");
    const view = document.getElementById("lv-mistakes");
    if (!tab || !view) return;
    if (!tab.classList.contains("show") || !view.classList.contains("show")) return;
    window.vaultView();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ensure);
  else ensure();
})();

