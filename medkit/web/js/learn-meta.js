/* EP-01：元认知视图（校准曲线 / 错误类型热力图 / 跨轮次迁移 / 减法清单）
 *
 * 加载顺序契约（V-15）：本片属 `learn` 族，接在 learn-review.js 之后。
 * ⚠️ **加载期只定义、不执行**——所有 DOM 访问都发生在 mtLoad() 被调用时
 *    （showLearnView("meta") 触发），因此本片不产生任何跨片前向引用。
 *
 * 数据来源：/api/errors/* 系列端点（EP-01 错题归因流水线）。
 * 三条红线的前端体现：
 *   ① 本视图**只读**，不提供任何修改 confidence / my_reasoning 的入口
 *      （这两个字段必须在看答案前填，事后不可补填）；
 *   ② 不展示 / 不请求任何「正确答案」用于比对（那是用户自己的事）；
 *   ③ 每个统计块都能在无数据时给出可读说明，不抛异常、不白屏。
 */
/* exported mtLoad, mtRenderCalibration, mtRenderHeatmap, mtRenderMigration, mtRenderSubtract,
   mtLoadSocratic, mtRenderSocratic, mtStartSocratic, mtRenderSocraticTurn, mtQtypeLabel,
   mtSubmitSocratic, mtCloseSocratic */

// 与后端 metacog.CONF_LEVELS 对齐（5 = 很有把握 … 1 = 纯猜）
const MT_CONF_LABELS = { 5: "很有把握", 4: "比较有把握", 3: "一般", 2: "不太确定", 1: "基本靠猜" };

function mtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  return Math.round(Number(v) * 100) + "%";
}

/** 空态提示（统一措辞，避免各块各写一套） */
function mtEmpty(msg) {
  return `<div class="hint">${esc(msg)}</div>`;
}

/** 主入口：一次拉全量并渲染四张表。切换视图时由 showLearnView("meta") 调用。
 *
 * ⚠️ 只发**一个**请求：`/api/errors/overview` 已一次返回全部统计（calibration /
 *   heatmap / migration / agreement / subtract / counts）。曾误写成并发打 4 个
 *   sub-endpoint——每个内部都会重跑一遍全量 analyze，等于白算 5 遍。
 */
async function mtLoad() {
  const ov = $("mt_overview");
  if (!ov) return;   // 视图未在 DOM 中（老版本 HTML）→ 静默跳过，不报错
  ov.innerHTML = `<div class="hint"><span class="spin"></span>汇总中…</div>`;
  let d;
  try {
    d = await api("/api/errors/overview");
  } catch (e) {
    const msg = "统计加载失败：" + (e.message || e);
    ["mt_overview", "mt_cal", "mt_heat", "mt_mig", "mt_sub"].forEach(id => {
      const el = $(id);
      if (el) el.innerHTML = mtEmpty(msg);
    });
    return;
  }
  // 各块独立渲染：单块异常不影响其它块（与后端 fail-soft 口径一致）
  mtGuard("mt_overview", () => mtRenderOverview($("mt_overview"), d));
  mtGuard("mt_cal", () => mtRenderCalibration($("mt_cal"), d.calibration));
  mtGuard("mt_heat", () => mtRenderHeatmap($("mt_heat"), d.heatmap));
  mtGuard("mt_mig", () => mtRenderMigration($("mt_mig"), d.migration));
  mtGuard("mt_sub", () => mtRenderSubtract($("mt_sub"), d.subtract));
}

/** 单块渲染保护：渲染报错只降级该块，绝不让整页白屏。 */
function mtGuard(elId, fn) {
  try {
    fn();
  } catch (e) {
    const el = $(elId);
    if (el) el.innerHTML = mtEmpty("该图表渲染失败：" + (e.message || e));
  }
}

/** 总览。字段口径与 `errorpipe.analyze()` 的 `counts` 对齐（cards/events/gated/kp_ids）。 */
function mtRenderOverview(el, d) {
  const c = (d && d.counts) || {};
  const cards = c.cards | 0;
  const cal = (d && d.calibration) || {};
  const rated = cal.rated | 0;
  const unrated = cal.unrated | 0;

  if (!cards) {
    el.innerHTML = mtEmpty("还没有错题数据——先去「错题本」录入几道，或在错题本里批量导入。");
    return;
  }
  const bits = [`错题总数 <b>${cards}</b>`];
  bits.push(`知识点 <b>${c.kp_ids | 0}</b>`);
  bits.push(`已填把握程度 <b>${rated}</b>`);
  if (c.events) bits.push(`追踪记录 <b>${c.events}</b>`);
  let html = `<div class="hint" style="line-height:1.9">${bits.join(" · ")}</div>`;

  if (unrated > 0) {
    html += `<div class="hint" style="margin-top:8px;color:var(--warn)">有 <b>${unrated}</b> 道错题没填「把握程度」。
      下次做错题时先写下把握程度再对答案——这是本页所有分析的前提，<b>事后无法补填</b>（也刻意不提供补填入口）。</div>`;
  }
  if (!rated) {
    html += `<div class="hint" style="margin-top:8px">还没有可用于校准的样本：需要同时有「把握程度」和「是否答对」。</div>`;
  }
  el.innerHTML = html;
}

/**
 * 校准曲线：柱状图（HTML/CSS，零依赖）。
 * 灰虚线 = 理想校准位（自评 N 对应正确率 N/5）。柱子在线上=偏保守，线下=过度自信。
 */
function mtRenderCalibration(el, d) {
  const c = d || {};
  const buckets = (c.buckets || []).slice().reverse();   // 1→5 从左到右
  if (!c.rated) {
    el.innerHTML = mtEmpty("暂无有效样本（需要「把握程度」+「是否答对」都齐全的错题）。");
    mtSetMeta("mt_cal_meta", "");
    return;
  }
  const bars = buckets.map(b => {
    const h = Math.max(2, Math.round(b.accuracy * 100));
    const ideal = Math.round(b.expected * 100);
    const over = b.n > 0 && b.gap < 0;   // 实际低于自评 = 过度自信
    const color = b.low_sample ? "var(--dim)" : (over ? "#f87171" : "#34d399");
    const title = `自评 ${b.confidence}（${MT_CONF_LABELS[b.confidence] || ""}）：${b.correct}/${b.n} 正确，实际 ${mtPct(b.accuracy)}，预期 ${mtPct(b.expected)}`;
    return `<div class="mt-col" title="${esc(title)}">
      <div class="mt-colwrap">
        <div class="mt-ideal" style="bottom:${ideal}%"></div>
        <div class="mt-bar" style="height:${h}%;background:${color}"></div>
      </div>
      <div class="mt-collab">${b.confidence}<br><span class="hint">n=${b.n}</span></div>
    </div>`;
  }).join("");

  const alerts = [];
  if (c.alert && c.alert_msg) alerts.push(`<div class="hint" style="color:var(--warn);margin-top:10px"><b>⚠ 过度自信预警</b>：${esc(c.alert_msg)}</div>`);
  if (c.jol_bias !== null && c.jol_bias !== undefined) {
    const dir = c.jol_bias > 0 ? "偏高（偏乐观）" : "偏低（偏保守）";
    alerts.push(`<div class="hint" style="margin-top:8px">自评与实际的整体偏差：<b>${c.jol_bias > 0 ? "+" : ""}${mtPct(c.jol_bias)}</b>（${dir}）；
      Brier 分数 <b>${c.brier === null ? "—" : c.brier}</b>（越低越准）。</div>`);
  }
  if (c.unrated) alerts.push(`<div class="hint" style="margin-top:6px">另有 <b>${c.unrated}</b> 道未填把握程度，未计入。</div>`);

  el.innerHTML = `<div class="mt-chart">${bars}</div>
    <div class="hint" style="text-align:center;margin-top:4px">把握程度（1 = 基本靠猜 → 5 = 很有把握）· 灰虚线 = 理想校准位</div>
    ${alerts.join("")}`;
  mtSetMeta("mt_cal_meta", `${c.rated} 道有效样本`);
}

/** 错误类型 × 科目 热力图（表格形态，浅色/暗色都用背景透明度表达强弱） */
function mtRenderHeatmap(el, d) {
  const h = d || {};
  const tags = h.tags || [];
  const rows = h.rows || [];
  if (!rows.length || !tags.length) {
    el.innerHTML = mtEmpty("暂无带标签的错题。录入时选一个「错误类型」，或跑一次 AI 归因。");
    mtSetMeta("mt_heat_meta", "");
    return;
  }
  const max = Math.max(1, ...rows.flatMap(r => tags.map(t => (r.counts || {})[t] || 0)));
  const head = tags.map(t => `<th title="${esc(t)}">${esc(t.slice(0, 2))}</th>`).join("");
  const body = rows.map(r => {
    const cells = tags.map(t => {
      const n = (r.counts || {})[t] || 0;
      const a = n ? (0.12 + 0.55 * (n / max)).toFixed(2) : 0;
      const style = n ? `background:rgba(248,113,113,${a});font-weight:600` : "color:var(--dim)";
      return `<td style="${style}" title="${esc(r.subject)} · ${esc(t)}：${n} 道">${n || ""}</td>`;
    }).join("");
    const adv = r.advice ? `<div class="hint" style="margin-top:4px;color:var(--accent2)">${esc(r.advice)}</div>` : "";
    return `<tr><th class="mt-rowh">${esc(r.subject)}<span class="hint"> · ${r.total}</span>${adv}</th>${cells}</tr>`;
  }).join("");

  let foot = "";
  if (h.untagged) foot = `<div class="hint" style="margin-top:8px">另有 <b>${h.untagged}</b> 道未归类，未计入。</div>`;
  const fieldName = h.field === "ai_error_tag" ? "AI 归因" : (h.field === "error_tag" ? "你自评的标签" : "你自评优先 / AI 兜底");
  el.innerHTML = `<div class="mt-scroll"><table class="mt-heat"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="hint" style="margin-top:6px">口径：${esc(fieldName)}（列名截取前 2 字，悬停看全称）</div>${foot}`;
  mtSetMeta("mt_heat_meta", `${rows.length} 个科目`);
}

/** 跨轮次迁移矩阵：哪些知识点"一直在换着花样错" */
function mtRenderMigration(el, d) {
  const m = d || {};
  const chains = m.chains || [];
  if (!chains.length) {
    el.innerHTML = mtEmpty("暂无跨轮次数据（需要同一知识点在两个及以上轮次都有错题记录）。");
    mtSetMeta("mt_mig_meta", "");
    return;
  }
  const VC = { "改善": "#34d399", "未变": "#fbbf24", "恶化": "#f87171", "数据不足": "var(--dim)" };
  const rounds = m.rounds || [];
  const head = rounds.map(r => `<th>${esc(r)}</th>`).join("");
  const rows = chains.map(ch => {
    const cells = rounds.map(r => {
      const t = (ch.tags || {})[r];
      return `<td>${t ? esc(t) : '<span class="hint">—</span>'}</td>`;
    }).join("");
    const c = VC[ch.verdict] || "var(--dim)";
    return `<tr>
      <th class="mt-rowh" title="${esc(ch.kp_id)}">${esc((ch.kp_id || "").slice(0, 10))}</th>
      ${cells}
      <td style="color:${c};font-weight:600">${esc(ch.verdict)}</td>
      <td class="hint" style="text-align:left">${esc(ch.msg || "")}</td>
    </tr>`;
  }).join("");

  let stuck = "";
  if (m.stuck_count) {
    stuck = `<div class="hint" style="margin-top:10px;color:var(--warn)"><b>${m.stuck_count}</b> 个知识点跨轮次没有改善——
      说明原来的修补方法无效，建议换个角度重学（如从机制图入手而非背诵）。</div>`;
  }
  el.innerHTML = `<div class="mt-scroll"><table class="mt-heat mt-mig">
      <thead><tr><th></th>${head}<th>判定</th><th>说明</th></tr></thead>
      <tbody>${rows}</tbody></table></div>${stuck}`;
  mtSetMeta("mt_mig_meta", `${chains.length} 个知识点`);
}

/** 减法清单：本周可以不排的章节（与「薄弱点推荐」方向相反，二者互补） */
function mtRenderSubtract(el, d) {
  const s = d || {};
  const skip = s.skip || [];
  const rows = s.rows || [];
  if (!rows.length) {
    el.innerHTML = mtEmpty("暂无足够数据。需要带「科目 / 章节」与作答结果的错题。");
    mtSetMeta("mt_sub_meta", "");
    return;
  }
  let html = "";
  if (!skip.length) {
    html = mtEmpty("本章节数据显示没有明显可以砍掉的部分——继续保持。");
  } else {
    const items = skip.map(r => `<li style="margin:4px 0"><b>${esc(r.subject || "")} · ${esc(r.chapter || "")}</b>
      <span class="hint">（${r.n} 道，正确率 ${mtPct(r.accuracy)}）</span></li>`).join("");
    html = `<div class="hint" style="margin-bottom:6px">以下章节正确率高、题量少，<b>本周不排</b>，把时间让给薄弱项：</div>
      <ul style="margin:0;padding-left:20px">${items}</ul>`;
  }
  if (s.freq_missing) {
    html += `<div class="hint" style="margin-top:8px">提示：未导入真题考频，当前只按「题量 × 正确率」排序。
      导入真题后会把「近三年考频」一并计入（高频章节即使正确率高也不会被砍）。</div>`;
  }
  html += `<div class="hint" style="margin-top:10px">共 ${rows.length} 个章节参与评估，其中 <b>${skip.length}</b> 个建议本周不排。</div>`;
  el.innerHTML = html;
  mtSetMeta("mt_sub_meta", `cut=${s.cut === undefined ? "—" : s.cut}`);
}

/* ---------------------------------------------------------------- 苏格拉底复习（阶段 3）
 *
 * 与其余四块的区别：**这一块会写数据**（开一场复习会话），其余都是只读统计。
 * 故它单独发一个请求（/api/errors/socratic/eligible），且**不并进 mtLoad 的主请求**：
 * 统计是"看板"，复习是"动作"——动作失败不该把看板拖成错误态。
 *
 * 红线在 UI 侧的体现：
 *   ① 只列出**已过闸门且已归因**的错题（后端 eligible 已过滤，前端不自行放宽）；
 *   ② 会话界面**不显示正确答案**——连"看答案"按钮都不给，因为复习的目的
 *      是让学生自己走通，不是核对（答案在错题本里本来就能看）。
 */

/** 加载可复习错题列表（进入视图时调用；独立于 mtLoad 的只读统计）。 */
async function mtLoadSocratic() {
  const el = $("mt_soc");
  if (!el) return;   // 视图不在 DOM → 静默跳过
  el.innerHTML = `<div class="hint"><span class="spin"></span>加载可复习错题…</div>`;
  let d;
  try {
    d = await api("/api/errors/socratic/eligible");
  } catch (e) {
    el.innerHTML = mtEmpty("可复习列表加载失败：" + (e.message || e));
    return;
  }
  try {
    mtRenderSocratic(el, d);
  } catch (e) {
    el.innerHTML = mtEmpty("该列表渲染失败：" + (e.message || e));
  }
}

function mtRenderSocratic(el, d) {
  const items = (d && d.items) || [];
  if (!items.length) {
    el.innerHTML = mtEmpty(
      "暂无可复习的错题。开复习需要同时满足：① 已填「把握程度」和「当时的想法」（看答案前）；" +
      "② 已完成 AI 归因。归因过的错题会出现在这里。");
    mtSetMeta("mt_soc_meta", "");
    return;
  }
  const rows = items.map(r => `<tr>
    <td>${esc(r.subject || "")}</td>
    <td>${esc(r.chapter || "")}${r.topic ? " · " + esc(r.topic) : ""}</td>
    <td><span class="tag">${esc(r.error_tag || "")}</span></td>
    <td class="num">${r.freq ? r.freq : "—"}</td>
    <td>${r.confidence === null || r.confidence === undefined ? "—" : esc(String(r.confidence)) + "/5"}</td>
    <td><button class="act gray" data-mid="${esc(r.id)}" onclick="mtStartSocratic(this)">开始复习</button></td>
  </tr>`).join("");
  el.innerHTML = `<div class="mt-scroll"><table class="mt-table">
    <thead><tr><th>科目</th><th>章节</th><th>错误类型</th><th>真题频次</th><th>自评</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="hint" style="margin-top:8px">按「真题频次」降序——高频考点即使已归因也值得再走一遍。
      复习会用一个提问把您带回当初那个判断，不会直接给解析。</div>`;
  mtSetMeta("mt_soc_meta", `${items.length} 道可复习`);
}

/** 开一场苏格拉底复习：拿到第一问后弹会议面板。 */
async function mtStartSocratic(btn) {
  // RV1：动态值走 data-* + this 传参（不拼进内联事件 JS）。
  const mid = btn && btn.dataset ? btn.dataset.mid : "";
  const panel = $("mt_soc_panel");
  if (!panel) return;
  panel.innerHTML = `<div class="hint"><span class="spin"></span>正在准备第一问…</div>`;
  panel.style.display = "block";
  let d;
  try {
    d = await api("/api/errors/socratic/start", { method: "POST", body: { mistake_id: mid } });
  } catch (e) {
    // 403（闸门）/409（未归因）/502（模型失败）都在这条路径 → 原文展示后端中文说明
    panel.innerHTML = mtEmpty("无法开始复习：" + (e.message || e));
    return;
  }
  mtRenderSocraticTurn(panel, d, null);
}

/** 渲染一轮复习（首问或判分后）。`turn` 为本轮判分结果，首问时为 null。 */
function mtRenderSocraticTurn(panel, d, turn) {
  const sid = d.session && d.session.id;
  if (!sid) { panel.innerHTML = mtEmpty("会话创建失败（无会话 ID）"); return; }
  const q = (d.question !== undefined ? d.question : (d.next_question || {}).text) || "";
  const qtype = (d.type || (d.next_question || {}).type) || "explain";
  let head = "";
  if (turn) {
    const scoreTxt = turn.score === -1 ? "未能评分" : (turn.score + " / 3");
    head = `<div class="hint" style="margin-bottom:6px">
      本轮：<b>${esc(scoreTxt)}</b>
      ${turn.hit_crossroad ? '<span class="mt-ok">· 已回到当初的判断</span>'
        : '<span class="mt-warn">· 还没回到当初那个岔路口</span>'}
      ${turn.redacted ? '· <span class="mt-warn">模型反馈含答案信息，已按红线拦截</span>' : ""}
    </div>`;
    if (turn.gap) head += `<div class="mt-gap">${esc(turn.gap)}</div>`;
    if (turn.hint_allowed) {
      head += `<div class="hint" style="margin-top:6px">已连续 ${turn.stuck} 轮未命中——可以让模型给一个<b>方向性提示</b>（仍不会给答案）。</div>`;
    }
  }
  panel.innerHTML = `${head}
    <div class="mt-q">${esc(q)}</div>
    <textarea id="mt_soc_input" class="mt-soc-input" rows="3"
      placeholder="用自己的话讲一遍你的推理…（不看答案，讲错也没关系）"></textarea>
    <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
      <button class="act" data-sid="${esc(sid)}" onclick="mtSubmitSocratic(this)">提交</button>
      <button class="act gray" onclick="mtCloseSocratic()">结束复习</button>
      <span class="hint" id="mt_soc_type">提问类型：${esc(mtQtypeLabel(qtype))}</span>
    </div>`;
}

const MT_QTYPE_LABELS = {
  explain: "讲清推理过程", apply: "换一个情境", contrast: "与易混概念对比",
  predict: "预测结果", trace: "从结果倒推原因",
};

function mtQtypeLabel(t) { return MT_QTYPE_LABELS[t] || t; }

async function mtSubmitSocratic(btn) {
  const sid = btn && btn.dataset ? btn.dataset.sid : "";
  const panel = $("mt_soc_panel");
  const box = $("mt_soc_input");
  if (!panel || !box) return;
  const text = (box.value || "").trim();
  if (!text) { box.focus(); return; }
  box.disabled = true;
  const prev = panel.innerHTML;
  panel.innerHTML = `<div class="hint"><span class="spin"></span>判分中…</div>`;
  let d;
  try {
    d = await api("/api/errors/socratic/answer", {
      method: "POST", body: { session_id: sid, user_answer: text },
    });
  } catch (e) {
    panel.innerHTML = prev;      // 恢复输入内容，别让用户白打一段字
    const el = $("mt_soc_input");
    if (el) { el.disabled = false; el.value = text; }
    panel.insertAdjacentHTML("afterbegin",
      `<div class="hint mt-warn">提交失败：${esc(e.message || e)}</div>`);
    return;
  }
  mtRenderSocraticTurn(panel, d, d);
}

function mtCloseSocratic() {
  const panel = $("mt_soc_panel");
  if (panel) { panel.style.display = "none"; panel.innerHTML = ""; }
  mtLoadSocratic();   // 刷新列表（刚复习过的仍在列表里，可继续下一道）
}

function mtSetMeta(id, text) {
  const el = $(id);
  if (el) el.textContent = text || "";
}
