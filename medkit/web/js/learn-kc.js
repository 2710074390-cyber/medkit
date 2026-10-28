/* exported kcPanelLoad, kcMapInit, kcMapLoad, kcMapConfirm, kcMapUnset, kcBackfillRun, kcStateOf */
/* v0.22 阶段 5：学习者模型的**渲染层**（接口在 kc-api.js，零映射）。

   ## 两块视图

   1. `#kc_panel`（「今天」页侧栏）：知识点掌握 + 自评校准。
      —— 回答「我到底掌握了什么、我对自己水平的判断准不准」。
   2. `#kc_map_card`（「设置 › 进阶功能」）：题目 ↔ 知识点映射确认台。
      —— 回答「系统把这些题挂对考点了吗」。**必须人工确认**：只让 exact 自动确认。

   ## 三条渲染原则

   - **存在才渲染**：接口没给的字段不占位、不显示「undefined」；
   - **数字必须可解释**：`p_mastery` 一定带「N 次 N 对」的原始计数，不单独甩一个百分比；
   - **空态说人话**：没有数据时告诉用户「去练几道题」，不显示 0% 冒充「掌握度 0」。 */

/* ---------------- 一、知识点掌握 + 校准（「今天」页） ---------------- */
async function kcPanelLoad() {
  const box = $("kc_state_box");
  const meta = $("kc_meta");
  if (!box || !kcAvailable()) return;
  try {
    const ov = await kcOverview({});
    const st = await kcStateList({ order: "priority", limit: 8 });
    const cal = await kcCalibration({ days: 30 });
    if (meta) {
      meta.textContent = ov.total_kcs
        ? `已练 ${ov.tracked_kcs} / 考点 ${ov.total_kcs} · 今日到期 ${ov.due_today}`
        : "";
    }
    box.innerHTML = _kcStateHTML(ov, st) + _kcCalHTML(cal);
  } catch (e) {
    box.innerHTML = /功能「kc」|404/.test(e.message || "")
      ? '<div class="hint">知识点模型已在服务端关闭（features.kc）</div>'
      : `<div class="hint">读取失败：${esc(e.message)}</div>`;
  }
}

function kcStateOf(p) {
  if (p >= 0.85) return "mastered";
  if (p >= 0.65) return "solid";
  if (p >= 0.40) return "shaky";
  return "weak";
}

const _KC_STATE_LABEL = { weak: "薄弱", shaky: "不稳", solid: "较熟", mastered: "已掌握" };
/* 状态 → CSS 类名（**字面量写出**，不拼字符串）：
   拼出来的类名（如 `kc-${state}`）在 CSS 里找不到对应定义，样式会静默失效；
   而且「CSS 孤儿类」守卫按字面量扫描，拼写就查不到 ⇒ 守卫会误报「没人用」，
   或者更糟：真的没人用也没人知道。 */
const _KC_STATE_CLASS = { weak: "kc-weak", shaky: "kc-shaky", solid: "kc-solid", mastered: "kc-mastered" };

function _kcStateHTML(ov, st) {
  const items = (st && st.items) || [];
  if (!items.length) {
    const why = ov && ov.total_kcs
      ? `还没有作答记录——去「刷题」练几道，这里就会长出掌握度。`
      : "还没有可用的知识点（先在自己的题库里做题，或导入大纲）。";
    return `<div class="hint">${why}</div>`;
  }
  const rows = items.map(it => {
    const p = Number(it.p_mastery || 0);
    const pct = Math.round(p * 100);
    const state = it.state || kcStateOf(p);
    const att = Number(it.attempts || 0);
    const ok = Number(it.correct || 0);
    return `<div class="kc-row">
        <div class="kc-name" title="${esc(it.item || it.id || "")}（${esc(_KC_STATE_LABEL[state] || state)}）"
          >${esc(it.item || it.id || "")}</div>
        <div class="kc-bar" role="img"
          aria-label="掌握度 ${pct}%（${esc(_KC_STATE_LABEL[state] || state)}；${att} 次作答，${ok} 次正确）">
          <span class="kc-fill ${esc(_KC_STATE_CLASS[state] || "kc-weak")}" style="width:${pct}%"></span>
        </div>
        <div class="kc-num"><span class="kc-p">${pct}%</span>
          <span class="hint">${att} 次 ${ok} 对</span></div>
      </div>`;
  }).join("");
  const dist = ov && ov.by_state
    ? `薄弱 ${ov.by_state.weak} · 不稳 ${ov.by_state.shaky} · 较熟 ${ov.by_state.solid} · 已掌握 ${ov.by_state.mastered}`
    : "";
  return `${dist ? `<div class="hint" style="margin-bottom:var(--space-2)">${dist}</div>` : ""}
    <div class="kc-list">${rows}</div>`;
}

/* 校准：自评 1..5 各桶的**实际正确率**。柱子越高说明这批人（你自己）答得越准。
   理想情况下「很有把握」的桶正确率应明显高于「没把握」的桶；
   如果两者差不多，说明自评没有区分力（过度自信或过度谨慎）。 */
function _kcCalHTML(cal) {
  if (!cal || !cal.n) {
    return '<div class="hint" style="margin-top:var(--space-3)">作答前给自己打个「把握分」，这里会画出你的校准曲线。</div>';
  }
  const buckets = cal.buckets || [];
  const W = 260, H = 96, BASE = 78, MAXH = 62, GAP = 8;
  const bw = (W - GAP * (buckets.length + 1)) / Math.max(buckets.length, 1);
  const bars = buckets.map((b, i) => {
    const x = GAP + i * (bw + GAP);
    const h = Math.round(MAXH * Number(b.accuracy || 0));
    const y = BASE - h;
    const label = `${b.confidence}`;
    return `<rect x="${x.toFixed(1)}" y="${y}" width="${bw.toFixed(1)}" height="${h}"
              fill="var(--accent)" opacity="${b.n ? 0.85 : 0.25}" rx="3"></rect>
            <text x="${(x + bw / 2).toFixed(1)}" y="${(BASE + 13)}" text-anchor="middle"
              font-size="10" fill="var(--text-2)">${label}</text>
            ${b.n ? `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 3)}" text-anchor="middle"
              font-size="9" fill="var(--text-2)">${Math.round(Number(b.accuracy) * 100)}%</text>` : ""}`;
  }).join("");
  const brier = cal.insufficient ? "样本不足" : Number(cal.brier).toFixed(3);
  const desc = buckets.map(b => `自评 ${b.confidence}：${b.n} 题，正确 ${Math.round(Number(b.accuracy) * 100)}%`).join("；");
  return `<div style="margin-top:var(--space-4)">
      <div class="row" style="justify-content:space-between;align-items:baseline">
        <b style="font-size:var(--fs-3)">自评校准</b>
        <span class="hint">${cal.insufficient ? `样本不足（${cal.n}/10）` : `Brier ${brier}`}</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img"
        aria-label="自评把握度与实际正确率对照柱状图：${esc(desc)}">
        <line x1="0" y1="${BASE}" x2="${W}" y2="${BASE}" stroke="var(--line-ctl)" stroke-width="1"></line>
        ${bars}
      </svg>
      <div class="hint">横轴＝作答前的把握分（1 没把握 → 5 很有把握）；柱顶是**实际正确率**。</div>
    </div>`;
}

/* ---------------- 二、映射确认台（「设置 › 进阶功能」） ---------------- */
const _kcMap = { status: "pending", offset: 0, limit: 10, total: 0, loaded: false };

function kcMapInit() {
  if (!kcAvailable()) return;
  const card = $("kc_map_card");
  if (!card) return;
  if (_kcMap.loaded) return;     // 已初始化过：切页签回来不重复拉
  _kcMap.loaded = true;
  // 事件委托：列表是重绘的，绑在容器上一次即可（每行不重复绑）
  const box = $("kc_map_body");
  if (box) {
    box.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-kc-confirm],[data-kc-unset]");
      if (!btn) return;
      if (btn.hasAttribute("data-kc-confirm")) {
        kcMapConfirm(btn.dataset.item, btn.dataset.kc);
      } else {
        kcMapUnset(btn.dataset.item);
      }
    });
  }
  kcMapLoad(0);
}

async function kcMapLoad(offset) {
  if (!kcAvailable()) return;
  const box = $("kc_map_body");
  const meta = $("kc_map_meta");
  if (!box) return;
  _kcMap.offset = Math.max(0, Number(offset) || 0);
  box.innerHTML = `<div class="hint"><span class="spin"></span>读取中…</div>`;
  try {
    const r = await kcItems({ status: _kcMap.status, limit: _kcMap.limit, offset: _kcMap.offset });
    _kcMap.total = r.total || 0;
    if (meta) meta.textContent = `共 ${r.total} 题 · 第 ${_kcMap.offset + 1}–${Math.min(_kcMap.offset + _kcMap.limit, r.total)} 题`;
    box.innerHTML = (r.items || []).map(_kcMapRowHTML).join("")
      || `<div class="hint">没有符合条件的题。${_kcMap.status === "pending" ? "（「待确认」为空说明规则回填已全部人工处理过）" : ""}</div>`;
    const pager = $("kc_map_pager");
    if (pager) {
      pager.innerHTML = `
        <button class="act gray" onclick="kcMapLoad(${Math.max(0, _kcMap.offset - _kcMap.limit)})"
          ${_kcMap.offset <= 0 ? "disabled" : ""}>上一页</button>
        <button class="act gray" onclick="kcMapLoad(${_kcMap.offset + _kcMap.limit})"
          ${_kcMap.offset + _kcMap.limit >= _kcMap.total ? "disabled" : ""}>下一页</button>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="hint">读取失败：${esc(e.message)}</div>`;
  }
}

function _kcMapRowHTML(it) {
  // 动态参数一律走 data-* + 事件委托：**不要把值拼进内联 onXXX**——
  // `esc()` 只做 HTML 转义，属性值里的实体会先被浏览器解码再交 JS 解析，
  // 单引号复活即注入（仓库已有守卫：test_u_batch3_render_safety）。
  const confirmed = (it.mapped || []).map(m => `
      <span class="kc-chip on">${esc(m.kc_id.slice(0, 6))} <button class="kc-x" title="取消这条确认"
        aria-label="取消确认 ${esc(m.kc_id)}" data-kc-unset data-item="${esc(it.item_id)}">×</button></span>`).join("");
  const cands = (it.candidates || []).map(c => `
      <button class="kc-chip" title="确认为该考点（会替换本题全部已确认映射）"
        data-kc-confirm data-item="${esc(it.item_id)}" data-kc="${esc(c.kc_id)}">
        ${esc(c.item || c.kc_id)} <span class="hint">${Math.round(Number(c.score || 0) * 100)}%</span>
      </button>`).join("");
  return `<div class="kc-maprow">
      <div class="kc-maq">${esc((it.question || "").slice(0, 60))}</div>
      <div class="hint" style="margin-bottom:6px">考点原文：${esc(it.topic || "—")} · 状态：${esc(it.status)}</div>
      <div class="kc-chips">${confirmed}${cands || '<span class="hint">没有候选——可在下方「规则回填」后再看</span>'}</div>
    </div>`;
}

async function kcMapConfirm(itemId, kcId) {
  try {
    await kcMap(itemId, [kcId]);
    toast("已确认映射");
    kcMapLoad(_kcMap.offset);
  } catch (e) { toast(e.message, false); }
}

async function kcMapUnset(itemId) {
  try {
    await kcMap(itemId, []);
    toast("已取消该题的确认映射");
    kcMapLoad(_kcMap.offset);
  } catch (e) { toast(e.message, false); }
}

async function kcBackfillRun(dry) {
  try {
    const r = await kcBackfill({ method: "rule", dry_run: !!dry });
    const msg = dry
      ? `预演：扫 ${r.scanned} 题，匹配 ${r.matched}，其中可自动确认 ${r.would_confirm ?? 0} 条，未匹配 ${r.unmatched}`
      : `已回填：新确认 ${r.created} 条（匹配 ${r.matched} 题，未匹配 ${r.unmatched}）`;
    toast(msg);
    kcMapLoad(0);
  } catch (e) { toast(e.message, false); }
}
