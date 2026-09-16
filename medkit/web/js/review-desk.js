/* exported BLOOM_SEGS, RATIO_SEGS, autoMap, b, bar, barW, base, baseUrl, baseUrlDirty, bindSegBar, bloomSum, body, box, btn, cap, chosen, createToken, cum, d, doPickProvider, el, estT, fillModelSelect, gen, got, grabOff, h, handle, html, i, in1, inp, j, k, keysR, labelTxt, lbl, leftOk, leftPos, letters, list, loadConfig, loadKeys, loadSearchOptions, manual, modelValue, move, moved, mu, normAnswer, note, nv, old, pct, pickProvider, pid, prefix, prev, prov, provNote, qc, r, r0, ratioSum, rect, renderSegBar, rightOk, s, savedGen, savedIds, savedQc, scheduleReady, searchBackends, seg, seg1, seg2, segEl, segVals, sel, setW, si, startX, sum, syncWsManual, t, target, total, u, up, updateWsNote, v, v1, validBaseUrl, vals, value, vs, wsc */
/* U-17：跨文件 / 内联 HTML 处理器引用的顶层声明（经典脚本共享全局作用域）*/
  /* U-17：跨文件/内联 HTML 引用的顶层声明（经典脚本共享全局作用域）*/
/* ---- ① 服务商 */
let createToken = "";   // R3-08：建课题意图令牌（双击/双标签幂等；失败保留供重试复用）
/* R3-16：统一选项字母标签（ABCDEFGHIJ 前 n 位，n 上限 10）——试出/审核台/复制同口径 */
function letters(n) { return "ABCDEFGHIJ".slice(0, Math.max(0, Math.min(parseInt(n, 10) || 0, 10))); }
/* C-11：答案归一化第三口径——去空格并剥离中英文逗号/顿号/分号（B,D → BD） */
function normAnswer(s) { return String(s || "").replace(/[\s,，、;；]+/g, "").toUpperCase(); }
function modelValue(id) {
  const manual = $(id + "_manual");
  if (manual.style.display !== "none" && manual.value.trim()) return manual.value.trim();
  const v = $(id).value;
  return (v && v !== "__manual__") ? v : (manual.value.trim() || "");
}
function fillModelSelect(selId, models, saved, hintId) {
  const sel = $(selId), manual = $(selId + "_manual");
  manual.style.display = "none"; manual.value = "";
  sel.innerHTML = "";
  const list = (models || []).slice();
  let value = saved || "";
  if (list.length) {
    list.forEach(m => sel.append(new Option(m, m)));
    if (saved && !list.includes(saved)) {
      // A-新5：已保存模型不在新列表 → 作为附加 option（标注已保存）追加并保持选中，不得替换为 list[0]
      sel.append(new Option(`${saved}（已保存）`, saved));
    }
    value = saved || list[0];
  } else if (value) {
    sel.append(new Option(`${value}（已保存，待获取列表）`, value));
  } else {
    sel.append(new Option("（点击「获取模型列表」自动填充最新）", ""));
  }
  sel.append(new Option("手动输入…", "__manual__"));
  sel.value = value || "";
  if (hintId) $(hintId).textContent = list.length ? `已加载 ${list.length} 个模型，默认选最新` : "";
}
["model_gen", "model_qc"].forEach(id => {
  $(id).addEventListener("change", () => {
    const manual = $(id + "_manual");
    if ($(id).value === "__manual__") { manual.style.display = "block"; manual.focus(); manual.placeholder = "手动输入模型名（如 deepseek-v4-flash）"; }
    else manual.style.display = "none";
  });
});
async function loadConfig() {
  const [c, p] = await Promise.all([api("/api/config"), api("/api/providers")]);
  state.cfg = c; state.providers = p.providers || [];
  applyFeatures(c);   // IMP-02：合并服务端 feature flags（缺省全开）
  if (c.config_corrupt) {
    toast("检测到配置文件损坏：已备份并恢复默认设置，请重新选择服务商并保存配置", false);
  }
  state.provider = c.provider || "";
  const keysR = await api("/api/keys").catch(() => ({ keys: [] }));
  const savedIds = new Set((keysR.keys || []).filter(k => k.saved).map(k => k.id));
  const box = $("provs"); box.innerHTML = "";
  p.providers.forEach(pr => {
    const d = document.createElement("button");
    d.type = "button";
    d.className = "prov" + (c.provider === pr.id ? " on" : "");
    d.dataset.id = pr.id;
    d.setAttribute("role", "radio");
    d.setAttribute("aria-checked", c.provider === pr.id ? "true" : "false");
    d.innerHTML = `<b>${esc(pr.name)}</b><span>${esc(pr.note || "")}</span>
      <span class="stag ${pr.search_support ? "builtin" : "external"}">${pr.search_support ? "自带网络搜索 ✓" : (pr.id === "custom" ? "自定义端点 · 需外部搜索" : "需外部搜索（不自带联网工具）")}</span>
      ${(savedIds.has(pr.id) || (c.provider === pr.id && c.api_key_masked)) ? `<span class="stag" style="color:var(--good);border-color:var(--good)">已配置 Key ✓</span>` : ""}
      ${c.provider === pr.id && c.model_gen ? `<span class="stag">模型：${esc(c.model_gen)}</span>` : ""}
      ${pr.register_url ? `<a class="provlink" href="${esc(pr.register_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">官网注册 ↗</a>` : ""}`;
    d.onclick = () => pickProvider(pr);
    box.appendChild(d);
  });
  $("base_url").value = c.base_url || "";
  fillModelSelect("model_gen", [], c.model_gen, "gen_hint");
  fillModelSelect("model_qc", [], c.model_qc || c.model_gen, "");
  $("keymask").textContent = c.api_key_masked ? "已保存：" + c.api_key_masked : "未保存";
  const mu = c.mineru || {};
  $("minerumask").textContent = mu.api_key_masked ? "已保存：" + mu.api_key_masked : "未保存（使用免 Token 轻量 API）";
  $("t_autoocr").checked = mu.auto_ocr !== false;
  const t = $("t_ocr"); if (t) t.checked = mu.auto_ocr !== false;
  const wsc = c.web_search || {};
  $("ws_key").placeholder = wsc.api_key_masked ? ("已保存：" + wsc.api_key_masked) : "留空 = 保留已保存的 Key";
  $("t_web").checked = !!wsc.enabled;
  $("t_web_trusted").checked = !!wsc.trusted_only;
  $("ws_trusted_domains").value = (wsc.trusted_domains || []).join(", ");
  syncWsManual();
  loadSearchOptions().then(() => { $("ws_backend").value = wsc.backend || "auto"; updateWsNote(); syncWsManual(); });
  loadPresets().catch(() => {});
  loadKeys().catch(() => {});
}
/* v0.5.1：API Key 管理（多服务商存档，仿 Cherry Studio 服务商独立配置） */
async function loadKeys() {
  const box = $("keymgmt");
  const r = await api("/api/keys");
  if (!r.keys.some(k => k.saved)) {
    box.innerHTML = '<div class="hint" style="margin-top:6px">尚无存档——在上方配置任一并「保存配置」后，这里会自动出现（当前生效的 Key 也会归档）。</div>';
    return;
  }
  box.innerHTML = r.keys.filter(k => k.saved).map(k => `
    <div class="keyrow${k.active ? " on" : ""}">
      <b>${esc(k.name)}${k.active ? '<span class="ktag">使用中</span>' : ""}</b>
      <span class="kmask">${esc(k.key_masked)}</span>
      <button class="act gray mini" data-use="${esc(k.id)}" ${k.active ? "disabled" : ""}>${k.active ? "当前" : "使用"}</button>
      ${k.active ? "" : `<button class="act gray mini" data-del="${esc(k.id)}">删除</button>`}
      <span class="kmeta">${esc(k.base_url)}${k.model_gen ? " · " + esc(k.model_gen) : ""}${k.model_qc && k.model_qc !== k.model_gen ? " / " + esc(k.model_qc) : ""}${k.active ? " · 当前生效配置（切换服务商时自动归档，届时可删）" : ""}</span>
    </div>`).join("");
  box.querySelectorAll("[data-use]").forEach(b => b.onclick = async () => {
    const pid = b.dataset.use;
    const k = r.keys.find(x => x.id === pid);
    if (!k) return;
    try {
      b.disabled = true; b.textContent = "切换中…";
      await api("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: pid, base_url: k.base_url, api_key: "",
          model_gen: k.model_gen || "", model_qc: k.model_qc || "",
          web_search_enabled: $("t_web").checked, web_search_api_key: "",
          web_search_backend: $("ws_backend").value,
          mineru_api_key: "", mineru_auto_ocr: $("t_autoocr").checked }) });
      // A-新2：切换后清空 api_key 输入框——否则刚填的 A 家 Key 未保存就切 B 再保存会被归档到 B 名下
      if ($("api_key")) $("api_key").value = "";
      toast(`已切换到 ${k.name}（存档 Key 已生效；输入框已清空，如需换 Key 请重新粘贴）`);
      loadConfig();
    } catch (e) { toast(e.message, false); b.disabled = false; b.textContent = "使用"; }
  });
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
    const pid = b.dataset.del;
    const k = r.keys.find(x => x.id === pid);
    confirmModal("删除 Key 存档", `删除「${esc(k.name)}」的已存档 Key？<br><span class="hint">仅清除该服务商的存档，不影响当前生效配置。</span>`,
      "删除", async () => {
        try { await api("/api/keys/" + encodeURIComponent(pid), { method: "DELETE" }); toast("存档已删除"); loadKeys(); }
        catch (e) { toast(e.message, false); }
      });
  });
}
/* 检索能力标注（本轮新增：DeepSeek 选项 + 自带/需外部 告知） */
let searchBackends = null;
async function loadSearchOptions() {
  try {
    const r = await api("/api/search/backends");
    searchBackends = r;
    const sel = $("ws_backend");
    sel.innerHTML = '<option value="auto">自动匹配（按所选服务商能力）</option>';
    (r.backends || []).forEach(b => {
      const cap = b.builtin === true ? "自带搜索" : b.builtin === false ? "需外部搜索" : "无在线检索";
      sel.append(new Option(`${b.label}（${cap}）`, b.id));
    });
  } catch (e) { toast("检索后端列表加载失败：" + e.message, false); }
}
function updateWsNote() {
  const note = $("ws_note");
  const base = searchBackends ? searchBackends.note : "";
  const prov = (state.providers || []).find(p => p.id === state.provider);
  let provNote = "";
  if (prov) {
    const autoMap = (searchBackends && searchBackends.builtin_backend_by_provider) || {};
    const lbl = autoMap[prov.id];
    const b = (searchBackends && searchBackends.backends || []).find(x => x.id === lbl);
    provNote = prov.search_support
      ? `当前服务商：<b style="color:var(--good)">${esc(prov.name)} 自带网络搜索</b> → 自动匹配「${b ? esc(b.label) : lbl || "自带工具"}」`
      : `当前服务商：<b style="color:var(--warn)">${esc(prov.name)} 端点能力未知</b> → 建议配「博查 Key」或「手动粘贴」`;
  }
  const chosen = $("ws_backend").value;
  const b = (searchBackends && searchBackends.backends || []).find(x => x.id === chosen);
  note.innerHTML = `${esc(base)}<br>${provNote}<br>`
    + (b ? `已选：${esc(b.label)} —— ${esc(b.note)}` : "已选：自动匹配（按所选服务商能力）");
}
$("ws_backend").addEventListener("change", () => { syncWsManual(); updateWsNote(); });
/* A4：接口地址客户端预校验（http/https 或 OpenAI 兼容端点；留空 = 服务商默认） */
function validBaseUrl(v) {
  const s = String(v || "").trim();
  if (!s) return true;
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch (e) { return false; }
}
/* A5：用户手改过 base_url 后切换服务商 → 确认再覆盖（防止静默吞掉自定义端点） */
let baseUrlDirty = false;
$("base_url").addEventListener("input", () => { baseUrlDirty = true; $("base_url").classList.remove("err"); });
function pickProvider(pr) {
  const prev = state.provider;
  if (prev && prev !== pr.id && baseUrlDirty && $("base_url").value.trim()) {
    // A5：手改的 base_url + 不是默认值 → 覆盖前确认（防止静默吞掉自定义端点）
    confirmModal("切换服务商？",
      `<p style="margin:0;color:var(--dim)">你修改过「接口地址」为 <b>${esc($("base_url").value.trim())}</b>。<br>
      切换后将被覆盖为「${esc(pr.name)}」的默认地址（回答可重填）。</p>`,
      "覆盖并切换", () => { baseUrlDirty = false; doPickProvider(pr); }, false);
    return;
  }
  doPickProvider(pr);
}
function doPickProvider(pr) {
  const prev = state.provider;
  document.querySelectorAll(".prov").forEach(x => {
    x.classList.toggle("on", x.dataset.id === pr.id);
    x.setAttribute("aria-checked", x.dataset.id === pr.id ? "true" : "false");
  });
  state.provider = pr.id;
  if (prev && prev !== pr.id) {
    // Key 跟随服务商：换卡片清空输入框，防止把上一家服务商的 Key 存到这一家名下
    if ($("api_key").value.trim()) {
      $("api_key").value = "";
      toast("已切换服务商：请填写「" + pr.name + "」的 Key（原 Key 不通用，输入框已清空）");
    }
    $("keymask").textContent = "已切换到 " + pr.name + "——请填写该服务商的 Key";
  }
  $("base_url").value = pr.base_url || "";
  baseUrlDirty = false;
  scheduleReady();   // R3-10：切换服务商 → 价格口径变化，刷新成本预估
  if (pr.default_model) {
    // A-新4：切服务商不得静默覆盖手填模型名——model_gen 已有非空值时保留，仅空值时填默认模型
    const gen = modelValue("model_gen");
    if (!gen) fillModelSelect("model_gen", [], pr.default_model, "gen_hint");
    const qc = modelValue("model_qc");
    if (!qc || qc === "deepseek-v4-flash" || qc === "glm-5.3" || qc === "qwen-plus" || qc === "deepseek-chat") {
      fillModelSelect("model_qc", [], pr.default_model, "");
    }
  } else {
    fillModelSelect("model_gen", [], "", "gen_hint");
  }
  updateWsNote();
}
$("btn_test").onclick = async () => {
  if (!validBaseUrl($("base_url").value.trim())) {
    toast("接口地址格式不对（需 http:// 或 https:// 开头，或留空用默认）", false);
    $("base_url").classList.add("err"); $("base_url").focus();
    return;
  }
  const btn = $("btn_test"); btn.disabled = true;
  const old = btn.textContent; btn.textContent = "连接中…";
  $("test_result").innerHTML = '<span class="spin"></span>正在连接…';
  try {
    const r = await api("/api/llm/test", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: $("base_url").value.trim(), api_key: $("api_key").value.trim(), model: modelValue("model_gen") }) });
    $("test_result").innerHTML = `<span class="hint ${r.ok ? "good" : "bad"}">${esc(r.msg)}</span>`;
  } catch (e) { $("test_result").innerHTML = `<span class="hint bad">${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = old; }
};
$("btn_models").onclick = async () => {
  if (!validBaseUrl($("base_url").value.trim())) {
    toast("接口地址格式不对（需 http:// 或 https:// 开头，或留空用默认）", false);
    $("base_url").classList.add("err"); $("base_url").focus();
    return;
  }
  const btn = $("btn_models"); btn.disabled = true;
  $("btn_models").innerHTML = '<span class="spin"></span>获取中…';
  try {
    const r = await api("/api/llm/models", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: $("base_url").value.trim(), api_key: $("api_key").value.trim() }) });
    if (r.models && r.models.length) {
      const savedGen = modelValue("model_gen");
      const savedQc = modelValue("model_qc");
      fillModelSelect("model_gen", r.models, savedGen || (await api("/api/config")).model_gen || "", "gen_hint");
      fillModelSelect("model_qc", r.models, savedQc || "", "");
      toast("已加载 " + r.models.length + " 个模型，默认选中最新");
    } else {
      fillModelSelect("model_gen", [], modelValue("model_gen"), "gen_hint");
      toast(r.msg || "未能获取模型列表（可「手动输入」）", false);
    }
  } catch (e) { toast(e.message, false); }
  finally { btn.disabled = false; btn.textContent = "获取模型列表"; }
};
$("btn_save").onclick = async () => {
  if (!validBaseUrl($("base_url").value.trim())) {
    toast("接口地址格式不对（需 http:// 或 https:// 开头，或留空用默认地址）", false);
    $("base_url").classList.add("err"); $("base_url").focus();
    return;
  }
  try {
    const body = {
      provider: state.provider || "deepseek",
      base_url: $("base_url").value.trim(),
      api_key: $("api_key").value.trim(),
      model_gen: modelValue("model_gen"),
      model_qc: modelValue("model_qc"),
      web_search_enabled: $("t_web").checked,
      web_search_api_key: $("ws_key").value.trim(),
      web_search_backend: $("ws_backend").value,
      web_search_trusted_only: $("t_web_trusted").checked,
      web_search_trusted_domains: $("ws_trusted_domains").value.trim(),
      mineru_api_key: $("mineru_key").value.trim(),
      mineru_auto_ocr: $("t_autoocr").checked,
    };
    const r = await api("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast(r.key_encrypted === false
      ? "配置已保存（本机 ~/.medkit/config.json；⚠️ 当前环境未能 DPAPI 加密 Key，已明文保存——请注意本机安全）"
      : "配置已保存（本机 ~/.medkit/config.json，Key 已加密）");
    $("api_key").value = ""; $("mineru_key").value = ""; baseUrlDirty = false; loadConfig();
    updateReady();   // R3-10：保存配置（可能换服务商/模型）后立即刷新成本预估
  } catch (e) { toast(e.message, false); }
};
$("btn_mineru_test").onclick = async () => {
  const btn = $("btn_mineru_test"); btn.disabled = true;
  const old = btn.textContent; btn.textContent = "测试中…";
  $("mineru_test_result").innerHTML = '<span class="spin"></span>测试 OCR 服务…';
  try {
    const r = await api("/api/mineru/test", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: $("mineru_key").value.trim() }) });
    $("mineru_test_result").innerHTML = `<span class="hint ${r.ok ? "good" : "bad"}">${esc(r.msg)}</span>`;
  } catch (e) { $("mineru_test_result").innerHTML = `<span class="hint bad">${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = old; }
};

/* ---- ① 网络检索设置 */
function syncWsManual() {
  $("ws_manual_wrap").style.display = $("ws_backend").value === "manual" ? "block" : "none";
}
$("btn_ws_test").onclick = async () => {
  const btn = $("btn_ws_test"); btn.disabled = true;
  const old = btn.textContent; btn.textContent = "测试中…";
  $("ws_test_result").innerHTML = '<span class="spin"></span>测试检索后端…';
  try {
    const r = await api("/api/search/test", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend: $("ws_backend").value, api_key: $("ws_key").value.trim() }) });
    $("ws_test_result").innerHTML = `<span class="hint ${r.ok ? "good" : "bad"}">${esc(r.msg)}</span>`;
  } catch (e) { $("ws_test_result").innerHTML = `<span class="hint bad">${esc(e.message)}</span>`; }
  finally { btn.disabled = false; btn.textContent = old; }
};
$("btn_ws_save").onclick = async () => {
  // A-新3：检索设置保存补齐 base_url 预校验（与「保存配置」同口径；自定义端点必填非空）
  const baseUrl = $("base_url").value.trim();
  if (!validBaseUrl(baseUrl)) {
    toast("接口地址格式不对（需 http:// 或 https:// 开头，或留空用默认地址）", false);
    $("base_url").classList.add("err"); $("base_url").focus();
    return;
  }
  if (state.provider === "custom" && !baseUrl) {
    toast("自定义端点必须填写接口地址（base_url）", false);
    $("base_url").classList.add("err"); $("base_url").focus();
    return;
  }
  try {
    const body = {
      provider: state.provider || "deepseek",
      base_url: baseUrl,
      api_key: $("api_key").value.trim(),
      model_gen: modelValue("model_gen"),
      model_qc: modelValue("model_qc"),
      web_search_enabled: $("t_web").checked,
      web_search_api_key: $("ws_key").value.trim(),
      web_search_backend: $("ws_backend").value,
      web_search_trusted_only: $("t_web_trusted").checked,
      web_search_trusted_domains: $("ws_trusted_domains").value.trim(),
      mineru_api_key: "",
      mineru_auto_ocr: $("t_autoocr").checked,
    };
    const got = await api("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    // 后端单独走 config 的 web_search.backend 字段：PUT 不覆盖 backend，直接透传当前选择
    state.cfg = got;
    toast("网络检索设置已保存（默认关；项目内还需开启「网络检索」开关并设定引用配额）");
    if ($("api_key")) $("api_key").value = "";   // A-新3：保存成功后清空 api_key 输入框
    loadConfig();
  } catch (e) { toast(e.message, false); }
};

/* ---- ② 配比实时合计 + 可视化配比条（题型 / Bloom 共用） ---- */
const RATIO_SEGS = [["A1", "r_a1"], ["A2", "r_a2"], ["B1", "r_b1"], ["X", "r_x"]];
const BLOOM_SEGS = [["记忆", "b_mem"], ["理解", "b_und"], ["应用", "b_app"], ["创造", "b_cre"]];
function renderSegBar(barId, segs, label) {
  const bar = $(barId);
  const vals = segs.map(s => +$(s[1]).value || 0);
  const sum = vals.reduce((a, b) => a + b, 0);
  bar.classList.toggle("over", sum > 100);
  let html = "";
  let cum = 0;
  segs.forEach((s, i) => {
    const v = Math.max(0, vals[i]);
    const labelTxt = v >= 8 ? `<b>${esc(s[0])} ${vals[i]}%</b>` : "";
    html += `<i data-seg="${i}" style="width:${v}%;background:var(--s${i + 1})" title="${esc(s[0])} ${vals[i]}%">
      ${labelTxt}</i>`;
    cum += v;
    // 边界把手：相邻两段之和 > 0 即渲染（某段被拖到 0 时把手仍在，可拖回）
    if (i < segs.length - 1 && v + Math.max(0, vals[i + 1]) > 0) {
      html += `<span class="seghandle" data-h="${i}" style="left:${cum}%" tabindex="0" role="slider"
        aria-label="拖动调整 ${esc(s[0])} 与 ${esc(segs[i + 1][0])} 的配比（方向键 ±5%）"
        aria-valuenow="${vals[i]}" aria-valuemin="0" aria-valuemax="${vals[i] + vals[i + 1]}"></span>`;
    }
  });
  if (sum < 100) html += `<i class="gap" style="width:${100 - sum}%"
      title="未分配 ${(100 - sum).toFixed(1)}%（合计 ${sum}%）"></i>`;
  bar.innerHTML = html;
  bar.setAttribute("aria-label", label + "：" +
    segs.map((s, i) => `${s[0]} ${vals[i]}%`).join("，") + `，合计 ${sum}%`);
  segs.forEach((s, i) => {
    const seg = bar.querySelector(`i[data-seg="${i}"]`);
    if (!seg) return;
    seg.onclick = () => {
      if (bar._dragged) return;             // 刚结束一次拖拽 → 抑制 click 聚焦
      const inp = $(s[1]); inp.focus(); inp.select();
    };
    // 窄屏像素级适配：标签放不下整段就隐藏（信息仍可从 title 悬停与下方输入框获得）
    const b = seg.querySelector("b");
    if (b && b.scrollWidth > seg.clientWidth - 6) b.style.display = "none";
  });
}
/* 配比条拖拽：把手或色块按下 → 边界跟手平移（抓取偏移补偿，按下不跳变，1% 步进，3px 死区）；
   释放后统一重绘；键盘 ←/→ = ±5%。 */
function bindSegBar(barId, segs, sumFn) {
  const bar = $(barId);
  const segVals = () => segs.map(s => Math.max(0, +$(s[1]).value || 0));
  bar.addEventListener("pointerdown", e => {
    if (e.button !== undefined && e.button !== 0) return;
    const h = e.target.closest(".seghandle");
    const segEl = h ? null : e.target.closest("i[data-seg]");
    if (!h && !segEl) return;
    const vs = segVals();
    let i;                                   // 边界左侧段索引
    if (h) {
      i = +h.dataset.h;
    } else {
      const si = +segEl.dataset.seg;
      const leftOk = si > 0 && vs[si] + vs[si - 1] > 0;          // 左边界（si-1 | si）
      const rightOk = si < segs.length - 1 && vs[si] + vs[si + 1] > 0;  // 右边界（si | si+1）
      if (!leftOk && !rightOk) return;
      if (leftOk && rightOk) {              // 两侧都有可拖边界 → 取离鼠标近的
        const r0 = bar.getBoundingClientRect();
        const pct = (e.clientX - r0.left) / r0.width * 100;
        const leftPos = vs.slice(0, si).reduce((a, b) => a + b, 0);
        i = pct - leftPos < vs[si] / 2 ? si - 1 : si;
      } else i = leftOk ? si - 1 : si;
    }
    const j = i + 1;
    if (j >= segs.length) return;
    const total = vs[i] + vs[j];
    const prefix = vs.slice(0, i).reduce((a, b) => a + b, 0);
    const rect = bar.getBoundingClientRect();
    const barW = rect.width;
    if (!barW) return;
    // 抓取偏移（%）：边界与鼠标保持按下时的相对距离 → 拖动跟手、按下瞬间不跳变
    const grabOff = (e.clientX - rect.left) / barW * 100 - (prefix + vs[i]);
    const startX = e.clientX;
    let v1 = vs[i];
    let moved = false;
    const seg1 = bar.querySelector(`i[data-seg="${i}"]`);
    const seg2 = bar.querySelector(`i[data-seg="${j}"]`);
    const handle = h || bar.querySelector(`.seghandle[data-h="${i}"]`);
    const setW = (el, v, name) => {
      if (!el) return;
      el.style.width = v + "%";
      const b = el.querySelector("b");
      if (!b) return;
      b.textContent = `${name} ${v}%`;
      b.style.display = "";                   // 先恢复可见再测量（display:none 时 scrollWidth=0 会误判）
      if (b.scrollWidth > el.clientWidth - 6) b.style.display = "none";
    };
    bar.classList.add("dragging");
    const target = handle || bar;
    try { target.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    const move = ev => {
      if (!moved && Math.abs(ev.clientX - startX) <= 3) return;   // 3px 死区：单击抖动不改值
      moved = true;
      let nv = Math.round((ev.clientX - rect.left) / barW * 100 - grabOff - prefix);
      nv = Math.min(total, Math.max(0, nv));
      if (nv === v1) return;
      v1 = nv;
      $(segs[i][1]).value = nv;
      $(segs[j][1]).value = total - nv;
      if (handle) {
        handle.style.left = (prefix + nv) + "%";
        handle.setAttribute("aria-valuenow", nv);
      }
      setW(seg1, nv, segs[i][0]);
      setW(seg2, total - nv, segs[j][0]);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      bar.classList.remove("dragging");
      if (moved) {
        bar._dragged = true;                  // 抑制随后的 click 聚焦
        setTimeout(() => { bar._dragged = false; }, 50);
        sumFn();   // 全量重绘 + 合计 + aria 同步
      }
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
    e.preventDefault();                      // 阻止拖拽时的文本选择
  });
  bar.addEventListener("keydown", e => {
    const h = e.target.closest(".seghandle");
    if (!h || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    const i = +h.dataset.h, j = i + 1;
    const in1 = $(segs[i][1]), in2 = $(segs[j][1]);
    let v1 = +in1.value || 0;
    const total = v1 + (+in2.value || 0);
    const nv = Math.min(total, Math.max(0, v1 + (e.key === "ArrowRight" ? 5 : -5)));
    if (nv === v1) return;
    in1.value = nv;
    in2.value = total - nv;
    sumFn();
  });
}
bindSegBar("bar_ratios", RATIO_SEGS, ratioSum);
bindSegBar("bar_bloom", BLOOM_SEGS, bloomSum);
/* 重置：回到 HTML 默认值（defaultValue 跟随源码，改默认只需改 value 属性） */
$("btn_reset_ratio").onclick = () => {
  RATIO_SEGS.forEach(s => { $(s[1]).value = $(s[1]).defaultValue; });
  ratioSum();
  toast("题型配比已重置为默认（A1 40 / A2 30 / B1 20 / X 10）");
};
$("btn_reset_bloom").onclick = () => {
  BLOOM_SEGS.forEach(s => { $(s[1]).value = $(s[1]).defaultValue; });
  bloomSum();
  toast("Bloom 认知层级已重置为默认（记忆 30 / 理解 40 / 应用 25 / 创造 5）");
};
function ratioSum() {
  const r = { A1: +$("r_a1").value || 0, A2: +$("r_a2").value || 0, B1: +$("r_b1").value || 0, X: +$("r_x").value || 0 };
  const s = Object.values(r).reduce((a, b) => a + b, 0);
  const el = $("ratio_sum");
  // B7：合计≠100 时给可视化提示——「还差 X%」/「超出 X%，请调低」，配比条同步 over 态
  el.textContent = "合计 " + s + "%"
    + (s < 100 ? `（还差 ${100 - s}%）` : s > 100 ? `（超出 ${s - 100}%，请调低）` : " ✓");
  el.classList.toggle("bad", s !== 100);
  renderSegBar("bar_ratios", RATIO_SEGS, "题型配比");
  return r;
}
["r_a1", "r_a2", "r_b1", "r_x"].forEach(id => $(id).addEventListener("input", ratioSum));
function bloomSum() {
  const b = { 记忆: +$("b_mem").value || 0, 理解: +$("b_und").value || 0, 应用: +$("b_app").value || 0, 创造: +$("b_cre").value || 0 };
  const s = Object.values(b).reduce((a, x) => a + x, 0);
  const el = $("bloom_sum");
  el.textContent = "合计 " + s + "%"
    + (s < 100 ? `（还差 ${100 - s}%）` : s > 100 ? `（超出 ${s - 100}%，请调低）` : " ✓");
  el.classList.toggle("bad", s !== 100);
  renderSegBar("bar_bloom", BLOOM_SEGS, "Bloom 认知层级");
  return b;
}
["b_mem", "b_und", "b_app", "b_cre"].forEach(id => $(id).addEventListener("input", bloomSum));
$("web_quota").addEventListener("input", () => { $("web_quota_val").textContent = $("web_quota").value + "%"; });
$("requirements").addEventListener("input", () => { $("req_count").textContent = $("requirements").value.length + "/500"; });
/* B6：题数/配比/Bloom/检索配额变化 → 防抖刷新成本预估与就绪检查（与最终配置一致） */
let estT = null;
function scheduleReady() {
  clearTimeout(estT);
  estT = setTimeout(() => { if ($("tab-bank") && $("tab-bank").classList.contains("show")) updateReady(); }, 450);
}
["target", "r_a1", "r_a2", "r_b1", "r_x", "b_mem", "b_und", "b_app", "b_cre", "web_quota"]
  .forEach(id => { const el = $(id); if (el) el.addEventListener("input", scheduleReady); });
["model_gen", "model_qc"]   // R3-10：换模型也刷新成本预估（模型/服务商价格口径变化）
  .forEach(id => { const el = $(id); if (el) el.addEventListener("change", scheduleReady); });
