/* exported bankDrillConf, _bankRationalesHTML, _bankKcHTML, _bankConfHTML */
/* v0.22 阶段 5：刷题页的「把握分」与「结果区三段」（从 bank-drill.js 拆出）。

   为什么拆：bank-drill.js 一度 835 行，超 U-17 的 800 行上限（守卫 tests/test_v15_frontend_split.py）。
   这两块是**独立可测**的展示逻辑（把握分采集 / 逐选项理由 / 原文引句 / 考点变化），
   拆出去后 bank-drill.js 只剩作答引擎本身。

   加载期前向引用：本文件只定义函数、不在加载期调用别的分片的函数 ⇒ 与 bank-drill.js 无顺序依赖；
   bank-drill.js 在**运行时**调用本文件的函数（经典脚本共享全局作用域，顺序无关）。
*/
/* ---- v0.22：作答前的自评把握（1..5，可跳过）----
   为什么放在选项上面：单选是「点中即判分」，一旦点下去就没有「作答前」这个时刻了——
   采集入口必须在选项之前，否则这个字段永远拿不到。跳过时 confidence=0（后端归一为未采集），
   **不能因为它没填就不让人做题**。 */
function _bankConfHTML() {
  const cur = _bankDrill.confidence || 0;
  const btn = (n, label) => `<button type="button" class="kc-chip${cur === n ? " on" : ""}"
      role="radio" aria-checked="${cur === n ? "true" : "false"}"
      onclick="bankDrillConf(${n})" title="${label}">${n}</button>`;
  return `<div class="bank-conf" role="radiogroup" aria-label="作答前把握分（1 没把握，5 很有把握；可跳过）"
      style="display:flex;align-items:center;gap:6px;margin:0 0 var(--space-2);flex-wrap:wrap">
      <span class="hint" style="flex:none">把握？</span>
      ${btn(1, "完全没把握")}${btn(2, "有点虚")}${btn(3, "一般")}${btn(4, "比较有把握")}${btn(5, "很有把握")}
      <button type="button" class="kc-x" title="不采集（跳过）" aria-label="跳过把握分"
        onclick="bankDrillConf(0)">跳过</button>
      <span class="hint" style="margin-left:auto">${cur ? `已选 ${cur}` : "可跳过"}</span>
    </div>`;
}

function bankDrillConf(n) {
  const d = _bankDrill;
  if (d.runmode === _BANK_RECITE || d.graded) return;
  d.confidence = Math.max(0, Math.min(5, Number(n) || 0));
  const box = $("bank_conf_box");
  if (box) box.innerHTML = _bankConfHTML();
}


/* v0.22 阶段 4：逐选项理由（有才渲染；旧题没有这个字段 ⇒ 返回空串，版式与以前一致）。 */
function _bankRationalesHTML(r) {
  const rats = r.option_rationales || {};
  const keys = Object.keys(rats).sort();
  if (!keys.length) return "";
  const mics = r.misconceptions || {};
  const rows = keys.map(k => `<li><b>${esc(k)}</b> ${esc(rats[k])}` +
    (mics[k] ? ` <i class="bank-mis">误解：${esc(mics[k])}</i>` : "") + "</li>").join("");
  return `<div style="margin-top:var(--space-2)"><b>逐选项</b><ul class="bank-rat">${rows}</ul></div>`;
}

/* v0.22 阶段 3：这道题让哪些知识点动了、动了多少（未映射 ⇒ 明说「不计入掌握度」）。 */
function _bankKcHTML(r) {
  const kc = r.kc;
  if (!kc) return "";
  if (!kc.mapped) {
    return '<div class="hint" style="margin-top:var(--space-2)">这题还没映射到考点，暂不计入掌握度'
      + '（可在「设置 › 进阶功能 › 知识点映射」里确认）</div>';
  }
  const rows = (kc.kcs || []).map(k => {
    const dlt = Number(k.delta || 0);
    const sign = dlt > 0 ? "+" : "";
    const color = dlt > 0 ? "var(--good)" : (dlt < 0 ? "var(--bad)" : "var(--text-2)");
    return `<span class="kc-chip on">掌握度 ${Math.round(Number(k.p_mastery) * 100)}%`
      + ` <span style="color:${color}">${sign}${Math.round(dlt * 100)}</span>`
      + ` <span class="hint">${k.attempts} 次 ${k.correct} 对</span></span>`;
  }).join("");
  return `<div style="margin-top:var(--space-2)"><b>考点</b> <span class="kc-chips">${rows}</span></div>`;
}

