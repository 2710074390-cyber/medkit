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

/* ---- ② 预设（2C） */
function currentFormPayload() {
  return {
    exam: $("exam").value, target: parseInt($("target").value || "100"),
    ratios: ratioSum(),
    bloom: bloomSum(),
    knobs: collectKnobs(),
    requirements: $("requirements").value.trim(),
    official_quota: parseInt($("official_quota") ? $("official_quota").value : "0") || 0,
  };
}
function collectKnobs() {
  const k = {};
  if ($("k_difficulty").value) k.difficulty = $("k_difficulty").value;
  if ($("k_analysis").value) k.analysis_style = $("k_analysis").value;
  if ($("k_stem").value) k.stem_style = $("k_stem").value;
  return k;
}
function fillPayload(p) {
  if (!p) return;
  if (p.exam && [...$("exam").options].some(o => o.value === p.exam)) $("exam").value = p.exam;
  if (p.target) $("target").value = p.target;
  const r = p.ratios || {};
  $("r_a1").value = r.A1 ?? 40; $("r_a2").value = r.A2 ?? 30;
  $("r_b1").value = r.B1 ?? 20; $("r_x").value = r.X ?? 10;
  const b = p.bloom || {};
  $("b_mem").value = b["记忆"] ?? 30; $("b_und").value = b["理解"] ?? 40;
  $("b_app").value = b["应用"] ?? 25; $("b_cre").value = b["创造"] ?? 5;
  const k = p.knobs || {};
  $("k_difficulty").value = k.difficulty || ""; $("k_analysis").value = k.analysis_style || "";
  $("k_stem").value = k.stem_style || "";
  $("requirements").value = (p.requirements || "").slice(0, 500);
  if ($("official_quota")) $("official_quota").value = p.official_quota ?? 0;
  ratioSum(); bloomSum();
  $("req_count").textContent = $("requirements").value.length + "/500";
}
async function loadPresets() {
  const r = await api("/api/presets");
  state.presets = r;
  renderChips(r);
}
function renderChips(r) {
  const box = $("preset_chips");
  if (!box) return;
  box.innerHTML = "";
  const mk = (p) => {
    const c = document.createElement("button");
    c.className = "chip" + (p.builtin ? " builtin" : "");
    c.title = p.desc || "";
    c.innerHTML = `${p.builtin ? "◆" : "◇"} ${esc(p.name)}`;
    if (!p.builtin) {
      // R4-25：删除入口不再拼接行内 onclick（p.id 含撇号会击穿 JS）——事件绑定 + DOM 挂载
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "✕";
      x.onclick = (ev) => { ev.stopPropagation(); delPreset(p.id); };
      c.appendChild(x);
    }
    c.onclick = () => confirmModal("应用预设？",
      `「${esc(p.name)}」将<b>覆盖当前参数</b>（'科目'不覆盖）。<br><span class="hint">${esc(p.desc || "")}</span>`,
      "应用", () => { fillPayload(p.payload); toast("预设已应用：" + p.name); }, false);
    box.appendChild(c);
  };
  [...(r.builtins || []), ...(r.customs || [])].forEach(mk);
}
async function delPreset(id) {
  try { await api("/api/presets/" + id, { method: "DELETE" }); toast("预设已删除"); loadPresets(); }
  catch (e) { toast(e.message, false); }
}
$("btn_preset_save").onclick = () => {
  askModal("保存预设", "预设名称：", "如：期末冲刺·计算题加强", async name => {
    try {
      const r = await api("/api/presets", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, desc: "", payload: currentFormPayload() }) });
      toast("预设已保存：" + r.name);
      loadPresets();
    } catch (e) { toast(e.message, false); }
  });
};
$("btn_preset_export").onclick = () => {
  const data = JSON.stringify({ kind: "medkit-preset", payload: currentFormPayload() }, null, 2);
  const a = document.createElement("a");
  a.download = "medkit-preset-" + ($("subject").value.trim() || "untitled") + ".json";
  a.href = "data:application/json;charset=utf-8," + encodeURIComponent(data);
  a.click();
  toast("预设文件已导出（另一台机器「导入」即可回填）");
};
$("btn_preset_import").onclick = () => $("f_preset_import").click();
$("f_preset_import").onchange = async () => {
  const f = $("f_preset_import").files[0];
  $("f_preset_import").value = "";
  if (!f) return;
  try {
    const j = JSON.parse(await f.text());
    const p = (j.kind === "medkit-preset" && j.payload) ? j.payload : j.payload || j;
    if (!p || typeof p !== "object") throw new Error("格式不符");
    fillPayload(p);
    toast("预设已导入并回填");
  } catch (e) { toast("预设文件不合法：" + e.message, false); }
};

/* ---- ② 素材拖拽 */
const ROLE_LABEL = { textbook: "教材", teacher: "教师重点", exam: "真题", extra: "资料" };
/* B1：上传类型白名单与后端 TEXT_SUFFIXES 对齐（.bmp 后端支持但 accept 未列 → 补上；.doc 不支持） */
const UP_OK_EXT = ["pdf", "docx", "md", "markdown", "txt", "text", "png", "jpg", "jpeg", "webp", "bmp"];
["textbook", "teacher", "exam", "extra"].forEach(role => {
  const dz = $("dz_" + role), input = $("f_" + role);
  input.onchange = () => { addFiles(role, [...input.files]); input.value = ""; };
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", e => {
    e.preventDefault(); dz.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      addFiles(role, [...e.dataTransfer.files]);
      toast(`已加入${ROLE_LABEL[role] || role}：${e.dataTransfer.files.length} 个文件`);
    }
  });
  $("dzc_" + role).onclick = () => { state.files[role] = []; renderDz(role); };
});
function pickFiles(role) { $("f_" + role).click(); }
function addFiles(role, files) {
  const list = state.files[role];
  for (const f of files) {
    if (!f || !f.size) { toast("空文件已忽略", false); continue; }
    const ext = (f.name || "").split(".").pop().toLowerCase();
    if (ext && !UP_OK_EXT.includes(ext)) {
      toast(`「${f.name}」类型不支持（支持 PDF/DOCX/MD/TXT/图片 png·jpg·webp·bmp），已跳过`, false);
      continue;
    }
    list.push({ name: f.name, size: f.size, file: f });
  }
  renderDz(role);
}
function renderDz(role) {
  const el = $("dzl_" + role);
  const list = state.files[role];
  el.innerHTML = list.map((f, i) =>
    `<div class="dzfile"><span class="nm">${esc(f.name)}</span><span class="sz">${(f.size / 1048576).toFixed(1)}MB</span>
     <button onclick="removeFile('${role}',${i})">移除</button></div>`).join("");
  $("dzc_" + role).style.display = list.length ? "block" : "none";
}
function removeFile(role, i) {
  const doRemove = () => { state.files[role].splice(i, 1); renderDz(role); };
  if (role === "textbook" || role === "teacher") {
    confirmModal("移除文件", `从清单移除「${esc(state.files[role][i].name)}」？`, "移除", doRemove, false);
  } else doRemove();
}

/* ---- ② 解析 */
let pres = state.pres;

async function parseGroup(role) {
  const files = state.files[role].map(f => f.file);
  if (!files.length) return null;
  // B1：单个超限文件只跳过该文件，不再让整组解析失败
  const okFiles = [];
  const skipped = [];
  for (const f of files) {
    if (f.size > 200 * 1024 * 1024) {
      toast(`「${f.name}」超过 200 MB：已跳过，其余文件继续解析（建议按章节拆分后重传）`, false);
      skipped.push(f);
      continue;
    }
    okFiles.push(f);
  }
  let res = { results: [] };
  if (okFiles.length) {
    const fd = new FormData();
    okFiles.forEach(f => fd.append("files", f));
    fd.append("role", role);
    fd.append("ocr", $("t_ocr").checked ? "1" : "0");
    res = await api("/api/parse", { method: "POST", body: fd });
  }
  // B29：跳过的超大文件在解析结果里可见（error 行）；同时返回过滤后的文件列表，OCR 对位不再错行
  skipped.forEach(f => res.results.push({ name: f.name, error: "超过 200 MB 已跳过（建议按章节拆分后重传）" }));
  res.files = okFiles;
  return res;
}

async function runOcrJobs(group, role) {
  const myToken = ++ocrRunToken;   // v0.5：离开页面（自增 token）→ 轮询循环终止
  // B29：与 parseGroup 共用同一份过滤后文件列表（跳过超大文件的列表），OCR 对位不再错行
  const files = (group.files && group.files.length) ? group.files : state.files[role].map(f => f.file);
  const jobs = [];
  group.results.forEach((r, i) => {
    if (r.ocr_needed && $("t_ocr").checked && files[i]) {
      jobs.push({ file: files[i], idx: i, jobId: null, state: "queued", msg: "排队中", row: null });
    }
  });
  if (!jobs.length) return;
  const box = $("ocr_progress");
  box.innerHTML = `
    <div class="ocrwrap" role="status" aria-live="polite">
      <div class="ocrhead">
        <span class="spin"></span><span class="t" data-role="t">正在识别扫描件 / 图片</span>
        <span class="cnt" data-role="cnt">0/${jobs.length} 完成</span>
        <span class="note">MinerU · 文件上传至云端识别</span>
      </div>
      <div class="ocrbar running" data-role="bar"><i></i></div>
      <div data-role="rows"></div>
    </div>`;
  const rowsBox = box.querySelector('[data-role="rows"]');
  const ROW_STATE = {  // 状态 → 芯片文案/样式（queued 由轮询更新为 run）
    queued: ["queued", "排队中"],
    running: ["run", "识别中"],
    done: ["done", "完成 ✓"],
    failed: ["failed", "失败"],
    cancelled: ["cancel", "已取消"],
  };
  const updateOcrUi = () => {
    const n = jobs.length;
    const doneN = jobs.filter(j => j.state === "done").length;
    const failN = jobs.filter(j => j.state === "failed").length;
    const cancelN = jobs.filter(j => j.state === "cancelled").length;
    const runningN = jobs.filter(j => !["done", "failed", "cancelled"].includes(j.state)).length;
    const finished = doneN + failN + cancelN;
    const tEl = box.querySelector('[data-role="t"]');
    const cntEl = box.querySelector('[data-role="cnt"]');
    if (!runningN && finished === n) {                       // 全部终态
      tEl.textContent = failN ? "识别结束（部分失败）" : cancelN === n ? "识别已取消" : "识别完成 ✓";
      const cls = failN ? "bad" : cancelN === n ? "" : "good";
      tEl.style.color = cls === "bad" ? "var(--bad)" : cls === "good" ? "var(--good)" : "var(--accent2)";
      cntEl.textContent = `完成 ${doneN}/${n}` + (failN ? ` · 失败 ${failN}` : "") + (cancelN ? ` · 取消 ${cancelN}` : "");
    } else {
      tEl.style.color = "";
      cntEl.textContent = runningN ? `识别中 ${runningN}/${n}` : `完成 ${doneN}/${n}`;
    }
    const bar = box.querySelector('[data-role="bar"]');
    bar.classList.toggle("running", runningN > 0);
    bar.classList.toggle("bad", failN > 0);
    bar.querySelector("i").className = failN ? "bad" : "";
    bar.querySelector("i").style.width = (finished / n * 100).toFixed(1) + "%";
    jobs.forEach(j => {
      const row = j.row; if (!row) return;
      const [cls, txt] = ROW_STATE[j.state] || ROW_STATE.queued;
      const stEl = row.querySelector("[data-role=st]");
      stEl.className = "ocrst " + cls;
      stEl.textContent = txt;
      row.classList.toggle("running", j.state === "queued" || j.state === "running");
      const msgEl = row.querySelector("[data-role=msg]");
      msgEl.textContent = j.state === "done" ? "已自动加入输入" : j.state === "queued" ? "" : (j.msg || "");
      msgEl.className = "msg " + (j.state === "done" ? "good" : j.state === "failed" ? "bad" : "");
      row.querySelector("[data-role=cancel]").disabled = ["done", "failed", "cancelled"].includes(j.state);
    });
  };
  jobs.forEach(j => {
    const fd = new FormData();
    fd.append("file", j.file); fd.append("role", "ocr");
    j.promise = api("/api/ocr/start", { method: "POST", body: fd })
      .then(x => { j.jobId = x.job_id; });
    j.promise.catch(() => { j.state = "failed"; j.msg = "任务创建失败"; });
    const row = document.createElement("div");
    row.className = "ocrrow";
    row.innerHTML = `<span class="fname" title="${esc(j.file.name)}">${esc(j.file.name)}</span>
      <span class="ocrst queued" data-role="st">排队中</span>
      <span class="msg" data-role="msg"></span>
      <span class="ocrmini"></span>
      <button data-role="cancel" class="cancel">取消</button>`;
    row.querySelector("[data-role=cancel]").onclick = async () => {
      if (j.jobId) { await api("/api/ocr/jobs/" + j.jobId, { method: "DELETE" }).catch(() => {}); }
      j.state = "cancelled"; j.msg = "已取消";
      updateOcrUi();
    };
    rowsBox.appendChild(row);
    j.row = row;
  });
  updateOcrUi();
  await Promise.all(jobs.map(j => j.promise));

  const started = jobs.filter(j => j.jobId);
  while (myToken === ocrRunToken && started.some(j => !["done", "failed", "cancelled"].includes(j.state))) {
    await new Promise(r => setTimeout(r, 2000));
    await Promise.all(started.map(async j => {
      if (["done", "failed", "cancelled"].includes(j.state)) return;
      const s = await api("/api/ocr/jobs/" + j.jobId).catch(() => null);
      if (!s) return;
      j.state = s.state; j.msg = s.msg || s.state;
      if (s.state === "done" && s.result) {
        group.results[j.idx] = s.result;
      } else if (s.state === "failed") {
        group.results[j.idx] = { name: j.file.name, error: (s.msg || "识别失败") };
      }
    }));
    updateOcrUi();
  }
  updateOcrUi();
  started.forEach(j => {
    if (j.state === "cancelled") group.results[j.idx] = { name: j.file.name, error: "已取消识别" };
  });
}

function filesCount(res) { return (res && res.results || []).filter(r => r.ok).length; }
function resTotalChars(res) {
  return (res && res.results || []).filter(r => r.ok).reduce((a, r) => a + (r.chars || 0), 0);
}
function renderResults(roleLabel, res) {
  const el = $("parse_results");
  const wrap = document.createElement("div");
  wrap.innerHTML = `<div class="hint good">—— ${roleLabel} ——</div>`;
  (res.results || []).forEach(r => {
    if (r.ok) {
      const ocrBadge = (r.via && r.via.startsWith("mineru"))
        ? `<span class="tag">${r.via === "mineru-v4" ? "MinerU 精准 API" : "MinerU 轻量 API"}</span><span class="hint good">${esc(r.via_note || "已自动加入输入")}</span> `
        : "";
      const warns = (r.warnings || []).map(w => `<div class="warnline">${esc(w)}</div>`).join("");
      wrap.innerHTML += `<div class="res"><span class="name">${esc(r.name)}</span> · ${r.chars} 字 · ${r.slice_count} 切片 · 估算输入 ≈ ${((r.est_tokens || 0) / 10000).toFixed(1)} 万 token
        ${ocrBadge}${warns}
        <details><summary>预览切片（${(r.slices || []).length} 条，全部展开）</summary>${(r.slices || []).map(s => `<div><b>[${esc(s.sid)}] ${esc(s.title || "（全文）")}</b><br>${esc(s.preview)}${(s.text || "").length > (s.preview || "").length ? "…" : ""}</div>`).join("<hr>")}</details></div>`;
    } else {
      wrap.innerHTML += `<div class="res" style="border-color:var(--bad)"><span class="name">${esc(r.name)}</span> <span class="hint bad">${esc(r.error)}</span></div>`;
    }
  });
  el.appendChild(wrap);
}
/* S2：成本公式统一走后端（core/cost.estimate_run，与 Python 同源），旧内嵌公式删除 */
async function estimateCost() {
  const chars = resTotalChars(pres.textbook) + resTotalChars(pres.teacher);
  const nSlices = (pres.textbook && pres.textbook.results || []).filter(r => r.ok)
    .reduce((a, r) => a + (r.slice_count || 0), 0);
  const nQ = Math.max(parseInt($("target").value || "100"), 1);
  try {
    const r = await api("/api/cost/estimate", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chars_textbook: resTotalChars(pres.textbook) || 0,
                             chars_teacher: resTotalChars(pres.teacher) || 0,
                             n_slices: nSlices, n_questions: nQ }) });
    return { inp: r.input_tokens, out: r.output_tokens, tot: r.total_tokens };
  } catch (e) { return null; }   // A-新8：接口失败 → null，调用方显示「预估不可用（点击重试）」而非 ≈0.0
}
async function updateReady() {
  const tb = pres.textbook, tc = pres.teacher, ex = pres.exam, xt = pres.extra;
  const warns = [];
  [...(tb && tb.results || []), ...(tc && tc.results || [])].forEach(r => warns.push(...(r.warnings || [])));
  let estLine = "解析后此处显示成本预估";
  if (resTotalChars(tb) || resTotalChars(tc)) {
    const est = await estimateCost();
    if (!est) {
      // A-新8：成本预估接口失败 → 明确「预估不可用（点击重试）」，不得显示误导性的 ≈0.0/¥0.00
      estLine = `预估不可用（<a href="javascript:void(0)" onclick="updateReady()" style="text-decoration:underline">点击重试</a>）`;
    } else {
    const prov = (state.providers || []).find(p => p.id === state.provider);
    const price = prov && prov.price;
    const cny = price ? (est.inp / 1e6 * (price.input || 0) + est.out / 1e6 * (price.output || 0)) : null;
    const model = modelValue("model_gen") || (state.cfg && state.cfg.model_gen) || "";
    estLine = `预计注入/输出合计 ≈ ${(est.tot / 10000).toFixed(1)} 万 token`
      + (cny != null ? ` · 约 ¥${cny.toFixed(2)}` : "")
      + `（${prov ? prov.name : "当前服务商"} ${esc(model)} 参考价，以官网为准）`
      + (warns.length ? " · 有 " + warns.length + " 条体检提示" : "")
      + ($("t_web").checked ? " · ＋网络检索 ≈ 3 轮 × 3~5 次查询（费用以所选后端官网为准）" : "");
    }
  }
  $("ready_list").innerHTML = `
    <div class="ready">
      <span>素材就绪检查：</span>
      ${filesCount(tb) ? `<span class="ok">教材 ✓ ${filesCount(tb)} 文件</span>` : `<span class="no">教材 ✗ 未解析（必填）</span>`}
      ${filesCount(tc) ? `<span class="ok">教师重点 ✓ ${filesCount(tc)} 文件</span>` : `<span class="no">教师重点 ✗ 未解析（必填）</span>`}
      ${filesCount(ex) ? `<span class="ok">自备真题 ✓ ${filesCount(ex)} 文件</span>` : `<span class="opt">自备真题 —（可选）</span>`}
      ${filesCount(xt) ? `<span class="ok">补充资料 ✓ ${filesCount(xt)} 文件</span>` : `<span class="opt">补充资料 —（可选）</span>`}
      ${pres.sample ? `<span class="opt">（示例素材）</span>` : ""}
      ${$("t_web").checked ? `<span class="opt">网络检索已开启</span>` : ""}
      <span class="est">${estLine}</span>
    </div>`;
}
$("btn_parse").onclick = async () => {
  const btn = $("btn_parse"); const old = btn.textContent;
  // B19：解析期间禁用按钮（防双击 → 重复解析/409 混淆）
  btn.disabled = true; btn.textContent = "解析中…";
  $("parse_results").innerHTML = '<div class="hint"><span class="spin"></span>解析中…</div>';
  $("ocr_progress").innerHTML = "";
  try {
    const groups = [
      { res: await parseGroup("textbook"), role: "textbook", label: "教材（必填）", key: "textbook", render: true },
      { res: await parseGroup("teacher"), role: "teacher", label: "教师重点（必填）", key: "teacher", render: true },
      { res: await parseGroup("exam"), role: "exam", label: "自备真题（可选）", key: "exam", render: false },
      { res: await parseGroup("extra"), role: "extra", label: "补充资料（可选）", key: "extra", render: false },
    ].filter(g => g.res && g.res.results.length);
    $("parse_results").innerHTML = "";
    for (const g of groups) {
      await runOcrJobs(g.res, g.role);
      pres[g.key] = g.res;
      if (g.render || g.res.results.some(r => r.ok)) renderResults(g.label, g.res);
    }
    pres.sample = false;
    await updateReady();
    toast("解析完成：体检与成本预估已更新");
  } catch (e) { $("parse_results").innerHTML = `<div class="hint bad">${esc(e.message)}</div>`; }
  finally { btn.disabled = false; btn.textContent = old; }
};

$("btn_sample").onclick = async () => {
  try {
    $("btn_sample").disabled = true; $("btn_sample").textContent = "载入中…";
    const s = await api("/api/sample");
    if (!s.sample) throw new Error(s.error || "示例加载失败");
    pres = { textbook: { results: [s.textbook] }, teacher: { results: [s.teacher] }, exam: null, extra: null, sample: true };
    state.pres = pres;
    $("subject").value = s.subject;
    $("parse_results").innerHTML = "";
    $("ocr_progress").innerHTML = "";
    renderResults("教材（示例）", pres.textbook);
    renderResults("教师重点（示例）", pres.teacher);
    await updateReady();
    toast("示例素材已载入：先点「试出一题」看效果，满意后「创建课题 →」");
  } catch (e) { toast(e.message, false); }
  $("btn_sample").disabled = false; $("btn_sample").textContent = "手头还没素材？载入示例体验";
};
/* WP-12：纯净安装包无示例数据 → 探测并降级“载入示例”按钮（开发版不受影响） */
async function probeSampleAvailability() {
  try {
    const s = await api("/api/sample");
    const btn = $("btn_sample");
    if (btn && !s.sample && s.available === false) {
      btn.disabled = true; btn.title = s.error || "";
      btn.textContent = "示例仅开发版可用（纯净版请自备素材/上传官方大纲）";
    }
  } catch (e) { /* 探测失败不阻塞页面 */ }
}

/* ---- S3：素材库（历史解析会话）与项目模板 ---- */
async function loadSessions() {
  try {
    const r = await api("/api/sessions");
    const box = $("sess_box");
    if (!box) return;
    const list = r.sessions || [];
    if (!list.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="card">
      <h3 style="margin-bottom:6px">素材库（历史解析会话 · 跨项目复用 / 多教材合并）</h3>
      <div class="hint">勾选多个会话 → 「合并载入为教材」（quota 跨 session 按章加权）；单个会话可载入为教师重点。删除即失效。</div>
      ${list.map(s => `<div class="sessrow" style="display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)">
        <input type="checkbox" class="sessck" data-id="${esc(s.id)}">
        <b style="width:220px">${esc(s.name)}</b>
        <span class="hint">${esc(s.role)} · ${(s.chars || 0).toLocaleString()} 字 · ${s.slice_count} 章节 · ${esc(s.created)}</span>
        <button class="inlineBtn blue" data-a="loadtg" data-id="${esc(s.id)}">载入为教师重点</button>
        <button class="inlineBtn" data-a="del" data-id="${esc(s.id)}">删除</button>
      </div>`).join("")}
      <div class="btns"><button class="act" id="sess_merge">合并载入为教材</button></div>
    </div>`;
    box.querySelectorAll("[data-a=loadtg]").forEach(b => b.onclick = () => loadSessionAs(b.dataset.id, "teacher"));
    box.querySelectorAll("[data-a=del]").forEach(b => b.onclick = async () => {
      try { await api("/api/sessions/" + b.dataset.id, { method: "DELETE" }); toast("会话已删除"); loadSessions(); }
      catch (e) { toast(e.message, false); }
    });
    $("sess_merge").onclick = () => {
      const ids = [...box.querySelectorAll(".sessck:checked")].map(x => x.dataset.id);
      if (!ids.length) { toast("请先勾选要合并的会话", false); return; }
      loadSessionsAsTextbook(ids);
    };
  } catch (e) { /* 素材库不可用不阻塞主流程 */ }
}
async function loadSessionAs(sid, role) {
  const s = await api("/api/sessions/" + sid);
  const chars = (s.slices || []).reduce((a, x) => a + (x.text || "").length, 0);
  const res = { ok: true, name: s.name, chars: chars, slice_count: s.slice_count,
                slices: s.slices, est_tokens: Math.round(chars * 0.8), warnings: [], via: "session" };
  if (role === "teacher") { pres.teacher = { results: [res], sample: false }; renderResults("教师重点（会话）", pres.teacher); }
  else { pres.textbook = { results: [res], sample: false }; renderResults("教材（会话）", pres.textbook); }
  state.pres = pres;
  await updateReady();
  toast(`已载入「${s.name}」（${s.slice_count} 章节）`);
}
async function loadSessionsAsTextbook(ids) {
  const slices = [];
  const names = [];
  for (const sid of ids) {
    const s = await api("/api/sessions/" + sid);
    slices.push(...(s.slices || []));
    names.push(s.name);
  }
  // F4：多会话合并 → 各会话切片 sid 均从 S001 起始，合并后统一重编号（后端 create_project 亦防御性重编号）
  slices.forEach((s, i) => { s.sid = `S${String(i + 1).padStart(3, "0")}`; });
  const chars = slices.reduce((a, x) => a + (x.text || "").length, 0);
  const res = { ok: true, name: names.join(" + "), chars: chars, slice_count: slices.length,
                slices: slices, est_tokens: Math.round(chars * 0.8), warnings: [], via: "session" };
  pres.textbook = { results: [res], sample: false };
  state.pres = pres;
  renderResults("教材（多会话合并）", pres.textbook);
  await updateReady();
  toast(`已合并载入 ${slices.length} 章节（来自 ${ids.length} 个会话）`);
}
$("btn_sess").onclick = async () => {
  try {
    let saved = 0;
    for (const [role, g, label] of [["textbook", pres.textbook, "教材"],
                                    ["teacher", pres.teacher, "教师重点"]]) {
      const slices = ((g && g.results) || []).flatMap(r => r.slices || []);
      if (!slices.length) continue;
      await api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: label + "会话", role, slices,
                               source_name: ((g.results || [])[0] || {}).name || "" }) });
      saved++;
    }
    if (!saved) { toast("请先「解析并预览」（或用会话载入素材）再保存", false); return; }
    toast("已保存 " + saved + " 个素材会话（见下方素材库，可跨项目复用）");
    loadSessions();
  } catch (e) { toast(e.message, false); }
};
/* 项目配置模板：subject/exam/target/题型配比/Bloom/旋钮/附加要求 一键存/取 */
$("btn_tpl_save").onclick = () => {
  try {
    const tpl = {
      subject: $("subject").value, exam: $("exam").value, target: $("target").value,
      ratios: { A1: $("r_a1").value, A2: $("r_a2").value, B1: $("r_b1").value, X: $("r_x").value },
      bloom: { 记忆: $("b_mem").value, 理解: $("b_und").value, 应用: $("b_app").value, 创造: $("b_cre").value },
      knobs: { difficulty: $("k_difficulty").value, analysis_style: $("k_analysis").value, stem_style: $("k_stem").value },
      requirements: $("requirements").value,
    };
    localStorage.setItem("medkit-tpl-project", JSON.stringify(tpl));
    toast("已存为项目模板（配比/Bloom/旋钮/附加要求）");
  } catch (e) { toast(e.message, false); }
};
$("btn_tpl_apply").onclick = async () => {
  try {
    const t = JSON.parse(localStorage.getItem("medkit-tpl-project") || "null");
    if (!t) { toast("还没有保存过模板", false); return; }
    if (t.subject) $("subject").value = t.subject;
    if (t.exam) $("exam").value = t.exam;
    if (t.target) { $("target").value = t.target; }
    (["r_a1", "r_a2", "r_b1", "r_x"]).forEach(k => {
      if (t.ratios) { const v = t.ratios[{ "r_a1": "A1", "r_a2": "A2", "r_b1": "B1", "r_x": "X" }[k]]; if (v) $(k).value = v; }
    });
    (["b_mem", "b_und", "b_app", "b_cre"]).forEach(k => { if (t.bloom) { const v = t.bloom[{ "b_mem": "记忆", "b_und": "理解", "b_app": "应用", "b_cre": "创造" }[k]]; if (v) $(k).value = v; } });
    if (t.knobs) {
      if (t.knobs.difficulty) $("k_difficulty").value = t.knobs.difficulty;
      if (t.knobs.analysis_style) $("k_analysis").value = t.knobs.analysis_style;
      if (t.knobs.stem_style) $("k_stem").value = t.knobs.stem_style;
    }
    if (t.requirements) $("requirements").value = t.requirements;
    ratioSum(); bloomSum();                                  // 刷新可视化配比条
    $("req_count").textContent = $("requirements").value.length + "/500";
    toast("模板已应用（服务商/模型为全局配置，在「连接服务商」确认）");
    await updateReady();
  } catch (e) { toast("模板应用失败：" + e.message, false); }
};
loadSessions();

function fullSlices(res) {
  return (res && res.results || []).filter(r => r.ok).flatMap(r => (r.slices || []));
}

/* ---- 试出一题（迭代1B） */
$("btn_trial").onclick = async () => {
  if (!(state.cfg && state.cfg.api_key_masked)) {
    toast("试出一题需要用 API Key——请先在「我的 → 连接服务商」保存", false);
    showTab("mine"); $("api_key").focus();
    return;
  }
  const slices = fullSlices(pres.textbook);
  if (!slices.length) return toast("请先解析教材（或点「载入示例」）再试出题", false);
  if (!filesCount(pres.teacher)) return toast("请先解析教师重点（必填）", false);
  const s = slices[Math.floor(Math.random() * slices.length)];
  const box = $("trial_box");
  box.innerHTML = `<div class="hint"><span class="spin"></span>试生成中（首次约 30~60 秒），来自切片 ${esc(s.sid)} · ${esc(s.title || "")}…</div>`;
  $("btn_trial").disabled = true;
  try {
    const teacherText = fullSlices(pres.teacher).map(x => x.text).join("\n");
    const examText = fullSlices(pres.exam).map(x => x.text).join("\n");      // v0.5.2：真题参与试出（风格校准）
    const extraText = fullSlices(pres.extra).map(x => x.text).join("\n");    // v0.5.2：资料参与试出
    const r = await api("/api/trial", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject: $("subject").value.trim() || "未命名科目",
        exam: $("exam").value,
        requirements: $("requirements").value.trim(),
        knobs: collectKnobs(),
        ratios: ratioSum(),
        bloom: bloomSum(),
        slice_sid: s.sid, slice_title: s.title || "", slice_text: s.text,
        teacher_text: teacherText,
        exam_text: examText, extra_text: extraText,
      }) });
    const q = r.question;
    const L = letters((q.options || []).length);   // R3-16：试出题同样支持 10 选项
    const issues = (r.issues || []).map(i =>
      `<div class="${i.severity === "fail" ? "failline" : "warnline"}">[${esc(i.code)}] ${esc(i.reason)}</div>`).join("");
    box.innerHTML = `
      <div class="trialq">
        <div style="margin:0 0 8px;padding:6px 10px;border:1px dashed var(--warn);border-radius:8px;font-size:12px;color:var(--warn)">⚠️ ${esc(r.note || "试出题不含网络检索/大纲锚定/图片素材，正式生成风格可能不同")}</div>
        <div class="src">试出题 · ${esc(r.from_slice || "")} · <span class="tag">${esc(q.type || "")}</span><span class="tag">${esc(q.bloom || "")}</span></div>
        <div class="qtext">${esc(q.question)}</div>
        <div class="opts">${(q.options || []).map((o, i) => L[i] + ". " + esc(o)).join("<br>")}</div>
        <details><summary>显示答案</summary>
          <div class="ans">✓ 答案：<b>${esc(q.answer)}</b><br>${esc(q.analysis)}</div>
        </details>
        ${issues ? `<div style="margin-top:8px">${issues}</div>` : `<div class="hint good">门禁即检：未发现问题 ✓</div>`}
        <div class="btns" style="margin-top:8px">
          <button class="act" onclick="$('btn_create').click()">满意，创建课题 →</button>
          <button class="act gray" onclick="doclickTrialAgain()">不满意，再试一题</button>
        </div>
        <div class="hint">每次随机换切片；创建后将带着这套参数正式生成全部题（创建前会展示成本预估）</div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="hint bad">试出题失败：${esc(e.message)}</div>`;
  }
  $("btn_trial").disabled = false;
};
function doclickTrialAgain() { $("btn_trial").click(); }

/* 校验失败：标红 + 滚动定位 + toast（长表单上方可见） */
function markErr(el, msg) {
  (Array.isArray(el) ? el : [el]).forEach(x => {
    x.classList.add("err");
    x.addEventListener("input", () => x.classList.remove("err"), { once: true });
  });
  const first = Array.isArray(el) ? el[0] : el;
  first.scrollIntoView({ behavior: "smooth", block: "center" });
  first.focus({ preventScroll: true });
  toast(msg, false);
}
$("btn_create").onclick = async () => {
  if (!$("subject").value.trim()) return markErr($("subject"), "请填写科目");
  if (!filesCount(pres.textbook)) {
    toast("请先解析教材（必填）——或点「载入示例」体验", false);
    return $("dz_textbook").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (!filesCount(pres.teacher)) {
    toast("请先解析教师重点（必填）", false);
    return $("dz_teacher").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if ($("requirements").value.trim().length > 500) return markErr($("requirements"), "附加要求超过 500 字");
  const ratios = ratioSum();
  const bloom = bloomSum();
  if (Object.values(ratios).reduce((a, b) => a + b, 0) !== 100)
    return markErr([$("r_a1"), $("r_a2"), $("r_b1"), $("r_x")], "题型配比合计应为 100%（当前 " + Object.values(ratios).reduce((a, b) => a + b, 0) + "%）");
  if (Object.values(bloom).reduce((a, b) => a + b, 0) !== 100)
    return markErr([$("b_mem"), $("b_und"), $("b_app"), $("b_cre")], "Bloom 配比合计应为 100%（当前 " + Object.values(bloom).reduce((a, b) => a + b, 0) + "%）");
  try {
    $("btn_create").disabled = true; $("btn_create").textContent = "创建中…";
    // R3-08：创建意图令牌——双击/双标签重复提交后端幂等去重（只建一个项目、只扣一次配额）
    if (!createToken) createToken = "ct-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
    const body = {
      client_token: createToken,
      subject: $("subject").value.trim(),
      exam: $("exam").value,
      target: parseInt($("target").value || "100"),
      ratios, bloom,
      knobs: collectKnobs(),
      requirements: $("requirements").value.trim(),
      toggles: { qbank: $("t_qbank").checked, paper: $("t_paper").checked, review: $("t_review").checked },
      textbook_slices: fullSlices(pres.textbook), teacher_slices: fullSlices(pres.teacher),
      exam_slices: fullSlices(pres.exam), extra_slices: fullSlices(pres.extra),
      teacher_text: fullSlices(pres.teacher).map(s => s.text).join("\n"),
      web_search: $("t_web").checked,
      web_backend: $("ws_backend").value,
      web_ref_quota: parseInt($("web_quota").value || "0"),
      web_manual_text: $("ws_manual").value,
      official_quota: parseInt($("official_quota") ? $("official_quota").value : "0") || 0,
    };
    const r = await api("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    createToken = "";   // 成功即重置：下一次创建是新的意图
    toast("课题已创建：" + r.pid + "（已打开项目详情）");
    // A-新13：创建成功后清空表单（subject 等）、已选文件列表与解析结果，避免第二门课带错素材
    $("subject").value = "";
    ["textbook", "teacher", "exam", "extra"].forEach(role => { state.files[role] = []; renderDz(role); });
    pres = { textbook: null, teacher: null, exam: null, extra: null, sample: false };
    state.pres = pres;
    $("parse_results").innerHTML = "";
    $("ocr_progress").innerHTML = "";
    $("trial_box").innerHTML = "";
    location.hash = "bank";
    showTab("bank");
    showProject(r.pid);
  } catch (e) { toast(e.message, false); }
  $("btn_create").disabled = false; $("btn_create").textContent = "创建课题 →";
};

/* ---- ③ 项目 */
/* v0.5：currentPid/pollTimer/pollFails 已提前声明于脚本顶部（showTab 需在初始化时安全调用 stopPoll） */

async function loadProjects() {
  const r = await api("/api/projects");
  const box = $("proj_list");
  if (!r.projects.length) {
    box.innerHTML = `<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><use href="#i-mine"></use></svg>
      <div class="sub">还没有项目 · 上传教材与教师重点，生成你的第一套题库</div>
      <button class="act" onclick="document.getElementById('tab-bank').scrollIntoView()">去上方「新建课题」↑</button>
    </div>`;
    return;
  }
  box.innerHTML = "";
  r.projects.forEach(p => {
    const d = document.createElement("div");
    d.className = "proj" + (p.meta_missing ? " orphan" : "");
    d.innerHTML = `<b>${esc(p.subject)}</b>
      <span class="stage${p.running ? " running" : ""}">${p.running ? "● 运行中" : esc(p.stage_label || "……")}</span>
      <div class="meta">${p.meta_missing ? "元数据缺失 · 可删除" : ((p.exam || "") + " · 目标 " + (p.target || 0) + " 题 · " + (p.created || "").slice(0, 16).replace("T", " "))}</div>`;
    d.onclick = () => {
      if (p.meta_missing) {
        // R3-20：孤儿项目（meta 缺失）不可进详情 → 直接提供删除入口
        confirmModal("删除孤儿项目？",
          "项目目录 <b>" + esc(p.pid) + "</b> 缺少元数据（可能因中断产生），将直接删除整个目录，不可恢复。",
          "直接删除", async () => {
            try {
              await api("/api/projects/" + encodeURIComponent(p.pid), { method: "DELETE" });
              toast("孤儿项目已删除");
              loadProjects();
            } catch (e) { toast(e.message, false); }
          });
      } else showProject(p.pid);
    };
    box.appendChild(d);
  });
}
const ART_LABEL = [
  [/^qbank\.md$/i, "📄", "题库 MD"],
  [/^qbank\.html$/i, "🌐", "题库 · 在线"],
  [/押题卷.*\.html$/i, "✍️", "押题卷 · 交互"],
  [/复习手册.*\.md$/i, "📘", "手册 MD"],
  [/复习手册.*\.html$/i, "📗", "手册 · 在线"],
  [/anki_export\.txt$/i, "🧠", "Anki 文本"],
  [/\.apkg$/i, "🃏", "Anki 卡包"],
];
function artifactLinks(pid, names) {
  return `<div class="artgrid">` + (names || []).map(n => {
    const hit = ART_LABEL.find(([re]) => re.test(n));
    const [ico, label] = hit ? [hit[1], hit[2]] : ["📃", n];   // C-06：三元组 [正则,图标,标题] 取下标 1/2
    const href = n.endsWith(".apkg")
      ? `/api/projects/${encodeURIComponent(pid)}/export/apkg`
      : `/api/projects/${encodeURIComponent(pid)}/files/${encodeURIComponent(n)}`;
    const dl = n.endsWith(".apkg") ? " download" : "";
    return `<a class="artchip" href="${esc(href)}"${dl} target="${n.endsWith(".apkg") ? "" : "_blank"}" rel="noopener">
      <span class="ai">${ico}</span><span><b>${esc(label)}</b><small>${esc(n)}</small></span></a>`;
  }).join("") + `</div>`;
}
/* B17：仅重渲染单个产物（后端复用审核渲染层；题库内容不变、无 token 消耗） */
async function rerenderArtifact(pid, what) {
  const label = { qbank: "题库", paper: "押题卷", review: "复习手册", anki: "Anki" }[what] || what;
  confirmModal(`仅重渲染「${label}」？`, `<p style="margin:0;color:var(--dim)">不会改动题库内容，只重新生成对应产物文件（无 token 消耗）。</p>`,
    "重渲染", async () => {
      try {
        const r = await api("/api/projects/" + encodeURIComponent(pid) + "/rerender", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ what }) });
        toast("重渲染完成：" + (r.rendered || []).join("、"));
        showProject(pid);
      } catch (e) { toast(e.message, false); }
    }, false);
}
const STEPS = [["websearch", "网络检索"], ["generating", "出题"], ["gate1", "门禁①"], ["qc", "质检"], ["fixing", "修复"],
               ["finalizing", "汇总"], ["reviewing", "复习"], ["rendering", "产物"]];
function stepIdx(stage) {
  if (stage === "done") return STEPS.length;
  // 终态/未进入阶段不给步骤高亮（stage_label 负责显示真实状态，stepper 保持全灰避免误导）
  if (["cancelled", "error", "quota", "parsing"].includes(stage)) return -1;
  const i = STEPS.findIndex(s => s[0] === stage);
  return i === -1 ? 0 : i;
}
function fmtClock(iso) {
  try { const d = new Date(iso); return d.toLocaleTimeString("zh-CN", { hour12: false }); }
  catch (e) { return ""; }
}
function renderStepper(stage, progress) {
  const cur = stepIdx(stage);
  let subStr = "", desc = "";
  if (progress) {
    const pct = progress.pct || 0;
    subStr = progress.sub_total
      ? ` · ${esc(progress.sub || "子任务")} ${progress.sub_done || 0}/${progress.sub_total}`
      : (progress.sub ? ` · ${esc(progress.sub)}` : "");
    desc = progress.detail ? esc(progress.detail)
      : (stage === "done" ? "已完成" : (pct > 0 ? "进行中…" : "准备中…"));
  }
  return STEPS.map((s, i) =>
    `<span class="stp ${i === cur ? "cur" : i < cur ? "done" : ""}">${s[1]}</span>`).join("")
    + `<span class="stp ${cur >= STEPS.length ? "done" : ""}">完成</span>`
    + (progress ? `<div style="flex:1;min-width:180px">
        <div class="pvbar"><i style="width:${progress.pct || 0}%"></i></div>
        <div id="pvtext">${desc} · ${progress.pct || 0}%${subStr}`
        + (progress.updated ? ` · 更新 ${fmtClock(progress.updated)}` : "") + `</div>
      </div>` : "");
}
const SUBSTEP_LABEL = {pending:"排队中", running:"进行中", done:"完成", failed:"失败", retry:"重试", cancelled:"已取消"};
const SUBSTEP_ICON = {pending:"•", running:"⏳", done:"✓", failed:"✗", retry:"↻", cancelled:"⏹"};
function renderSubsteps(rows, stage) {
  // 终态（done/error/cancelled）展示全部最近事件，运行中按当前阶段过滤
  const isTerminal = ["done", "error", "cancelled"].includes(stage);
  const list = (rows || []).filter(s => isTerminal || !stage || s.stage === stage);
  if (!list.length) return `<div class="hint">暂无子步骤记录${stage ? "（当前阶段：" + esc(stage) + "）" : ""}</div>`;
  return `<details class="substeps-card" open>
    <summary>子步骤 · ${list.length} 条${stage ? " · " + (isTerminal ? "全部阶段" : "阶段 " + esc(stage)) : ""}</summary>
    <div class="substeps">` + list.map(s => {
      const st = s.status || "pending";
      const cls = st === "running" ? " running" : st === "done" ? " done"
        : st === "failed" ? " failed" : st === "retry" ? " retry"
        : st === "cancelled" ? " cancelled" : "";
      return `<details class="substep${cls}"${st === "running" ? " open" : ""}>
        <summary><span class="ss-ico">${SUBSTEP_ICON[st] || "•"}</span> ${esc(s.label || s.step)}
          <small class="hint">${esc(SUBSTEP_LABEL[st] || st)}${s.detail ? " · " + esc(s.detail) : ""}</small></summary>
        <div class="substep-detail">${esc(s.step)}${s.detail ? " · " + esc(s.detail) : ""}${s.ts ? " · " + fmtClock(s.ts) : ""}</div>
      </details>`;
    }).join("") + `</div></details>`;
}
async function showProject(pid) {
  if (currentPid && pid !== currentPid && typeof reviewDirtyGuard === "function" && !reviewDirtyGuard()) return;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  const meta = await api("/api/projects/" + pid);
  $("proj_detail").style.display = "block";
  $("pd_title").textContent = `项目详情 · ${meta.subject}`;
  const quota = (meta.quota || []).map(q =>
    `<span>${esc(q.title ? (q.title.length > 14 ? q.title.slice(0, 14) + "…" : q.title) : q.sid)}：${q.count}题</span>`).join("");
  // R3S-01：meta.usage 仅在管线成功跑完才写入——先判空再计算，新建/取消/error 项目详情不再整页崩溃
  const usage = meta.usage ? `<div class="hint" style="margin-top:6px">本次消耗：输入 ${((meta.usage.prompt_tokens || 0) / 10000).toFixed(2)} + 输出 ${((meta.usage.completion_tokens || 0) / 10000).toFixed(2)} 万 token`
    + (meta.usage.est_cost_cny != null ? ` ≈ ¥${meta.usage.est_cost_cny}` : "") + `（以官网为准）</div>` : "";
  // ME-9：Anki 导出按「产物文件是否存在」判断（后端已放开 stage 门禁）——error/取消后已产出的文件同样可下载
  const ankiOk = (meta.artifacts || []).some(n => /\.apkg$/i.test(n) || /^anki_export\.txt$/i.test(n));
  const extra = [];
  if (meta.requirements) extra.push(`附加要求：${esc(meta.requirements).slice(0, 60)}`);
  if (meta.bloom && Object.keys(meta.bloom).length) extra.push(`Bloom：${["记忆","理解","应用","创造"].map(k => (meta.bloom[k] ?? 0) + "%").join("/")}`);
  if (meta.web_search) extra.push(`网络检索${meta.web_ref_quota ? "（引用 " + meta.web_ref_quota + "%）" : ""}`);
  if (meta.image_warning) extra.push(`⚠️ 本轮未产出图题（已有图片素材可重试/加大题量）`);
  if (meta.exam_chars) extra.push(`自备真题 ${meta.exam_chars.toLocaleString()} 字（考点/风格校准，不照抄）`);
  if (meta.extra_chars) extra.push(`补充资料 ${meta.extra_chars.toLocaleString()} 字`);
  if ((meta.artifacts || []).some(n => /人工复核清单/.test(n))) {
    extra.push("📋 人工复核清单：被门禁/质检/网络冲突拦截、需人工确认的题与原因（见下方产物卡片）");
  }
  $("pd_body").innerHTML = `
    <div class="meta hint">${esc(meta.exam)} · 目标 ${meta.target} 题 · 阶段：<span id="pd_stage">${esc(meta.stage_label || meta.stage || "……")}</span><br>
    产物开关：${meta.toggles.qbank ? "题库✓" : "题库✗"} ${meta.toggles.paper ? "押题卷✓" : "押题卷✗"} ${meta.toggles.review ? "复习手册✓" : "复习手册✗"}
    ${ankiOk ? `<a class="btnart" href="/api/projects/${encodeURIComponent(pid)}/export/anki">导出 Anki（.txt）</a>
      <a class="btnart" href="/api/projects/${encodeURIComponent(pid)}/export/apkg" download>S3 导出 Anki（.apkg）</a>
      <a class="btnart" href="javascript:void(0)" onclick="ankiPreview('${esc(pid)}')" title="导出前先看卡面样式">预览 Anki 卡样</a>
      <a class="btnart" href="javascript:void(0)" onclick="ankiHelp()" title="如何把导出文件导入 Anki">Anki 导入指引</a>` : ""}</div>
    ${extra.length ? `<div class="hint" style="margin-top:6px">${extra.join(" · ")}</div>` : ""}
    <div id="pd_stepper" class="stepper">${renderStepper(meta.stage, meta.progress)}</div>
    <div id="pd_substeps" class="substeps-card">${renderSubsteps(meta.substeps, meta.stage)}</div>
    <div class="hint" style="margin-top:6px">各章节配额（教师重点词频加权）：</div>
    <div class="quota">${quota}</div>
    <div id="pd_arts" class="hint" style="margin-top:8px">${artifactLinks(pid, meta.artifacts)}</div>
    ${(meta.artifacts || []).some(n => /^qbank\.html$/i.test(n))
      ? `<div class="hint" style="margin-top:6px">仅重渲染（不重跑管线 · 无 token 消耗）：` +
        Object.entries([["qbank", "题库"], ["paper", "押题卷"], ["review", "复习手册"], ["anki", "Anki"]])
          .map(([w, l]) => `<button class="mini-btn" style="padding:2px 9px" onclick="rerenderArtifact('${esc(pid)}','${w}')">${l}</button>`).join(" ")
        + ` <span class="hint" style="font-size:11px">— 用于只改产物不改题</span></div>`
      : ""}
    ${usage}
    <div id="pd_assets_box" style="margin-top:12px">
      <div class="hint"><b>图片素材（图/表题）</b>：上传教材插图 / 心电图 / 影像 / 辅检表截图，生成时提示出图题（image_ref 门禁校验，错题可随图查看）；无素材项目零影响。</div>
      <div id="pd_assets" class="hint">加载中…</div>
      <div class="row" style="margin-top:6px;flex-wrap:wrap">
        <input type="text" id="pd_asset_cap" placeholder="图注（如：心电图 · 急性心梗）" style="flex:1;min-width:150px">
        <button class="act gray" onclick="pdAssetPick()">上传图片</button>
        <input type="file" id="pd_asset_file" accept="image/*,.png,.jpg,.jpeg,.webp,.gif" style="display:none" onchange="pdAssetUp(this)">
      </div>
    </div>
    <pre id="pd_log"></pre>`;
  currentPid = pid;
  pdAssets();
  stopPoll();
  updateRunBtn(meta.running, meta.stage);
  $("btn_review").style.display = meta.stage === "done" ? "inline-block" : "none";
  $("review_panel").style.display = "none";
  $("proj_detail").scrollIntoView({ behavior: "smooth", block: "start" });
  if (meta.running) startPoll(pid);
  else if (["done", "error", "cancelled"].includes(meta.stage)) loadLog(pid);
}
async function pdAssets() {
  if (!FEATURES.image_q) return;   // IMP-02：flag 关闭时整卡已隐藏，跳过加载
  try {
    const r = await api("/api/projects/" + currentPid + "/assets");
    const box = $("pd_assets");
    if (!r.assets || !r.assets.length) { box.innerHTML = "暂无图片素材——上传后生成时可出图/表题。"; return; }
    box.innerHTML = r.assets.map(a => `<div class="mk-row" style="margin:6px 0">
      <img src="/api/projects/${esc(currentPid)}/assets/${esc(a.sid)}" alt="${esc(a.caption)}"
        style="max-width:160px;max-height:110px;border-radius:8px;border:1px solid var(--line);margin-right:10px;vertical-align:middle"
        onerror="this.style.display='none'">
      <b>${esc(a.sid)}</b> · ${esc(a.caption)}<span class="hint"> · ${((a.bytes || 0) / 1024).toFixed(0)}KB</span>
      <button class="act gray" style="padding:3px 9px;font-size:11px;margin-left:8px" onclick="pdAssetDel('${esc(a.sid)}')">删除</button>
    </div>`).join("");
  } catch (e) { const box = $("pd_assets"); if (box) box.innerHTML = "素材加载失败：" + esc(e.message); }
}
function pdAssetPick() { $("pd_asset_file").click(); }
async function pdAssetUp(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  fd.append("caption", $("pd_asset_cap").value || "");
  try {
    const r = await api("/api/projects/" + currentPid + "/assets", { method: "POST", body: fd });
    toast(`已上传素材 ${r.sid}（生成时出图/表题并做 image_ref 门禁）`);
    pdAssets();
  } catch (e) { toast(e.message, false); }
  finally { input.value = ""; }
}
async function pdAssetDel(sid) {
  await api("/api/projects/" + currentPid + "/assets/" + sid, { method: "DELETE" });
  toast("已删除素材 " + sid);
  pdAssets();
}

async function loadLog(pid) {
  try {
    const s = await api("/api/projects/" + pid + "/status");
    renderLog($("pd_log"), s.log || []);
    const ss = $("pd_substeps");
    if (ss) ss.innerHTML = renderSubsteps(s.substeps, s.stage);
  } catch (e) { /* ignore */ }
}
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } pollFails = 0; }
function updateRunBtn(running, stage, cancelling) {
  const b = $("btn_run");
  if (!b) return;
  if (cancelling) {   // R3-09：取消请求已发出、各阶段检查点陆续生效——禁用停止按钮防重复请求
    b.textContent = "正在取消中…";
    b.disabled = true;
    b.dataset.running = "1";
    b.dataset.resume = "";
    return;
  }
  b.disabled = false;
  if (running) {
    b.textContent = "⏹ 停止生成（保留断点）";
    b.className = "act danger";
    b.dataset.running = "1";
    b.dataset.resume = "";
  } else {
    // B9：error/cancelled 后按钮统一进入「重试生成（从断点）」态（离开再回来也保持）
    const canResume = stage === "error" || stage === "cancelled";
    b.textContent = canResume ? "重试生成（从断点）" : "开始生成";
    b.className = "act";
    b.dataset.running = "";
    b.dataset.resume = canResume ? "1" : "";
  }
}
function fmtLogLine(l) {
  const cls = (l.includes("❌") || l.includes("失败") || l.includes("错误")
    || l.includes("Exception") || l.includes("Traceback")) ? " lg-err"
    : (l.includes("⚠️")) ? " lg-warn" : "";
  return `<div class="lg${cls}">${esc(l)}</div>`;
}
function renderLog(el, lines) {
  if (!el) return;
  el.innerHTML = lines.map(fmtLogLine).join("");
  el.scrollTop = el.scrollHeight;
}
function startPoll(pid) {
  stopPoll();
  pollTimer = setInterval(async () => {
    try {
      const s = await api("/api/projects/" + pid + "/status");
      pollFails = 0;
      const stageEl = $("pd_stage");
      if (stageEl) {
        if (s.cancelling) {
          stageEl.innerHTML = "正在取消中… <span class='spin'></span>";
          stageEl.style.color = "var(--warn)";
        } else {
          stageEl.innerHTML = esc(s.stage_label) + (s.running ? " <span class='spin'></span>" : "");
          stageEl.style.color = "";
        }
      }
      const st = $("pd_stepper");
      if (st) st.innerHTML = renderStepper(s.stage, s.progress);
      const ss = $("pd_substeps");
      if (ss) ss.innerHTML = renderSubsteps(s.substeps, s.stage);
      const logEl = $("pd_log");
      if (logEl) renderLog(logEl, s.log || []);
      const arts = $("pd_arts");
      if (arts && s.artifacts) arts.innerHTML = "已生成：" + artifactLinks(pid, s.artifacts);
      updateRunBtn(s.running, s.stage, s.cancelling);
      if (!s.running && ["done", "error", "cancelled"].includes(s.stage)) {
        stopPoll();
        if (s.stage === "done") { toast("全部产物生成完成 "); $("btn_review").style.display = "inline-block"; }
        if (s.stage === "cancelled") toast("已取消：题目与断点已保留，可再次「开始生成」续跑", false);
        if (s.stage === "error") {
          toast("生成出错：详见下方日志；可点「重试生成（从断点）」续跑（质检及之后阶段会重跑）", false);
        }
        loadLog(pid);
      }
    } catch (e) {
      pollFails++;
      if (pollFails >= 3) {
        stopPoll();
        toast("进度刷新失败：" + e.message, false);
      }
    }
  }, 2500);
}
$("btn_run").onclick = async () => {
  const b = $("btn_run");
  if (b.dataset.busy === "1") return;   // B19：请求在途防双击（重复 POST → 409/双解析）
  try {
    const pid = currentPid;
    if (b.dataset.running === "1") {
      confirmModal("停止生成？",
        "<p>已生成的部分题目与断点会保留；再次「开始生成」将从断点续跑（<b>质检/修复/复习/渲染阶段会重跑</b>）。</p>",
        "停止", async () => {
          b.dataset.busy = "1";
          try {
            await api("/api/projects/" + pid + "/run", { method: "DELETE" });
            toast("正在停止…（已生成部分保留）");
          } catch (e) { toast(e.message, false); }
          finally { b.dataset.busy = ""; }
        });
    } else {
      const resume = b.dataset.resume === "1";
      const go = async () => {
        b.dataset.busy = "1";
        try {
          await api("/api/projects/" + pid + "/run", { method: "POST" });
          toast("管线已启动：⓪网络检索(可选) → MedGen 出题 → 门禁① → MedQC 质检 → MedFix 修复 → 汇总 → MedReview 复习手册 → 渲染产物");
          startPoll(pid);
        } catch (e) { toast(e.message, false); }
        finally { b.dataset.busy = ""; }
      };
      if (resume) {
        // B9：重试前说明重跑范围与费用（用户不知情下重跑可能再次消耗 token）
        confirmModal("从断点重试？",
          "<p>已完成的切片<b>不会</b>重跑；<b>质检/修复/复习/渲染阶段会重跑</b>并产生相应 token 消耗（以实际用量为准）。</p>",
          "从断点重试", go, false);
      } else go();
    }
  } catch (e) { toast(e.message, false); }
};
$("btn_delete").onclick = () => {
  if (!currentPid) return;
  const pid = currentPid;
  confirmModal("确认删除该项目？",
    `项目 <b>${esc(pid)}</b> 及其<b>全部产物</b>将被删除，此操作不可恢复。`,
    "确认删除", async () => {
      try {
        await api("/api/projects/" + pid, { method: "DELETE" });
        toast("项目已删除");
        $("proj_detail").style.display = "none";
        loadProjects();
      } catch (e) { toast(e.message, false); }
    });
};

/* ---- 迭代4：逐题审核台 */
$("btn_review").onclick = () => openReview();
let reviewState = { questions: [], keep: null, drop: new Set(), edits: {}, dirty: false,
                    select: new Set(),
                    filter: { q: "", type: "", bloom: "", year: "" } };
/* 审核台脏状态守卫：切主 tab / 换项目 / 刷新关闭都要确认（防未保存修改静默丢失） */
function reviewDirtyGuard() {
  if (reviewState.dirty && !confirm("审核台有未保存的修改（剔除/编辑/重掷尚未保存），确定离开？修改将丢失。")) return false;
  return true;
}
window.addEventListener("beforeunload", (e) => {
  if (reviewState.dirty) { e.preventDefault(); e.returnValue = ""; }
});
async function openReview(preserve = false, scrollToId = null) {
  if (!currentPid) return;
  $("review_panel").style.display = "block";
  const r = await api("/api/projects/" + currentPid + "/questions");
  const prevEdits = preserve ? reviewState.edits : {};
  const prevDrop = preserve ? reviewState.drop : new Set();
  // 状态保留：只保留仍存在的题目 id（重掷/重渲染后原 id 可能变化）
  const ids = new Set(r.questions.map(x => x.id));
  reviewState.edits = Object.fromEntries(Object.entries(prevEdits).filter(([k]) => ids.has(k)));
  reviewState.drop = new Set([...prevDrop].filter(x => ids.has(x)));
  reviewState.questions = r.questions;
  reviewState.dirty = Object.keys(reviewState.edits).length > 0 || reviewState.drop.size > 0;
  renderReview(scrollToId);
}
function updateRevCount() {
  const el = $("rev_title");
  if (!el) return;
  const qs = reviewState.questions;
  const kept = qs.filter(q => !reviewState.drop.has(q.id)).length;
  el.textContent = `逐题审核（保留 ${kept} / 共 ${qs.length} 题）`;
}
function revFilterText(q) {
  const ed = reviewState.edits[q.id] || {};
  return ((q.id || "") + " " + (q.type || "") + " " + (q.bloom || "") + " " + (q.subtopic || "")
    + " " + (q.source_type || "") + " " + String(q.source_year || "")
    + " " + (ed.question ?? q.question ?? "")).toLowerCase();
}
function applyReviewFilter() {
  const f = reviewState.filter;
  const list = $("rev_list");
  if (!list) return;
  let visible = 0;
  [...list.children].forEach(el => {
    if (el.classList.contains("casehead")) return;
    const qid = el.dataset.qid;
    const q = reviewState.questions.find(x => x.id === qid);
    if (!q) { el.style.display = "none"; return; }
    const okT = !f.type || q.type === f.type;
    const okB = !f.bloom || q.bloom === f.bloom;
    const okQ = !f.q || revFilterText(q).includes(f.q);
    const okY = !f.year || (q.source_year || "") === f.year;
    const show = okT && okB && okQ && okY;
    el.style.display = show ? "" : "none";
    if (show) visible++;
  });
  // 组头：组内无可见题目时隐藏
  [...list.children].forEach(el => {
    if (!el.classList.contains("casehead")) return;
    let any = false, sib = el.nextElementSibling;
    while (sib && !sib.classList.contains("casehead")) {
      if (sib.style.display !== "none") { any = true; break; }
      sib = sib.nextElementSibling;
    }
    el.style.display = any ? "" : "none";
  });
  const cnt = $("rev_filter_cnt");
  if (cnt) cnt.textContent = `筛选后 ${visible} / ${reviewState.questions.length} 题`;
}
/* B10/C-11：答案键校验（与后端 R0 口径一致；先归一化第三口径再判非法键；
   A1/A2/A3/A4/B1 单字母，X 型≥2 字母且不重复；R3-06：无 4 选项地板，按实际选项数） */
function answerIssue(type, ans, optCount) {
  const a = normAnswer(ans);
  const lts = letters(optCount);
  if (!a) return "答案键不能为空";
  if (type === "X") {
    if (a.length < 2) return "X 型答案至少 2 个字母（当前「" + a + "」）";
    if (new Set(a).size !== a.length) return "答案键有重复字母";
  } else if (a.length !== 1) return "单选/案例题答案应为单字母（当前「" + a + "」）";
  if ([...a].some(c => lts.indexOf(c) < 0)) return "含选项字母范围外字符（选项 A~" + (lts.slice(-1) || "") + "）";
  return "";
}
function renderReview(scrollToId = null) {
  const box = $("review_panel");
  const qs = reviewState.questions;
  const kept = qs.filter(q => !reviewState.drop.has(q.id)).length;
  const BLOOMS = ["", "记忆", "理解", "应用", "创造"];
  box.innerHTML = `<div class="card" style="margin-top:14px">
    <div class="cardh"><h2 id="rev_title">逐题审核（保留 ${kept} / 共 ${qs.length} 题）</h2>
      <span class="hint" id="rev_filter_cnt"></span></div>
    <div class="hint">✓ 默认保留 · ✗ 剔除（可反悔）· 行内编辑 · 单题重掷（约 30 秒，消耗一次调用）· 完成后点「保存并重渲染」</div>
    <div class="revtool">
      <input type="search" id="rev_search" placeholder="🔍 搜索题号/类型/考点/题干…" value="${esc(reviewState.filter.q)}">
      <select id="rev_ftype"><option value="">全部题型</option>
        ${["A1", "A2", "B1", "X", "A3", "A4"].map(t => `<option value="${t}" ${reviewState.filter.type === t ? "selected" : ""}>${t}</option>`).join("")}
      </select>
      <select id="rev_fbloom"><option value="">全部层级</option>
        ${BLOOMS.slice(1).map(b => `<option value="${b}" ${reviewState.filter.bloom === b ? "selected" : ""}>${b}</option>`).join("")}
      </select>
      <select id="rev_fyear" title="按真题年份过滤"><option value="">全部年份</option>
        ${[...new Set(qs.map(q => String(q.source_year || "").slice(0, 4)))]
          .filter(y => y)
          .sort().reverse()
          .map(y => `<option value="${esc(y)}" ${reviewState.filter.year === y ? "selected" : ""}>${esc(y)} 年</option>`).join("")}
      </select>
      <span id="rev_batch" style="display:none;align-items:center;gap:6px">
        <span class="hint" id="rev_batch_n" style="margin:0"></span>
        <select id="rev_bb" style="min-width:96px"><option value="">Bloom →</option>
          <option>记忆</option><option>理解</option><option>应用</option><option>创造</option></select>
        <button class="act gray" id="rev_bb_apply" style="padding:8px 12px;font-size:12.5px">应用</button>
        <button class="act gray" id="rev_drop_sel" style="padding:8px 12px;font-size:12.5px;color:#f87171">批量剔除</button>
        <button class="act gray" id="rev_restore_sel" style="padding:8px 12px;font-size:12.5px;color:var(--good)">批量恢复</button>
        <button class="act gray" id="rev_sel_vis" style="padding:8px 12px;font-size:12.5px">全选可见</button>
        <button class="act gray" id="rev_sel_inv" style="padding:8px 12px;font-size:12.5px">反选</button>
        <button class="act gray" id="rev_unsel" style="padding:8px 12px;font-size:12.5px">取消选择</button>
      </span>
      <button class="act gray" id="rev_keepall" style="padding:8px 14px;font-size:12.5px">全部保留</button>
      <button class="act gray" id="rev_dropall" style="padding:8px 14px;font-size:12.5px;color:#f87171">全部剔除</button>
      <button class="act" id="rev_save" ${reviewState.dirty ? "" : "disabled"} style="margin-left:auto">保存并重渲染</button>
      <button class="act gray" id="rev_refresh">刷新</button>
      <button class="act gray" id="rev_hide">隐藏已剔除</button>
    </div>
    <div id="rev_list"></div>
  </div>`;
  const list = $("rev_list");
  $("rev_hide").onclick = () => {
    const hidden = list.classList.toggle("hide-dropped");
    $("rev_hide").textContent = hidden ? "显示已剔除" : "隐藏已剔除";
  };
  $("rev_refresh").onclick = () => {
    if (reviewState.dirty) {
      confirmModal("刷新将丢弃未保存的修改", `<p style="margin:0;color:var(--dim)">当前有未保存的剔除/编辑，刷新会将其丢弃（产物未变）。继续刷新？</p>`,
        "刷新", () => openReview());
    } else openReview();
  };
  $("rev_search").oninput = e => { reviewState.filter.q = e.target.value.trim().toLowerCase(); applyReviewFilter(); };
  $("rev_ftype").onchange = e => { reviewState.filter.type = e.target.value; applyReviewFilter(); };
  $("rev_fbloom").onchange = e => { reviewState.filter.bloom = e.target.value; applyReviewFilter(); };
  $("rev_fyear").onchange = e => { reviewState.filter.year = e.target.value; applyReviewFilter(); };
  $("rev_keepall").onclick = () => {
    reviewState.drop.clear();
    reviewState.dirty = true;
    document.querySelectorAll(".revq").forEach(d => {
      d.classList.remove("dropped");
      d.querySelector("[data-a=drop]").textContent = "✗ 剔除";
    });
    updateRevCount(); $("rev_save").disabled = false;
    toast("已全部保留");
  };
  $("rev_dropall").onclick = () => {
    const f = reviewState.filter;
    const filtered = !!(f.type || f.bloom || f.q);
    // 有筛选时「全部剔除」只作用于当前可见（筛选结果），防误删被隐藏的题
    const visIds = filtered
      ? [...document.querySelectorAll(".revq")].filter(d => d.style.display !== "none").map(d => d.dataset.qid)
      : null;
    const target = visIds && visIds.length ? visIds : qs.map(q => q.id);
    confirmModal(filtered ? "剔除筛选结果？" : "全部剔除？",
      `<p style="margin:0;color:var(--dim)">${filtered
        ? `将把当前筛选后可见的 <b>${target.length} 道题</b>标记为剔除（共 ${qs.length} 题；可逐题恢复后保存）。确定继续？`
        : `将把<b>全部 ${qs.length} 道题</b>标记为剔除（可逐题恢复后保存）。确定继续？`}</p>`,
      "剔除", () => {
        target.forEach(id => reviewState.drop.add(id));
        reviewState.dirty = true;
        document.querySelectorAll(".revq").forEach(d => {
          if (target.includes(d.dataset.qid)) {
            d.classList.add("dropped");
            d.querySelector("[data-a=drop]").textContent = "↩ 恢复";
          }
        });
        updateRevCount(); $("rev_save").disabled = false;
      });
  };
  /* S3：审核台组维度折叠（案例/选项组），子题仍可单独剔除/编辑/重掷 */
  const groups = [];
  const gix = new Map();
  qs.forEach(q => {
    const cid = q.case_id || "";
    if (cid && gix.has(cid)) { groups[gix.get(cid)].items.push(q); return; }
    if (cid) { gix.set(cid, groups.length); groups.push({ cid, stem: q.case_stem || "", items: [] }); }
    if (cid) { groups[gix.get(cid)].items.push(q); }
    else groups.push({ cid: null, items: [q] });
  });
  groups.forEach(g => {
    if (g.cid) {
      const hd = document.createElement("div");
      hd.className = "casehead";
      hd.dataset.cid = g.cid;
      hd.innerHTML = `案例 <b>${esc(g.cid)}</b> · ${g.items.length} 题 · <span class="hint">${esc((g.stem || "").slice(0, 50))}</span>`;
      hd.style.cursor = "pointer";
      hd.onclick = () => {
        let sib = hd.nextElementSibling;
        while (sib && !sib.classList.contains("casehead")) {
          sib.style.display = sib.style.display === "none" ? "" : "none";
          sib = sib.nextElementSibling;
        }
      };
      list.appendChild(hd);
    }
    g.items.forEach(q => {
    const dropped = reviewState.drop.has(q.id);
    const ed = reviewState.edits[q.id] || {};
    // B1 组题共享选项在 group.options（自身 options 为空）——与渲染层 _effective_options 同口径
    const optSrc = (qq, ee) => {
      const o = (ee && ee.options) || qq.options || [];
      if (o.length || qq.group_kind !== "option_group") return o;
      const gg = qq.group || {};
      return Array.isArray(gg.options) ? gg.options : [];
    };
    const opList = optSrc(q, ed);
    const d = document.createElement("div");
    d.className = "revq" + (dropped ? " dropped" : "");
    d.dataset.qid = q.id;
    d.innerHTML = `
      <div class="qhead">
        <input type="checkbox" class="revck" title="勾选以批量操作">
        <b>${esc(q.id)}</b><span class="tag">${esc(q.type)}</span><span class="tag">${esc(q.bloom)}</span>
        ${q.source_type === "真题" ? `<span class="tag" style="background:rgba(245,158,11,.15);color:var(--warn)">${esc((q.source_year ? String(q.source_year).slice(0, 4) + " " : "") + "真题")}</span>` : ""}
        <span class="hint">${esc(q.subtopic || "")}</span>
        <button class="inlineBtn revact" data-a="drop">${dropped ? "↩ 恢复" : "✗ 剔除"}</button>
        <button class="inlineBtn blue revact" data-a="edit">编辑</button>
        <button class="inlineBtn blue revact" data-a="regen">重掷</button>
        <button class="inlineBtn blue revact" data-a="copy" title="复制题面文本到剪贴板">复制</button>
      </div>
      <div class="qbody" data-f="body">${(q.image_ref || q.data_table)
        ? `<div class="hint" style="margin:0 0 6px">${q.image_ref ? `🖼 含图：${esc(q.image_ref)}（如图所示）` : ""}${q.data_table ? `<span class="tag">📋 含表格数据</span>` : ""}</div>` : ""}${esc(ed.question ?? q.question)}
        <ul>${opList.map((o, i) => `${letters(opList.length)[i]}. ${esc(o)}`).join("</li><li>")}</ul>
        <div class="hint good">✓ 答案：${esc(ed.answer ?? q.answer)} · ${esc((ed.analysis ?? q.analysis) || "")}</div>
      </div>
      <div class="optsrow" data-f="editrow" style="${ed._editOpen ? "" : "display:none"}">
        <select class="eb" data-e="type" style="max-width:96px" title="题型">
          <option value="">题型</option>
          ${["A1","A2","A3","A4","B1","X"].map(t => `<option value="${t}" ${(ed.type ?? q.type) === t ? "selected" : ""}>${t}</option>`).join("")}
        </select>
        <input class="eb" data-e="subtopic" placeholder="章节/知识点" value="${esc(ed.subtopic ?? q.subtopic ?? "")}" style="max-width:170px">
        <input class="eb" data-e="question" placeholder="题干" value="${esc(ed.question ?? q.question)}">
        <input class="eb" data-e="analysis" placeholder="解析" value="${esc((ed.analysis ?? q.analysis) || "")}">
        <input class="eb" data-e="answer" placeholder="答案键（如 B / BDE）" value="${esc(ed.answer ?? q.answer)}" style="max-width:120px">
        <input class="eb" data-e="bloom" placeholder="Bloom（记忆/理解/应用/创造）" value="${esc(ed.bloom ?? q.bloom)}" style="max-width:180px">
      </div>
      <div class="hint bad" data-f="anschk" style="display:none;margin:4px 0"></div>
      <div class="optsrow" data-f="editopts" style="${ed._editOpen ? "" : "display:none"}">
        ${q.group_kind === "option_group"
          ? `<div class="hint" style="margin:0 0 4px">共享选项（本组所有子题共用）——修改会同步整组</div>
             <input class="eb" data-e="groupoptions" placeholder="每行一个共享选项（所有子题共用）" value="${esc((ed.groupOptions || (q.group && q.group.options) || []).join("\n"))}">`
          : opList.map((o, i) =>
              `<input class="eb" data-e="opt${i}" placeholder="选项${letters(opList.length)[i]}" value="${esc(o)}">`).join("")}
      </div>`;
    d.querySelector("[data-a=drop]").onclick = () => {
      const dropBtn = d.querySelector("[data-a=drop]");
      if (reviewState.drop.has(q.id)) reviewState.drop.delete(q.id);
      else reviewState.drop.add(q.id);
      reviewState.dirty = true;
      const nowDropped = reviewState.drop.has(q.id);
      d.classList.toggle("dropped", nowDropped);
      dropBtn.textContent = nowDropped ? "↩ 恢复" : "✗ 剔除";
      updateRevCount();
      $("rev_save").disabled = false;
    };
    d.querySelector("[data-a=edit]").onclick = () => {
      const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
      e._editOpen = !e._editOpen;
      d.querySelector('[data-f="editrow"]').style.display = e._editOpen ? "" : "none";
      d.querySelector('[data-f="editopts"]').style.display = e._editOpen ? "" : "none";
    };
    d.querySelector("[data-a=copy]").onclick = () => {
      const e = reviewState.edits[q.id] || {};
      const optsSrc = (e.options ?? optSrc(q, null));
      const L = letters(optsSrc.length);   // R3-16：复制题面同样按实际选项数
      const opts = optsSrc.map((o, i) => `${L[i]}. ${o}`).join("\n");
      const text = `[${q.id}] ${q.type} · ${(e.bloom ?? q.bloom) || ""} · ${(e.subtopic ?? q.subtopic) || ""}\n`
        + `${(e.question ?? q.question) || ""}\n${opts}\n答案：${(e.answer ?? q.answer) || ""}\n解析：${(e.analysis ?? q.analysis) || ""}`;
      copyText(text, d.querySelector("[data-a=copy]"));
    };
    d.querySelector("[data-a=regen]").onclick = async () => {
      // B12：案例/选项组子题、图/表题重掷会破坏组结构或 image_ref——前端先拦截
      if (q.group_kind === "case" || q.group_kind === "option_group" || q.case_id || q.image_ref || q.data_table) {
        toast("该题属于案例/选项组或含图/表：重掷会破坏组结构或图题引用，请用「编辑」修改", false);
        return;
      }
      const btn = d.querySelector("[data-a=regen]");
      btn.disabled = true; btn.textContent = "重掷中…";
      try {
        const rr = await api("/api/projects/" + currentPid + "/regen", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: q.id }) });
        toast(rr.warning ? `已重掷 ${q.id} · ${rr.warning}` : `已重掷 ${q.id}`);   // C-16：新题含案例字段 → 提示
        // 保留其它题目的编辑/剔除状态；该题重掷后作废旧编辑
        delete reviewState.edits[q.id];
        reviewState.drop.delete(q.id);
        await openReview(true, q.id);
      } catch (e) { toast(e.message, false); btn.disabled = false; btn.textContent = "重掷"; }
    };
    /* B10：答案键按题型即时校验（A 型单字母 / X 型≥2 字母且均在选项范围内） */
    const checkAns = () => {
      const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
      const t = e.type || q.type || "";
      const a = e.answer !== undefined ? e.answer : q.answer;
      const n = e.options ? e.options.length : opList.length;
      const err = t ? answerIssue(t, a, n) : "";
      e._answerInvalid = err;
      const c = d.querySelector('[data-f="anschk"]');
      if (c) { c.style.display = err ? "" : "none"; c.textContent = err ? "✗ " + err : ""; }
    };
    d.querySelectorAll("[data-e]").forEach(inp => {
      inp.oninput = () => {
        const e = reviewState.edits[q.id] = reviewState.edits[q.id] || {};
        const k = inp.dataset.e;
        if (k === "groupoptions") {
          // R3-11：共享选项入口 → 每行一项，写 group.options（保存时后端同步整组）
          e.groupOptions = inp.value.split("\n").map(x => x.trim()).filter(Boolean);
          e.options = e.groupOptions;
        } else if (k.startsWith("opt")) { e.options = (e.options || optSrc(q, null).slice()); e.options[+k.slice(3)] = inp.value; }
        else e[k] = inp.value;
        checkAns();
        reviewState.dirty = true;
        $("rev_save").disabled = false;
      };
      if (inp.tagName === "SELECT") inp.onchange = inp.oninput;
    });
    checkAns();
    list.appendChild(d);
    });
  });
  /* ---- 批量操作（勾选 → 改 Bloom / 批量剔除 / 批量恢复 / 取消选择）---- */
  reviewState.select = reviewState.select || new Set();
  const updBatch = () => {
    const bar = $("rev_batch");
    const n = reviewState.select.size;
    bar.style.display = n ? "flex" : "none";
    $("rev_batch_n").textContent = n ? `已选 ${n} 题 · ` : "";
  };
  const setDrop = (id, now) => {
    const card = document.querySelector(`.revq[data-qid="${CSS.escape(id)}"]`);
    if (!card) return;
    card.classList.toggle("dropped", now);
    const b = card.querySelector("[data-a=drop]");
    if (b) b.textContent = now ? "↩ 恢复" : "✗ 剔除";
  };
  document.querySelectorAll(".revq .revck").forEach(ck => {
    const qid = ck.closest(".revq").dataset.qid;
    ck.onchange = () => { if (ck.checked) reviewState.select.add(qid); else reviewState.select.delete(qid); updBatch(); };
  });
  $("rev_bb_apply").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    const v = $("rev_bb").value;
    if (!v) return toast("请先选择要应用的 Bloom 层级", false);
    reviewState.select.forEach(id => {
      reviewState.edits[id] = reviewState.edits[id] || {};
      reviewState.edits[id].bloom = v;
      const tags = document.querySelectorAll(`.revq[data-qid="${CSS.escape(id)}"] .qhead .tag`);
      if (tags[1]) tags[1].textContent = v;
    });
    reviewState.dirty = true;
    $("rev_save").disabled = false;
    toast(`已将 ${reviewState.select.size} 题的 Bloom 改为「${v}」`);
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_drop_sel").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    reviewState.select.forEach(id => { reviewState.drop.add(id); setDrop(id, true); });
    reviewState.dirty = true; $("rev_save").disabled = false;
    toast(`已剔除 ${reviewState.select.size} 题（保存后生效）`);
    updateRevCount();
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_restore_sel").onclick = () => {
    if (!reviewState.select.size) return toast("请先勾选题目", false);
    reviewState.select.forEach(id => { reviewState.drop.delete(id); setDrop(id, false); });
    reviewState.dirty = true; $("rev_save").disabled = false;
    toast(`已恢复 ${reviewState.select.size} 题`);
    updateRevCount();
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  $("rev_unsel").onclick = () => {
    reviewState.select.clear();
    document.querySelectorAll(".revq .revck").forEach(c => { c.checked = false; });
    updBatch();
  };
  /* B13：全选当前筛选可见项 / 反选（均只作用于可见项，避免误选被筛选隐藏的题） */
  const visChecks = () => [...document.querySelectorAll(".revq")]
    .filter(d => d.style.display !== "none").map(d => d.querySelector(".revck")).filter(Boolean);
  $("rev_sel_vis").onclick = () => {
    visChecks().forEach(c => { c.checked = true; reviewState.select.add(c.closest(".revq").dataset.qid); });
    updBatch();
  };
  $("rev_sel_inv").onclick = () => {
    visChecks().forEach(c => {
      c.checked = !c.checked;
      const id = c.closest(".revq").dataset.qid;
      if (c.checked) reviewState.select.add(id); else reviewState.select.delete(id);
    });
    updBatch();
  };
  updBatch();
  $("rev_save").onclick = async () => {
    // B10：答案键校验未通过的编辑不允许保存（防单选/多选键错乱污染产物与判分）
    const bad = Object.entries(reviewState.edits)
      .filter(([k, v]) => v._answerInvalid && !reviewState.drop.has(k));
    if (bad.length) {
      const first = bad[0][0];
      toast(`答案键校验未通过（如 ${first}），请修正后再保存`, false);
      const el = document.querySelector(`.revq[data-qid="${CSS.escape(first)}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const keep = qs.map(x => x.id).filter(id => !reviewState.drop.has(id));
    // C-10/R3-14：保留 0 题 → 弹确认说明并中止（后端拒绝保存空题库；剔除意图不再静默蒸发）
    if (qs.length && !keep.length) {
      confirmModal("无法保存空题库",
        "<p>你将剔除全部题目（后端拒绝保存空题库）。<br>请至少保留一题；整卷作废请到「我的项目」删除项目。</p>",
        "知道了", null, false);
      return;
    }
    const edits = Object.entries(reviewState.edits).filter(([k, v]) => k !== "drop" && v && Object.keys(v).some(x => !x.startsWith("_")) && !reviewState.drop.has(k))
      .map(([id, v]) => {
        const clean = { id };
        ["question", "options", "answer", "analysis", "bloom", "type", "subtopic"].forEach(f => {
          if (v[f] !== undefined) clean[f] = (f === "answer" ? normAnswer(v[f]) : v[f]);   // C-11：存紧凑形式 BD
        });
        return clean;
      });
    const btn = $("rev_save");
    btn.disabled = true; btn.textContent = "保存中…";
    try {
      // B15：明示重渲染范围（题库/押题卷/手册/Anki/.apkg），避免大批量保存时误以为卡死
      $("rev_filter_cnt").textContent = "正在重渲染 5 项产物（题库 MD/HTML · 押题卷 · 复习手册 · Anki）…";
      await api("/api/projects/" + currentPid + "/questions/review", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep: keep, drop: [], edits: edits }) });
      toast("已保存并重渲染全部产物");
      await openReview();
      await showProject(currentPid);
    } catch (e) {
      toast(e.message, false);
      btn.disabled = false; btn.textContent = "保存并重渲染";
      applyReviewFilter();   // B33：失败后还原「正在重渲染…」状态文案（恢复筛选计数）
    }
  };
  applyReviewFilter();
  if (scrollToId) {
    const el = document.querySelector(`.revq[data-qid="${CSS.escape(scrollToId)}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/* ---- ④ 提示词 */
let promptCache = null;
async function loadPrompts() {
  const box = $("prompt_list");
  if (!box) return;
  box.innerHTML = '<div class="hint"><span class="spin"></span>加载中…</div>';
  try {
    const r = await api("/api/prompts");
    promptCache = r.prompts;
    box.innerHTML = "";
    if (!r.prompts.length) {
      box.innerHTML = '<div class="empty"><div class="sub">未找到内置提示词（安装可能不完整）</div></div>';
      return;
    }
    r.prompts.forEach(p => renderPrompt(p));
  } catch (e) {
    box.innerHTML = `<div class="hint bad">加载失败：${esc(e.message)}</div>`;
  }
}
function promptStatusBadge(p) {
  if (p.using === "custom" && p.drifted) return `<span class="pstatus drift">官方已更新 · 使用旧自定义</span>`;
  if (p.using === "custom") return `<span class="pstatus custom">已自定义（影子副本生效中）</span>`;
  return `<span class="pstatus">使用内置</span>`;
}
function renderPrompt(p) {
  const d = document.createElement("details");
  d.className = "card";
  d.style.marginTop = "12px";
  const ph = (p.placeholders || []).map(x => `<span class="chip" data-ph="${esc(x)}">${esc(x)}</span>`).join("");
  d.innerHTML = `
    <summary style="font-size:15px">
      <span class="ppm" style="display:inline-flex;margin:0">
        <span class="b">${esc(p.name)}</span><span>${esc(p.role)}</span>${promptStatusBadge(p)}
      </span>
    </summary>
    <div class="hint">占位符（运行时替换，勿删）：<span class="phchips">${ph}</span></div>
    <div class="btns">
      <button class="act gray" onclick="openPromptEdit('${esc(p.name)}')">编辑</button>
      ${p.using === "custom" ? `<button class="act gray" onclick="restorePrompt('${esc(p.name)}')">恢复默认</button>
      <button class="act gray" onclick="diffPrompt('${esc(p.name)}')">与官方版对比</button>` : ""}
    </div>
    <pre class="pview" id="pv_${esc(p.name).replace(".", "_")}">${esc(p.content)}</pre>
    <div id="peditor_${esc(p.name).replace(".", "_")}" style="display:none"></div>
    <div id="pdiff_${esc(p.name).replace(".", "_")}" style="display:none"></div>`;
  // 占位符高亮
  const pre = d.querySelector("pre.pview");
  let html = esc(p.content);
  (p.placeholders || []).forEach(x => { html = html.split(esc(x)).join(`<mark>${esc(x)}</mark>`); });
  pre.innerHTML = html;
  box_append(promptContainer(), d);
}
function promptContainer() { return $("prompt_list"); }
function box_append(el, d) { el.appendChild(d); }
async function openPromptEdit(name) {
  const r = promptCache.find(x => x.name === name);
  if (!r) return;
  const cur = r.content;
  const ed = $("peditor_" + name.replace(".", "_"));
  ed.style.display = "block";
  ed.innerHTML = `
    <textarea class="pedit" id="pedit_${esc(name).replace(".", "_")}" style="width:100%">${esc(cur)}</textarea>
    <div class="hint" id="phcheck_${esc(name).replace(".", "_")}">校验：编辑时请保留全部占位符</div>
    <div class="btns">
      <button class="act" onclick="savePrompt('${esc(name)}')">保存（写入影子副本）</button>
      <button class="act gray" onclick="document.getElementById('peditor_${esc(name).replace(".", "_")}').style.display='none'">取消</button>
    </div>`;
  const ta = ed.querySelector("textarea");
  ta.oninput = () => {
    const missing = (r.placeholders || []).filter(x => !ta.value.includes(x));
    const el = $("phcheck_" + name.replace(".", "_"));
    el.textContent = missing.length ? `缺少：${missing.join("、")}` : "占位符齐全 ✓";
    el.className = "hint " + (missing.length ? "bad" : "good");
    ed.querySelectorAll(".phchips .chip").forEach(c => {
      const t = c.dataset.ph;
      c.classList.toggle("missing", missing.includes(t));
    });
  };
  ed.querySelector("textarea").focus();
}
async function savePrompt(name) {
  const ta = document.querySelector("#peditor_" + name.replace(".", "_") + " textarea");
  try {
    const r = await api("/api/prompts/" + encodeURIComponent(name), { method: "PUT",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: ta.value }) });
    toast("已保存影子副本（生效中）");
    loadPrompts();
    confirmModal("先试出一题验证效果？", "提示词已改——建议在「新建课题」点「试出一题」验证效果，防止改坏。",
      "去试出题", () => { location.hash = "bank"; showTab("bank"); }, false);
  } catch (e) { toast(e.message, false); }
}
async function restorePrompt(name) {
  confirmModal("恢复默认？", `将删除影子副本，恢复内置提示词（对「${esc(name)}」）。`,
    "恢复默认", async () => {
      try { await api("/api/prompts/" + encodeURIComponent(name), { method: "DELETE" }); toast("已恢复内置"); loadPrompts(); }
      catch (e) { toast(e.message, false); }
    });
}
async function diffPrompt(name) {
  const r = promptCache.find(x => x.name === name);
  if (!r || !r.custom) return;
  const el = $("pdiff_" + name.replace(".", "_"));
  el.style.display = "block";
  const a = r.builtin.split("\n"), b = r.custom.split("\n");
  const n = Math.max(a.length, b.length);
  let la = "", lb = "";
  for (let i = 0; i < n; i++) {
    const sa = a[i] || "", sb = b[i] || "";
    const mark = sa !== sb;
    la += (mark ? "<b>" : "") + esc(sa || " ") + (mark ? "</b>" : "") + "\n";
    lb += (mark ? "<b>" : "") + esc(sb || " ") + (mark ? "</b>" : "") + "\n";
  }
  el.innerHTML = `<div class="diffwrap"><pre>【内置】\n${la}</pre><pre>【自定义】\n${lb}</pre></div>`;
}

/* ---- 首启欢迎向导（医学生视角，3 步）----
   触发：未配置 Key 且从未完成引导；完成或「跳过」写 localStorage，关闭/ESC 不写（下次再提示） */
let wzStep = 0;
function wzDone() {
  try { localStorage.setItem("medkit-onboarded", "1"); } catch (e) { /* ignore */ }
  $("wizard_mask").style.display = "none";
}
/* 关闭/ESC 不写「已完成」（下次仍会提示），但记本会话内不再重复弹（防反复打扰） */
function wzClose() {
  try { sessionStorage.setItem("medkit-wz-seen", "1"); } catch (e) { /* ignore */ }
  $("wizard_mask").style.display = "none";
}
function wzSetDots() {
  document.querySelectorAll(".wsteps i").forEach((d, i) => d.classList.toggle("on", i === wzStep));
}
function wzRender() {
  wzSetDots();
  const body = $("wz_body"), btns = $("wz_btns");
  if (wzStep === 0) {
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">把你的<b>教材 + 老师划的重点</b>交给 AI，本地生成一套全新的复习资料。
      所有素材只保存在你自己的电脑上，AI 调用使用你自己的 API Key（约 ¥1~5/套，费用透明可见）。</div>
      <div class="wgrid">
        <div class="wcard"><svg class="ic"><use href="#i-bank"></use></svg><b>全新题库</b><span>按你的教材章节出题<br>A1/A2/X 型 + 图表题</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-paper"></use></svg><b>交互押题卷</b><span>计时答题 · 自动判分<br>错题重练 · 可打印</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-learn"></use></svg><b>复习手册</b><span>考点速记 / 易混淆<br>临床路径 / 数值速查</span></div>
        <div class="wcard"><svg class="ic"><use href="#i-target"></use></svg><b>学习中心</b><span>错题沉淀 · 掌握度诊断<br>教材讲解 · 提问式复习</span></div>
      </div>
      <div class="hint">还可以导出 <b>Anki 卡片包</b>，直接导入 Anki 背题。</div>`;
    btns.innerHTML = `<button class="act" id="wz_next">下一步：连接 AI（2 分钟）</button>`;
    $("wz_next").onclick = () => { wzStep = 1; wzRender(); };
  } else if (wzStep === 1) {
    const provs = (state.providers || []).filter(p => p.register_url);
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">出题需要一个大模型「API Key」——相当于你和 AI 服务商之间的<b>充值卡</b>。
      推荐注册 <b>DeepSeek</b>（便宜，充值 ¥10 可出多套题）：点官网注册 → 充值 → 复制 Key，回到本软件「我的 → 连接服务商」粘贴保存即可。</div>
      ${provs.map(p => `<div class="wprov"><svg class="ic" style="width:20px;height:20px;color:var(--accent);flex:none"><use href="#i-key"></use></svg>
        <b>${esc(p.name)}</b><span>${esc(p.note || "")}</span>
        <a class="provlink" href="${esc(p.register_url)}" target="_blank" rel="noopener">官网注册 ↗</a></div>`).join("")}
      <div class="hint" style="margin-top:10px">不知道选哪个？先用 DeepSeek 就够了。稍后再配置也不影响了解软件。</div>`;
    btns.innerHTML = `<button class="act gray" id="wz_back">上一步</button>
      <button class="act" id="wz_next">我已拿到 Key（去配置）</button>
      <button class="act gray" id="wz_later">稍后配置，先看看</button>`;
    $("wz_back").onclick = () => { wzStep = 0; wzRender(); };
    $("wz_next").onclick = () => { wzDone(); showTab("mine"); $("api_key").focus(); };
    $("wz_later").onclick = () => { wzStep = 2; wzRender(); };
  } else {
    body.innerHTML = `
      <div class="hint" style="line-height:1.9">准备好了？两种开始方式任选：</div>
      <div class="wentry" id="wz_demo" role="button" tabindex="0">
        <svg class="ic"><use href="#i-paper"></use></svg>
        <div><b>载入示例，立即体验</b><span>不用上传任何文件，30 秒看懂出题效果（点「试出一题」）</span></div>
      </div>
      <div class="wentry" id="wz_own" role="button" tabindex="0">
        <svg class="ic"><use href="#i-proj"></use></svg>
        <div><b>用我自己的教材开始</b><span>上传 PDF/Word 教材 + 老师重点 → 创建课题 → 生成</span></div>
      </div>`;
    btns.innerHTML = `<button class="act gray" id="wz_back">上一步</button>
      <button class="act" id="wz_fin">完成，开始使用</button>`;
    $("wz_back").onclick = () => { wzStep = 1; wzRender(); };
    $("wz_fin").onclick = () => wzDone();
    const goDemo = () => {
      // ME-1/A1：无 Key 时「载入示例」= 流程必然失败——先定向到连接页；
      // R3-05/A-新1：先关遮罩再跳（否则盖板压在连接页上，表单点不到）
      if (!(state.cfg && state.cfg.api_key_masked)) {
        toast("试出一题需要用 API Key——请先完成「我的 → 连接服务商」配置（充值 ¥10 可出多套题）", false);
        wzClose();
        showTab("mine"); $("api_key").focus();
        return;
      }
      wzDone(); showTab("bank"); $("btn_sample").click();
    };
    const goOwn = () => { wzDone(); showTab("bank"); };
    $("wz_demo").onclick = goDemo;
    $("wz_demo").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goDemo(); } };
    $("wz_own").onclick = goOwn;
    $("wz_own").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goOwn(); } };
  }
}
function maybeShowWizard() {
  try {
    if (localStorage.getItem("medkit-onboarded")) return;
    if (sessionStorage.getItem("medkit-wz-seen")) return;   // 本会话已看过（关闭/ESC 过）→ 不重复弹
  } catch (e) { return; }
  const c = state.cfg;
  if (!c || c.api_key_masked) return;   // 已有 Key → 老用户，不打扰
  wzStep = 0; wzRender();
  $("wizard_mask").style.display = "flex";
}
$("wz_skip").onclick = wzDone;
$("wizard_mask").addEventListener("click", e => { if (e.target.id === "wizard_mask") wzClose(); });

/* ---- init */
ratioSum(); bloomSum();
/* Ctrl/⌘+1..5 快速切换页签（v0.8.1：开始/刷题/题库/学习中心/我的） */
const TAB_KEYS = ["", "start", "study", "bank", "learn", "mine"];
window.addEventListener("keydown", e => {
  // A6：焦点在输入框/编辑器时不触发页签快捷键（防打字时被切走/吞键）
  const t = e.target;
  // A-新15：焦点守卫纳入 SELECT（下拉聚焦时不触发页签快捷键，防误切/吞键）
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  if (!(e.ctrlKey || e.metaKey) || !(e.key >= "1" && e.key <= "5")) return;
  const tk = TAB_KEYS[+e.key];
  if (!tk) return;
  e.preventDefault();
  location.hash = tk;
  showTab(tk);
});
/* 窄屏适配：窗口尺寸变化（如侧栏折叠）→ 防抖重绘配比条，重新做标签像素级适配 */
let segResizeT = 0;
window.addEventListener("resize", () => {
  clearTimeout(segResizeT);
  segResizeT = setTimeout(() => {
    if ($("tab-bank").classList.contains("show")) { ratioSum(); bloomSum(); }
  }, 120);
});
$("req_count").textContent = "0/500";
api("/api/health").then(h => {
  state.version = h.version || "";
  $("side_ver").textContent = "v" + (h.version || "");
}).catch(() => {});
/* v0.6:版本号可点 → 手动检查更新;启动 4s 后静默检查一次 */
$("side_ver").onclick = () => checkUpdate(false);
$("side_ver").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); checkUpdate(false); } };
setTimeout(() => checkUpdate(true), 4000);
loadConfig().then(maybeShowWizard).catch(e => toast(e.message, false));
probeSampleAvailability();
