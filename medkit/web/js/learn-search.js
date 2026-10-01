/* EP-01 阶段 4：在自己的错题与笔记里检索（`/api/errors/search`）——从 learn-study.js 拆出。
 *
 * 拆片理由：加入本功能后 `learn-study.js` 达 871 行，超过项目自定判据「单文件 ≤800 行」
 *（闸门 `tests/test_v15_frontend_split.py`）。**纯搬迁、零逻辑改动**。
 * 加载顺序契约：本片属 `learn` 族，接在 `learn-meta-image.js` 之后；
 * 加载期只定义、不执行（所有 DOM 访问都发生在 mkSearch 被调用时）。
 */
/* exported mkSearch, mkSearchClear */

/* EP-01 阶段 4：在自己的错题与笔记里检索（`/api/errors/search`）。
 *
 * 三条与后端同口径的约定（写在这里免得前端"顺手"放宽）：
 * ① **检索面不含教材正文**——后端 `scope` 会明说，前端原样展示，不加"顺便搜教材"；
 * ② `tokens` 为空 = **查询串太短/全是单字**（后端 `len < 2` 一律过滤），
 *    与「搜了但没命中」是两件事，提示文案必须区分——否则用户会以为功能坏了；
 * ③ 片段里的高亮必须**先 esc 再包 `<mark>`**（token 也 esc）——
 *    直接往未转义文本里插标签就是 XSS。
 */
function mkSearchClear() {
  const inp = $("mk_search");
  if (inp) inp.value = "";
  const box = $("mk_search_results");
  if (box) box.innerHTML = "";
  const clr = $("btn_mk_search_clear");
  if (clr) clr.style.display = "none";
  const meta = $("mk_search_meta");
  if (meta) meta.textContent = "";
}

function _mkHighlight(text, tokens) {
  let out = esc(text);
  // 长 token 优先，避免短 token 先命中把长 token 切碎
  for (const t of [...tokens].sort((a, b) => b.length - a.length)) {
    const et = esc(t);
    if (!et) continue;
    out = out.split(et).join(`<mark>${et}</mark>`);
  }
  return out;
}

async function mkSearch() {
  const inp = $("mk_search");
  const box = $("mk_search_results");
  const meta = $("mk_search_meta");
  const btn = $("btn_mk_search");
  if (!inp || !box) return;
  const q = (inp.value || "").trim();
  if (!q) { mkSearchClear(); return; }
  const subject = $("mk_subject") ? $("mk_subject").value : "";
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "搜索中…"; }
  box.innerHTML = `<div class="hint"><span class="spin"></span>检索中…</div>`;
  try {
    const r = await api("/api/errors/search?q=" + encodeURIComponent(q)
      + "&subject=" + encodeURIComponent(subject));
    const clr = $("btn_mk_search_clear");
    if (clr) clr.style.display = "";
    if (!r.tokens || !r.tokens.length) {
      // ③ 区分「没搜」与「没命中」
      box.innerHTML = `<div class="hint">查询词太短（单个字会命中太多）——请至少输入两个字，例如「湿啰音」。</div>`;
      if (meta) meta.textContent = "";
      return;
    }
    if (meta) {
      meta.textContent = `命中 ${r.count} 条 · 分词：${r.tokens.join(" / ")} · 范围：${r.scope}`;
    }
    if (!r.count) {
      box.innerHTML = `<div class="hint">没有命中。检索范围是<b>你自己的错题与笔记</b>`
        + `（不含教材正文）；缩写暂不支持（如「心衰」搜不到「心力衰竭」——需要别名表）。</div>`;
      return;
    }
    const rows = r.items.map(it => `
      <div class="mk-row" style="cursor:default">
        <div class="mk-q">${_mkHighlight(it.question || it.snippet, r.tokens)}</div>
        <div class="mk-meta">
          <span class="tag">${esc(it.error_tag || "未归类")}</span>
          ${esc(it.subject || "")}${it.chapter ? " · " + esc(it.chapter) : ""}${it.round ? " · " + esc(it.round) : ""}
          <span class="hint">命中：${esc((it.matched_fields || []).join("/"))} · 覆盖率 ${Math.round((it.coverage || 0) * 100)}%</span>
        </div>
        <div class="hint" style="margin-top:3px">…${_mkHighlight(it.snippet, r.tokens)}…</div>
      </div>`).join("");
    box.innerHTML = `<div class="mk-group" style="margin-bottom:10px">
        <div class="mk-group-title">检索结果<span class="mk-group-count">${r.count}</span></div>
        ${rows}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint">检索失败：${esc(e.message || e)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}


window.mkSearch = mkSearch; window.mkSearchClear = mkSearchClear;
