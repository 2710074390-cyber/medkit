/* =========================================================================
   外壳：路由 / 各页面渲染 / 导入导出
   -------------------------------------------------------------------------
   四个页面各自独立渲染，共享一个 toast 与一个底部导航。
   没有框架：这个 App 的页面数固定为 4，引入框架的收益（响应式、组件复用）
   在这里抵不过"多一个构建步骤"的成本。
   ========================================================================= */
(function () {
  "use strict";

  const $ = id => document.getElementById(id);
  const TITLES = {
    drill: ["刷题", "选一个题库开始"],
    mistakes: ["错题", "按薄弱点聚类"],
    review: ["复习", "到你该复习的时候了"],
    data: ["数据", "同步与备份"],
  };

  let tab = "drill";
  let drillSession = null;
  let reviewSession = null;
  let lastResult = null;

  // ---------------------------------------------------------------- 小工具
  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 2200);
  }
  window.toast = toast;

  const esc = s => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  function fmtDate(iso) {
    if (!iso) return "";
    const d = String(iso).slice(0, 10);
    return d === SM2.todayISO() ? "今天" : d;
  }

  function statHTML(list) {
    return list.map(s => `
      <div class="stat ${s.tone || ""}">
        <div class="stat-n">${esc(s.n)}</div>
        <div class="stat-k">${esc(s.k)}</div>
      </div>`).join("");
  }

  // ---------------------------------------------------------------- 路由
  function switchTab(name) {
    tab = name;
    document.querySelectorAll(".tabbar button").forEach(b => {
      const on = b.dataset.tab === name;
      b.classList.toggle("on", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    for (const t of ["drill", "mistakes", "review", "data"]) {
      $(`scr-${t}`).hidden = t !== name;
    }
    const [title, sub] = TITLES[name];
    $("page_title").textContent = title;
    $("page_sub").textContent = sub;
    document.title = `${title} · MedKit 刷题`;
    location.hash = name;
    if (name === "drill") renderDrillHome();
    if (name === "mistakes") renderMistakes();
    if (name === "review") renderReview();
    if (name === "data") renderData();
  }

  // ---------------------------------------------------------------- 刷题
  async function renderDrillHome() {
    if (drillSession) return;              // 正在作答：不要被切页打断
    const s = await Model.stats();
    $("drill_run").hidden = true;
    if (!s.items) {
      $("drill_empty").hidden = false;
      $("drill_home").hidden = true;
      return;
    }
    $("drill_empty").hidden = true;
    $("drill_home").hidden = false;
    $("drill_stats").innerHTML = statHTML([
      { n: s.items, k: "题库总题数" },
      { n: s.mistakes, k: "错题", tone: s.mistakes ? "alert" : "" },
      { n: s.todayDone, k: "今日已做" },
    ]);

    const banks = await DB.all("banks");
    $("bank_list").innerHTML = banks.map(b => `
      <button class="rowbtn" data-bank="${esc(b.id)}">
        <div class="rowbtn-main">
          <div class="rowbtn-title">${esc(b.name)}</div>
          <div class="rowbtn-sub">
            <span>${b.item_count || 0} 题</span>
            ${b.subject ? `<span>· ${esc(b.subject)}</span>` : ""}
            ${lastResult && lastResult.bank === b.id
              ? `<span class="tag">上次 ${lastResult.right}/${lastResult.total}</span>` : ""}
          </div>
        </div>
        <span class="rowbtn-go" aria-hidden="true">›</span>
      </button>`).join("") || `<p class="sec-hint">还没有题库。</p>`;

    $("bank_list").querySelectorAll("[data-bank]").forEach(btn => {
      btn.addEventListener("click", () => startBank(btn.dataset.bank, btn));
    });
  }

  async function startBank(bid, btn) {
    const bank = await DB.get("banks", bid);
    const items = await DB.byIndex("items", "bank_id", bid);
    if (!items.length) { toast("这个题库里没有题目"); return; }
    // 从上次中断处继续（同一天内）——手机刷题常常被打断
    const key = `pos:${bid}`;
    const pos = await DB.getMeta(key, 0);
    const startAt = Number.isFinite(pos) && pos > 0 && pos < items.length ? pos : 0;
    if (startAt) toast(`从第 ${startAt + 1} 题继续`);
    startDrill(items, { bankId: bid, bankName: bank ? bank.name : "", startAt });
  }

  function startDrill(items, opts) {
    opts = opts || {};
    const ordered = items.slice();
    if (opts.startAt) ordered.push(...ordered.splice(0, opts.startAt));
    lastResult = null;
    if (opts.bankName) $("page_sub").textContent = `${opts.bankName} · 共 ${ordered.length} 题`;
    $("drill_home").hidden = true;
    $("drill_empty").hidden = true;
    $("drill_run").hidden = false;
    const holder = document.createElement("div");
    drillSession = Drill({
      items: ordered,
      card: $("drill_card"),
      counter: $("drill_counter"),
      prog: $("drill_prog"),
      onFinish: async (r) => {
        drillSession = null;
        lastResult = { bank: opts.bankId || "", right: r.right, total: r.total };
        if (opts.bankId) await DB.setMeta(`pos:${opts.bankId}`, 0);
        $("drill_run").hidden = true;
        $("drill_home").hidden = false;
        $("page_sub").textContent = TITLES.drill[1];
        await renderDrillHome();
        toast(`做完 ${r.total} 题，对 ${r.right} 题${r.wrong ? `，错了 ${r.wrong} 题已进错题` : ""}`);
      },
      onExit: () => { drillSession = null; },
    });
    $("drill_quit").onclick = async () => {
      if (!drillSession) return;
      drillSession = null;
      if (opts.bankId) await DB.setMeta(`pos:${opts.bankId}`, 0);
      $("drill_run").hidden = true;
      $("drill_home").hidden = false;
      $("page_sub").textContent = TITLES.drill[1];
      await renderDrillHome();
    };
    drillSession.start();
    holder.remove();
  }

  // ---------------------------------------------------------------- 错题
  async function renderMistakes() {
    const s = await Model.stats();
    $("mk_stats").innerHTML = statHTML([
      { n: s.mistakes, k: "错题总数" },
      { n: s.due, k: "待复习", tone: s.due ? "alert" : "ok" },
      { n: s.mastered, k: "已掌握", tone: "ok" },
    ]);
    const groups = await Model.groupMistakes();
    const box = $("mk_groups");
    if (!groups.length) {
      box.innerHTML = `<div class="empty"><div class="empty-ico">🎉</div>
        <h2>还没有错题</h2><p>刷题时答错的题会自动收进来，并按考点聚类。</p>
        <button class="btn" data-go="drill">去刷题</button></div>`;
      bindGo(box);
      return;
    }
    box.innerHTML = groups.map((g, i) => `
      <button class="rowbtn" data-group="${esc(g.key)}">
        <div class="rowbtn-main">
          <div class="rowbtn-title">${esc(g.label)}</div>
          <div class="rowbtn-sub">
            <span>${g.count} 道错题</span>
            ${g.due ? `<span class="tag hot">${g.due} 待复习</span>` : ""}
            ${g.lastAt ? `<span>最近 ${fmtDate(g.lastAt)}</span>` : ""}
          </div>
        </div>
        <span class="rowbtn-go" aria-hidden="true">›</span>
      </button>`).join("");
    box.querySelectorAll("[data-group]").forEach(btn => {
      btn.addEventListener("click", () => drillGroup(btn.dataset.group));
    });
  }

  async function drillGroup(key) {
    const rows = await Model.mistakesOfGroup(key);
    if (!rows.length) return;
    const items = rows.map(m => ({
      id: m.item_id || m.id,
      mistake_id: m.id,           // 让判错时更新同一条错题，而不是再派生一条
      bank_id: m.bank_id, bank_name: m.bank_name,
      question: m.question, options: m.options, answer: m.answer,
      analysis: m.analysis, case_stem: m.case_stem,
      subject: m.subject, subtopic: m.subtopic,
    }));
    switchTab("drill");
    startDrill(items, { bankId: "", bankName: key });
  }

  // ---------------------------------------------------------------- 复习
  async function renderReview() {
    if (reviewSession) return;
    const cards = await Model.dueList();
    for (const id of ["rv_empty", "rv_run", "rv_done"]) $(id).hidden = true;
    if (!cards.length) {
      $("rv_empty").hidden = false;
      const s = await Model.stats();
      const sub = $("rv_empty_sub");
      sub.textContent = s.mistakes
        ? `下一批会在该复习的时候出现。目前共 ${s.mistakes} 道错题，其中 ${s.mastered} 道已掌握。`
        : "还没有错题。刷题时答错的题会自动进入复习队列。";
      return;
    }
    $("rv_run").hidden = false;
    reviewSession = Review({
      cards,
      card: $("rv_card"),
      counter: $("rv_counter"),
      prog: $("rv_prog"),
      onDone: (stats) => {
        reviewSession = null;
        $("rv_run").hidden = true;
        $("rv_done").hidden = false;
        $("rv_done_sub").textContent =
          `本轮 ${cards.length} 张：记得 ${stats.known}、模糊 ${stats.vague}、忘了 ${stats.forgot}。`
          + "按记忆曲线，下次该复习的时间已经排好。";
        refreshBadge();
      },
    });
    $("rv_quit").onclick = () => {
      reviewSession = null;
      $("rv_run").hidden = true;
      renderReview();
    };
    reviewSession.start();
  }

  // ---------------------------------------------------------------- 数据
  async function renderData() {
    const s = await Model.stats();
    $("data_stats").innerHTML = statHTML([
      { n: s.items, k: "题目" },
      { n: s.mistakes, k: "错题" },
      { n: s.todayDone, k: "今日作答" },
      { n: s.todayAcc == null ? "—" : s.todayAcc + "%", k: "今日正确率" },
    ]).replace('class="stat-row"', 'class="stat-row four"');
    $("data_stats").classList.add("four");
    const meta = await DB.getMeta("last_import", null);
    /* 注意：写的是 #last_sync 而不是 #import_status ——
       后者是「这次导入的结果」，一旦被这里的常规渲染覆盖，用户就永远看不到
       「同步完成：补入 N 条」这种一次性反馈（实测踩到）。 */
    $("last_sync").textContent = meta
      ? `上次同步：${fmtDate(meta.at)}（题库 ${meta.banks} · 题目 ${meta.items}）`
      : "还没有同步过（首次使用请先导入同步包）。";
  }

  async function onFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const st = $("import_status");
    st.className = "hint";
    st.textContent = "正在读取…";
    try {
      const text = await f.text();
      let data;
      try { data = JSON.parse(text); }
      catch (err) { throw new Error("文件不是合法 JSON（是不是选错了文件？）"); }
      const report = await Model.importBundle(data);
      st.className = "hint ok";
      st.textContent = `同步完成：题库 ${report.banks} 个 · 题目 ${report.items} 道`
        + ` · 补入作答 ${report.attempts} 条 · 补入错题 ${report.mistakes} 道。`
        + "（已有的复习进度未被覆盖）";
      toast("同步完成");
      await renderData();
      await refreshBadge();
    } catch (err) {
      st.className = "hint err";
      st.textContent = "同步失败：" + (err.message || err);
      toast("同步失败");
    } finally {
      e.target.value = "";     // 允许重复选同一个文件
    }
  }

  async function doExport() {
    const data = await Model.exportAll();
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `medkit-phone-backup-${SM2.todayISO()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("已导出，可用它换机恢复");
  }

  async function doClear() {
    if (!confirm("清空本机的题库、作答与错题？此操作不可恢复，建议先导出备份。")) return;
    await DB.clearAll();
    await renderData();
    await refreshBadge();
    toast("已清空");
  }

  // ---------------------------------------------------------------- 角标
  async function refreshBadge() {
    const s = await Model.stats();
    const btn = document.querySelector('.tabbar button[data-tab="review"]');
    if (!btn) return;
    btn.querySelector(".badge")?.remove();
    if (s.due > 0) {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = s.due > 99 ? "99+" : String(s.due);
      btn.appendChild(b);
    }
    if (tab === "drill") {
      const sub = $("page_sub");
      if (sub) sub.textContent = s.due ? `今天有 ${s.due} 道错题到期` : "选一个题库开始";
    }
  }

  function bindGo(scope) {
    (scope || document).querySelectorAll("[data-go]").forEach(b => {
      b.addEventListener("click", () => switchTab(b.dataset.go));
    });
  }

  // ---------------------------------------------------------------- 启动
  async function boot() {
    document.querySelectorAll(".tabbar button").forEach(b => {
      b.addEventListener("click", () => switchTab(b.dataset.tab));
    });
    $("file_input").addEventListener("change", onFile);
    $("btn_export").addEventListener("click", doExport);
    $("btn_clear").addEventListener("click", doClear);
    bindGo(document);

    document.addEventListener("keydown", e => {
      if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (drillSession && !$("drill_run").hidden) drillSession.key(e);
      else if (reviewSession && !$("rv_run").hidden) reviewSession.key(e);
    });

    try {
      await DB.open();
    } catch (e) {
      toast("本地数据库不可用（可能是无痕模式）");
    }
    const initial = (location.hash || "").replace("#", "");
    switchTab(TITLES[initial] ? initial : "drill");
    await refreshBadge();

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js").catch(() => { /* 离线能力降级，不影响使用 */ });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
