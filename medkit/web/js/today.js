/* exported loadStart, todayOpenStats, todayOpenWeak, todayDrill, todayReview, todayStop, todayImport, todayPaste */
/* ---- 今天页（2026-10-29「书桌」）：一张任务单 ----
 *
 * 设计口径见 docs/0.23.0-前端重设计_活人感_2026-10-29.md §5.3 / §7.1。
 * 三件事：① 抬头说「现在」（日期 / 问候 / 一句具体现状）；
 *         ② 正文只给三行任务，第一行有且只有一个主按钮；
 *         ③ 统计与知识点退到右栏折叠卡——periphery，不是第二个仪表盘。
 * **没有数据时不显示 0**：首启只给「先放一份题库进来」。
 * 依赖（全部运行期调用，加载期无前向引用）：app.js 的 $/api/showTab/toast/genEnabled/
 * examLoad/cachedDashboard/setLearnNavBadge/renderExamPlans；bank 域的三个入口走 typeof 守卫。
 */

/* ---- ① 今天：一张任务单 ----
 *
 * 设计口径见 docs/0.23.0-前端重设计_活人感_2026-10-29.md §5.3 / §7.1。
 * 三件事：① 抬头说「现在」（日期 / 问候 / 一句具体现状）；
 *         ② 正文只给三行任务，第一行有且只有一个主按钮；
 *         ③ 统计与知识点退到右栏折叠卡——periphery，不是第二个仪表盘。
 * **没有数据时不显示 0**：首启只给「先放一份题库进来」。
 */
const TODAY_GOAL_KEY = "medkit-bank-goal";

function _todayGoal() {
  let v = 20;
  try {
    const raw = localStorage.getItem(TODAY_GOAL_KEY);
    if (raw !== null && raw !== "" && !isNaN(Number(raw))) v = Math.max(1, Math.min(Number(raw), 500));
  } catch (e) { /* 隐私模式：用默认值 */ }
  return v;
}
function _todayMin(n) { return Math.max(2, Math.round(Number(n || 0) * 0.7)); }
function _todayWeek(d) { return "日一二三四五六"[d.getDay()]; }
function _todayExam() {
  try {
    if (typeof examLoad !== "function") return null;
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const rows = (examLoad() || []).map(e => {
      const dt = new Date(String(e.date || "") + "T00:00:00");
      return { e, dt };
    }).filter(x => !isNaN(x.dt.getTime()) && x.dt >= now)
      .sort((a, b) => a.dt - b.dt);
    if (!rows.length) return null;
    return { e: rows[0].e, days: Math.round((rows[0].dt - now) / 86400000) };
  } catch (e) { return null; }
}
function _todayExamText(x) {
  if (!x) return "";
  const title = x.e.title || "考试";
  return x.days === 0 ? `今天考${title}` : `距${title} ${x.days} 天`;
}
function _todaySetHead(now) {
  const m = now.getMonth() + 1, d = now.getDate();
  const exText = _todayExamText(_todayExam());
  const dateText = `${m} 月 ${d} 日 · 周${_todayWeek(now)}`;
  const dateEl = $("today_date");
  if (dateEl) dateEl.textContent = exText ? `${dateText} · ${exText}` : dateText;
  const sideD = $("side_date");
  if (sideD) sideD.textContent = `${m} 月 ${d} 日`;
  const sideW = $("side_week");
  if (sideW) sideW.textContent = `周${_todayWeek(now)}` + (exText ? ` · ${exText}` : "");
  const examMeta = $("exam_meta");
  if (examMeta) examMeta.textContent = exText || "还没设日期";
}
function _todayTopTopic(items) {
  const count = {};
  for (const it of (items || [])) {
    const name = String(it.topic || it.chapter || it.subject || "").trim();
    if (name) count[name] = (count[name] || 0) + 1;
  }
  const rows = Object.entries(count).sort((a, b) => b[1] - a[1]);
  return rows.length ? { name: rows[0][0], n: rows[0][1] } : null;
}
function _todayOpenFold(id) {
  const el = $(id);
  if (!el) return;
  el.open = true;
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
}
function todayOpenStats() { _todayOpenFold("stats_card"); }
function todayOpenWeak() { _todayOpenFold("weak_card"); }
function todayImport() {
  showTab("study");
  if (typeof bankImportPick === "function") bankImportPick();
}
function todayPaste() {
  showTab("study");
  if (typeof bankPasteOpen === "function") bankPasteOpen();
}
function todayDrill(bid, limit) {
  showTab("study");
  if (typeof bankDrillStart !== "function") return;
  const n = Math.max(1, Number(limit || _todayGoal()) || 20);
  bankDrillStart(String(bid || ""), "all", { runmode: "practice", limit: n });
}
function todayReview(limit) {
  showTab("learn");
  if (typeof bankDrillStartMistakes !== "function") return;
  bankDrillStartMistakes("all", "", { runmode: "practice", limit: Math.max(0, Number(limit || 0)) });
}
function todayStop() {
  const sheet = $("today_sheet");
  if (sheet) sheet.classList.add("is-done");
  const foot = $("today_sheet_foot");
  if (foot) foot.innerHTML = '<span class="meta">好，今天就到这儿。明天见。</span>';
  toast("今天到这儿。明天见。");
}
window.todayOpenStats = todayOpenStats;
window.todayImport = todayImport;
window.todayPaste = todayPaste;
window.todayOpenWeak = todayOpenWeak;
window.todayDrill = todayDrill;
window.todayReview = todayReview;
window.todayStop = todayStop;

function _todayFirstRun(on) {
  const grid = document.querySelector(".today-grid");
  if (grid) grid.classList.toggle("is-firstrun", !!on);
}

async function loadStart() {
  const box = $("start_body");
  if (!box) return;
  _todayFirstRun(false);
  const now = new Date();
  const h = now.getHours();
  const late = h >= 23 || h < 5;
  _todaySetHead(now);
  const helloEl = $("today_hello");
  if (helloEl) {
    helloEl.textContent = h < 5 ? "夜深了。" : h < 11 ? "早上好。"
      : h < 14 ? "中午好。" : h < 18 ? "下午好。" : h < 23 ? "晚上好。" : "夜深了。";
  }
  const subEl = $("today_sub");
  try {
    const safe = p => p.catch(() => null);
    const [d, banksR, todayR, dueR] = await Promise.all([
      safe(typeof cachedDashboard === "function" ? cachedDashboard("") : api("/api/library/dashboard")),
      safe(api("/api/bank/banks")),
      safe(api("/api/bank/today")),
      safe(api("/api/vault/review/due")),
    ]);
    const dash = d || {};
    const mastery = dash.mastery || {};
    const banks = (banksR && banksR.banks) || [];
    const bank = banks[0] || null;
    const attempts = Number((todayR && todayR.attempts) || 0);
    const goal = _todayGoal();
    const dueItems = (dueR && dueR.items) || [];
    const dueCount = Number((dueR && dueR.count) || dueItems.length || 0);
    const weakCount = Number(mastery.weak || 0);
    const weakest = (dash.readiness && dash.readiness.weakest && dash.readiness.weakest[0])
      ? String(dash.readiness.weakest[0].name || "") : "";
    const topDue = _todayTopTopic(dueItems);
    const bankLabel = [bank && bank.name, bank && bank.term].filter(Boolean).join(" · ") || "题库";

    // 首启：没有题库 / 题库还是空的 → 一张便签，一个动作，一个替代
    if (!bank || Number(bank.items || 0) === 0) {
      _todayFirstRun(true);
      if (subEl) subEl.textContent = "先放一份题库进来。";
      box.innerHTML = `<div class="sheet firstrun">
        <h2>先放一份题库进来。</h2>
        <p>导入老师发的 .md，或者粘贴一段题干试试。软件不自带题库，题目版权归你。</p>
        <div class="btns">
          <button class="act" onclick="todayImport()">导入题库文件</button>
          <button class="act gray" onclick="todayPaste()">粘贴一段题干</button>
        </div>
        <div class="tiny">导入后，这一页会按你的错题和薄弱点排三件事。</div>
      </div>`;
      renderExamPlans();
      return;
    }

    // 抬头：一句具体现状（不空夸、不报 0）
    if (subEl) {
      if (late && dueCount) {
        subEl.textContent = `快 ${h}:${String(now.getMinutes()).padStart(2, "0")} 了。今天不开始新题，过 ${Math.min(5, dueCount)} 道错题就睡。`;
      } else if (late) {
        subEl.textContent = "夜深了。今天不开始新题，明天再看。";
      } else if (dueCount) {
        subEl.textContent = `今天还有 ${dueCount} 道错题要复习`
          + (topDue && topDue.n > 1 ? `，其中 ${topDue.n} 道是「${topDue.name}」` : "") + "。";
      } else if (attempts >= goal) {
        subEl.textContent = `今天的 ${goal} 题做完了。到这儿就很好。`;
      } else if (weakCount) {
        subEl.textContent = `今天练了 ${attempts} 道，还差 ${Math.max(0, goal - attempts)} 道到目标。`;
      } else {
        subEl.textContent = "今天没有到期的错题。练一组新的，或者把错题过一遍。";
      }
    }

    // 任务单：按「今天必须做 → 可以做 → 可选」排序
    const tasks = [];
    if (late && dueCount) {
      const n = Math.min(5, dueCount);
      tasks.push({ t: `过 ${n} 道错题`, m: `${n} 道 · 约 ${_todayMin(n)} 分钟`, a: "todayReview(5)", b: "开始", p: true, n });
      tasks.push({ t: "看看这周", m: "练习记录与掌握率", a: "todayOpenStats()", b: "看看", p: false, n: 1 });
    } else {
      if (dueCount) {
        tasks.push({
          t: "复习错题",
          m: `${dueCount} 道 · 约 ${_todayMin(dueCount)} 分钟`
            + (topDue && topDue.n > 1 ? ` · ${topDue.name} ${topDue.n} 道` : ""),
          a: "todayReview()", b: "开始", p: true, n: _todayMin(dueCount),
        });
      }
      if (bank && Number(bank.items || 0) > 0) {
        tasks.push({
          t: "练一组新的", m: `${goal} 道 · 约 ${_todayMin(goal)} 分钟 · ${bankLabel}`,
          a: `todayDrill('${esc(String(bank.id || ""))}')`, b: dueCount ? "选题库" : "开始", p: !dueCount, n: _todayMin(goal),
        });
      }
      if (weakCount || weakest) {
        tasks.push({
          t: "看一眼薄弱点",
          m: `${weakCount || 1} 个` + (weakest ? ` · 先看「${weakest}」` : ""),
          a: "todayOpenWeak()", b: "看看", p: false, n: 2,
        });
      }
      if (!dueCount && attempts >= goal) {
        tasks.unshift({ t: "今天的题做完了", m: `已练 ${attempts} 道 · 到这儿就很好`, a: "", b: "", p: false, n: 0 });
      }
    }
    if (!tasks.length) {
      tasks.push({ t: "去题库看看", m: "导入或换一份题库", a: "showTab('study')", b: "去题库", p: true, n: 1 });
    }
    const totalMin = tasks.reduce((sum, x) => sum + (x.n || 0), 0);
    const rows = tasks.map(x => `<div class="task${x.p ? " first" : ""}">
        <span class="circ" aria-hidden="true"></span>
        <div class="tx"><b>${esc(x.t)}</b><span>${esc(x.m)}</span></div>
        ${x.b ? `<button class="act ${x.p ? "" : "gray"} go" onclick="${x.a}">${esc(x.b)}</button>` : ""}
      </div>`).join("");
    box.innerHTML = `<div class="sheet" id="today_sheet">
        <div class="sheet-h"><h2>今天这一页</h2><span class="hint">${tasks.length} 件${totalMin ? ` · 约 ${totalMin} 分钟` : ""}</span></div>
        <div class="sheet-b">${rows}</div>
        <div class="sheet-f" id="today_sheet_foot">
          <span class="meta">做完这一页，今天就够了。</span>
          <button class="act quiet" onclick="todayStop()">今天到这儿</button>
        </div>
      </div>
      ${late ? `<div class="today-note"><span>题不会跑。明天早上再看，效果比现在硬撑好。</span></div>` : ""}`;
    renderExamPlans();
    if (typeof setLearnNavBadge === "function") {
      const tutor = (dash.tutor && dash.tutor.in_progress) || 0;
      setLearnNavBadge(dueCount + tutor, { due: dueCount, tutor });
    }
  } catch (e) {
    _todayFirstRun(true);
    if (subEl) subEl.textContent = "这一页没读到。题库和错题都还在。";
    box.innerHTML = `<div class="sheet firstrun">
      <h2>这一页没读到。</h2>
      <p>题库和错题都还在。点「再试一次」；还不行就重启 MedKit。</p>
      <div class="btns"><button class="act" onclick="loadStart()">再试一次</button></div>
      <details class="hint"><summary>技术细节</summary><code>${esc(e.message)}</code></details>
    </div>`;
  }
}
