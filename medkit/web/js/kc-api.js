/* exported KC_FLAG, kcAvailable, kcItems, kcMap, kcBackfill, kcStateList, kcCalibration, kcOverview, kcEdges */
/* v0.22 阶段 5：`/api/kc/*` 的**唯一**调用层。

   ## 契约铁律（实施方案 §3.4 冻结，"避免反复返工"的核心机制）

   - 字段名**全小写下划线**，后端 JSON 与前端 JS **完全同名**；
   - 本文件**不做任何重命名映射**（`p_mastery` 就叫 `p_mastery`，不翻译成 `mastery`）；
   - 新增字段一律可选，渲染层「存在才渲染」。

   这一层只做三件事：拼 query、调 `api()`、把「服务端整组下线」翻译成 `KC_FLAG=false`。

   ## 为什么 404 要当"不可用"而不是"错误"

   `features.kc=false` 时整组端点返回 404（服务端下线，前端入口同批隐藏）。
   如果把它当普通错误弹 toast，用户每次进「今天」都会看到一条红字——
   那不是提示，是噪声。降级成「本域不可用 ⇒ 隐藏面板」才是诚实的处理。 */

let KC_FLAG = true;

function kcAvailable() { return KC_FLAG !== false; }

async function _kcGet(path, params) {
  const qs = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== "" && v !== null && v !== undefined) qs.set(k, String(v));
  });
  const url = qs.toString() ? `${path}?${qs.toString()}` : path;
  try {
    return await api(url);
  } catch (e) {
    // 「功能「kc」已在服务端禁用」/ 纯 404 ⇒ 整域下线，后续调用直接短路
    if (/功能「kc」/.test(e.message || "") || /\b404\b/.test(e.message || "")) KC_FLAG = false;
    throw e;
  }
}

async function _kcPost(path, body) {
  try {
    return await api(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (e) {
    if (/功能「kc」/.test(e.message || "")) KC_FLAG = false;
    throw e;
  }
}

/* 题目 + 映射状态（映射确认台）。status: all | unmapped | pending | mapped */
function kcItems(params) { return _kcGet("/api/kc/items", params); }
/* 唯一写入口：本次 kc_ids 即该题最终的全部**已确认**映射（空数组 = 全部取消） */
function kcMap(itemId, kcIds) {
  return _kcPost(`/api/kc/items/${encodeURIComponent(itemId)}/map`, { kc_ids: kcIds });
}
/* 规则回填（幂等、可重入；默认 dry_run=true 先看报告） */
function kcBackfill(opts) { return _kcPost("/api/kc/backfill", opts || {}); }
/* 知识点状态（掌握概率 + 优先级 + 支架等级 + 到期） */
function kcStateList(params) { return _kcGet("/api/kc/state", params); }
/* 自评置信度 → 实际正确率 + Brier（insufficient=true 时前端显示「样本不足」） */
function kcCalibration(params) { return _kcGet("/api/kc/calibration", params); }
/* 总览：覆盖度 / 状态分布 / 今日到期 / 校准 */
function kcOverview(params) { return _kcGet("/api/kc/overview", params); }
/* 知识点之间的关系（前置 / 关联 / 易混） */
function kcEdges(params) { return _kcGet("/api/kc/edges", params); }
